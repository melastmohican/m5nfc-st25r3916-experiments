"""NFC-A passive target (card emulation) on the ST25R3916, driving a target model.

Ported from ../arduino/src/Listener.cpp (itself from M5Stack's MIT-licensed M5Unit-NFC emulation
layer). The chip answers REQA/WUPA and the whole anticollision by itself from the PT memory (UID,
ATQA, SAK); this code follows the target state, reads the frames that arrive once a reader has
selected us and answers them from the target model (Type2Tag or IsoDepTarget: handle(frame) ->
(reply, nibble, halt), reset()).

Timing: a reader gives a tag about 5 ms to answer, so poll() has to be called back to back and
nothing may print while it runs. Events go into a small list and are printed afterwards with
log_lines(). One reply cycle of about ten I2C operations measured 1.7 ms from MicroPython on the
Feather (21 Nov 2026).
"""
import time

from nfc.st25r3916 import (
    CMD_CLEAR_FIFO,
    CMD_STOP,
    CMD_TX_WITH_CRC,
    CMD_TX_WITHOUT_CRC,
    IRQ_OSC,
    OP_CTRL_EN,
    OP_CTRL_RX_EN,
    OP_CTRL_TX_EN,
    REG_AUX,
    REG_AUX_DISPLAY,
    REG_BIT_RATE,
    REG_IRQ_MASK,
    REG_ISO14443A,
    REG_MODE,
    REG_NUM_TX1,
    REG_NUM_TX2,
    REG_OP_CTRL,
    REG_TIMER_EMV,
    AUX_NO_CRC_RX,
)

try:
    _us, _diff = time.ticks_us, time.ticks_diff
except AttributeError:  # CPython (host tests)
    def _us():
        return int(time.monotonic() * 1000000) & 0xFFFFFFFF

    def _diff(a, b):
        return (a - b) & 0xFFFFFFFF

REG_PT_DEF = 0x08
REG_MRT = 0x0F
REG_PT_MASK = 0x19
REG_PT_DISPLAY = 0x21
REG_BITRATE_DETECT = 0x24
REG_ANT_TUNE1 = 0x26
REG_ANT_TUNE2 = 0x27

CMD_GO_TO_SENSE = 0xCD
CMD_GO_TO_SLEEP = 0xCE
CMD_UNMASK_RX = 0xD1
OP_LOAD_PT_A = 0xA0

OP_CTRL_WU = 0x04
PT_D_AC_AP2P = 0x08
PT_D_212_424_1R = 0x04
PT_D_106_AC_A = 0x01
ISO_NFC_F0 = 0x20
ISO_NO_TX_PAR = 0x80
ISO_NO_RX_PAR = 0x40
AUX_EFD_O = 0x40
AUX_OSC_OK = 0x10

# IRQ bits in read_irqs() layout: main | timer << 8 | error << 16 | passive target << 24
I_OSC = IRQ_OSC
I_RXS = 1 << 5
I_RXE = 1 << 4
I_NFCT = 1 << 8
I_EON = 1 << (8 + 4)
I_EOF = 1 << (8 + 3)
I_CRC = 1 << (16 + 7)
I_PAR = 1 << (16 + 6)
I_ERR2 = 1 << (16 + 5)
I_ERR1 = 1 << (16 + 4)
I_RXE_PTA = 1 << (24 + 4)
I_WU_AX = 1 << (24 + 1)
I_WU_A = 1 << 24
I_ERRORS = I_CRC | I_PAR | I_ERR1 | I_ERR2
DEFAULT_IRQ = I_NFCT | I_RXS | I_EON | I_EOF | I_CRC | I_ERR1 | I_ERR2 | I_PAR
MODE_IRQ = I_WU_A | I_WU_AX | I_RXE_PTA
PTA_IDLE = 0x01
PTA_HALT = 0x09

OFF, IDLE, READY, ACTIVE, HALT = 0, 1, 2, 3, 4
_STATE_NAMES = ("OFF", "IDLE", "READY", "ACTIVE", "HALT")
EV_STATE, EV_FIELD, EV_RATE, EV_RX, EV_TX, EV_ERR, EV_IRQ = range(7)
LOG_SIZE = 256


class Listener:
    def __init__(self, rf, target):
        self.rf = rf
        self.target = target
        self.state = OFF
        self.fields = 0
        self.frames = 0
        self.log = []
        self.dropped = 0
        self._wakeup = False
        self._bitrate = -1  # 0 = 106 kbit/s, -1 unknown
        self._pending = 0

    def set_target(self, target):
        self.target = target

    # ---- log ----------------------------------------------------------
    def _log(self, kind, data=b""):
        if len(self.log) >= LOG_SIZE:  # full: drop the oldest
            del self.log[0]
            self.dropped += 1
        self.log.append((_us(), kind, bytes(data[:12]), len(data) > 12))

    def log_lines(self):
        """The event log as text lines; empties it."""
        lines = []
        if self.dropped:
            lines.append("(%d older events dropped)" % self.dropped)
        t0 = self.log[0][0] if self.log else 0
        for us, kind, data, more in self.log:
            d = _diff(us, t0)
            text = "%6d.%03d ms  " % (d // 1000, d % 1000)
            if kind == EV_STATE:
                text += "-> " + _STATE_NAMES[data[0]]
            elif kind == EV_FIELD:
                text += "reader field detected"
            elif kind == EV_RATE:
                text += "bit rate detected: 106 kbit/s"
            else:
                text += {EV_RX: "rx  ", EV_TX: "tx  ", EV_ERR: "rx error  ", EV_IRQ: "irq "}[kind]
                text += "".join("%02X " % b for b in data)
                if more:
                    text += "..."
            lines.append(text)
        self.log = []
        self.dropped = 0
        return lines

    # ---- helpers ------------------------------------------------------
    def _change(self, reg, value, mask):
        rf = self.rf
        rf.write_reg(reg, (rf.read_reg(reg) & ~mask & 0xFF) | (value & mask))

    def _enable_irqs(self, bits):  # interrupt mask registers: a 1 masks the interrupt
        rf = self.rf
        for i in range(4):
            m = (bits >> (8 * i)) & 0xFF
            if m:
                rf.write_reg(REG_IRQ_MASK + i, rf.read_reg(REG_IRQ_MASK + i) & ~m & 0xFF)

    def _disable_irqs(self, bits):
        rf = self.rf
        for i in range(4):
            m = (bits >> (8 * i)) & 0xFF
            if m:
                rf.write_reg(REG_IRQ_MASK + i, rf.read_reg(REG_IRQ_MASK + i) | m)

    def _irq(self, bits):
        """Reads (and clears) the chip's interrupt registers into _pending, returns the asked-for bits."""
        self._pending |= self.rf.read_irqs()
        got = self._pending & bits
        self._pending &= ~got
        return got

    # ---- set-up (configure_emulation_a + start_emulation) -----------------
    def start(self, uid, atqa=0x0044, sak=0x00):
        """Puts the chip in target mode with the given 7-byte UID."""
        rf = self.rf
        rf.init()  # reader mode, field on
        rf.command(CMD_STOP)
        rf.clear_bits(REG_OP_CTRL, OP_CTRL_TX_EN)  # field off
        rf.write_reg(REG_MODE, 0xC8)  # target, NFC-A, bit rate detection
        rf.write_reg(REG_PT_DEF, 0x5C)  # auto response for NFC-A only
        rf.write_reg(REG_PT_MASK, 0x02)  # mask I_wu_ax
        rf.write_reg(REG_TIMER_EMV, 0x08)  # MRT step 512/fc

        # PT memory A: UID(10, padded) ATQA(2, LE) SAK of level 1..3
        pt = bytearray(15)
        pt[0:7] = uid
        pt[10] = atqa & 0xFF
        pt[11] = atqa >> 8
        pt[12] = sak | 0x04  # UID not complete after level 1 (7-byte UID)
        pt[13] = sak & ~0x04 & 0xFF
        pt[14] = sak & ~0x04 & 0xFF
        self._change(REG_AUX, 0x10, 0x30)  # 7-byte UID
        rf.t.write(bytes([OP_LOAD_PT_A]) + pt)

        self._change(REG_PT_DEF, PT_D_AC_AP2P | PT_D_212_424_1R, PT_D_AC_AP2P | PT_D_212_424_1R | PT_D_106_AC_A)
        self._change(REG_TIMER_EMV, 0x00, 0xE0)
        rf.set_bits(REG_TIMER_EMV, 0x08)
        rf.write_reg(REG_MRT, 3)  # about 100 us at 512/fc steps
        rf.clear_bits(REG_ISO14443A, ISO_NO_TX_PAR | ISO_NO_RX_PAR | ISO_NFC_F0)
        rf.write_reg(REG_ANT_TUNE1, 0x00)
        rf.write_reg(REG_ANT_TUNE2, 0xFF)
        for reg in range(0x30, 0x34):
            rf.write_space_b(reg, 0x00)  # over/undershoot off
        # ST's RFAL listen set-up: answer with the chip's internal load modulation (AUX_MOD in register
        # space B: lm_dri on, lm_ext off). The reader-mode init does not touch it.
        aux_mod = rf.read_space_b(0x28)
        rf.write_space_b(0x28, (aux_mod & ~0x20 & 0xFF) | 0x10)
        after = rf.read_space_b(0x28)
        rf.command(CMD_UNMASK_RX)
        self.log = []
        self.dropped = 0
        self.fields = self.frames = 0
        self._log(EV_IRQ, bytes([aux_mod, after]))  # AUX_MOD before and after the change above
        self._goto_off()

    def stop(self):
        """Back to reader mode."""
        rf = self.rf
        rf.command(CMD_STOP)
        rf.init()
        rf.field_off()  # init() leaves the reader field on, which makes a phone nearby open Wallet

    # ---- state transitions ------------------------------------------------
    def _state(self, s):
        self._log(EV_STATE, bytes([s]))
        self.state = s

    def _goto_off(self):
        rf = self.rf
        self._state(OFF)
        self.target.reset()
        self._wakeup = False
        self._bitrate = -1
        rf.command(CMD_STOP)
        rf.set_bits(REG_OP_CTRL, OP_CTRL_RX_EN)
        rf.clear_bits(REG_PT_DEF, PT_D_106_AC_A)  # enable the automatic response for NFC-A
        rf.command(CMD_GO_TO_SENSE)
        rf.clear_bits(REG_ISO14443A, ISO_NFC_F0)
        rf._write_mask(0xFFFFFFFF)
        rf.read_irqs()
        self._pending = 0
        self._enable_irqs(I_OSC | DEFAULT_IRQ | MODE_IRQ)
        rf.set_bits(REG_AUX, AUX_NO_CRC_RX)
        self._change(REG_MODE, 0xC8, 0x78)  # bit rate detection mode
        if rf.read_reg(REG_AUX_DISPLAY) & AUX_EFD_O:
            self._goto_idle()
        else:
            rf.clear_bits(REG_OP_CTRL, OP_CTRL_TX_EN | OP_CTRL_RX_EN | OP_CTRL_EN)

    def _goto_idle(self):
        rf = self.rf
        was_active = self.state == ACTIVE
        self._state(IDLE)
        if not rf.read_reg(REG_OP_CTRL) & OP_CTRL_EN:
            rf.set_bits(REG_OP_CTRL, OP_CTRL_EN | OP_CTRL_RX_EN)
            if not rf.read_reg(REG_AUX_DISPLAY) & AUX_OSC_OK:
                t0 = _us()
                ok = False
                while _diff(_us(), t0) < 1000000:
                    if self._irq(I_OSC):
                        ok = True
                        break
                if not ok:
                    return self._goto_off()
        else:
            self._irq(I_OSC)
        rf.set_bits(REG_AUX, AUX_NO_CRC_RX)
        if was_active and not self._wakeup:
            rf.clear_bits(REG_PT_DEF, PT_D_106_AC_A)
            rf.command(CMD_GO_TO_SENSE)
        rf.command(CMD_CLEAR_FIFO)
        rf.command(CMD_UNMASK_RX)
        self._wakeup = False

    def _goto_ready(self):
        rf = self.rf
        self._state(READY)
        if self._irq(I_EOF):
            return self._goto_off()
        rf.clear_bits(REG_AUX, AUX_NO_CRC_RX)
        rf.write_reg(REG_BIT_RATE, 0x00)  # 106 kbit/s both ways
        rf.clear_bits(REG_OP_CTRL, OP_CTRL_WU)
        rf.write_reg(REG_MODE, 0x88)  # target, NFC-A, fixed bit rate

    def _goto_active(self):
        self._state(ACTIVE)
        self.rf.set_bits(REG_PT_DEF, PT_D_106_AC_A)  # stop the automatic response: we answer from now on
        self._irq(I_PAR | I_CRC | I_ERR2 | I_ERR1)
        self._enable_irqs(I_RXE)

    def _goto_halt(self):
        rf = self.rf
        self._state(HALT)
        rf.clear_bits(REG_PT_DEF, PT_D_106_AC_A)
        rf.command(CMD_GO_TO_SLEEP)
        self._change(REG_MODE, 0xC8, 0xFB)
        rf.clear_bits(REG_ISO14443A, ISO_NFC_F0)
        rf.command(CMD_UNMASK_RX)
        self._enable_irqs(I_NFCT | I_RXS | I_CRC | I_ERR1 | I_ERR2 | I_PAR | I_EON | I_EOF | MODE_IRQ)
        if not rf.read_reg(REG_AUX_DISPLAY) & AUX_EFD_O:
            self._goto_off()

    # ---- polling ------------------------------------------------------------
    def poll(self):
        """One step of the state machine; call it back to back."""
        s = self.state
        if s == ACTIVE:
            self._p_active()
        elif s == OFF:
            self._p_off()
        elif s == IDLE:
            self._p_idle()
        elif s == READY:
            self._p_ready()
        else:
            self._p_halt()

    def _read_bitrate(self):
        self._bitrate = min((self.rf.read_reg(REG_BITRATE_DETECT) >> 4) & 0x03, 2)
        self._log(EV_RATE)

    def _read_frame(self, cap):
        rf = self.rf
        n = min(rf.fifo_count(), cap)
        return rf.read_fifo(n) if n else b""

    def _p_off(self):
        if self._irq(I_EON):
            self.fields += 1
            self._log(EV_FIELD)
            self._goto_idle()

    def _p_idle(self):
        got = self._irq(I_NFCT | I_RXE | I_EOF | I_RXE_PTA)
        if not got:
            return
        if got & I_NFCT:
            self._read_bitrate()
        if got & I_EOF:
            return self._goto_off()
        rf = self.rf
        if got & I_RXE and self._bitrate >= 0:
            self._irq(I_RXE | I_EOF | I_CRC | I_PAR | I_ERR2 | I_ERR1)
            self._log(EV_RX, self._read_frame(32))  # a frame the chip did not answer by itself
            rf.command(CMD_CLEAR_FIFO)
            rf.command(CMD_UNMASK_RX)
            return
        if got & I_RXE_PTA and self._bitrate == 0:
            if rf.read_reg(REG_PT_DISPLAY) & 0x0F > PTA_IDLE:
                self._goto_ready()

    def _p_ready(self):
        got = self._irq(I_EOF | (I_WU_AX if self._wakeup else I_WU_A))
        if not got:
            return
        if got & I_EOF:
            return self._goto_off()
        self._goto_active()

    def _p_active(self):
        got = self._irq(I_EOF | I_RXE)
        if not got:
            return
        if got & I_EOF:
            return self._goto_off()
        got |= self._irq(I_PAR | I_CRC | I_ERR2 | I_ERR1)
        rf = self.rf
        raw = self._read_frame(272)  # up to a 256-byte ISO-DEP frame plus CRC
        if got & I_ERRORS or len(raw) <= 2:
            self._log(EV_ERR, raw)
            rf.command(CMD_CLEAR_FIFO)
            rf.command(CMD_UNMASK_RX)
            return self._goto_halt() if self._wakeup else self._goto_idle()
        frame = raw[:-2]  # CRC_A stays in the FIFO
        self.frames += 1
        reply, nibble, halt = self.target.handle(frame)
        if reply is not None or nibble >= 0:
            self._transmit(reply, nibble)  # answer first, log afterwards
        self._log(EV_RX, frame)
        if halt:
            self._disable_irqs(I_RXE)
            self._goto_halt()

    def _transmit(self, reply, nibble):
        rf = self.rf
        rf.command(CMD_CLEAR_FIFO)
        if reply is None:  # 4-bit ACK/NAK frame, no CRC
            rf.load_fifo(bytes([nibble]))
            rf.write_reg(REG_NUM_TX1, 0x00)
            rf.write_reg(REG_NUM_TX2, 4)  # 0 bytes, 4 bits
            rf.command(CMD_TX_WITHOUT_CRC)
            self._log(EV_TX, bytes([nibble]))
            return
        n = len(reply)
        rf.load_fifo(reply)
        rf.write_reg(REG_NUM_TX1, (n << 3) >> 8)
        rf.write_reg(REG_NUM_TX2, (n << 3) & 0xFF)
        rf.command(CMD_TX_WITH_CRC)
        self._log(EV_TX, reply)

    def _p_halt(self):
        got = self._irq(I_NFCT | I_RXE | I_EOF | I_RXE_PTA)
        if not got:
            return
        if got & I_NFCT:
            self._read_bitrate()
        if got & I_EOF:
            return self._goto_off()
        rf = self.rf
        if got & I_RXE and self._bitrate >= 0:
            rf.command(CMD_CLEAR_FIFO)
            rf.command(CMD_UNMASK_RX)
            return
        if got & I_RXE_PTA and self._bitrate == 0:
            if rf.read_reg(REG_PT_DISPLAY) & 0x0F > PTA_HALT:
                self._wakeup = True
                self._goto_ready()
