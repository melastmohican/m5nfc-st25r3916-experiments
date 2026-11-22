package nfc

// ISO 14443-4 (ISO-DEP) block layer for card emulation: the PICC side of RATS/ATS, I-blocks,
// R-blocks, S(DESELECT) and chaining of received commands. No hardware. The application on top sees
// whole APDUs.
//
// Limits: no CID, no NAD, 106 kbit/s only, replies are never chained (an application must keep its
// response within IsoDepMaxResponse bytes).
const (
	IsoDepMaxResponse = 250  // fits one I-block of a 256-byte frame
	IsoDepMaxCommand  = 1024 // chained commands are collected up to this size
)

// ApduApp answers whole APDUs. Apdu returns the response length (including SW1 SW2).
type ApduApp interface {
	Apdu(cmd, resp []byte) int
	Reset() // a new ISO-DEP session starts (RATS)
	// TakeWtxRequests: after Apdu(), how many S(WTX) "wait longer" requests to send before the answer,
	// for a command that takes a while (a screen refresh). 0 = answer at once. Read once per command.
	TakeWtxRequests() int
}

// The ATS sent in answer to RATS (TL T0 TA1 TB1 TC1): FSC 256, 106 kbit/s only, FWT about 77 ms.
var isoDepATS = [5]byte{0x05, 0x78, 0x80, 0x80, 0x00}

// IsoDepATS returns a copy of the ATS.
func IsoDepATS() []byte { a := isoDepATS; return a[:] }

type IsoDepTarget struct {
	app       ApduApp
	activated bool
	cmd       [IsoDepMaxCommand]byte
	cmdLen    int
	last      [IsoDepMaxResponse + 3]byte // last I-block sent, for retransmission after an R(NAK)
	lastLen   int
	lastBn    byte
	// a delayed answer: S(WTX) requests go out first, the I-block follows the last WTX response
	pending    [IsoDepMaxResponse + 3]byte
	pendingLen int
	wtxTotal   int
	wtxSent    int
}

func NewIsoDepTarget(app ApduApp) *IsoDepTarget { return &IsoDepTarget{app: app} }

func (t *IsoDepTarget) Activated() bool { return t.activated }

func (t *IsoDepTarget) Reset() {
	t.activated = false
	t.cmdLen = 0
	t.lastLen = 0
	t.lastBn = 0
	t.pendingLen = 0
	t.wtxTotal, t.wtxSent = 0, 0
	t.app.Reset()
}

func (t *IsoDepTarget) Handle(f, out []byte) TargetReply {
	var r TargetReply
	n := len(f)
	if n == 0 || len(out) < IsoDepMaxResponse+3 {
		return r
	}

	if f[0] == 0xE0 { // RATS: E0 <FSDI|CID>
		if n != 2 {
			return r
		}
		t.Reset()
		t.activated = true
		copy(out, isoDepATS[:])
		r.Len = len(isoDepATS)
		return r
	}
	if !t.activated {
		return r
	}

	pcb := f[0]
	bn := pcb & 0x01

	if pcb&0xC2 == 0x02 { // I-block (bit 2 is always 1)
		if pcb&0x0C != 0 { // CID or NAD present: not supported, stay silent
			return r
		}
		info := f[1:]
		if t.cmdLen+len(info) > IsoDepMaxCommand {
			t.cmdLen = 0
			return r
		}
		copy(t.cmd[t.cmdLen:], info)
		t.cmdLen += len(info)
		if pcb&0x10 != 0 { // chaining: ask for the next block
			out[0] = 0xA2 | bn
			r.Len = 1
			return r
		}
		rl := t.app.Apdu(t.cmd[:t.cmdLen], out[1:1+IsoDepMaxResponse])
		t.cmdLen = 0
		if rl > IsoDepMaxResponse {
			rl = 0
		}
		out[0] = 0x02 | bn
		r.Len = rl + 1
		copy(t.last[:], out[:r.Len])
		t.lastLen = r.Len
		t.lastBn = bn
		if wtx := t.app.TakeWtxRequests(); wtx > 0 { // answer later: ask the reader to wait
			copy(t.pending[:], out[:r.Len])
			t.pendingLen = r.Len
			t.wtxTotal = wtx
			t.wtxSent = 1
			out[0] = 0xF2 // S(WTX) request, WTXM = 1
			out[1] = 0x01
			r.Len = 2
		}
		return r
	}

	if pcb&0xC2 == 0x82 { // R-block: ACK (0xA2/0xA3) or NAK (0xB2/0xB3)
		if pcb&0x10 != 0 { // NAK: repeat the last I-block, or acknowledge if we have none
			if t.lastLen != 0 && bn == t.lastBn {
				copy(out, t.last[:t.lastLen])
				r.Len = t.lastLen
			} else {
				out[0] = 0xA2 | bn
				r.Len = 1
			}
		} else if t.lastLen != 0 && bn == t.lastBn { // ACK for a block we already sent: repeat it
			copy(out, t.last[:t.lastLen])
			r.Len = t.lastLen
		}
		return r
	}

	if pcb&0xF7 == 0xF2 && t.pendingLen != 0 { // S(WTX) response from the reader
		if t.wtxSent < t.wtxTotal {
			t.wtxSent++
			out[0] = 0xF2
			out[1] = 0x01
			r.Len = 2
		} else { // the wait is over: the delayed answer
			copy(out, t.pending[:t.pendingLen])
			r.Len = t.pendingLen
			t.pendingLen = 0
		}
		return r
	}

	if pcb&0xC7 == 0xC2 { // S-block
		if pcb&0x30 == 0x00 { // DESELECT: answer and leave the session
			out[0] = 0xC2
			r.Len = 1
			r.Halt = true
			t.activated = false
			t.cmdLen = 0
		}
	}
	return r
}
