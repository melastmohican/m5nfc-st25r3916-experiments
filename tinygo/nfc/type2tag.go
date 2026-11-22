package nfc

// An NTAG215-like NFC Forum Type 2 tag in RAM, for card emulation (see listener.go).
// Handles one command frame (CRC already removed) and says what to send back.
//
//	READ 30 pp        16 bytes (wraps past the last page, as a real tag does)
//	FAST_READ 3A s e  pages s..e
//	WRITE A2 pp d0..3 ACK; pages 0-2 (UID, lock) are refused with a NAK
//	GET_VERSION 60    NTAG215: 00 04 04 02 01 00 11 03
//	READ_SIG 3C 00    32 zero bytes (an emulated tag has no signature)
//	HLTA 50 00        no reply, the tag goes to the halt state
//	anything else     NAK
const (
	T2Pages = 135 // NTAG215
	T2Bytes = T2Pages * 4
)

type Type2Tag struct {
	mem     [T2Bytes]byte
	changed [(T2Pages + 7) / 8]byte
	writes  int
}

// NewType2Tag has the 7-byte UID (BCC bytes are computed), capability container E1 10 3E 00 and an
// empty NDEF message.
func NewType2Tag(uid [7]byte) *Type2Tag {
	t := &Type2Tag{}
	copy(t.mem[0:3], uid[0:3])
	t.mem[3] = 0x88 ^ uid[0] ^ uid[1] ^ uid[2] // BCC0 (cascade tag 0x88)
	copy(t.mem[4:8], uid[3:7])
	t.mem[8] = uid[3] ^ uid[4] ^ uid[5] ^ uid[6] // BCC1
	t.mem[12], t.mem[13], t.mem[14], t.mem[15] = 0xE1, 0x10, 0x3E, 0x00
	// Configuration pages of a blank NTAG215: no protection (AUTH0 = FF), default password.
	t.mem[130*4+3] = 0xBD // dynamic lock bytes
	t.mem[131*4+3] = 0xFF // AUTH0: no page needs the password
	t.mem[132*4+1] = 0x05 // ACCESS / VCTID
	for i := 0; i < 4; i++ {
		t.mem[133*4+i] = 0xFF // PWD
	}
	t.mem[16], t.mem[17], t.mem[18] = 0x03, 0x00, 0xFE // empty NDEF TLV
	return t
}

func (t *Type2Tag) Memory() []byte  { return t.mem[:] }
func (t *Type2Tag) WriteCount() int { return t.writes }
func (t *Type2Tag) Reset()          {}

// Changed is true when a WRITE changed the page since construction or ClearChanged.
func (t *Type2Tag) Changed(page int) bool {
	return page >= 0 && page < T2Pages && (t.changed[page>>3]>>(uint(page)&7))&1 == 1
}

func (t *Type2Tag) ClearChanged() {
	for i := range t.changed {
		t.changed[i] = 0
	}
}

func nak(code byte) TargetReply { return TargetReply{Ack4: true, Nibble: code} }

func (t *Type2Tag) Handle(f, out []byte) TargetReply {
	var r TargetReply
	n := len(f)
	if n == 0 || len(out) < 64 {
		return r
	}
	switch f[0] {
	case 0x30: // READ
		if n != 2 || int(f[1]) >= T2Pages {
			return nak(0x0)
		}
		for i := 0; i < 16; i++ {
			out[i] = t.mem[(int(f[1])*4+i)%T2Bytes]
		}
		r.Len = 16
	case 0x3A: // FAST_READ
		if n != 3 || f[1] > f[2] || int(f[2]) >= T2Pages {
			return nak(0x0)
		}
		bytes := (int(f[2]) - int(f[1]) + 1) * 4
		if bytes > len(out) {
			return nak(0x0)
		}
		copy(out, t.mem[int(f[1])*4:int(f[1])*4+bytes])
		r.Len = bytes
	case 0xA2: // WRITE
		if n != 6 {
			return nak(0x1)
		}
		p := int(f[1])
		if p < 3 || p >= T2Pages {
			return nak(0x0)
		}
		if string(t.mem[p*4:p*4+4]) != string(f[2:6]) {
			copy(t.mem[p*4:p*4+4], f[2:6])
			t.changed[p>>3] |= 1 << (uint(p) & 7)
		}
		t.writes++
		r.Ack4, r.Nibble = true, 0xA
	case 0x60: // GET_VERSION (NTAG215)
		copy(out, []byte{0x00, 0x04, 0x04, 0x02, 0x01, 0x00, 0x11, 0x03})
		r.Len = 8
	case 0x3C: // READ_SIG
		for i := 0; i < 32; i++ {
			out[i] = 0
		}
		r.Len = 32
	case 0x50: // HLTA
		if n == 2 && f[1] == 0x00 {
			r.Halt = true
			return r
		}
		return nak(0x0)
	default:
		return nak(0x0)
	}
	return r
}
