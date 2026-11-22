package nfc

import (
	"strconv"
)

// The `emulate` and `emu-dump` commands: act as a tag and show what the reader did to it.
// Same behaviour as arduino/src/Cli.cpp.

// ByteReader is the serial input; any byte received ends `emulate`.
type ByteReader interface {
	Buffered() int
	ReadByte() (byte, error)
}

type emuMode uint8

const (
	emuNone emuMode = iota
	emuT2
	emuT4
	emuSniff
)

var emuUID = [7]byte{0x04, 0x11, 0x22, 0x33, 0x44, 0x55, 0x66}

// emuState is created on the first `emulate`, so the buffers (about 22 KB) exist only if it is used.
type emuState struct {
	tag         *Type2Tag
	t4App       *Type4NdefApp
	t4Target    *IsoDepTarget
	sniffApp    *SniffApp
	sniffTarget *IsoDepTarget
	listener    *Listener
	mode        emuMode
}

func (c *Cli) emuInit() *emuState {
	if c.emu == nil {
		e := &emuState{tag: NewType2Tag(emuUID), t4App: NewType4NdefApp(), sniffApp: NewSniffApp()}
		e.t4Target = NewIsoDepTarget(e.t4App)
		e.sniffTarget = NewIsoDepTarget(e.sniffApp)
		e.listener = NewListener(c.RF, e.tag)
		e.listener.NowUs = c.NowUs
		c.emu = e
	}
	return c.emu
}

// emulate acts as a tag. No serial output while it runs: a reader gives a tag about 5 ms to answer.
func (c *Cli) emulate(args []string) error {
	seconds := 30
	mode := emuT2
	for _, a := range args {
		switch a {
		case "t2":
			mode = emuT2
		case "t4":
			mode = emuT4
		case "sniff":
			mode = emuSniff
		default:
			n, err := strconv.Atoi(a)
			if err != nil {
				return ErrParam
			}
			seconds = n
		}
	}
	if seconds < 1 || seconds > 600 {
		return ErrParam
	}
	e := c.emuInit()
	var sak byte
	what := "an NTAG215"
	switch mode {
	case emuT2:
		e.listener.SetTarget(e.tag)
	case emuT4:
		e.listener.SetTarget(e.t4Target)
		sak = 0x20 // ISO-DEP: the reader sends RATS
		what = "a Type 4 NDEF tag"
	case emuSniff:
		e.listener.SetTarget(e.sniffTarget)
		e.sniffApp.Clear()
		sak = 0x20
		what = "an ISO-DEP card that logs every APDU"
	}
	// the line ending of this command (CR LF) must not count as the byte that ends the run
	for c.In != nil && c.In.Buffered() > 0 {
		c.In.ReadByte()
	}
	if err := e.listener.Start(emuUID, 0x0044, sak); err != nil {
		return err
	}
	e.mode = mode
	c.printf("emulating %s, UID 04112233445566, for %d s: hold the phone to the unit (any byte ends it)\n", what, seconds)
	t0 := c.RF.NowMs()
	var spins uint32
	for c.RF.NowMs()-t0 < uint32(seconds)*1000 {
		e.listener.Poll()
		spins++
		if spins&0x3F == 0 && c.In != nil && c.In.Buffered() > 0 { // any byte on the serial port ends it
			c.In.ReadByte()
			break
		}
	}
	err := e.listener.Stop()
	e.listener.PrintLog(c.Out)
	c.printf("fields seen: %d, frames answered: %d", e.listener.FieldCount(), e.listener.FrameCount())
	switch mode {
	case emuT2:
		c.printf(", tag writes: %d", e.tag.WriteCount())
	case emuT4:
		c.printf(", NDEF file updates: %d", e.t4App.UpdateCount())
	case emuSniff:
		c.printf(", APDUs: %d, sessions: %d", e.sniffApp.ApduCount(), e.sniffApp.SessionCount())
	}
	c.printf("\n")
	return err
}

// emuDump prints what the last `emulate` run captured: the NTAG215 memory in the format of `dump`
// (t2), the NDEF file (t4) or every APDU in full (sniff).
func (c *Cli) emuDump() error {
	if c.emu == nil || c.emu.mode == emuNone {
		c.printf("nothing captured: run `emulate` first\n")
		return nil
	}
	e := c.emu
	if e.mode == emuSniff {
		c.dumpApdus(e.sniffApp)
		return nil
	}
	var msg []byte
	if e.mode == emuT4 {
		f := e.t4App.NdefFile()
		nlen := int(f[0])<<8 | int(f[1])
		c.printf("NDEF file: NLEN = %d, %d update(s)\n", nlen, e.t4App.UpdateCount())
		if nlen == 0 || nlen > Type4NdefFileSize-2 {
			c.printf("no NDEF message\n")
			return nil
		}
		for i := 0; i < nlen+2; i += 16 {
			c.printf("%04X ", i)
			for j := i; j < i+16 && j < nlen+2; j++ {
				c.printf(" %02X", f[j])
			}
			c.printf("\n")
		}
		msg = f[2 : 2+nlen]
	} else {
		mem := e.tag.Memory()
		for page := 0; page < T2Pages; page++ {
			b := mem[page*4 : page*4+4]
			var t [4]byte
			for i, x := range b {
				t[i] = '.'
				if x >= 32 && x < 127 {
					t[i] = x
				}
			}
			mark := ""
			if e.tag.Changed(page) {
				mark = "  (written)"
			}
			c.printf("%3d  %02X %02X %02X %02X  %s%s\n", page, b[0], b[1], b[2], b[3], t[:], mark)
		}
		m, ok := FindMessage(mem[16:])
		if !ok || len(m) == 0 {
			c.printf("no NDEF message\n")
			return nil
		}
		msg = m
	}
	recs, err := DecodeMessage(msg)
	if err != nil {
		return ErrNDEF
	}
	for _, r := range recs {
		c.printf("%s\n", r.Describe())
	}
	return nil
}

// "C> <hex>" for a command the reader sent, "R> <hex>" for our answer, one APDU per line.
func (c *Cli) dumpApdus(s *SniffApp) {
	full := ""
	if s.Full() {
		full = " (capture buffer full, later APDUs lost)"
	}
	c.printf("APDUs captured: %d in %d session(s)%s\n", s.ApduCount(), s.SessionCount(), full)
	p := s.Capture()
	for len(p) >= 3 {
		n := int(p[1])<<8 | int(p[2])
		if 3+n > len(p) {
			break
		}
		if p[0] == 1 {
			c.printf("C>")
		} else {
			c.printf("R>")
		}
		for _, b := range p[3 : 3+n] {
			c.printf(" %02X", b)
		}
		c.printf("\n")
		p = p[3+n:]
	}
}
