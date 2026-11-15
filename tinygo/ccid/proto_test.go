package ccid

import (
	"bytes"
	"testing"
)

type fake struct {
	status int
	resp   []byte
}

func (f *fake) Status() (int, error)              { return f.status, nil }
func (f *fake) PowerOn() ([]byte, error)          { return ContactlessATR(), nil }
func (f *fake) PowerOff()                         {}
func (f *fake) Transmit(a []byte) ([]byte, error) { return f.resp, nil }

func msg(t byte, slot byte, payload []byte) []byte {
	return append([]byte{t, byte(len(payload)), 0, 0, 0, slot, 7, 0, 0, 0}, payload...)
}

func TestATR(t *testing.T) {
	a := ContactlessATR()
	if len(a) != 20 || a[0] != 0x3B {
		t.Fatal(a)
	}
	var x byte
	for _, b := range a[1:] {
		x ^= b
	}
	if x != 0 {
		t.Fatal("TCK")
	}
}

func TestSlot(t *testing.T) {
	s := Slot{&fake{status: IccInactive, resp: []byte{1, 2, 0x90, 0}}}
	r := s.Handle(msg(GetSlotStatus, 0, nil))
	if r[0] != SlotStatus || r[6] != 7 || r[7] != IccInactive {
		t.Fatal(r)
	}
	r = s.Handle(msg(IccPowerOn, 0, nil))
	if r[0] != DataBlock || len(r) != HeaderLen+20 {
		t.Fatal(r)
	}
	s.BE.(*fake).status = IccActive
	r = s.Handle(msg(XfrBlock, 0, []byte{0xFF, 0xCA, 0, 0, 0}))
	if !bytes.Equal(r[HeaderLen:], []byte{1, 2, 0x90, 0}) || r[1] != 4 {
		t.Fatal(r)
	}
	if r := s.Handle(msg(GetSlotStatus, 1, nil)); r[7] != CmdFailed|IccAbsent || r[8] != errSlot {
		t.Fatal(r)
	}
	if r := s.Handle(msg(0x99, 0, nil)); r[7]&CmdFailed == 0 {
		t.Fatal(r)
	}
	s.BE.(*fake).status = IccAbsent
	if r := s.Handle(msg(XfrBlock, 0, []byte{1, 2, 3, 4})); r[8] != errIccMute {
		t.Fatal(r)
	}
	if s.Handle([]byte{1, 2}) != nil {
		t.Fatal("short")
	}
}

func TestATRFromATS(t *testing.T) {
	// ATS of a DESFire EV1: TL=6, T0=75, TA, TB, TC, one historical byte 0x80
	if got := ATRFromATS([]byte{0x06, 0x75, 0x77, 0x81, 0x02, 0x80}); !bytes.Equal(got, []byte{0x3B, 0x81, 0x80, 0x01, 0x80, 0x80}) {
		t.Fatalf("% x", got)
	}
	// no TA/TB/TC and no historical bytes
	if got := ATRFromATS([]byte{0x02, 0x00}); !bytes.Equal(got, []byte{0x3B, 0x80, 0x80, 0x01, 0x01}) {
		t.Fatalf("% x", got)
	}
	if ATRFromATS([]byte{0x06}) != nil || ATRFromATS(nil) != nil {
		t.Fatal("short ATS accepted")
	}
}
