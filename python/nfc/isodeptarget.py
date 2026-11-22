"""ISO 14443-4 (ISO-DEP) block layer for card emulation: the PICC side of RATS/ATS, I-blocks,
R-blocks, S(DESELECT) and chaining of received commands. No hardware, host-tested
(tests/test_emulation.py). The application on top sees whole APDUs.

An application has apdu(cmd) -> response bytes (including SW1 SW2), reset(), and take_wtx(): after
apdu(), how many S(WTX) "wait longer" requests to send before the answer (0 = answer at once).

handle(frame) returns (reply, nibble, halt) like Type2Tag.handle.

Limits: no CID, no NAD, 106 kbit/s only, replies are never chained (an application must keep its
response within MAX_RESPONSE bytes).
"""
MAX_RESPONSE = 250  # fits one I-block of a 256-byte frame
MAX_COMMAND = 1024  # chained commands are collected up to this size

# The ATS sent in answer to RATS (TL T0 TA1 TB1 TC1): FSC 256, 106 kbit/s only, FWT about 77 ms.
ATS = b"\x05\x78\x80\x80\x00"

_NOTHING = (None, -1, False)


class IsoDepTarget:
    def __init__(self, app):
        self.app = app
        self.activated = False
        self._cmd = b""
        self._last = None  # last I-block sent, for retransmission after an R(NAK)
        self._last_bn = 0
        self._pending = None  # a delayed answer: S(WTX) requests go out first, the I-block after the last
        self._wtx_total = 0
        self._wtx_sent = 0

    def reset(self):
        self.activated = False
        self._cmd = b""
        self._last = None
        self._last_bn = 0
        self._pending = None
        self._wtx_total = self._wtx_sent = 0
        self.app.reset()

    def handle(self, f):
        n = len(f)
        if n == 0:
            return _NOTHING
        if f[0] == 0xE0:  # RATS: E0 <FSDI|CID>
            if n != 2:
                return _NOTHING
            self.reset()
            self.activated = True
            return (ATS, -1, False)
        if not self.activated:
            return _NOTHING

        pcb = f[0]
        bn = pcb & 0x01

        if pcb & 0xC2 == 0x02:  # I-block (bit 2 is always 1)
            if pcb & 0x0C:  # CID or NAD present: not supported, stay silent
                return _NOTHING
            if len(self._cmd) + n - 1 > MAX_COMMAND:
                self._cmd = b""
                return _NOTHING
            self._cmd += bytes(f[1:])
            if pcb & 0x10:  # chaining: ask for the next block
                return (bytes([0xA2 | bn]), -1, False)
            resp = self.app.apdu(self._cmd)
            self._cmd = b""
            if len(resp) > MAX_RESPONSE:
                resp = b""
            block = bytes([0x02 | bn]) + resp
            self._last = block
            self._last_bn = bn
            wtx = self.app.take_wtx()
            if wtx > 0:  # answer later: ask the reader to wait, send the I-block after its last S(WTX) response
                self._pending = block
                self._wtx_total = wtx
                self._wtx_sent = 1
                return (b"\xF2\x01", -1, False)  # S(WTX) request, WTXM = 1
            return (block, -1, False)

        if pcb & 0xC2 == 0x82:  # R-block: ACK (0xA2/0xA3) or NAK (0xB2/0xB3)
            if pcb & 0x10:  # NAK: repeat the last I-block, or acknowledge if we have none
                if self._last is not None and bn == self._last_bn:
                    return (self._last, -1, False)
                return (bytes([0xA2 | bn]), -1, False)
            if self._last is not None and bn == self._last_bn:  # ACK for a block we already sent: repeat it
                return (self._last, -1, False)
            return _NOTHING

        if pcb & 0xF7 == 0xF2 and self._pending is not None:  # S(WTX) response from the reader
            if self._wtx_sent < self._wtx_total:
                self._wtx_sent += 1
                return (b"\xF2\x01", -1, False)
            block, self._pending = self._pending, None  # the wait is over: the delayed answer
            return (block, -1, False)

        if pcb & 0xC7 == 0xC2 and pcb & 0x30 == 0x00:  # DESELECT: answer and leave the session
            self.activated = False
            self._cmd = b""
            return (b"\xC2", -1, True)
        return _NOTHING
