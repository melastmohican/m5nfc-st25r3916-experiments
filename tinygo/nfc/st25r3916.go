package nfc

// I2C frame formats:
//   register read   write [reg|0x40] then read N bytes (auto-increment)
//   register write  write [reg, val]
//   space-B read    write [0xFB, (reg&0x3F)|0x40] then read 1
//   space-B write   write [0xFB, reg&0x3F, val]
//   direct command  write [cmd]
//   FIFO load       write [0x80, data...]
//   FIFO read       write [0x9F] then read N bytes

const (
	opRead     = 0x40
	cmdSpaceB  = 0xFB
	opFifoLoad = 0x80
	opFifoRead = 0x9F
	cmdSetDef  = 0xC1
	cmdStop    = 0xC2
	cmdTxCRC   = 0xC4
	cmdTxNoCRC = 0xC5
	CmdTxREQA  = 0xC6
	CmdTxWUPA  = 0xC7
	cmdFieldOn = 0xC8
	cmdRstGain = 0xD5
	cmdAdjReg  = 0xD6
	cmdClrFifo = 0xDB
	cmdTestAcc = 0xFC

	regOpCtrl     = 0x02
	regMode       = 0x03
	regBitRate    = 0x04
	regISO14443A  = 0x05
	regAux        = 0x0A
	regNRT1       = 0x10
	regNRT2       = 0x11
	regTimerEMV   = 0x12
	regIrqMask    = 0x16
	regIrqMain    = 0x1A
	regFifoStatus = 0x1E
	regNumTx1     = 0x22
	regNumTx2     = 0x23
	regAuxDisplay = 0x31
	regICIdentity = 0x3F

	opCtrlEn     = 0x80
	opCtrlTxEn   = 0x08
	opCtrlRxEn   = 0x40
	auxNoCRCRx   = 0x80 // position unverified; transceive strips CRC_A itself
	auxDispOscOK = 0x10

	// IRQ bits: main | timer<<8 | error<<16 | target<<24
	irqOsc    = 1 << 7
	irqRXS    = 1 << 5
	irqRXE    = 1 << 4
	irqCol    = 1 << 2
	irqNRE    = 1 << (8 + 6)
	irqCRC    = 1 << (16 + 7)
	irqPar    = 1 << (16 + 6)
	irqErr2   = 1 << (16 + 5)
	irqErr1   = 1 << (16 + 4)
	irqErrors = irqCRC | irqPar | irqErr2 | irqErr1
)

type ST25R3916 struct {
	t       Transport
	rxCRC   int8  // -1 unknown, 0 off, 1 on
	nrt     int64 // cached no-response timer setting, -1 unknown
	ioError bool
	// fifoStatus2 is FIFO_STATUS2 as read by the last fifoCount(): fifo_lb is in bits 3:1.
	fifoStatus2 byte
}

func New(t Transport) *ST25R3916 { return &ST25R3916{t: t, rxCRC: -1, nrt: -1} }

// TakeIOError reports whether any I2C transfer failed since the last call.
func (r *ST25R3916) TakeIOError() bool { e := r.ioError; r.ioError = false; return e }

// ---- raw access

func (r *ST25R3916) ReadRegs(reg byte, out []byte) {
	if !r.t.Exchange([]byte{reg | opRead}, out) {
		r.ioError = true
		for i := range out {
			out[i] = 0
		}
	}
}

func (r *ST25R3916) ReadReg(reg byte) byte {
	var v [1]byte
	r.ReadRegs(reg, v[:])
	return v[0]
}

func (r *ST25R3916) WriteReg(reg, val byte) {
	if !r.t.Write([]byte{reg, val}) {
		r.ioError = true
	}
}

func (r *ST25R3916) SetBits(reg, mask byte)   { r.WriteReg(reg, r.ReadReg(reg)|mask) }
func (r *ST25R3916) ClearBits(reg, mask byte) { r.WriteReg(reg, r.ReadReg(reg)&^mask) }

func (r *ST25R3916) ReadSpaceB(reg byte) byte {
	var v [1]byte
	if !r.t.Exchange([]byte{cmdSpaceB, reg&0x3F | opRead}, v[:]) {
		r.ioError = true
	}
	return v[0]
}

func (r *ST25R3916) WriteSpaceB(reg, val byte) {
	if !r.t.Write([]byte{cmdSpaceB, reg & 0x3F, val}) {
		r.ioError = true
	}
}

func (r *ST25R3916) command(cmd byte, payload []byte, delayMs uint32) {
	if len(payload) > 7 {
		r.ioError = true
		return
	}
	b := append([]byte{cmd}, payload...)
	if !r.t.Write(b) {
		r.ioError = true
	}
	if delayMs != 0 {
		r.t.SleepMs(delayMs)
	}
}

func (r *ST25R3916) loadFifo(data []byte) {
	if len(data) > 64 {
		r.ioError = true
		return
	}
	if !r.t.Write(append([]byte{opFifoLoad}, data...)) {
		r.ioError = true
	}
}

func (r *ST25R3916) fifoCount() int {
	var s [2]byte
	r.ReadRegs(regFifoStatus, s[:])
	r.fifoStatus2 = s[1]
	return int(s[0]) | int(s[1]&0xC0)<<2
}

func (r *ST25R3916) readIrqs() uint32 {
	var b [4]byte
	r.ReadRegs(regIrqMain, b[:])
	return uint32(b[0]) | uint32(b[1])<<8 | uint32(b[2])<<16 | uint32(b[3])<<24
}

func (r *ST25R3916) writeMask(v uint32) {
	for i := 0; i < 4; i++ {
		r.WriteReg(byte(regIrqMask+i), byte(v>>(24-8*i)))
	}
}

// ---- bring-up

// Identify returns the IC type and revision.
func (r *ST25R3916) Identify() (icType, rev byte, err error) {
	raw := r.ReadReg(regICIdentity)
	if r.TakeIOError() {
		return 0, 0, ErrI2C
	}
	if raw == 0xFF || raw == 0 {
		return 0, 0, ErrNoChip
	}
	icType, rev = raw>>3&0x1F, raw&7
	if icType != 0x05 || rev == 0 {
		return 0, 0, ErrNoChip
	}
	return icType, rev, nil
}

// Init resets, configures NFC-A reader mode and switches the field on.
func (r *ST25R3916) Init() error {
	if _, _, err := r.Identify(); err != nil {
		return err
	}
	r.command(cmdSetDef, nil, 10)
	r.command(cmdTestAcc, []byte{0x04, 0x10}, 0)
	// Sequence follows M5Unit-NFC-Go and DS12484.
	r.WriteReg(0x00, 0x10)
	r.WriteReg(0x01, 0x84)
	r.WriteReg(0x28, 0xD0)
	r.WriteReg(0x00, r.ReadReg(0x00)&^0x07|0x07)
	r.WriteSpaceB(0x2A, 0x80)
	r.SetBits(0x01, 0x20)
	r.WriteSpaceB(0x2A, 0x00)
	r.WriteReg(0x2A, 0x13)
	r.WriteReg(0x2B, 0x02)
	r.WriteReg(0x08, r.ReadReg(0x08)&0x0F|0x50)
	r.WriteReg(0x29, 0x5F)
	r.WriteSpaceB(0x05, 0x40)
	r.WriteReg(0x26, 0x82)
	r.WriteReg(0x27, 0x82)
	r.SetBits(0x02, 0x03)
	r.command(cmdClrFifo, nil, 0)
	r.writeMask(0xFFFF00FF)
	r.readIrqs()
	if err := r.enableOscillator(); err != nil {
		return err
	}
	r.writeMask(0)
	r.command(cmdAdjReg, nil, 5)
	if err := r.configureNFCA(); err != nil {
		return err
	}
	if r.TakeIOError() {
		return ErrI2C
	}
	return nil
}

func (r *ST25R3916) enableOscillator() error {
	if r.ReadReg(regOpCtrl)&opCtrlEn != 0 {
		return nil
	}
	r.readIrqs()
	r.SetBits(regOpCtrl, opCtrlEn)
	deadline := r.t.NowMs() + 25
	seen := false
	for int32(r.t.NowMs()-deadline) < 0 {
		if r.ReadReg(regIrqMain)&irqOsc != 0 {
			seen = true
			break
		}
		r.t.SleepMs(1)
	}
	if !seen {
		if r.TakeIOError() {
			return ErrI2C
		}
		return ErrOsc
	}
	if r.ReadReg(regAuxDisplay)&auxDispOscOK == 0 {
		return ErrOsc
	}
	return nil
}

func (r *ST25R3916) configureNFCA() error {
	r.command(cmdStop, nil, 0)
	r.ClearBits(regOpCtrl, opCtrlTxEn|opCtrlRxEn)
	r.WriteReg(regMode, 0x09)
	r.WriteReg(regBitRate, 0x00)
	r.WriteReg(regISO14443A, 0x00)
	r.ClearBits(regAux, 0x04)
	for _, rv := range [][2]byte{{0x30, 0x40}, {0x31, 0x03}, {0x32, 0x40}, {0x33, 0x03}, {0x0C, 0x47}, {0x0D, 0x00}} {
		r.WriteSpaceB(rv[0], rv[1])
	}
	for _, rv := range [][2]byte{{0x0B, 0x08}, {0x0C, 0x2D}, {0x0D, 0xD8}, {0x0E, 0x22}} {
		r.WriteReg(rv[0], rv[1])
	}
	r.writeMask(0)
	r.command(cmdRstGain, nil, 0)
	r.rxCRC, r.nrt = -1, -1
	r.FieldOn()
	if r.TakeIOError() {
		return ErrI2C
	}
	return nil
}

func (r *ST25R3916) FieldOn() {
	r.command(cmdFieldOn, nil, 5)
	r.SetBits(regOpCtrl, opCtrlTxEn|opCtrlRxEn)
}

func (r *ST25R3916) FieldOff() {
	r.command(cmdStop, nil, 5)
	r.ClearBits(regOpCtrl, opCtrlTxEn|opCtrlRxEn)
}

// FieldReset power-cycles the RF field so cards restart.
func (r *ST25R3916) FieldReset() {
	r.FieldOff()
	r.t.SleepMs(20)
	r.FieldOn()
	r.t.SleepMs(10)
}

// ---- transceive

func (r *ST25R3916) setRxCRC(check bool) {
	want := int8(0)
	if check {
		want = 1
	}
	if r.rxCRC == want {
		return
	}
	if check {
		r.ClearBits(regAux, auxNoCRCRx)
	} else {
		r.SetBits(regAux, auxNoCRCRx)
	}
	r.rxCRC = want
}

// setNRT programs the no-response timer. Units: fine = 64/fc, coarse = 4096/fc, fc = 13.56 MHz.
func (r *ST25R3916) setNRT(ms uint32) {
	us := int64(ms) * 1000
	// steps = us * 13.56 / 64 (fine) or / 4096 (coarse), rounded; 13.56 = 1356/100
	fine := (us*1356 + 3200) / 6400
	var steps int64
	coarse := false
	if fine <= 0xFFFF {
		steps = fine
		if steps < 1 {
			steps = 1
		}
	} else {
		steps = (us*1356 + 204800) / 409600
		if steps > 0xFFFF {
			steps = 0xFFFF
		}
		coarse = true
	}
	key := steps << 1
	if coarse {
		key |= 1
	}
	if r.nrt == key {
		return
	}
	timer := r.ReadReg(regTimerEMV)
	if coarse {
		timer |= 1
	} else {
		timer &^= 1
	}
	r.WriteReg(regTimerEMV, timer)
	r.WriteReg(regNRT1, byte(steps>>8))
	r.WriteReg(regNRT2, byte(steps))
	r.nrt = key
}

func (r *ST25R3916) prepare(rxCRC bool, timeoutMs uint32) {
	r.command(cmdClrFifo, nil, 0)
	r.readIrqs()
	r.setRxCRC(rxCRC)
	r.setNRT(timeoutMs)
}

func (r *ST25R3916) collect(timeoutMs uint32, ignore uint32, rx []byte, shortRx bool) (int, error) {
	deadline := r.t.NowMs() + timeoutMs + 50
	var irq uint32
	for {
		irq |= r.readIrqs()
		if r.TakeIOError() {
			return 0, ErrI2C
		}
		if irq&irqRXE != 0 {
			break
		}
		if irq&irqNRE != 0 && irq&irqRXS == 0 {
			return 0, ErrTimeout
		}
		if int32(r.t.NowMs()-deadline) > 0 {
			return 0, ErrTimeout
		}
	}
	irq |= r.readIrqs()
	if irq&irqCol != 0 {
		return 0, ErrCollision
	}
	if irq&irqErrors&^ignore != 0 {
		return 0, ErrFrame
	}
	n := r.fifoCount()
	if shortRx && (n != 1 || r.fifoStatus2>>1&0x07 == 0) {
		return 0, ErrFrame // not a 4-bit frame
	}
	if n > len(rx) {
		return 0, ErrParam
	}
	for got := 0; got < n; {
		chunk := n - got
		if chunk > 64 {
			chunk = 64
		}
		if !r.t.Exchange([]byte{opFifoRead}, rx[got:got+chunk]) {
			return 0, ErrI2C
		}
		got += chunk
	}
	if r.TakeIOError() {
		return 0, ErrI2C
	}
	return n, nil
}

// SendShort sends REQA/WUPA and returns the ATQA bytes read into rx.
func (r *ST25R3916) SendShort(cmd byte, rx []byte, timeoutMs uint32) (int, error) {
	r.prepare(false, timeoutMs)
	r.command(cmd, nil, 0)
	return r.collect(timeoutMs, 0, rx, false)
}

// Transceive sends whole bytes and returns the number of response bytes in rx.
// txCRC: chip appends CRC_A. rxCRC: chip checks CRC_A and it is stripped here; rx needs 2 spare
// bytes then, because the chip leaves CRC_A in the FIFO. shortRx ignores the CRC/parity flags
// that the 4-bit WRITE ACK trips, but accepts only a one-byte frame that ends in an incomplete
// byte, the rule ST's RFAL applies to the T2T ACK/NAK (rfalErrorHandling).
func (r *ST25R3916) Transceive(tx, rx []byte, txCRC, rxCRC bool, timeoutMs uint32, shortRx bool) (int, error) {
	if len(tx) == 0 || len(tx) > 64 {
		return 0, ErrParam
	}
	r.prepare(rxCRC, timeoutMs)
	bits := uint32(len(tx)) << 3
	r.WriteReg(regNumTx1, byte(bits>>8))
	r.WriteReg(regNumTx2, byte(bits))
	r.loadFifo(tx)
	if txCRC {
		r.command(cmdTxCRC, nil, 0)
	} else {
		r.command(cmdTxNoCRC, nil, 0)
	}
	var ignore uint32
	if shortRx {
		ignore = irqCRC | irqPar
	}
	n, err := r.collect(timeoutMs, ignore, rx, shortRx)
	if err != nil {
		return 0, err
	}
	if rxCRC && n >= 3 {
		crc := CRCA(rx[:n-2])
		if rx[n-2] == byte(crc) && rx[n-1] == byte(crc>>8) {
			n -= 2
		}
	}
	return n, nil
}
