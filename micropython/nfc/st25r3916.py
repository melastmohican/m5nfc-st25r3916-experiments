"""ST25R3916 NFC front-end driver over I2C (M5Stack NFC unit, 7-bit address 0x50).

The init sequence follows the M5Unit-NFC-Go driver and the ST25R3916 datasheet (DS12484).
Written for MicroPython: no typing or dataclasses, time.ticks_ms as the clock. It talks to a
Transport (write / exchange); MachineI2CTransport in transport.py provides one on machine.I2C.

I2C frame formats:
    register read   write [reg|0x40] then read N bytes (auto-increment)
    register write  write [reg, val]
    space-B read    write [0xFB, (reg&0x3F)|0x40] then read 1
    space-B write   write [0xFB, reg&0x3F, val]
    direct command  write [cmd]
    FIFO load       write [0x80, data...]
    FIFO read       write [0x9F] then read N bytes
"""
import time

I2C_ADDR = 0x50

OP_READ = 0x40
CMD_SPACE_B = 0xFB
OP_FIFO_LOAD = 0x80
OP_FIFO_READ = 0x9F

# Direct commands
CMD_SET_DEFAULT = 0xC1
CMD_STOP = 0xC2
CMD_TX_WITH_CRC = 0xC4
CMD_TX_WITHOUT_CRC = 0xC5
CMD_TX_REQA = 0xC6
CMD_TX_WUPA = 0xC7
CMD_INITIAL_FIELD_ON = 0xC8
CMD_RESET_RX_GAIN = 0xD5
CMD_ADJUST_REGULATORS = 0xD6
CMD_CLEAR_FIFO = 0xDB
CMD_TEST_ACCESS = 0xFC

# Registers (space A)
REG_IO_CONF1 = 0x00
REG_IO_CONF2 = 0x01
REG_OP_CTRL = 0x02
REG_MODE = 0x03
REG_BIT_RATE = 0x04
REG_ISO14443A = 0x05
REG_AUX = 0x0A
REG_RX_CONF1 = 0x0B
REG_NRT1 = 0x10
REG_NRT2 = 0x11
REG_TIMER_EMV = 0x12
REG_IRQ_MASK = 0x16  # 0x16..0x19
REG_IRQ_MAIN = 0x1A  # 0x1A..0x1D, read clears
REG_FIFO_STATUS1 = 0x1E
REG_NUM_TX1 = 0x22
REG_NUM_TX2 = 0x23
REG_AUX_DISPLAY = 0x31
REG_IC_IDENTITY = 0x3F

OP_CTRL_EN = 0x80
OP_CTRL_TX_EN = 0x08
OP_CTRL_RX_EN = 0x40
AUX_NO_CRC_RX = 0x80  # AUX (0x0A) bit 7, no_crc_rx (ST RFAL st25r3916_com.h: ST25R3916_REG_AUX_no_crc_rx = 1U<<7). transceive() strips CRC_A itself.
AUX_DISPLAY_OSC_OK = 0x10

# IRQ bits, combined as main | timer<<8 | error<<16 | target<<24
IRQ_OSC = 1 << 7
IRQ_RXS = 1 << 5
IRQ_RXE = 1 << 4
IRQ_TXE = 1 << 3
IRQ_COL = 1 << 2
IRQ_NRE = 1 << (8 + 6)
IRQ_CRC = 1 << (16 + 7)
IRQ_PAR = 1 << (16 + 6)
IRQ_ERR2 = 1 << (16 + 5)
IRQ_ERR1 = 1 << (16 + 4)
IRQ_ERRORS = IRQ_CRC | IRQ_PAR | IRQ_ERR2 | IRQ_ERR1

NRT_STEP_US = 4096 / 13.56  # 4096/fc, in microseconds
NRT_FINE_US = 64 / 13.56  # 64/fc


def _monotonic():
    return time.ticks_ms() / 1000


class NfcError(Exception):
    pass


class NfcTimeout(NfcError):
    """No response from the card (no-response timer expired)."""


class NfcFrameError(NfcError):
    """CRC / parity / framing error on receive."""


class NfcCollision(NfcError):
    pass


class St25r3916:
    def __init__(
        self,
        transport,
        sleep=time.sleep,
        monotonic=_monotonic,
    ) -> None:
        self.t = transport
        self._sleep = sleep
        self._now = monotonic
        self._rx_crc: bool | None = None
        self._nrt: int | None = None

    # ---- raw access -------------------------------------------------
    def read_regs(self, reg: int, n: int = 1) -> bytes:
        return bytes(self.t.exchange(bytes([reg | OP_READ]), n))

    def read_reg(self, reg: int) -> int:
        return self.read_regs(reg, 1)[0]

    def write_reg(self, reg: int, val: int) -> None:
        self.t.write(bytes([reg, val & 0xFF]))

    def set_bits(self, reg: int, mask: int) -> None:
        self.write_reg(reg, self.read_reg(reg) | mask)

    def clear_bits(self, reg: int, mask: int) -> None:
        self.write_reg(reg, self.read_reg(reg) & ~mask & 0xFF)

    def read_space_b(self, reg: int) -> int:
        return bytes(self.t.exchange(bytes([CMD_SPACE_B, (reg & 0x3F) | OP_READ]), 1))[0]

    def write_space_b(self, reg: int, val: int) -> None:
        self.t.write(bytes([CMD_SPACE_B, reg & 0x3F, val & 0xFF]))

    def command(self, cmd: int, payload: bytes = b"", delay: float = 0.0) -> None:
        self.t.write(bytes([cmd]) + payload)
        if delay:
            self._sleep(delay)

    def load_fifo(self, data: bytes) -> None:
        self.t.write(bytes([OP_FIFO_LOAD]) + data)

    def read_fifo(self, n: int) -> bytes:
        out = b""
        while n > 0:
            chunk = min(n, 64)
            out += bytes(self.t.exchange(bytes([OP_FIFO_READ]), chunk))
            n -= chunk
        return out

    def fifo_count(self) -> int:
        s1, s2 = self.read_regs(REG_FIFO_STATUS1, 2)
        return s1 | ((s2 & 0xC0) << 2)

    def read_irqs(self) -> int:
        m, t, e, g = self.read_regs(REG_IRQ_MAIN, 4)
        return m | (t << 8) | (e << 16) | (g << 24)

    def _write_mask(self, value: int) -> None:
        for i in range(4):
            self.write_reg(REG_IRQ_MASK + i, (value >> (24 - 8 * i)) & 0xFF)

    # ---- bring-up ---------------------------------------------------
    def identify(self) -> tuple[int, int]:
        """Return (ic_type, revision). Raises if not an ST25R3916."""
        raw = self.read_reg(REG_IC_IDENTITY)
        if raw == 0xFF or raw == 0x00:
            raise NfcError(f"no ST25R3916 on bus (identity=0x{raw:02X})")
        ic_type, rev = (raw >> 3) & 0x1F, raw & 0x07
        if ic_type != 0x05 or rev == 0:
            raise NfcError(f"unexpected IC identity 0x{raw:02X}")
        return ic_type, rev

    def init(self) -> None:
        """Reset and configure the chip, then set NFC-A reader mode."""
        self.identify()
        self.command(CMD_SET_DEFAULT, delay=0.010)
        self.command(CMD_TEST_ACCESS, bytes([0x04, 0x10]))
        # Init sequence follows the M5Unit-NFC-Go driver.
        self.write_reg(0x00, 0x10)
        self.write_reg(0x01, 0x84)
        self.write_reg(0x28, 0xD0)
        self.write_reg(0x00, (self.read_reg(0x00) & ~0x07 & 0xFF) | 0x07)
        self.write_space_b(0x2A, 0x80)
        self.set_bits(0x01, 0x20)
        self.write_space_b(0x2A, 0x00)
        self.write_reg(0x2A, 0x13)
        self.write_reg(0x2B, 0x02)
        self.write_reg(0x08, (self.read_reg(0x08) & 0x0F) | 0x50)
        self.write_reg(0x29, 0x5F)
        self.write_space_b(0x05, 0x40)
        self.write_reg(0x26, 0x82)
        self.write_reg(0x27, 0x82)
        self.set_bits(0x02, 0x03)
        self.command(CMD_CLEAR_FIFO)
        self._write_mask(0xFFFF00FF)
        self.read_irqs()
        self._enable_oscillator()
        self._write_mask(0x00000000)
        self.command(CMD_ADJUST_REGULATORS, delay=0.005)
        self.configure_nfca()

    def _enable_oscillator(self) -> None:
        if self.read_reg(REG_OP_CTRL) & OP_CTRL_EN:
            return
        self.read_irqs()
        self.set_bits(REG_OP_CTRL, OP_CTRL_EN)
        deadline = self._now() + 0.025
        seen = False
        while self._now() < deadline:
            if self.read_reg(REG_IRQ_MAIN) & IRQ_OSC:
                seen = True
                break
            self._sleep(0.001)
        if not seen:
            raise NfcError("oscillator did not start (is the unit powered with 5V?)")
        if not self.read_reg(REG_AUX_DISPLAY) & AUX_DISPLAY_OSC_OK:
            raise NfcError("oscillator not stable")

    def configure_nfca(self) -> None:
        self.command(CMD_STOP)
        self.clear_bits(REG_OP_CTRL, OP_CTRL_TX_EN | OP_CTRL_RX_EN)
        self.write_reg(REG_MODE, 0x09)
        self.write_reg(REG_BIT_RATE, 0x00)
        self.write_reg(REG_ISO14443A, 0x00)
        self.clear_bits(REG_AUX, 0x04)
        for reg, val in ((0x30, 0x40), (0x31, 0x03), (0x32, 0x40), (0x33, 0x03),
                         (0x0C, 0x47), (0x0D, 0x00)):
            self.write_space_b(reg, val)
        for reg, val in ((0x0B, 0x08), (0x0C, 0x2D), (0x0D, 0xD8), (0x0E, 0x22)):
            self.write_reg(reg, val)
        self._write_mask(0)
        self.command(CMD_RESET_RX_GAIN)
        self._rx_crc = None
        self._nrt = None
        self.field_on()

    def field_on(self) -> None:
        self.command(CMD_INITIAL_FIELD_ON, delay=0.005)
        self.set_bits(REG_OP_CTRL, OP_CTRL_TX_EN | OP_CTRL_RX_EN)

    def field_off(self) -> None:
        self.command(CMD_STOP, delay=0.005)
        self.clear_bits(REG_OP_CTRL, OP_CTRL_TX_EN | OP_CTRL_RX_EN)

    def field_reset(self, off_time: float = 0.020) -> None:
        """Power-cycle the RF field so cards in ACTIVE/HALT state restart."""
        self.field_off()
        self._sleep(off_time)
        self.field_on()
        self._sleep(0.010)

    # ---- transceive -------------------------------------------------
    def _set_rx_crc(self, check: bool) -> None:
        if self._rx_crc is check:
            return
        if check:
            self.clear_bits(REG_AUX, AUX_NO_CRC_RX)
        else:
            self.set_bits(REG_AUX, AUX_NO_CRC_RX)
        self._rx_crc = check

    def _set_nrt(self, ms: float) -> None:
        us = ms * 1000.0
        if us / NRT_FINE_US <= 0xFFFF:
            steps, coarse = max(1, round(us / NRT_FINE_US)), False
        else:
            steps, coarse = min(0xFFFF, round(us / NRT_STEP_US)), True
        key = (steps << 1) | int(coarse)
        if self._nrt == key:
            return
        timer = self.read_reg(REG_TIMER_EMV)
        self.write_reg(REG_TIMER_EMV, (timer | 0x01) if coarse else (timer & ~0x01 & 0xFF))
        self.write_reg(REG_NRT1, steps >> 8)
        self.write_reg(REG_NRT2, steps & 0xFF)
        self._nrt = key

    def _prepare(self, rx_crc: bool, timeout_ms: float) -> None:
        self.command(CMD_CLEAR_FIFO)
        self.read_irqs()  # clear stale flags
        self._set_rx_crc(rx_crc)
        self._set_nrt(timeout_ms)

    def _collect(self, timeout_ms: float, ignore: int = 0, short_rx: bool = False) -> bytes:
        deadline = self._now() + timeout_ms / 1000.0 + 0.05
        irq = 0
        while True:
            irq |= self.read_irqs()
            if irq & IRQ_RXE:
                break
            if irq & IRQ_NRE and not irq & IRQ_RXS:
                raise NfcTimeout("no response from card")
            if self._now() > deadline:
                raise NfcTimeout("host timeout waiting for card")
        irq |= self.read_irqs()  # late error flags
        if irq & IRQ_COL:
            raise NfcCollision("collision")
        if irq & IRQ_ERRORS & ~ignore:
            raise NfcFrameError(f"receive error (irq=0x{irq:08X})")
        s1, s2 = self.read_regs(REG_FIFO_STATUS1, 2)
        n = s1 | ((s2 & 0xC0) << 2)
        if short_rx and (n != 1 or (s2 >> 1) & 0x07 == 0):
            # Only a one-byte frame ending in an incomplete byte counts as a 4-bit ACK/NAK, the
            # rule ST's RFAL applies (rfalErrorHandling).
            raise NfcFrameError("not a 4-bit frame")
        return self.read_fifo(n)

    def send_short(self, cmd: int, timeout_ms: float = 5.0) -> bytes:
        """REQA (CMD_TX_REQA) or WUPA (CMD_TX_WUPA); returns the 2-byte ATQA."""
        self._prepare(rx_crc=False, timeout_ms=timeout_ms)
        self.command(cmd)
        return self._collect(timeout_ms)

    def transceive(
        self,
        data: bytes,
        *,
        tx_crc: bool = True,
        rx_crc: bool = True,
        timeout_ms: float = 10.0,
        short_rx: bool = False,
    ) -> bytes:
        """Send whole bytes and return the card's response.

        tx_crc: chip appends CRC_A. rx_crc: chip checks and strips CRC_A.
        """
        self._prepare(rx_crc, timeout_ms)
        self.write_reg(REG_NUM_TX1, (len(data) << 3) >> 8)
        self.write_reg(REG_NUM_TX2, (len(data) << 3) & 0xFF)
        self.load_fifo(data)
        self.command(CMD_TX_WITH_CRC if tx_crc else CMD_TX_WITHOUT_CRC)
        # short_rx: the 4-bit ACK trips the CRC/parity flags. Ignore them, but accept only a
        # one-byte frame ending in an incomplete byte (ST RFAL's rule).
        resp = self._collect(timeout_ms, IRQ_CRC | IRQ_PAR if short_rx else 0, short_rx)
        if rx_crc and len(resp) >= 3:
            # Real silicon leaves the (already checked) CRC_A in the FIFO.
            from .nfca import crc_a

            if resp[-2:] == crc_a(resp[:-2]):
                resp = resp[:-2]
        return resp
