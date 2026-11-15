// Package ccid is the CCID message layer (independent of USB and of the NFC chip), ported
// from ../../arduino/src/CcidProto.cpp. Slot.Handle turns one PC_to_RDR bulk message into
// the RDR_to_PC reply, talking to a Backend. The USB class driver is in usb.go (TinyGo only).
package ccid

import "errors"

// PC_to_RDR
const (
	IccPowerOn      = 0x62
	IccPowerOff     = 0x63
	GetSlotStatus   = 0x65
	XfrBlock        = 0x6F
	GetParameters   = 0x6C
	SetParameters   = 0x61
	ResetParameters = 0x6D
	Escape          = 0x6B
	// RDR_to_PC
	DataBlock        = 0x80
	SlotStatus       = 0x81
	Parameters       = 0x82
	EscapeResp       = 0x83
	NotifySlotChange = 0x50
)

// bStatus: bits 0-1 = ICC status, bits 6-7 = command status (1 = failed)
const (
	IccActive   = 0
	IccInactive = 1
	IccAbsent   = 2
	CmdOK       = 0x00
	CmdFailed   = 0x40
)

// bError
const (
	errCmdNotSupported = 0x00
	errSlot            = 0x05
	errIccMute         = 0xFE
	errHW              = 0xFB
)

const (
	HeaderLen  = 10
	MaxMessage = 271 // header + 261, as announced in the class descriptor
)

// ErrAbsent tells the slot the card is gone; any other error is a hardware failure.
var ErrAbsent = errors.New("card absent")

type Backend interface {
	// Status returns IccActive / IccInactive / IccAbsent, or an error on a chip failure.
	Status() (int, error)
	PowerOn() (atr []byte, err error)
	PowerOff()
	// Transmit sends an APDU and returns the response including SW1 SW2.
	Transmit(apdu []byte) ([]byte, error)
}

// ContactlessATR is the PC/SC part 3 ATR for an ISO 14443-A part 3 card (20 bytes).
func ContactlessATR() []byte {
	body := []byte{0x8F, 0x80, 0x01, 0x80, 0x4F, 0x0C, 0xA0, 0x00, 0x00,
		0x03, 0x06, 0x03, 0x00, 0x03, 0x00, 0x00, 0x00, 0x00}
	var tck byte
	for _, b := range body {
		tck ^= b
	}
	return append(append([]byte{0x3B}, body...), tck)
}

// ATRFromATS is the PC/SC part 3 ATR for an ISO 14443-4 (ISO-DEP) card, built from its ATS (length
// byte first): TS=3B, T0=0x80|K, TD1=0x80, TD2=0x01, the K historical bytes, TCK. nil if it cannot
// be built.
func ATRFromATS(ats []byte) []byte {
	if len(ats) < 2 {
		return nil
	}
	t0 := ats[1]
	skip := 1
	for _, bit := range []byte{0x10, 0x20, 0x40} { // TA(1), TB(1), TC(1)
		if t0&bit != 0 {
			skip++
		}
	}
	end := int(ats[0])
	if end > len(ats) {
		end = len(ats)
	}
	var hist []byte
	if end > 1+skip {
		hist = ats[1+skip : end]
	}
	if len(hist) > 15 {
		return nil
	}
	atr := append([]byte{0x3B, 0x80 | byte(len(hist)), 0x80, 0x01}, hist...)
	var tck byte
	for _, b := range atr[1:] {
		tck ^= b
	}
	return append(atr, tck)
}

// SlotChangeMessage is RDR_to_PC_NotifySlotChange (2 bytes).
func SlotChangeMessage(present, changed bool) [2]byte {
	var b byte
	if present {
		b |= 1
	}
	if changed {
		b |= 2
	}
	return [2]byte{NotifySlotChange, b}
}

type Slot struct{ BE Backend }

func reply(typ, slot, seq, status, errc, specific byte, data []byte) []byte {
	n := len(data)
	out := []byte{typ, byte(n), byte(n >> 8), 0, 0, slot, seq, status, errc, specific}
	return append(out, data...)
}

func failed(typ, slot, seq, icc, errc byte) []byte {
	return reply(typ, slot, seq, CmdFailed|icc, errc, 0, nil)
}

func (s *Slot) iccStatus() byte {
	st, err := s.BE.Status()
	if err != nil || st < 0 {
		return IccAbsent
	}
	return byte(st)
}

// Handle returns the reply for one message (nil = no reply).
func (s *Slot) Handle(msg []byte) []byte {
	if len(msg) < HeaderLen {
		return nil
	}
	mtype, slot, seq := msg[0], msg[5], msg[6]
	length := int(msg[1]) | int(msg[2])<<8 | int(msg[3])<<16 | int(msg[4])<<24
	payload := msg[HeaderLen:]
	if length > len(payload) {
		length = len(payload)
	}
	payload = payload[:length]

	dataType := byte(SlotStatus)
	switch mtype {
	case IccPowerOn, XfrBlock:
		dataType = DataBlock
	case Escape:
		dataType = EscapeResp
	case GetParameters, SetParameters, ResetParameters:
		dataType = Parameters
	}
	if slot != 0 {
		return failed(dataType, slot, seq, IccAbsent, errSlot)
	}

	switch mtype {
	case GetSlotStatus:
		st, err := s.BE.Status()
		if err != nil {
			return failed(SlotStatus, slot, seq, IccAbsent, errHW)
		}
		return reply(SlotStatus, slot, seq, CmdOK|byte(st), 0, 0, nil)
	case IccPowerOn:
		atr, err := s.BE.PowerOn()
		if err == ErrAbsent {
			return failed(DataBlock, slot, seq, IccAbsent, errIccMute)
		}
		if err != nil {
			return failed(DataBlock, slot, seq, s.iccStatus(), errHW)
		}
		return reply(DataBlock, slot, seq, CmdOK|IccActive, 0, 0, atr)
	case IccPowerOff:
		s.BE.PowerOff()
		return reply(SlotStatus, slot, seq, CmdOK|s.iccStatus(), 0, 0, nil)
	case XfrBlock:
		st, err := s.BE.Status()
		if err != nil {
			return failed(DataBlock, slot, seq, IccAbsent, errHW)
		}
		if st == IccAbsent {
			return failed(DataBlock, slot, seq, IccAbsent, errIccMute)
		}
		resp, err := s.BE.Transmit(payload)
		if err == ErrAbsent {
			return failed(DataBlock, slot, seq, IccAbsent, errIccMute)
		}
		if err != nil {
			return failed(DataBlock, slot, seq, s.iccStatus(), errHW)
		}
		return reply(DataBlock, slot, seq, CmdOK|IccActive, 0, 0, resp)
	case GetParameters, SetParameters, ResetParameters:
		// T=1 defaults; contactless cards ignore them
		return reply(Parameters, slot, seq, CmdOK|s.iccStatus(), 0, 1,
			[]byte{0x11, 0x10, 0x00, 0x45, 0x00, 0xFE, 0x00})
	}
	return failed(dataType, slot, seq, s.iccStatus(), errCmdNotSupported)
}
