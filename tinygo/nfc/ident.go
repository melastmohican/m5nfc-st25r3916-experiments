package nfc

// NTAG identification (NTAG21x datasheet 10.1 GET_VERSION, 10.8 READ_SIG, 10.6 READ_CNT).

type Ident byte

const (
	IdentVersion   Ident = 0x60
	IdentSignature Ident = 0x3C
	IdentCounter   Ident = 0x39
)

// NtagTypeName gives the family from a GET_VERSION reply (datasheet Table 28).
func NtagTypeName(v []byte) string {
	if len(v) < 8 || v[1] != 0x04 {
		return "unknown"
	}
	if v[2] != 0x04 {
		return "NXP, not an NTAG"
	}
	switch v[6] {
	case 0x0F:
		return "NTAG213 (NXP)"
	case 0x11:
		return "NTAG215 (NXP)"
	case 0x13:
		return "NTAG216 (NXP)"
	}
	return "NTAG, unknown size (NXP)"
}

// CounterValue decodes READ_CNT: a 24-bit counter, low byte first.
func CounterValue(b []byte) (uint32, bool) {
	if len(b) != 3 {
		return 0, false
	}
	return uint32(b[0]) | uint32(b[1])<<8 | uint32(b[2])<<16, true
}

// IdentCmd sends GET_VERSION (8 bytes), READ_SIG (32) or READ_CNT (3). addr is the address byte of
// READ_SIG (0x00) and READ_CNT (0x02). If the tag does not answer it is selected again: a tag that
// still answers refused the command (ErrRefused), otherwise the original error is returned.
func IdentCmd(r *ST25R3916, which Ident, addr byte) ([]byte, error) {
	tx := []byte{byte(which)}
	want := 8
	switch which {
	case IdentSignature:
		tx, want = append(tx, addr), 32
	case IdentCounter:
		tx, want = append(tx, addr), 3
	}
	var buf [40]byte // reply plus the CRC_A the chip leaves in the FIFO
	n, err := r.Transceive(tx, buf[:], true, true, 5, false)
	if err == nil && n == want {
		return append([]byte(nil), buf[:n]...), nil
	}
	if err == nil {
		err = ErrBadResponse
	}
	if err == ErrTimeout || err == ErrFrame || err == ErrBadResponse {
		if _, e := SelectCard(r); e == nil {
			return nil, ErrRefused
		}
	}
	return nil, err
}
