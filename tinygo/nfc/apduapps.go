package nfc

// Applications for the ISO-DEP target (isodeptarget.go). No hardware.
//
//	Type4NdefApp  an NFC Forum Type 4 tag: SELECT the NDEF application (D2760000850101), the
//	              capability container E103 and the NDEF file E104, READ BINARY and UPDATE BINARY.
//	SniffApp      records every APDU in full. Class 00 commands (SELECT, READ BINARY) are served
//	              by a real Type 4 NDEF tag, so the phone's own background tag reader finishes
//	              instead of looping; everything else gets 90 00, so an app that sends a vendor
//	              protocol (for example F0 D2 image packets) keeps talking.
const (
	Type4NdefFileSize = 1024 // NLEN (2 bytes) + message
	Type4MaxRead      = 0xF0 // MLe / MLc announced in the capability container

	SniffCaptureSize = 16384
	// After a screen refresh (F0 D4) the first SniffBusyPolls busy queries (F0 DE) answer "busy" (01),
	// like a real panel that needs a second or two; then "idle" (00).
	SniffBusyPolls = 3
	// A real e-paper board answers the refresh command (F0 D4) with S(WTX) "wait longer" requests,
	// five of them in the Flipper Zero app's model of a Good Display board, then 90 00.
	SniffRefreshWtxRequests = 5
)

var ndefAID = [7]byte{0xD2, 0x76, 0x00, 0x00, 0x85, 0x01, 0x01}

func apduSW(resp []byte, sw1, sw2 byte) int {
	resp[0], resp[1] = sw1, sw2
	return 2
}

type t4Selected uint8

const (
	t4None t4Selected = iota
	t4App
	t4CC
	t4NdefFile
)

type Type4NdefApp struct {
	cc          [15]byte
	ndef        [Type4NdefFileSize]byte
	selected    t4Selected
	appSelected bool
	updates     int
}

func NewType4NdefApp() *Type4NdefApp {
	a := &Type4NdefApp{}
	a.cc = [15]byte{0x00, 0x0F, // CCLEN
		0x20,                                   // mapping version 2.0
		0x00, Type4MaxRead, 0x00, Type4MaxRead, // MLe, MLc
		0x04, 0x06, 0xE1, 0x04, // NDEF file control TLV: file E104
		Type4NdefFileSize >> 8, Type4NdefFileSize & 0xFF, // maximum NDEF file size
		0x00, 0x00} // read and write access: open
	return a // NLEN = 0: empty message
}

func (a *Type4NdefApp) Reset()               { a.selected = t4None }
func (a *Type4NdefApp) TakeWtxRequests() int { return 0 }
func (a *Type4NdefApp) NdefFile() []byte     { return a.ndef[:] } // NLEN + message
func (a *Type4NdefApp) UpdateCount() int     { return a.updates }

func (a *Type4NdefApp) Apdu(c, resp []byte) int {
	n := len(c)
	if n < 4 || len(resp) < Type4MaxRead+2 {
		return apduSW(resp, 0x67, 0x00)
	}
	if c[0] != 0x00 {
		return apduSW(resp, 0x6E, 0x00)
	}
	ins, p1, p2 := c[1], c[2], c[3]

	switch ins {
	case 0xA4: // SELECT
		lc := 0
		if n > 4 {
			lc = int(c[4])
		}
		if n >= 5+lc && p1 == 0x04 { // by name: the NDEF application
			if lc == len(ndefAID) && string(c[5:5+lc]) == string(ndefAID[:]) {
				a.appSelected = true
				a.selected = t4App
				return apduSW(resp, 0x90, 0x00)
			}
			return apduSW(resp, 0x6A, 0x82)
		}
		if p1 == 0x00 && lc == 2 && n >= 7 && a.appSelected { // by file id
			id := uint16(c[5])<<8 | uint16(c[6])
			switch id {
			case 0xE103:
				a.selected = t4CC
				return apduSW(resp, 0x90, 0x00)
			case 0xE104:
				a.selected = t4NdefFile
				return apduSW(resp, 0x90, 0x00)
			}
			return apduSW(resp, 0x6A, 0x82)
		}
		return apduSW(resp, 0x6A, 0x86)

	case 0xB0: // READ BINARY
		if a.selected != t4CC && a.selected != t4NdefFile {
			return apduSW(resp, 0x69, 0x86)
		}
		if p1&0x80 != 0 {
			return apduSW(resp, 0x6B, 0x00)
		}
		off := int(p1)<<8 | int(p2)
		le := 0
		if n > 4 {
			le = int(c[4])
		}
		if le == 0 || le > Type4MaxRead {
			le = Type4MaxRead
		}
		file := a.ndef[:]
		if a.selected == t4CC {
			file = a.cc[:]
		}
		if off > len(file) {
			return apduSW(resp, 0x6B, 0x00)
		}
		if off+le > len(file) {
			le = len(file) - off
		}
		copy(resp, file[off:off+le])
		resp[le], resp[le+1] = 0x90, 0x00
		return le + 2

	case 0xD6: // UPDATE BINARY
		if a.selected != t4NdefFile {
			if a.selected == t4CC {
				return apduSW(resp, 0x69, 0x82)
			}
			return apduSW(resp, 0x69, 0x86)
		}
		if p1&0x80 != 0 {
			return apduSW(resp, 0x6B, 0x00)
		}
		off := int(p1)<<8 | int(p2)
		lc := 0
		if n > 4 {
			lc = int(c[4])
		}
		if n != 5+lc || lc == 0 || lc > Type4MaxRead {
			return apduSW(resp, 0x67, 0x00)
		}
		if off+lc > len(a.ndef) {
			return apduSW(resp, 0x6B, 0x00)
		}
		copy(a.ndef[off:], c[5:5+lc])
		a.updates++
		return apduSW(resp, 0x90, 0x00)
	}
	return apduSW(resp, 0x6D, 0x00)
}

// SniffApp records every APDU. The capture is a list of records:
// [1 = command, 2 = response][length hi][length lo][bytes].
type SniffApp struct {
	ndef     *Type4NdefApp
	buf      [SniffCaptureSize]byte
	used     int
	apdus    int
	sessions int
	busyLeft int
	wtx      int
	full     bool
}

func NewSniffApp() *SniffApp { return &SniffApp{ndef: NewType4NdefApp()} }

func (s *SniffApp) Capture() []byte   { return s.buf[:s.used] }
func (s *SniffApp) ApduCount() int    { return s.apdus }
func (s *SniffApp) SessionCount() int { return s.sessions }
func (s *SniffApp) Full() bool        { return s.full }
func (s *SniffApp) Clear() {
	s.used, s.apdus, s.sessions, s.busyLeft, s.wtx = 0, 0, 0, 0, 0
	s.full = false
}

func (s *SniffApp) TakeWtxRequests() int {
	w := s.wtx
	s.wtx = 0
	return w
}

func (s *SniffApp) Reset() {
	s.sessions++
	s.ndef.Reset()
	s.wtx = 0
}

func (s *SniffApp) record(kind byte, data []byte) {
	if s.used+3+len(data) > SniffCaptureSize {
		s.full = true
		return
	}
	s.buf[s.used] = kind
	s.buf[s.used+1] = byte(len(data) >> 8)
	s.buf[s.used+2] = byte(len(data))
	copy(s.buf[s.used+3:], data)
	s.used += 3 + len(data)
}

func (s *SniffApp) Apdu(c, resp []byte) int {
	s.apdus++
	s.record(1, c)
	n := len(c)
	if n >= 4 && c[0] == 0x00 { // class 00: a real NDEF tag, so the phone's background reader stops asking
		rl := s.ndef.Apdu(c, resp)
		s.record(2, resp[:rl])
		return rl
	}
	if n >= 4 && c[0] == 0xF0 && c[1] == 0xD4 { // screen refresh starts
		s.busyLeft = SniffBusyPolls
		s.wtx = SniffRefreshWtxRequests
	}
	var rl int
	if n >= 4 && c[0] == 0xF0 && c[1] == 0xDE { // vendor "get busy": 01 busy, 00 idle
		resp[0] = 0x00
		if s.busyLeft > 0 {
			resp[0] = 0x01
			s.busyLeft--
		}
		resp[1], resp[2] = 0x90, 0x00
		rl = 3
	} else {
		resp[0], resp[1] = 0x90, 0x00
		rl = 2
	}
	s.record(2, resp[:rl])
	return rl
}
