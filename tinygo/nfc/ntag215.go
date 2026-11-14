package nfc

import "bytes"

// NTAG215: 135 pages x 4 bytes, user memory = pages 4-129 (504 bytes).
const (
	PageSize      = 4
	TotalPages    = 135
	DumpBytes     = TotalPages * PageSize
	UserFirst     = 4
	UserLast      = 129
	UserBytes     = (UserLast - UserFirst + 1) * PageSize
	UserReadBytes = 512 // 32 READs of 16 bytes cover pages 4..131
	PageCC        = 3
	PagePWD       = 133
	PagePACK      = 134
)

var ccNTAG215 = []byte{0xE1, 0x10, 0x3E, 0x00}

type Ntag215 struct{ RF *ST25R3916 }

func (n Ntag215) ReadPage(page byte) ([PageSize]byte, error) {
	var out [PageSize]byte
	b, err := T2Read(n.RF, page)
	copy(out[:], b[:PageSize])
	return out, err
}

// Dump returns all 135 pages; PWD/PACK read as zeros.
func (n Ntag215) Dump() ([]byte, error) {
	out := make([]byte, 0, DumpBytes)
	for page := byte(0); page < 132; page += 4 {
		b, err := T2Read(n.RF, page)
		if err != nil {
			return nil, err
		}
		out = append(out, b[:]...)
	}
	b, err := T2Read(n.RF, 131) // pages 131..134; 131 is already in the dump
	if err != nil {
		return nil, err
	}
	return append(out, b[PageSize:]...), nil
}

// IsNtag215 checks the capability container is E1 10 3E 00.
func (n Ntag215) IsNtag215() (bool, error) {
	cc, err := n.ReadPage(PageCC)
	return err == nil && bytes.Equal(cc[:], ccNTAG215), err
}

// WritePage refuses lock/config/CC pages (outside 4..129) unless force; those bits are one-way.
func (n Ntag215) WritePage(page byte, data [PageSize]byte, force, verify bool) error {
	if page < 2 || page >= TotalPages {
		return ErrProtected
	}
	if (page < UserFirst || page > UserLast) && !force {
		return ErrProtected
	}
	if err := T2Write(n.RF, page, data); err != nil {
		return err
	}
	if verify && page != PagePWD && page != PagePACK && page != 2 && page != 130 {
		got, err := n.ReadPage(page)
		if err != nil {
			return err
		}
		if got != data {
			return ErrVerify
		}
	}
	return nil
}

// ReadUserMemory returns 512 bytes (pages 4..131); only the first UserBytes are user memory.
func (n Ntag215) ReadUserMemory() ([]byte, error) {
	out := make([]byte, 0, UserReadBytes)
	for page := byte(UserFirst); page <= UserLast; page += 4 {
		b, err := T2Read(n.RF, page)
		if err != nil {
			return nil, err
		}
		out = append(out, b[:]...)
	}
	return out, nil
}

// ReadNDEF returns the records (nil if the tag holds no message).
func (n Ntag215) ReadNDEF() ([]Record, error) {
	user, err := n.ReadUserMemory()
	if err != nil {
		return nil, err
	}
	msg, ok := FindMessage(user[:UserBytes])
	if !ok || len(msg) == 0 {
		return nil, nil
	}
	return DecodeMessage(msg)
}

// WriteNDEF writes the message and returns the number of pages written (identical pages are skipped).
func (n Ntag215) WriteNDEF(recs []Record) (int, error) {
	cc, err := n.ReadPage(PageCC)
	if err != nil {
		return 0, err
	}
	if cc[0] != 0xE1 {
		return 0, ErrNoCC
	}
	msg := EncodeMessage(recs)
	if len(msg) == 0 {
		return 0, ErrTooLarge
	}
	blob := WrapTLV(msg, UserBytes)
	if blob == nil {
		return 0, ErrTooLarge
	}
	for len(blob)%PageSize != 0 {
		blob = append(blob, 0)
	}
	cur, err := n.ReadUserMemory()
	if err != nil {
		return 0, err
	}
	count := 0
	for i := 0; i < len(blob); i += PageSize {
		if !bytes.Equal(cur[i:i+PageSize], blob[i:i+PageSize]) {
			var d [PageSize]byte
			copy(d[:], blob[i:])
			if err := n.WritePage(byte(UserFirst+i/PageSize), d, false, true); err != nil {
				return count, err
			}
			count++
		}
	}
	return count, nil
}
