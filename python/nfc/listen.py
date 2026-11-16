"""NFC-A passive target (card emulation) on the ST25R3916: experiment, ported from M5Unit-NFC.

The register sequence and the state machine follow M5Stack's MIT-licensed emulation layer
(M5Unit-NFC, src/nfc/layer/a/emulation_layer_a_ST25R3916.cpp, and configure_emulation_a() in
src/unit/unit_ST25R3916_nfca.cpp). The chip answers REQA/WUPA and the whole anticollision by
itself from the PT memory (UID, ATQA, SAK); this code only has to follow the target state and
read the frames that arrive once the reader has selected us.

States: OFF (no field) -> IDLE (field seen, waiting for the bit rate) -> READY (selected by
anticollision) -> ACTIVE (receiving frames) -> HALT (after HLTA) -> back to IDLE / OFF.

Timing: every I2C operation through pyftdi's I2C layer costs one USB latency tick (see
transport.py), so replies are slow. This module is for finding out what a phone sends.
"""
from __future__ import annotations

import time
from typing import Callable

from .st25r3916 import (
    CMD_CLEAR_FIFO,
    CMD_STOP,
    CMD_TX_WITH_CRC,
    IRQ_OSC,
    OP_CTRL_EN,
    OP_CTRL_RX_EN,
    OP_CTRL_TX_EN,
    REG_AUX,
    REG_AUX_DISPLAY,
    REG_FIFO_STATUS1,
    REG_IRQ_MAIN,
    REG_ISO14443A,
    REG_MODE,
    REG_NUM_TX1,
    REG_NUM_TX2,
    REG_OP_CTRL,
    REG_TIMER_EMV,
    AUX_NO_CRC_RX,
    NfcError,
    St25r3916,
)

# Registers and commands used only for listening
REG_PT_DEF = 0x08  # NFCIP-1 passive target definition
REG_MRT = 0x0F  # mask receive timer
REG_BITRATE_DETECT = 0x24
REG_PT_DISPLAY = 0x21
REG_ANT_TUNE1 = 0x26
REG_ANT_TUNE2 = 0x27

CMD_GO_TO_SENSE = 0xCD
CMD_GO_TO_SLEEP = 0xCE
CMD_UNMASK_RX = 0xD1
OP_LOAD_PT_A = 0xA0  # write PT memory A: UID(10) ATQA(2) SAK(3)
OP_READ_PT = 0xBF

PT_D_AC_AP2P = 0x08
PT_D_212_424_1R = 0x04
PT_D_106_AC_A = 0x01
ISO_NFC_F0 = 0x20
ISO_NO_TX_PAR = 0x80
ISO_NO_RX_PAR = 0x40
AUX_EFD_O = 0x40  # external field detected
MODE_TARG = 0x80

# IRQ bits in our combined layout: main | timer << 8 | error << 16 | passive target << 24
I_OSC = IRQ_OSC
I_RXS = 1 << 5
I_RXE = 1 << 4
I_TXE = 1 << 3
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

OFF, IDLE, READY, ACTIVE, HALT = "off", "idle", "ready", "active", "halt"


def crc_a(data: bytes) -> bytes:
    from .nfca import crc_a as _crc

    return _crc(data)


class Listener:
    """NFC-A target with a fixed UID. Call start(), then poll() repeatedly."""

    def __init__(
        self,
        rf: St25r3916,
        uid: bytes,
        atqa: int = 0x0044,
        sak: int = 0x00,
        on_frame: Callable[[bytes], bytes | None] | None = None,
        log: Callable[[str], None] = print,
    ) -> None:
        if len(uid) not in (4, 7):
            raise ValueError("UID must be 4 or 7 bytes")
        self.rf = rf
        self.uid = uid
        self.atqa = atqa
        self.sak = sak
        self.on_frame = on_frame
        self.log = log
        self.state = OFF
        self._wakeup = False
        self._bitrate = None
        self._pending = 0
        self.t0 = time.perf_counter()

    # ---- helpers --------------------------------------------------
    def _t(self) -> str:
        return f"{(time.perf_counter() - self.t0) * 1000:9.1f} ms"

    def _change(self, reg: int, value: int, mask: int) -> None:
        self.rf.write_reg(reg, (self.rf.read_reg(reg) & ~mask & 0xFF) | (value & mask))

    def _enable_irqs(self, bits: int) -> None:
        """Unmask these interrupts (mask bit 0 = enabled)."""
        for i in range(4):
            reg = 0x16 + i
            cur = self.rf.read_reg(reg)
            self.rf.write_reg(reg, cur & ~((bits >> (8 * i)) & 0xFF) & 0xFF)

    def _disable_irqs(self, bits: int) -> None:
        for i in range(4):
            reg = 0x16 + i
            cur = self.rf.read_reg(reg)
            self.rf.write_reg(reg, cur | ((bits >> (8 * i)) & 0xFF))

    def _irq(self, bits: int) -> int:
        """Read the interrupt registers (this clears them), keep what is not asked for."""
        self._pending |= self.rf.read_irqs()
        got = self._pending & bits
        self._pending &= ~got
        return got

    # ---- set-up (start_emulation + configure_emulation_a) ---------
    def start(self) -> None:
        rf = self.rf
        rf.init()  # leaves the chip in reader mode with the field on
        rf.command(CMD_STOP)
        rf.clear_bits(REG_OP_CTRL, OP_CTRL_TX_EN)  # field off
        # configure_emulation_a
        rf.write_reg(REG_MODE, 0xC8)  # target, NFC-A, bit rate detection
        rf.write_reg(REG_PT_DEF, 0x5C)  # fdel 5, auto response for NFC-A only
        rf.write_reg(0x19, 0x02)  # mask I_wu_ax
        rf.write_reg(REG_TIMER_EMV, 0x08)  # MRT step 512/fc
        # load_config: PT memory A = UID(10, padded) ATQA(2, LE) SAK level 1..3
        sak = self.sak
        sak1 = (sak & ~0x04) if len(self.uid) == 4 else (sak | 0x04)
        pt = bytes(self.uid).ljust(10, b"\x00") + bytes([self.atqa & 0xFF, self.atqa >> 8, sak1, sak & ~0x04, sak & ~0x04])
        self._change(REG_AUX, 0x10 if len(self.uid) == 7 else 0x00, 0x30)
        rf.t.write(bytes([OP_LOAD_PT_A]) + pt)
        # start_emulation
        self._change(REG_PT_DEF, PT_D_AC_AP2P | PT_D_212_424_1R, PT_D_AC_AP2P | PT_D_212_424_1R | PT_D_106_AC_A)
        self._change(REG_TIMER_EMV, 0x00, 0xE0)
        rf.set_bits(REG_TIMER_EMV, 0x08)
        rf.write_reg(REG_MRT, 3)  # about 100 us at 512/fc steps
        rf.clear_bits(REG_ISO14443A, ISO_NO_TX_PAR | ISO_NO_RX_PAR | ISO_NFC_F0)
        rf.write_reg(REG_ANT_TUNE1, 0x00)
        rf.write_reg(REG_ANT_TUNE2, 0xFF)
        for reg in (0x30, 0x31, 0x32, 0x33):
            rf.write_space_b(reg, 0x00)  # over/undershoot protection off
        rf.command(CMD_UNMASK_RX)
        self._goto_off()

    # ---- state transitions ----------------------------------------
    def _goto_off(self) -> None:
        rf = self.rf
        self.log(f"{self._t()}  -> OFF")
        self.state = OFF
        self._wakeup = False
        self._bitrate = None
        rf.command(CMD_STOP)
        rf.set_bits(REG_OP_CTRL, OP_CTRL_RX_EN)
        rf.clear_bits(REG_PT_DEF, PT_D_106_AC_A)  # enable auto response for NFC-A
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

    def _goto_idle(self) -> None:
        rf = self.rf
        self.log(f"{self._t()}  -> IDLE")
        was_active = self.state == ACTIVE
        self.state = IDLE
        if not rf.read_reg(REG_OP_CTRL) & OP_CTRL_EN:
            rf.set_bits(REG_OP_CTRL, OP_CTRL_EN | OP_CTRL_RX_EN)
            if not rf.read_reg(REG_AUX_DISPLAY) & 0x10:  # osc_ok
                deadline = time.perf_counter() + 1.0
                ok = False
                while time.perf_counter() < deadline:
                    if self._irq(I_OSC):
                        ok = True
                        break
                if not ok:
                    self.log("oscillator not ready")
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

    def _goto_ready(self) -> None:
        rf = self.rf
        self.log(f"{self._t()}  -> READY (selected by the reader, wakeup={self._wakeup})")
        self.state = READY
        if self._irq(I_EOF):
            return self._goto_off()
        rf.clear_bits(REG_AUX, AUX_NO_CRC_RX)
        rf.write_reg(0x04, (self._bitrate or 0) << 4 | (self._bitrate or 0))
        rf.clear_bits(REG_OP_CTRL, 0x04)  # wu off
        rf.write_reg(REG_MODE, 0x88)  # target, NFC-A, fixed bit rate

    def _goto_active(self) -> None:
        self.log(f"{self._t()}  -> ACTIVE")
        self.state = ACTIVE
        self.rf.set_bits(REG_PT_DEF, PT_D_106_AC_A)  # stop auto response, we answer from now on
        self._irq(I_PAR | I_CRC | I_ERR2 | I_ERR1)
        self._enable_irqs(I_RXE)

    def _goto_halt(self) -> None:
        rf = self.rf
        self.log(f"{self._t()}  -> HALT")
        self.state = HALT
        rf.clear_bits(REG_PT_DEF, PT_D_106_AC_A)
        rf.command(CMD_GO_TO_SLEEP)
        self._change(REG_MODE, 0xC8, 0xFB)
        rf.clear_bits(REG_ISO14443A, ISO_NFC_F0)
        rf.command(CMD_UNMASK_RX)
        self._enable_irqs(I_NFCT | I_RXS | I_CRC | I_ERR1 | I_ERR2 | I_PAR | I_EON | I_EOF | MODE_IRQ)
        if not self.rf.read_reg(REG_AUX_DISPLAY) & AUX_EFD_O:
            self._goto_off()

    # ---- the polling step -----------------------------------------
    def poll(self) -> None:
        {OFF: self._p_off, IDLE: self._p_idle, READY: self._p_ready, ACTIVE: self._p_active, HALT: self._p_halt}[self.state]()

    def _read_bitrate(self) -> None:
        br = (self.rf.read_reg(REG_BITRATE_DETECT) >> 4) & 0x03
        self._bitrate = min(br, 2)
        self.log(f"{self._t()}  bit rate detected: {(106, 212, 424)[self._bitrate]} kbit/s")

    def _p_off(self) -> None:
        if self._irq(I_EON):
            self.log(f"{self._t()}  external field detected")
            self._goto_idle()

    def _p_idle(self) -> None:
        irq = self._irq(I_NFCT | I_RXE | I_EOF | I_RXE_PTA)
        if not irq:
            return
        if irq & I_NFCT:
            self._read_bitrate()
        if irq & I_EOF:
            return self._goto_off()
        if irq & I_RXE and self._bitrate is not None:
            more = self._irq(I_RXE | I_EOF | I_CRC | I_PAR | I_ERR2 | I_ERR1)
            frame = self._read_frame()
            self.log(f"{self._t()}  idle rx (anticollision frame seen by the chip): {frame.hex(' ')}  irq={irq | more:08X}")
            self.rf.command(CMD_CLEAR_FIFO)
            self.rf.command(CMD_UNMASK_RX)
            return
        if irq & I_RXE_PTA and self._bitrate == 0:
            pta = self.rf.read_reg(REG_PT_DISPLAY)
            self.log(f"{self._t()}  passive target state {pta & 0x0F:#x}")
            if (pta & 0x0F) > PTA_IDLE:
                self._goto_ready()

    def _p_ready(self) -> None:
        irq = self._irq(I_EOF | (I_WU_AX if self._wakeup else I_WU_A))
        if not irq:
            return
        if irq & I_EOF:
            return self._goto_off()
        self._goto_active()

    def _read_frame(self) -> bytes:
        s1, s2 = self.rf.read_regs(REG_FIFO_STATUS1, 2)
        n = s1 | ((s2 & 0xC0) << 2)
        return self.rf.read_fifo(n) if n else b""

    def _p_active(self) -> None:
        irq = self._irq(I_EOF | I_RXE)
        if not irq:
            return
        if irq & I_EOF:
            return self._goto_off()
        if irq & I_RXE:
            irq |= self._irq(I_PAR | I_CRC | I_ERR2 | I_ERR1)
            raw = self._read_frame()
            if irq & I_ERRORS or len(raw) <= 2:
                self.log(f"{self._t()}  rx error irq={irq:08X} raw={raw.hex(' ')}")
                self.rf.command(CMD_CLEAR_FIFO)
                self.rf.command(CMD_UNMASK_RX)
                return self._goto_halt() if self._wakeup else self._goto_idle()
            frame = raw[:-2]
            self.log(f"{self._t()}  rx  {frame.hex(' ')}")
            reply = self.on_frame(frame) if self.on_frame else None
            if reply is not None:
                self.transmit(reply)
            # HLTA ends the session: 50 00 + CRC
            if frame[:2] == b"\x50\x00":
                self._disable_irqs(I_RXE)
                self._goto_halt()

    def transmit(self, data: bytes) -> None:
        rf = self.rf
        rf.command(CMD_CLEAR_FIFO)
        rf.load_fifo(data)
        rf.write_reg(REG_NUM_TX1, (len(data) << 3) >> 8)
        rf.write_reg(REG_NUM_TX2, (len(data) << 3) & 0xFF)
        rf.command(CMD_TX_WITH_CRC)
        self.log(f"{self._t()}  tx  {data.hex(' ')}")

    def _p_halt(self) -> None:
        irq = self._irq(I_NFCT | I_RXE | I_EOF | I_RXE_PTA)
        if not irq:
            return
        if irq & I_NFCT:
            self._read_bitrate()
        if irq & I_EOF:
            return self._goto_off()
        if irq & I_RXE and self._bitrate is not None:
            self.rf.command(CMD_CLEAR_FIFO)
            self.rf.command(CMD_UNMASK_RX)
            return
        if irq & I_RXE_PTA and self._bitrate == 0:
            pta = self.rf.read_reg(REG_PT_DISPLAY)
            if (pta & 0x0F) > PTA_HALT:
                self._wakeup = True
                self._goto_ready()
