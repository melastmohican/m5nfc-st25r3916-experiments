package nfc

import "fmt"

const (
	ct      = 0x88 // cascade tag
	t2Read  = 0x30
	t2Write = 0xA2
	t2Ack   = 0x0A
)

type Card struct {
	UID  []byte
	ATQA [2]byte
	SAK  byte
}

func (c Card) UIDHex() string {
	s := ""
	for i, b := range c.UID {
		if i > 0 {
			s += ":"
		}
		s += fmt.Sprintf("%02X", b)
	}
	return s
}

// CRCA is the ISO14443-A CRC, low byte first in the returned value.
func CRCA(data []byte) uint16 {
	crc := uint16(0x6363)
	for _, b := range data {
		ch := b ^ byte(crc)
		ch ^= ch << 4
		crc = crc>>8 ^ uint16(ch)<<8 ^ uint16(ch)<<3 ^ uint16(ch>>4)
	}
	return crc
}

func bcc(d []byte) (x byte) {
	for _, b := range d {
		x ^= b
	}
	return
}

func anticollision(r *ST25R3916, sel byte) ([5]byte, error) {
	var cl [5]byte
	var resp [8]byte
	n, err := r.Transceive([]byte{sel, 0x20}, resp[:], false, false, 10, false)
	if err != nil {
		return cl, err
	}
	if n != 5 || bcc(resp[:4]) != resp[4] {
		return cl, ErrBadResponse
	}
	copy(cl[:], resp[:5])
	return cl, nil
}

func selectLevel(r *ST25R3916, sel byte, cl [5]byte) (byte, error) {
	var resp [8]byte
	n, err := r.Transceive(append([]byte{sel, 0x70}, cl[:]...), resp[:], true, true, 10, false)
	if err != nil {
		return 0, err
	}
	if n != 1 {
		return 0, ErrBadResponse
	}
	return resp[0], nil
}

// SelectCard runs WUPA plus cascade anticollision/select (4- and 7-byte UIDs). A tag left
// ACTIVE ignores WUPA until the field is power-cycled, so on timeout the field is reset once.
func SelectCard(r *ST25R3916) (Card, error) {
	var card Card
	var atqa [4]byte
	n, err := r.SendShort(CmdTxWUPA, atqa[:], 5)
	if err == ErrTimeout {
		r.FieldReset()
		n, err = r.SendShort(CmdTxWUPA, atqa[:], 5)
	}
	if err != nil {
		return card, err
	}
	if n != 2 {
		return card, ErrBadResponse
	}
	card.ATQA = [2]byte{atqa[0], atqa[1]}
	for _, sel := range []byte{0x93, 0x95, 0x97} {
		cl, err := anticollision(r, sel)
		if err != nil {
			return card, err
		}
		sak, err := selectLevel(r, sel, cl)
		if err != nil {
			return card, err
		}
		if cl[0] == ct && sak&0x04 != 0 {
			card.UID = append(card.UID, cl[1:4]...)
			continue
		}
		card.UID = append(card.UID, cl[:4]...)
		card.SAK = sak
		return card, nil
	}
	return card, ErrBadResponse // UID longer than 10 bytes
}

// T2Read returns 4 pages (16 bytes) starting at page.
func T2Read(r *ST25R3916, page byte) ([16]byte, error) {
	var out [16]byte
	var buf [18]byte // room for the CRC_A before it is stripped
	n, err := r.Transceive([]byte{t2Read, page}, buf[:], true, true, 10, false)
	if err != nil {
		return out, err
	}
	if n != 16 {
		return out, ErrBadResponse
	}
	copy(out[:], buf[:16])
	return out, nil
}

// T2Write writes one 4-byte page. The ACK is a 4-bit frame without CRC.
func T2Write(r *ST25R3916, page byte, data [4]byte) error {
	var resp [4]byte
	n, err := r.Transceive([]byte{t2Write, page, data[0], data[1], data[2], data[3]}, resp[:], true, false, 10, true)
	if err != nil {
		return err
	}
	if n == 0 {
		return ErrNoAck
	}
	return NakError(resp[0] & 0x0F)
}

// NakError maps the 4-bit reply to a WRITE: 0xA is the ACK (nil), the rest are NAKs.
func NakError(code byte) error {
	switch code {
	case t2Ack:
		return nil
	case 0x0:
		return ErrNakArg
	case 0x1:
		return ErrNakCRC
	case 0x4:
		return ErrNakAuth
	case 0x5:
		return ErrNakEEPROM
	}
	return ErrNakOther
}
