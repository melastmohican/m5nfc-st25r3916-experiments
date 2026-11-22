package nfc

import (
	"bytes"
	"testing"
)

var testUID = [7]byte{0x04, 0x11, 0x22, 0x33, 0x44, 0x55, 0x66}

func TestType2Tag(t *testing.T) {
	tag := NewType2Tag(testUID)
	out := make([]byte, 64)
	h := func(f ...byte) TargetReply { return tag.Handle(f, out) }

	// READ page 0: UID with BCC bytes
	r := h(0x30, 0x00)
	if r.Len != 16 || r.Ack4 {
		t.Fatalf("READ 0: %+v", r)
	}
	if out[0] != 0x04 || out[3] != 0x88^0x04^0x11^0x22 || out[4] != 0x33 || out[8] != 0x33^0x44^0x55^0x66 {
		t.Errorf("UID/BCC wrong: % X", out[:16])
	}
	if out[12] != 0xE1 || out[13] != 0x10 || out[14] != 0x3E {
		t.Errorf("CC wrong: % X", out[12:16])
	}
	// a blank NTAG215: AUTH0 = FF, PWD = FF FF FF FF
	r = h(0x30, 0x83)
	if r.Len != 16 || out[3] != 0xFF || out[8] != 0xFF || out[11] != 0xFF {
		t.Errorf("config pages wrong: % X", out[:16])
	}
	// READ wraps past the last page
	r = h(0x30, 0x86)
	if r.Len != 16 || !bytes.Equal(out[4:16], tag.Memory()[:12]) {
		t.Errorf("READ wrap: % X", out[:16])
	}
	// READ out of range
	if r = h(0x30, 0x87); r.Len != 0 || !r.Ack4 || r.Nibble != 0x0 {
		t.Errorf("READ 135: %+v", r)
	}
	// FAST_READ pages 3..5
	if r = h(0x3A, 0x03, 0x05); r.Len != 12 || out[0] != 0xE1 {
		t.Errorf("FAST_READ: %+v", r)
	}
	if r = h(0x3A, 0x05, 0x03); !r.Ack4 || r.Nibble != 0x0 {
		t.Errorf("FAST_READ reversed: %+v", r)
	}
	// WRITE: ACK, stored, marked changed
	if tag.Changed(4) {
		t.Error("page 4 changed too early")
	}
	r = h(0xA2, 0x04, 0x03, 0x0A, 0xD1, 0x01)
	if !r.Ack4 || r.Nibble != 0xA || tag.WriteCount() != 1 {
		t.Errorf("WRITE: %+v", r)
	}
	m := tag.Memory()
	if m[16] != 0x03 || m[19] != 0x01 || !tag.Changed(4) || tag.Changed(5) {
		t.Error("WRITE not stored or wrong changed bits")
	}
	// the same data again is ACKed but does not mark the page again
	tag.ClearChanged()
	r = h(0xA2, 0x04, 0x03, 0x0A, 0xD1, 0x01)
	if !r.Ack4 || r.Nibble != 0xA || tag.Changed(4) {
		t.Errorf("repeat WRITE: %+v changed=%v", r, tag.Changed(4))
	}
	// UID and lock pages are refused
	if r = h(0xA2, 0x00, 1, 2, 3, 4); !r.Ack4 || r.Nibble != 0x0 || tag.Memory()[0] != 0x04 {
		t.Errorf("WRITE page 0: %+v", r)
	}
	if r = h(0xA2, 0x02, 1, 2, 3, 4); !r.Ack4 || r.Nibble != 0x0 {
		t.Errorf("WRITE page 2: %+v", r)
	}
	// wrong length
	if r = h(0xA2, 0x04, 1, 2, 3); !r.Ack4 || r.Nibble != 0x1 {
		t.Errorf("short WRITE: %+v", r)
	}
	// GET_VERSION, READ_SIG
	if r = h(0x60); r.Len != 8 || out[2] != 0x04 || out[6] != 0x11 {
		t.Errorf("GET_VERSION: %+v % X", r, out[:8])
	}
	if r = h(0x3C, 0x00); r.Len != 32 {
		t.Errorf("READ_SIG: %+v", r)
	}
	// HLTA ends the session without a reply
	if r = h(0x50, 0x00); !r.Halt || r.Len != 0 || r.Ack4 {
		t.Errorf("HLTA: %+v", r)
	}
	// unknown command: NAK
	if r = h(0x99); !r.Ack4 || r.Nibble != 0x0 {
		t.Errorf("unknown: %+v", r)
	}
}

func txf(tg Target, f ...byte) ([]byte, TargetReply) {
	out := make([]byte, 300)
	r := tg.Handle(f, out)
	return append([]byte(nil), out[:r.Len]...), r
}

func eq(t *testing.T, line int, got []byte, want ...byte) {
	t.Helper()
	if !bytes.Equal(got, want) {
		t.Errorf("check %d: got % X, want % X", line, got, want)
	}
}

func TestType4ThroughIsoDep(t *testing.T) {
	app := NewType4NdefApp()
	tg := NewIsoDepTarget(app)

	if r, _ := txf(tg, 0x02, 0x00, 0xA4, 0x04, 0x00); len(r) != 0 { // before RATS: silent
		t.Errorf("answered before RATS: % X", r)
	}
	ats, _ := txf(tg, 0xE0, 0x80)
	eq(t, 1, ats, 0x05, 0x78, 0x80, 0x80, 0x00)
	if !tg.Activated() {
		t.Fatal("not activated")
	}
	r, _ := txf(tg, 0x02, 0x00, 0xA4, 0x04, 0x00, 0x07, 0xD2, 0x76, 0x00, 0x00, 0x85, 0x01, 0x01, 0x00)
	eq(t, 2, r, 0x02, 0x90, 0x00)
	r, _ = txf(tg, 0x03, 0x00, 0xA4, 0x00, 0x0C, 0x02, 0xE1, 0x03) // block number 1 is mirrored
	eq(t, 3, r, 0x03, 0x90, 0x00)
	r, _ = txf(tg, 0x02, 0x00, 0xB0, 0x00, 0x00, 0x0F)
	if len(r) != 1+15+2 || r[0] != 0x02 || r[1] != 0x00 || r[2] != 0x0F || r[3] != 0x20 || r[len(r)-2] != 0x90 {
		t.Errorf("READ BINARY of the CC: % X", r)
	}
	r, _ = txf(tg, 0x03, 0x00, 0xA4, 0x00, 0x0C, 0x02, 0xE1, 0x04)
	eq(t, 4, r, 0x03, 0x90, 0x00)
	r, _ = txf(tg, 0x02, 0x00, 0xB0, 0x00, 0x00, 0x02)
	eq(t, 5, r, 0x02, 0x00, 0x00, 0x90, 0x00)

	// UPDATE BINARY: NLEN = 0 first, then the data, then the real NLEN (the usual order)
	r, _ = txf(tg, 0x03, 0x00, 0xD6, 0x00, 0x00, 0x02, 0x00, 0x00)
	eq(t, 6, r, 0x03, 0x90, 0x00)
	r, _ = txf(tg, 0x02, 0x00, 0xD6, 0x00, 0x02, 0x05, 0xD1, 0x01, 0x01, 0x54, 0x00)
	eq(t, 7, r, 0x02, 0x90, 0x00)
	r, _ = txf(tg, 0x03, 0x00, 0xD6, 0x00, 0x00, 0x02, 0x00, 0x05)
	eq(t, 8, r, 0x03, 0x90, 0x00)
	if f := app.NdefFile(); f[1] != 0x05 || f[2] != 0xD1 || app.UpdateCount() != 3 {
		t.Errorf("NDEF file: % X, updates %d", f[:8], app.UpdateCount())
	}

	// an R(NAK) repeats the last I-block; an R(NAK) with a fresh block number just acknowledges
	r, _ = txf(tg, 0xB3)
	eq(t, 9, r, 0x03, 0x90, 0x00)
	r, _ = txf(tg, 0xB2)
	eq(t, 10, r, 0xA2)

	// chaining: two I-blocks with the chain bit set, then the last one
	app2 := NewType4NdefApp()
	t2 := NewIsoDepTarget(app2)
	txf(t2, 0xE0, 0x80)
	txf(t2, 0x02, 0x00, 0xA4, 0x04, 0x00, 0x07, 0xD2, 0x76, 0x00, 0x00, 0x85, 0x01, 0x01)
	txf(t2, 0x03, 0x00, 0xA4, 0x00, 0x0C, 0x02, 0xE1, 0x04)
	r, _ = txf(t2, 0x12, 0x00, 0xD6, 0x00, 0x00) // first part of a 4-byte UPDATE BINARY
	eq(t, 11, r, 0xA2)
	r, _ = txf(t2, 0x13, 0x04, 0xAA, 0xBB)
	eq(t, 12, r, 0xA3)
	r, _ = txf(t2, 0x02, 0xCC, 0xDD)
	eq(t, 13, r, 0x02, 0x90, 0x00)
	if f := app2.NdefFile(); f[0] != 0xAA || f[3] != 0xDD {
		t.Errorf("chained UPDATE BINARY: % X", f[:4])
	}

	// errors: unknown file, wrong class, unknown instruction
	r, _ = txf(t2, 0x03, 0x00, 0xA4, 0x00, 0x0C, 0x02, 0x3F, 0x00)
	eq(t, 14, r, 0x03, 0x6A, 0x82)
	r, _ = txf(t2, 0x02, 0x80, 0x00, 0x00, 0x00)
	eq(t, 15, r, 0x02, 0x6E, 0x00)
	r, _ = txf(t2, 0x03, 0x00, 0x88, 0x00, 0x00)
	eq(t, 16, r, 0x03, 0x6D, 0x00)

	// DESELECT answers and halts; afterwards the layer is silent until the next RATS
	r, rep := txf(t2, 0xC2)
	eq(t, 17, r, 0xC2)
	if !rep.Halt || t2.Activated() {
		t.Error("DESELECT did not halt")
	}
	if r, _ = txf(t2, 0x02, 0x00, 0xB0, 0x00, 0x00, 0x02); len(r) != 0 {
		t.Errorf("answered after DESELECT: % X", r)
	}
	r, _ = txf(t2, 0xE0, 0x80)
	eq(t, 18, r, ats...)
}

func TestSniffApp(t *testing.T) {
	sniff := NewSniffApp()
	t3 := NewIsoDepTarget(sniff)
	txf(t3, 0xE0, 0x80)
	r, _ := txf(t3, 0x02, 0xF0, 0xD2, 0x00, 0x00, 0x03, 0x11, 0x22, 0x33)
	eq(t, 1, r, 0x02, 0x90, 0x00)
	r, _ = txf(t3, 0x03, 0xF0, 0xDE, 0x00, 0x00, 0x01)
	eq(t, 2, r, 0x03, 0x00, 0x90, 0x00)
	if sniff.ApduCount() != 2 || sniff.SessionCount() != 1 {
		t.Errorf("counts: %d APDUs, %d sessions", sniff.ApduCount(), sniff.SessionCount())
	}
	// a refresh (F0 D4) is answered with five S(WTX) requests, then the I-block (same block number)
	r, _ = txf(t3, 0x02, 0xF0, 0xD4, 0x05, 0x80, 0x00)
	eq(t, 3, r, 0xF2, 0x01)
	for i := 1; i < SniffRefreshWtxRequests; i++ {
		r, _ = txf(t3, 0xF2, 0x01)
		eq(t, 4, r, 0xF2, 0x01)
	}
	r, _ = txf(t3, 0xF2, 0x01)
	eq(t, 5, r, 0x02, 0x90, 0x00)
	// after a refresh the first busy polls answer busy (01), then idle (00)
	for i := 0; i < SniffBusyPolls; i++ {
		r, _ = txf(t3, byte(0x03^(i&1)), 0xF0, 0xDE, 0x00, 0x00, 0x01)
		if len(r) != 4 || r[1] != 0x01 {
			t.Errorf("busy poll %d: % X", i, r)
		}
	}
	r, _ = txf(t3, 0x03, 0xF0, 0xDE, 0x00, 0x00, 0x01)
	if len(r) != 4 || r[1] != 0x00 {
		t.Errorf("idle poll: % X", r)
	}
	if sniff.ApduCount() != 2+1+SniffBusyPolls+1 {
		t.Errorf("APDU count %d", sniff.ApduCount())
	}
	// class 00 commands are served by a real NDEF tag
	txf(t3, 0x02, 0x00, 0xA4, 0x04, 0x00, 0x07, 0xD2, 0x76, 0x00, 0x00, 0x85, 0x01, 0x01)
	txf(t3, 0x03, 0x00, 0xA4, 0x00, 0x0C, 0x02, 0xE1, 0x03)
	r, _ = txf(t3, 0x02, 0x00, 0xB0, 0x00, 0x00, 0x0F)
	if len(r) != 1+15+2 || r[1] != 0x00 || r[2] != 0x0F || r[len(r)-2] != 0x90 {
		t.Errorf("READ BINARY via sniffer: % X", r)
	}
	// the capture is a list of [kind][len hi][len lo][bytes] records, commands and answers alternating
	sniff.Clear()
	txf(t3, 0x02, 0xF0, 0xD2, 0x00, 0x00, 0x03, 0x11, 0x22, 0x33)
	c := sniff.Capture()
	if c[0] != 1 || c[2] != 8 || c[3] != 0xF0 || c[3+8] != 2 || c[3+8+2] != 2 {
		t.Errorf("capture: % X", c)
	}
}
