package nfc

import "github.com/melastmohican/m5nfc-st25r3916-experiments/tinygo/ccid"

// Backend is the CCID backend on the NFC unit: it maps PC/SC pseudo-APDUs onto an NTAG tag.
//
//	FF CA 00 00 Le          -> UID (Le = 0: whole UID)
//	FF B0 00 <page> <Le>    -> read Le bytes starting at <page>  (Le = 0: 16 bytes)
//	FF D6 00 <page> 04 <d>  -> write one 4-byte page
//
// Lock/config pages are refused (Ntag215.WritePage guard) with SW 69 82.
type Backend struct {
	RF      *ST25R3916
	card    Card
	powered bool    // set while the host holds the slot powered
	isoDep  *IsoDep // set when the powered card is an ISO-DEP card, activated with RATS
}

var _ ccid.Backend = (*Backend)(nil)

func (b *Backend) Status() (int, error) {
	// An activated ISO-DEP card cannot be probed without disturbing it (a Type 2 READ would go to a
	// card that speaks ISO 14443-4), so it counts as present until a command fails.
	if b.powered && b.isoDep != nil {
		return ccid.IccActive, nil
	}
	if b.powered {
		if _, err := T2Read(b.RF, 0); err == nil {
			return ccid.IccActive, nil
		}
		b.powered = false
		return ccid.IccAbsent, nil
	}
	if _, err := SelectCard(b.RF); err == nil {
		return ccid.IccInactive, nil
	}
	return ccid.IccAbsent, nil
}

func (b *Backend) PowerOn() ([]byte, error) {
	b.RF.FieldReset()
	b.powered = false
	b.isoDep = nil
	card, err := SelectCard(b.RF)
	switch err {
	case nil:
	case ErrTimeout, ErrCollision, ErrFrame, ErrBadResponse:
		return nil, ccid.ErrAbsent
	default:
		return nil, err
	}
	b.card = card
	if card.SAK&0x20 != 0 { // ISO 14443-4 card: activate it (RATS) and answer with an ATR built from its ATS
		dep := NewIsoDep(RfIsoDepLink{b.RF})
		switch err := dep.Activate(); err {
		case nil:
		case ErrTimeout, ErrCollision, ErrFrame:
			return nil, ccid.ErrAbsent
		default:
			return nil, err
		}
		atr := ccid.ATRFromATS(dep.ATS())
		if atr == nil {
			return nil, ErrBadResponse
		}
		b.isoDep = dep
		b.powered = true
		return atr, nil
	}
	b.powered = true
	return ccid.ContactlessATR(), nil
}

func (b *Backend) PowerOff() {
	if b.isoDep != nil {
		_ = b.isoDep.Deselect() // best effort: the field is reset next anyway
		b.isoDep = nil
	}
	b.powered = false
	b.RF.FieldReset()
}

func sw(a, c byte) []byte { return []byte{a, c} }

func (b *Backend) Transmit(apdu []byte) ([]byte, error) {
	if !b.powered {
		return nil, ccid.ErrAbsent
	}
	if b.isoDep != nil {
		return b.transmitIsoDep(apdu)
	}
	if len(apdu) < 4 {
		return sw(0x67, 0x00), nil
	}
	if apdu[0] != 0xFF {
		return sw(0x6E, 0x00), nil // class not supported
	}
	var resp []byte
	var err error
	switch apdu[1] {
	case 0xCA:
		resp, err = b.getData(apdu)
	case 0xB0:
		resp, err = b.readBinary(apdu)
	case 0xD6:
		resp, err = b.updateBinary(apdu)
	case 0x60, 0x3C, 0x39: // NTAG GET_VERSION, READ_SIG, READ_CNT: FF 60/3C 00 00 00, FF 39 00 02 00
		resp, err = b.ident(apdu)
	default:
		return sw(0x6D, 0x00), nil // instruction not supported
	}
	if err == ErrTimeout { // the tag was lifted
		b.powered = false
		return nil, ccid.ErrAbsent
	}
	return resp, err
}

// transmitIsoDep sends any APDU to the card unchanged, except the PC/SC get-data for the UID, which
// the reader answers.
func (b *Backend) transmitIsoDep(apdu []byte) ([]byte, error) {
	if len(apdu) >= 4 && apdu[0] == 0xFF && apdu[1] == 0xCA {
		return b.getData(apdu)
	}
	resp, err := b.isoDep.Transceive(apdu)
	switch err {
	case nil:
		return resp, nil
	case ErrTimeout, ErrFrame, ErrCollision, ErrProtocol:
		b.powered = false // the card was lifted or the link broke
		b.isoDep = nil
		return nil, ccid.ErrAbsent
	}
	return nil, err
}

func le(apdu []byte) int {
	if len(apdu) > 4 {
		return int(apdu[4])
	}
	return 0
}

func (b *Backend) getData(apdu []byte) ([]byte, error) {
	if apdu[2] != 0 {
		return sw(0x6B, 0x00), nil
	}
	take := le(apdu)
	if take == 0 || take > len(b.card.UID) {
		take = len(b.card.UID)
	}
	return append(append([]byte(nil), b.card.UID[:take]...), 0x90, 0x00), nil
}

func (b *Backend) readBinary(apdu []byte) ([]byte, error) {
	page, n := int(apdu[3]), le(apdu)
	if n == 0 {
		n = 16
	}
	if page >= TotalPages {
		return sw(0x6A, 0x82), nil
	}
	out := make([]byte, 0, n+2)
	for p := page; len(out) < n && p < TotalPages; p += 4 {
		buf, err := T2Read(b.RF, byte(p))
		if err != nil {
			return nil, err
		}
		chunk := n - len(out)
		if chunk > 16 {
			chunk = 16
		}
		out = append(out, buf[:chunk]...)
	}
	return append(out, 0x90, 0x00), nil
}

// ident answers the identification APDUs. P2 is the address byte of READ_SIG and READ_CNT.
func (b *Backend) ident(apdu []byte) ([]byte, error) {
	data, err := IdentCmd(b.RF, Ident(apdu[1]), apdu[3])
	switch err {
	case nil:
		return append(data, 0x90, 0x00), nil
	case ErrRefused:
		return sw(0x6A, 0x81), nil // not supported, or refused with a NAK
	}
	return nil, err // a missing tag comes back as ErrTimeout and is reported absent
}

func (b *Backend) updateBinary(apdu []byte) ([]byte, error) {
	if len(apdu) < 9 || apdu[4] != 4 {
		return sw(0x67, 0x00), nil
	}
	var d [4]byte
	copy(d[:], apdu[5:9])
	err := Ntag215{b.RF}.WritePage(apdu[3], d, false, true)
	switch err {
	case nil:
		return sw(0x90, 0x00), nil
	case ErrTimeout:
		return nil, err
	case ErrProtected:
		return sw(0x69, 0x82), nil
	case ErrNakArg:
		return sw(0x6A, 0x82), nil // the tag refused the page address
	}
	return sw(0x65, 0x81), nil // memory failure (a failed read-back or any other NAK)
}
