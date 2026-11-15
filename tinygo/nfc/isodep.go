package nfc

// ISO-DEP (ISO/IEC 14443-4) block protocol for the reader side: RATS/ATS, I-block framing with the
// block-number toggle, chaining in both directions, waiting-time extension, retries and deselect.
//
// A port of arduino/src/IsoDep.cpp, which is a port of the ISO-DEP layer in embassy-rs/rnfc (MIT or
// Apache-2.0); the tests replay the same ISO 14443-4 Annex B scenarios. The frames go through an
// IsoDepLink, so the protocol runs on the host without a chip.

// IsoDepLink is one ISO 14443-A frame exchange. The link adds CRC_A when sending and checks and
// strips it on receive. Timeouts are in carrier clocks (1/13.56 MHz). ErrTimeout, ErrFrame and
// ErrCollision mean a lost or damaged frame (the protocol retries those); any other error is fatal.
type IsoDepLink interface {
	Transceive(tx, rx []byte, timeout1fc uint32) (int, error)
	SleepMs(ms uint32)
}

// RfIsoDepLink is an IsoDepLink on the ST25R3916.
type RfIsoDepLink struct{ RF *ST25R3916 }

func (l RfIsoDepLink) Transceive(tx, rx []byte, timeout1fc uint32) (int, error) {
	ms := (timeout1fc + 13559) / 13560 // carrier clocks to milliseconds, rounded up
	if ms < 1 {
		ms = 1
	}
	return l.RF.Transceive(tx, rx, true, true, ms, false)
}

func (l RfIsoDepLink) SleepMs(ms uint32) { l.RF.t.SleepMs(ms) }

const (
	ratsTimeout1fc = 65536
	fscMax         = 256
	// The chip loads at most 64 bytes into its FIFO for a transmit.
	txFrameMax  = 64
	maxRetries  = 10
	ratsRetries = 4
	// Largest response collected from chained blocks.
	maxResponse = 4096
	atsMax      = 32
)

var fsTable = [9]int{16, 24, 32, 40, 48, 64, 96, 128, 256}

func lost(err error) bool { return err == ErrTimeout || err == ErrFrame || err == ErrCollision }

type IsoDep struct {
	link     IsoDepLink
	ats      []byte
	fsc      int    // largest frame the card accepts, including header and CRC
	sfgt1fc  uint32 // start-up frame guard time
	fwt1fc   uint32 // frame waiting time
	blockNum byte
}

func NewIsoDep(l IsoDepLink) *IsoDep {
	return &IsoDep{link: l, fsc: 32, sfgt1fc: 4096, fwt1fc: 65536}
}

func (d *IsoDep) ATS() []byte     { return d.ats }
func (d *IsoDep) FSC() int        { return d.fsc }
func (d *IsoDep) SFGT1fc() uint32 { return d.sfgt1fc }
func (d *IsoDep) FWT1fc() uint32  { return d.fwt1fc }

// Activate sends RATS (E0 80) and reads the ATS. The card must already be selected.
func (d *IsoDep) Activate() error {
	req := []byte{0xE0, 0x80} // FSDI = 8 (256 bytes), CID = 0
	var res [atsMax + 2]byte
	var n int
	for tries := 1; ; tries++ {
		var err error
		n, err = d.link.Transceive(req, res[:], ratsTimeout1fc)
		if err == nil {
			break
		}
		if !lost(err) {
			return err
		}
		if tries >= ratsRetries {
			return ErrTimeout
		}
	}
	if n > atsMax {
		n = atsMax
	}
	d.ats = append([]byte(nil), res[:n]...)

	fsci, sfgi, fwi := 2, 0, 4
	if n >= 2 {
		t0 := res[1]
		fsci = int(t0 & 0x0F)
		if t0&0x20 != 0 { // TB(1) present: SFGI and FWI
			tb := 2
			if t0&0x10 != 0 {
				tb = 3
			}
			if tb < n {
				sfgi = int(res[tb] & 0x0F)
				fwi = int(res[tb] >> 4)
			}
		}
	}
	if fsci >= len(fsTable) {
		return ErrProtocol
	}
	if fwi > 14 { // 15 is reserved
		fwi = 4
	}
	if sfgi > 14 {
		sfgi = 0
	}
	d.fsc = fsTable[fsci]
	d.sfgt1fc = (256 * 16) << uint(sfgi) // SFGT = 256 x 16 / fc x 2^SFGI
	d.fwt1fc = (256 * 16) << uint(fwi)   // FWT  = 256 x 16 / fc x 2^FWI
	d.blockNum = 0
	d.link.SleepMs((d.sfgt1fc+13559)/13560 + 1) // the card needs SFGT before it takes the next frame
	return nil
}

// Deselect sends S(DESELECT); the card goes back to idle.
func (d *IsoDep) Deselect() error {
	var rx [4]byte
	n, err := d.link.Transceive([]byte{0xC2}, rx[:], d.fwt1fc)
	if err != nil {
		return err
	}
	if n != 1 || rx[0] != 0xC2 {
		return ErrProtocol
	}
	return nil
}

type send int

const (
	sendData send = iota
	sendAck
	sendNak
	sendWtx
)

// Transceive runs one command/response exchange of any length; it handles chaining and
// waiting-time extension.
func (d *IsoDep) Transceive(tx []byte) ([]byte, error) {
	var txBuf [fscMax]byte
	var rxBuf [fscMax + 2]byte // two spare bytes: the chip may leave the CRC in the frame
	mode := sendData
	var wtxMul byte
	// INF bytes per I-block: FSC less PCB and CRC, and no more than the chip can transmit.
	maxN := d.fsc - 3
	if maxN > txFrameMax-1 {
		maxN = txFrameMax - 1
	}
	if maxN <= 0 {
		return nil, ErrProtocol
	}
	var out []byte
	rxChaining := false
	retries := 0

	for {
		fwt := d.fwt1fc
		var frameLen int
		switch mode {
		case sendData:
			n := len(tx)
			if n > maxN {
				n = maxN
			}
			pcb := 0x02 | d.blockNum
			if n != len(tx) {
				pcb |= 0x10 // chaining: more blocks follow
			}
			txBuf[0] = pcb
			copy(txBuf[1:], tx[:n])
			frameLen = 1 + n
		case sendWtx:
			fwt *= uint32(wtxMul)
			txBuf[0], txBuf[1] = 0xF2, wtxMul
			frameLen = 2
		case sendAck:
			txBuf[0] = 0xA2 | d.blockNum
			frameLen = 1
		case sendNak:
			txBuf[0] = 0xB2 | d.blockNum
			frameLen = 1
		}

		got, err := d.link.Transceive(txBuf[:frameLen], rxBuf[:], fwt)
		if err != nil {
			if !lost(err) {
				return nil, err
			}
			retries++
			if retries >= maxRetries {
				return nil, ErrTimeout
			}
			if rxChaining {
				mode = sendAck
			} else {
				mode = sendNak
			}
			continue
		}
		if got == 0 {
			return nil, ErrProtocol
		}
		retries = 0

		switch pcb := rxBuf[0]; pcb {
		case 0x02, 0x03, 0x12, 0x13: // I-block
			if len(out)+got-1 > maxResponse {
				return nil, ErrParam
			}
			out = append(out, rxBuf[1:got]...)
			d.blockNum ^= 1
			if pcb&0x10 == 0 { // last block of the response
				return out, nil
			}
			rxChaining = true
			mode = sendAck
		case 0xA2, 0xA3: // R(ACK) for our chained block
			if pcb&1 == d.blockNum {
				if len(tx) <= maxN {
					return nil, ErrProtocol // ACK on the last block of a chain
				}
				tx = tx[maxN:]
				d.blockNum ^= 1
			}
			mode = sendData
		case 0xF2: // S(WTX): the card asks for more time
			if got != 2 {
				return nil, ErrProtocol
			}
			wtxMul = rxBuf[1] & 0x3F
			mode = sendWtx
		default:
			return nil, ErrProtocol
		}
	}
}

// DesfireCommand runs one native DESFire command wrapped in an ISO 7816 APDU (90 INS 00 00 00) and
// follows "additional frame" replies (SW 91 AF, sent again as 90 AF 00 00 00). It returns the data
// of all frames and the final SW1 SW2.
func DesfireCommand(d *IsoDep, ins byte) (data []byte, sw1, sw2 byte, err error) {
	for frames := 0; frames < 32; frames++ {
		resp, err := d.Transceive([]byte{0x90, ins, 0x00, 0x00, 0x00})
		if err != nil {
			return nil, 0, 0, err
		}
		if len(resp) < 2 {
			return nil, 0, 0, ErrProtocol
		}
		data = append(data, resp[:len(resp)-2]...)
		sw1, sw2 = resp[len(resp)-2], resp[len(resp)-1]
		if sw1 != 0x91 || sw2 != 0xAF {
			return data, sw1, sw2, nil
		}
		ins = 0xAF
	}
	return nil, 0, 0, ErrProtocol // a card that never stops asking for more
}
