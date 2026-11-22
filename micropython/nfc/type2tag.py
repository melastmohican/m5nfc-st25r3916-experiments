"""An NTAG215-like NFC Forum Type 2 tag in RAM, for card emulation (see listener.py).

handle(frame) takes one command frame (CRC already removed) and returns (reply, nibble, halt):
reply is the bytes to send (the chip adds the CRC) or None, nibble is a 4-bit frame to send instead
(ACK = 0xA, NAK = 0x0) or -1, and halt says to go to the halt state after sending.

    READ 30 pp         16 bytes (wraps past the last page, as a real tag does)
    FAST_READ 3A s e   pages s..e
    WRITE A2 pp d0..3  ACK; pages 0-2 (UID, lock) are refused with a NAK
    GET_VERSION 60     NTAG215: 00 04 04 02 01 00 11 03
    READ_SIG 3C 00     32 zero bytes (an emulated tag has no signature)
    HLTA 50 00         no reply, the tag goes to the halt state
    anything else      NAK

No hardware access, so it runs on the Mac (tests/test_emulation.py).
"""
PAGES = 135  # NTAG215
BYTES = PAGES * 4

_NOTHING = (None, -1, False)
_NAK0 = (None, 0x0, False)
_NAK1 = (None, 0x1, False)
_ACK = (None, 0xA, False)
_HALT = (None, -1, True)
_VERSION = b"\x00\x04\x04\x02\x01\x00\x11\x03"
_SIG = bytes(32)


class Type2Tag:
    def __init__(self, uid):
        mem = self.mem = bytearray(BYTES)
        mem[0:3] = uid[0:3]
        mem[3] = 0x88 ^ uid[0] ^ uid[1] ^ uid[2]  # BCC0 (cascade tag 0x88)
        mem[4:8] = uid[3:7]
        mem[8] = uid[3] ^ uid[4] ^ uid[5] ^ uid[6]  # BCC1
        mem[12:16] = b"\xE1\x10\x3E\x00"  # capability container: NDEF, version 1.0, 496 bytes
        # Configuration pages of a blank NTAG215: no protection (AUTH0 = FF), default password.
        mem[130 * 4 + 3] = 0xBD  # dynamic lock bytes
        mem[131 * 4 + 3] = 0xFF  # AUTH0: no page needs the password
        mem[132 * 4 + 1] = 0x05  # ACCESS / VCTID
        mem[133 * 4:133 * 4 + 4] = b"\xFF\xFF\xFF\xFF"  # PWD
        mem[16:19] = b"\x03\x00\xFE"  # empty NDEF TLV
        self.changed = bytearray((PAGES + 7) // 8)
        self.writes = 0

    def memory(self):
        return self.mem

    def is_changed(self, page):
        return 0 <= page < PAGES and (self.changed[page >> 3] >> (page & 7)) & 1 == 1

    def clear_changed(self):
        for i in range(len(self.changed)):
            self.changed[i] = 0

    def reset(self):
        pass

    def handle(self, f):
        n = len(f)
        if n == 0:
            return _NOTHING
        c = f[0]
        mem = self.mem
        if c == 0x30:  # READ
            if n != 2 or f[1] >= PAGES:
                return _NAK0
            p = f[1] * 4
            if p + 16 <= BYTES:
                return (bytes(mem[p:p + 16]), -1, False)
            return (bytes(mem[(p + i) % BYTES] for i in range(16)), -1, False)
        if c == 0x3A:  # FAST_READ
            if n != 3 or f[1] > f[2] or f[2] >= PAGES:
                return _NAK0
            return (bytes(mem[f[1] * 4:(f[2] + 1) * 4]), -1, False)
        if c == 0xA2:  # WRITE
            if n != 6:
                return _NAK1
            p = f[1]
            if p < 3 or p >= PAGES:
                return _NAK0
            if mem[p * 4:p * 4 + 4] != f[2:6]:
                mem[p * 4:p * 4 + 4] = f[2:6]
                self.changed[p >> 3] |= 1 << (p & 7)
            self.writes += 1
            return _ACK
        if c == 0x60:  # GET_VERSION (NTAG215)
            return (_VERSION, -1, False)
        if c == 0x3C:  # READ_SIG
            return (_SIG, -1, False)
        if c == 0x50:  # HLTA
            return _HALT if n == 2 and f[1] == 0x00 else _NAK0
        return _NAK0
