package nfc

import (
	"fmt"
	"io"
)

// NFC-A passive target (card emulation) on the ST25R3916, driving a Target.
//
// Ported from ../../arduino/src/Listener.cpp (itself from M5Stack's MIT-licensed M5Unit-NFC emulation
// layer). The chip answers REQA/WUPA and the whole anticollision by itself from the PT memory (UID,
// ATQA, SAK); this code follows the target state, reads the frames that arrive once a reader has
// selected us and answers them from the target model.
//
// Timing: a reader gives a tag about 5 ms to answer, so Poll has to be called back to back, and
// nothing may print or allocate while it runs. Events go into a small ring buffer and are printed
// afterwards with PrintLog.

const (
	regPtDef         = 0x08
	regMRT           = 0x0F
	regPtMask        = 0x19
	regPtDisplay     = 0x21
	regBitrateDetect = 0x24
	regAntTune1      = 0x26
	regAntTune2      = 0x27

	cmdGoToSense = 0xCD
	cmdGoToSleep = 0xCE
	cmdUnmaskRx  = 0xD1

	opCtrlWU      = 0x04
	ptdAcAP2P     = 0x08
	ptd212424_1R  = 0x04
	ptd106AcA     = 0x01
	isoNfcF0      = 0x20
	isoNoTxPar    = 0x80
	isoNoRxPar    = 0x40
	auxEfdO       = 0x40
	irqNFCT       = 1 << 8
	irqEON        = 1 << (8 + 4)
	irqEOF        = 1 << (8 + 3)
	irqRxePta     = 1 << (24 + 4)
	irqWuAx       = 1 << (24 + 1)
	irqWuA        = 1 << 24
	defaultIrqs   = irqNFCT | irqRXS | irqEON | irqEOF | irqCRC | irqErr1 | irqErr2 | irqPar
	modeIrqs      = irqWuA | irqWuAx | irqRxePta
	ptaIdle       = 0x01
	ptaHalt       = 0x09
	listenerLog   = 256
	listenerFrame = 272 // up to a 256-byte ISO-DEP frame plus CRC
)

type lState uint8

const (
	lOff lState = iota
	lIdle
	lReady
	lActive
	lHalt
)

var lStateNames = [...]string{"OFF", "IDLE", "READY", "ACTIVE", "HALT"}

type lKind uint8

const (
	evState lKind = iota
	evField
	evRate
	evRx
	evTx
	evErr
	evIRQ
)

type lEvent struct {
	us   uint32
	kind lKind
	n    uint8
	data [12]byte
}

type Listener struct {
	// NowUs is the microsecond clock for the event log. Without it the log uses the transport's
	// millisecond clock.
	NowUs func() uint32

	rf         *ST25R3916
	target     Target
	state      lState
	wakeup     bool
	bitrate    int8 // 0 = 106 kbit/s, -1 unknown
	pending    uint32
	fields     uint32
	frames     uint32
	log        [listenerLog]lEvent
	logHead    int
	logCount   int
	logDropped int
	raw        [listenerFrame]byte
	reply      [listenerFrame]byte
}

func NewListener(rf *ST25R3916, target Target) *Listener {
	return &Listener{rf: rf, target: target, bitrate: -1}
}

func (l *Listener) SetTarget(t Target) { l.target = t }
func (l *Listener) FieldCount() uint32 { return l.fields }
func (l *Listener) FrameCount() uint32 { return l.frames }

// ---- log ----------------------------------------------------------------------------------

func (l *Listener) now() uint32 {
	if l.NowUs != nil {
		return l.NowUs()
	}
	return l.rf.NowMs() * 1000
}

func (l *Listener) addLog(kind lKind, data []byte) {
	e := &l.log[(l.logHead+l.logCount)%listenerLog]
	if l.logCount == listenerLog { // full: drop the oldest
		l.logHead = (l.logHead + 1) % listenerLog
		l.logDropped++
	} else {
		l.logCount++
	}
	e.us = l.now()
	e.kind = kind
	e.n = uint8(copy(e.data[:], data))
}

func (l *Listener) logState(s lState) {
	b := [1]byte{byte(s)}
	l.addLog(evState, b[:])
}

func (l *Listener) clearLog() { l.logHead, l.logCount, l.logDropped = 0, 0, 0 }

// PrintLog writes the event log (then empties it).
func (l *Listener) PrintLog(w io.Writer) {
	var t0 uint32
	if l.logCount > 0 {
		t0 = l.log[l.logHead].us
	}
	if l.logDropped > 0 {
		fmt.Fprintf(w, "(%d older events dropped)\n", l.logDropped)
	}
	for i := 0; i < l.logCount; i++ {
		e := &l.log[(l.logHead+i)%listenerLog]
		d := e.us - t0
		fmt.Fprintf(w, "%6d.%03d ms  ", d/1000, d%1000)
		switch e.kind {
		case evState:
			fmt.Fprintf(w, "-> %s", lStateNames[e.data[0]])
		case evField:
			io.WriteString(w, "reader field detected")
		case evRate:
			io.WriteString(w, "bit rate detected: 106 kbit/s")
		case evRx:
			io.WriteString(w, "rx  ")
		case evTx:
			io.WriteString(w, "tx  ")
		case evErr:
			io.WriteString(w, "rx error  ")
		case evIRQ:
			io.WriteString(w, "irq ")
		}
		if e.kind == evRx || e.kind == evTx || e.kind == evErr || e.kind == evIRQ {
			for j := 0; j < int(e.n); j++ {
				fmt.Fprintf(w, "%02X ", e.data[j])
			}
			if int(e.n) == len(e.data) {
				io.WriteString(w, "...")
			}
		}
		io.WriteString(w, "\n")
	}
	l.clearLog()
}

// ---- helpers ------------------------------------------------------------------------------

func (l *Listener) change(reg, value, mask byte) {
	l.rf.WriteReg(reg, (l.rf.ReadReg(reg)&^mask)|(value&mask))
}

// Interrupt mask registers: a 1 masks the interrupt.
func (l *Listener) enableIrqs(bits uint32) {
	for i := 0; i < 4; i++ {
		if m := byte(bits >> (8 * uint(i))); m != 0 {
			reg := byte(regIrqMask + i)
			l.rf.WriteReg(reg, l.rf.ReadReg(reg)&^m)
		}
	}
}

func (l *Listener) disableIrqs(bits uint32) {
	for i := 0; i < 4; i++ {
		if m := byte(bits >> (8 * uint(i))); m != 0 {
			reg := byte(regIrqMask + i)
			l.rf.WriteReg(reg, l.rf.ReadReg(reg)|m)
		}
	}
}

// irq reads (and clears) the chip's interrupt registers into pending and returns the asked-for bits.
func (l *Listener) irq(bits uint32) uint32 {
	l.pending |= l.rf.readIrqs()
	got := l.pending & bits
	l.pending &^= got
	return got
}

// ---- set-up (configure_emulation_a + start_emulation) -------------------------------------

// Start puts the chip in target mode with the given 7-byte UID.
func (l *Listener) Start(uid [7]byte, atqa uint16, sak byte) error {
	rf := l.rf
	if err := rf.Init(); err != nil { // reader mode, field on
		return err
	}
	rf.command(cmdStop, nil, 0)
	rf.ClearBits(regOpCtrl, opCtrlTxEn) // field off
	rf.WriteReg(regMode, 0xC8)          // target, NFC-A, bit rate detection
	rf.WriteReg(regPtDef, 0x5C)         // auto response for NFC-A only
	rf.WriteReg(regPtMask, 0x02)        // mask I_wu_ax
	rf.WriteReg(regTimerEMV, 0x08)      // MRT step 512/fc

	// PT memory A: UID(10, padded) ATQA(2, LE) SAK of level 1..3
	var pt [15]byte
	copy(pt[:], uid[:])
	pt[10] = byte(atqa)
	pt[11] = byte(atqa >> 8)
	pt[12] = sak | 0x04 // UID not complete after level 1 (7-byte UID)
	pt[13] = sak &^ 0x04
	pt[14] = sak &^ 0x04
	l.change(regAux, 0x10, 0x30) // 7-byte UID
	rf.writePtMemoryA(pt[:])

	l.change(regPtDef, ptdAcAP2P|ptd212424_1R, ptdAcAP2P|ptd212424_1R|ptd106AcA)
	l.change(regTimerEMV, 0x00, 0xE0)
	rf.SetBits(regTimerEMV, 0x08)
	rf.WriteReg(regMRT, 3) // about 100 us at 512/fc steps
	rf.ClearBits(regISO14443A, isoNoTxPar|isoNoRxPar|isoNfcF0)
	// ST's RFAL listen set-up for the ST25R3916 (stm32duino/ST25R3916, src/rfal_rfst25r3916_analogConfigTbl.h,
	// "Chip-Specific Listen On"): antenna tuning A=0x00 and B=0xFF, over/undershoot protection off. The 3916B
	// table differs (0x00/0x00). M5Stack's 0x82/0x82 is the poller value from the same table, not the listen one.
	rf.WriteReg(regAntTune1, 0x00)
	rf.WriteReg(regAntTune2, 0xFF)
	for reg := byte(0x30); reg <= 0x33; reg++ {
		rf.WriteSpaceB(reg, 0x00) // over/undershoot off
	}
	// ST's RFAL listen set-up: answer with the chip's internal load modulation (AUX_MOD in register
	// space B: lm_dri on, lm_ext off). The reader-mode init does not touch it.
	auxMod := rf.ReadSpaceB(0x28)
	rf.WriteSpaceB(0x28, (auxMod&^0x20)|0x10)
	before := [2]byte{auxMod, rf.ReadSpaceB(0x28)}
	rf.command(cmdUnmaskRx, nil, 0)
	l.clearLog()
	l.fields, l.frames = 0, 0
	l.addLog(evIRQ, before[:]) // AUX_MOD before and after the change above
	l.gotoOff()
	if rf.TakeIOError() {
		return ErrI2C
	}
	return nil
}

// Stop returns the chip to reader mode.
func (l *Listener) Stop() error {
	l.rf.command(cmdStop, nil, 0)
	err := l.rf.Init()
	l.rf.FieldOff() // Init leaves the reader field on, which makes a phone nearby open Wallet
	return err
}

// ---- state transitions --------------------------------------------------------------------

func (l *Listener) gotoOff() {
	rf := l.rf
	l.logState(lOff)
	l.state = lOff
	l.target.Reset()
	l.wakeup = false
	l.bitrate = -1
	rf.command(cmdStop, nil, 0)
	rf.SetBits(regOpCtrl, opCtrlRxEn)
	rf.ClearBits(regPtDef, ptd106AcA) // enable the automatic response for NFC-A
	rf.command(cmdGoToSense, nil, 0)
	rf.ClearBits(regISO14443A, isoNfcF0)
	rf.writeMask(0xFFFFFFFF)
	rf.readIrqs()
	l.pending = 0
	l.enableIrqs(irqOsc | defaultIrqs | modeIrqs)
	rf.SetBits(regAux, auxNoCRCRx)
	l.change(regMode, 0xC8, 0x78) // bit rate detection mode
	if rf.ReadReg(regAuxDisplay)&auxEfdO != 0 {
		l.gotoIdle()
	} else {
		rf.ClearBits(regOpCtrl, opCtrlTxEn|opCtrlRxEn|opCtrlEn)
	}
}

func (l *Listener) gotoIdle() {
	rf := l.rf
	l.logState(lIdle)
	wasActive := l.state == lActive
	l.state = lIdle
	if rf.ReadReg(regOpCtrl)&opCtrlEn == 0 {
		rf.SetBits(regOpCtrl, opCtrlEn|opCtrlRxEn)
		if rf.ReadReg(regAuxDisplay)&auxDispOscOK == 0 {
			t0 := rf.NowMs()
			ok := false
			for rf.NowMs()-t0 < 1000 {
				if l.irq(irqOsc) != 0 {
					ok = true
					break
				}
			}
			if !ok {
				l.gotoOff()
				return
			}
		}
	} else {
		l.irq(irqOsc)
	}
	rf.SetBits(regAux, auxNoCRCRx)
	if wasActive && !l.wakeup {
		rf.ClearBits(regPtDef, ptd106AcA)
		rf.command(cmdGoToSense, nil, 0)
	}
	rf.command(cmdClrFifo, nil, 0)
	rf.command(cmdUnmaskRx, nil, 0)
	l.wakeup = false
}

func (l *Listener) gotoReady() {
	rf := l.rf
	l.logState(lReady)
	l.state = lReady
	if l.irq(irqEOF) != 0 {
		l.gotoOff()
		return
	}
	rf.ClearBits(regAux, auxNoCRCRx)
	rf.WriteReg(regBitRate, 0x00) // 106 kbit/s both ways
	rf.ClearBits(regOpCtrl, opCtrlWU)
	rf.WriteReg(regMode, 0x88) // target, NFC-A, fixed bit rate
}

func (l *Listener) gotoActive() {
	l.logState(lActive)
	l.state = lActive
	l.rf.SetBits(regPtDef, ptd106AcA) // stop the automatic response: we answer from now on
	l.irq(irqPar | irqCRC | irqErr2 | irqErr1)
	l.enableIrqs(irqRXE)
}

func (l *Listener) gotoHalt() {
	rf := l.rf
	l.logState(lHalt)
	l.state = lHalt
	rf.ClearBits(regPtDef, ptd106AcA)
	rf.command(cmdGoToSleep, nil, 0)
	l.change(regMode, 0xC8, 0xFB)
	rf.ClearBits(regISO14443A, isoNfcF0)
	rf.command(cmdUnmaskRx, nil, 0)
	l.enableIrqs(irqNFCT | irqRXS | irqCRC | irqErr1 | irqErr2 | irqPar | irqEON | irqEOF | modeIrqs)
	if rf.ReadReg(regAuxDisplay)&auxEfdO == 0 {
		l.gotoOff()
	}
}

// ---- polling ------------------------------------------------------------------------------

// Poll is one step of the state machine; call it back to back.
func (l *Listener) Poll() {
	switch l.state {
	case lOff:
		l.pollOff()
	case lIdle:
		l.pollIdle()
	case lReady:
		l.pollReady()
	case lActive:
		l.pollActive()
	case lHalt:
		l.pollHalt()
	}
}

func (l *Listener) readBitrate() {
	br := (l.rf.ReadReg(regBitrateDetect) >> 4) & 0x03
	if br > 2 {
		br = 2
	}
	l.bitrate = int8(br)
	l.addLog(evRate, nil)
}

// readFrame reads the FIFO into l.raw and returns the byte count.
func (l *Listener) readFrame(max int) int {
	count := l.rf.fifoCount()
	if count > max {
		count = max
	}
	if count == 0 {
		return 0
	}
	return l.rf.readFifo(l.raw[:count])
}

func (l *Listener) pollOff() {
	if l.irq(irqEON) != 0 {
		l.fields++
		l.addLog(evField, nil)
		l.gotoIdle()
	}
}

func (l *Listener) pollIdle() {
	got := l.irq(irqNFCT | irqRXE | irqEOF | irqRxePta)
	if got == 0 {
		return
	}
	if got&irqNFCT != 0 {
		l.readBitrate()
	}
	if got&irqEOF != 0 {
		l.gotoOff()
		return
	}
	if got&irqRXE != 0 && l.bitrate >= 0 {
		l.irq(irqRXE | irqEOF | irqCRC | irqPar | irqErr2 | irqErr1)
		n := l.readFrame(32)
		l.addLog(evRx, l.raw[:n]) // a frame the chip did not answer by itself
		l.rf.command(cmdClrFifo, nil, 0)
		l.rf.command(cmdUnmaskRx, nil, 0)
		return
	}
	if got&irqRxePta != 0 && l.bitrate == 0 {
		pta := l.rf.ReadReg(regPtDisplay)
		if pta&0x0F > ptaIdle {
			l.gotoReady()
		}
	}
}

func (l *Listener) pollReady() {
	want := uint32(irqEOF | irqWuA)
	if l.wakeup {
		want = irqEOF | irqWuAx
	}
	got := l.irq(want)
	if got == 0 {
		return
	}
	if got&irqEOF != 0 {
		l.gotoOff()
		return
	}
	l.gotoActive()
}

func (l *Listener) pollActive() {
	got := l.irq(irqEOF | irqRXE)
	if got == 0 {
		return
	}
	if got&irqEOF != 0 {
		l.gotoOff()
		return
	}
	got |= l.irq(irqPar | irqCRC | irqErr2 | irqErr1)
	n := l.readFrame(listenerFrame)
	if got&irqErrors != 0 || n <= 2 {
		l.addLog(evErr, l.raw[:n])
		l.rf.command(cmdClrFifo, nil, 0)
		l.rf.command(cmdUnmaskRx, nil, 0)
		if l.wakeup {
			l.gotoHalt()
		} else {
			l.gotoIdle()
		}
		return
	}
	n -= 2 // CRC_A stays in the FIFO
	l.frames++
	r := l.target.Handle(l.raw[:n], l.reply[:])
	if r.Len != 0 || r.Ack4 {
		l.transmit(r) // answer first, log afterwards
	}
	l.addLog(evRx, l.raw[:n])
	if r.Halt {
		l.disableIrqs(irqRXE)
		l.gotoHalt()
	}
}

func (l *Listener) transmit(r TargetReply) {
	rf := l.rf
	rf.command(cmdClrFifo, nil, 0)
	if r.Ack4 { // 4-bit ACK/NAK frame, no CRC
		nib := [1]byte{r.Nibble}
		rf.loadFifo(nib[:])
		rf.WriteReg(regNumTx1, 0x00)
		rf.WriteReg(regNumTx2, 4) // 0 bytes, 4 bits
		rf.command(cmdTxNoCRC, nil, 0)
		l.addLog(evTx, nib[:])
		return
	}
	rf.loadFifo(l.reply[:r.Len])
	rf.WriteReg(regNumTx1, byte((r.Len<<3)>>8))
	rf.WriteReg(regNumTx2, byte(r.Len<<3))
	rf.command(cmdTxCRC, nil, 0)
	l.addLog(evTx, l.reply[:r.Len])
}

func (l *Listener) pollHalt() {
	got := l.irq(irqNFCT | irqRXE | irqEOF | irqRxePta)
	if got == 0 {
		return
	}
	if got&irqNFCT != 0 {
		l.readBitrate()
	}
	if got&irqEOF != 0 {
		l.gotoOff()
		return
	}
	if got&irqRXE != 0 && l.bitrate >= 0 {
		l.rf.command(cmdClrFifo, nil, 0)
		l.rf.command(cmdUnmaskRx, nil, 0)
		return
	}
	if got&irqRxePta != 0 && l.bitrate == 0 {
		pta := l.rf.ReadReg(regPtDisplay)
		if pta&0x0F > ptaHalt {
			l.wakeup = true
			l.gotoReady()
		}
	}
}
