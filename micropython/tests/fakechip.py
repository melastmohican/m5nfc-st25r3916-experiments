"""Software model of an ST25R3916 (I2C frames) with an NTAG215 card behind it. Copy of ../python/tests/fakechip.py."""
from __future__ import annotations

from nfc.nfca import bcc

UID = bytes([0x04, 0xA1, 0xB2, 0xC3, 0xD4, 0xE5, 0xF6])


class FakeNtag215:
    def __init__(self) -> None:
        self.pages = [bytes(4) for _ in range(135)]
        self.pages[0] = bytes([UID[0], UID[1], UID[2], bcc(bytes([0x88]) + UID[:3])])
        self.pages[1] = UID[3:7]
        self.pages[2] = bytes([bcc(UID[3:7]), 0, 0, 0])
        self.pages[3] = bytes([0xE1, 0x10, 0x3E, 0x00])
        self.writes: list[int] = []

    def respond(self, frame: bytes) -> bytes | None:
        if frame == b"\x52" or frame == b"\x26":
            return bytes([0x44, 0x00])
        if frame == b"\x93\x20":
            cl = bytes([0x88]) + UID[:3]
            return cl + bytes([bcc(cl)])
        if frame == b"\x95\x20":
            cl = UID[3:7]
            return cl + bytes([bcc(cl)])
        if frame[:2] == b"\x93\x70":
            return b"\x04"
        if frame[:2] == b"\x95\x70":
            return b"\x00"
        if frame[0] == 0x30:
            page = frame[1]
            return b"".join(self.pages[(page + i) % 135] if (page + i) < 135 else bytes(4)
                            for i in range(4))
        if frame[0] == 0xA2:
            self.pages[frame[1]] = frame[2:6]
            self.writes.append(frame[1])
            return b"\x0a"
        return None


class FakeChip:
    """Implements the Transport protocol."""

    def __init__(self, card: FakeNtag215 | None = None) -> None:
        self.regs = bytearray(0x40)
        self.regs[0x3F] = (0x05 << 3) | 0x01
        self.space_b = bytearray(0x40)
        self.card = card
        self.fifo = bytearray()
        self.ack_bits = 4  # how many bits the WRITE ACK occupies in its FIFO byte (4 = real tag)
        self.lb = 0  # valid bits in the last FIFO byte (0 = full byte), as FIFO_STATUS2 fifo_lb
        self.log: list[tuple] = []

    # transport protocol
    def write(self, data: bytes) -> None:
        self.log.append(("w", bytes(data)))
        head = data[0]
        if head == 0xFB:
            self.space_b[data[1] & 0x3F] = data[2]
        elif head == 0x80:
            self.fifo += data[1:]
        elif head >= 0xC0:
            self._command(head)
        else:
            self.regs[head] = data[1]
            if head == 0x02 and data[1] & 0x80:  # oscillator enable
                self.regs[0x1A] |= 0x80
                self.regs[0x31] |= 0x10

    def exchange(self, out: bytes, read_len: int) -> bytes:
        self.log.append(("x", bytes(out), read_len))
        head = out[0]
        if head == 0x9F:
            data, self.fifo = self.fifo[:read_len], self.fifo[read_len:]
            return bytes(data)
        if head == 0xFB:
            return bytes([self.space_b[out[1] & 0x3F]])
        reg = head & 0x3F
        self.regs[0x1E] = len(self.fifo) & 0xFF
        self.regs[0x1F] = (len(self.fifo) >> 8 & 0x03) << 6 | self.lb << 1
        res = bytes(self.regs[reg:reg + read_len])
        if reg <= 0x1D and reg + read_len > 0x1A:  # IRQ status clears on read
            for r in range(max(reg, 0x1A), min(reg + read_len, 0x1E)):
                self.regs[r] = 0
        return res

    def _command(self, cmd: int) -> None:
        if cmd == 0xDB:
            self.fifo = bytearray()
        elif cmd in (0xC4, 0xC5, 0xC6, 0xC7):
            frame = b"\x26" if cmd == 0xC6 else b"\x52" if cmd == 0xC7 else bytes(self.fifo)
            self.fifo = bytearray()
            resp = self.card.respond(frame) if self.card else None
            if resp is None:
                self.regs[0x1B] |= 0x40  # no-response timer expired
            else:
                self.fifo += resp
                # Like real silicon: the 4-bit WRITE ACK ends in an incomplete byte and trips the
                # CRC and parity flags.
                self.lb = self.ack_bits if resp == b"\x0a" else 0
                if self.lb:
                    self.regs[0x1C] |= 0xC0
                self.regs[0x1A] |= 0x08 | 0x20 | 0x10  # TXE | RXS | RXE
