"""ISO-DEP (ISO/IEC 14443-4) block protocol for the reader side: RATS/ATS, I-block framing with the
block-number toggle, chaining in both directions, waiting-time extension, retries and deselect.

A port of ../arduino/src/IsoDep.cpp, which is a port of the ISO-DEP layer in embassy-rs/rnfc (MIT or
Apache-2.0); the tests replay the same ISO 14443-4 Annex B scenarios. The frames go through a link
object, so the protocol runs on the Mac against a script without a chip.

A link has transceive(tx, timeout_1fc) -> bytes (CRC_A added and checked by the chip; timeouts in
carrier clocks, 1/13.56 MHz) and sleep_ms(ms). It raises NfcTimeout, NfcFrameError or NfcCollision
for a lost or damaged frame (the protocol retries those); any other error is fatal.
"""
from .st25r3916 import NfcCollision, NfcError, NfcFrameError, NfcProtocol, NfcTimeout

_RATS_TIMEOUT_1FC = 65536
_FSC_MAX = 256
_TX_FRAME_MAX = 64  # the chip loads at most 64 bytes into its FIFO for a transmit
_MAX_RETRIES = 10
_RATS_RETRIES = 4
_MAX_RESPONSE = 4096
_ATS_MAX = 32
_FS_TABLE = (16, 24, 32, 40, 48, 64, 96, 128, 256)
_LOST = (NfcTimeout, NfcFrameError, NfcCollision)

_DATA, _ACK, _NAK, _WTX = 0, 1, 2, 3


class RfIsoDepLink:
    """A link on the ST25R3916 driver."""

    def __init__(self, rf):
        self.rf = rf

    def transceive(self, tx, timeout_1fc):
        ms = max(1, (timeout_1fc + 13559) // 13560)  # carrier clocks to milliseconds, rounded up
        return self.rf.transceive(tx, tx_crc=True, rx_crc=True, timeout_ms=ms)

    def sleep_ms(self, ms):
        self.rf._sleep(ms / 1000)


class IsoDep:
    def __init__(self, link):
        self.link = link
        self.ats = b""
        self.fsc = 32  # largest frame the card accepts, including header and CRC
        self.sfgt_1fc = 4096  # start-up frame guard time
        self.fwt_1fc = 65536  # frame waiting time
        self.block_num = 0

    def activate(self):
        """Send RATS (E0 80) and read the ATS. The card must already be selected."""
        tries = 0
        while True:
            tries += 1
            try:
                res = self.link.transceive(b"\xe0\x80", _RATS_TIMEOUT_1FC)  # FSDI = 8 (256 bytes), CID = 0
                break
            except _LOST:
                if tries >= _RATS_RETRIES:
                    raise NfcTimeout("no ATS from the card")
        res = bytes(res[:_ATS_MAX])
        self.ats = res
        fsci, sfgi, fwi = 2, 0, 4
        if len(res) >= 2:
            t0 = res[1]
            fsci = t0 & 0x0F
            if t0 & 0x20:  # TB(1) present: SFGI and FWI
                tb = 3 if t0 & 0x10 else 2
                if tb < len(res):
                    sfgi = res[tb] & 0x0F
                    fwi = res[tb] >> 4
        if fsci >= len(_FS_TABLE):
            raise NfcProtocol("bad FSCI in the ATS")
        if fwi > 14:  # 15 is reserved
            fwi = 4
        if sfgi > 14:
            sfgi = 0
        self.fsc = _FS_TABLE[fsci]
        self.sfgt_1fc = (256 * 16) << sfgi  # SFGT = 256 x 16 / fc x 2^SFGI
        self.fwt_1fc = (256 * 16) << fwi  # FWT  = 256 x 16 / fc x 2^FWI
        self.block_num = 0
        self.link.sleep_ms((self.sfgt_1fc + 13559) // 13560 + 1)  # the card needs SFGT before the next frame

    def deselect(self):
        """S(DESELECT): the card goes back to idle."""
        if self.link.transceive(b"\xc2", self.fwt_1fc) != b"\xc2":
            raise NfcProtocol("bad answer to DESELECT")

    def transceive(self, tx):
        """One command/response exchange of any length; handles chaining and waiting-time extension."""
        tx = bytes(tx)
        # INF bytes per I-block: FSC less PCB and CRC, and no more than the chip can transmit.
        max_n = min(self.fsc - 3, _TX_FRAME_MAX - 1)
        if max_n <= 0:
            raise NfcProtocol("frame size too small")
        mode = _DATA
        wtx_mul = 0
        out = b""
        rx_chaining = False
        retries = 0
        while True:
            fwt = self.fwt_1fc
            if mode == _DATA:
                n = min(len(tx), max_n)
                pcb = 0x02 | self.block_num | (0x10 if n != len(tx) else 0)  # 0x10: more blocks follow
                frame = bytes([pcb]) + tx[:n]
            elif mode == _WTX:
                fwt *= wtx_mul
                frame = bytes([0xF2, wtx_mul])
            elif mode == _ACK:
                frame = bytes([0xA2 | self.block_num])
            else:
                frame = bytes([0xB2 | self.block_num])

            try:
                rx = self.link.transceive(frame, fwt)
            except _LOST:
                retries += 1
                if retries >= _MAX_RETRIES:
                    raise NfcTimeout("the card stopped answering")
                mode = _ACK if rx_chaining else _NAK
                continue
            if len(rx) == 0:
                raise NfcProtocol("empty frame")
            retries = 0

            pcb = rx[0]
            if pcb in (0x02, 0x03, 0x12, 0x13):  # I-block
                if len(out) + len(rx) - 1 > _MAX_RESPONSE:
                    raise NfcError("response too big")
                out += rx[1:]
                self.block_num ^= 1
                if not pcb & 0x10:  # last block of the response
                    return out
                rx_chaining = True
                mode = _ACK
            elif pcb in (0xA2, 0xA3):  # R(ACK) for our chained block
                if pcb & 1 == self.block_num:
                    if len(tx) <= max_n:
                        raise NfcProtocol("ACK on the last block of a chain")
                    tx = tx[max_n:]
                    self.block_num ^= 1
                mode = _DATA
            elif pcb == 0xF2:  # S(WTX): the card asks for more time
                if len(rx) != 2:
                    raise NfcProtocol("bad S(WTX) length")
                wtx_mul = rx[1] & 0x3F
                mode = _WTX
            else:
                raise NfcProtocol("unknown block 0x%02X" % pcb)


def desfire_command(dep, ins):
    """Run one native DESFire command wrapped in an ISO 7816 APDU (90 INS 00 00 00), following
    "additional frame" replies (SW 91 AF, sent again as 90 AF 00 00 00). Returns (data, sw1, sw2)."""
    data = b""
    for _ in range(32):
        resp = dep.transceive(bytes([0x90, ins, 0x00, 0x00, 0x00]))
        if len(resp) < 2:
            raise NfcProtocol("short DESFire reply")
        data += resp[:-2]
        sw1, sw2 = resp[-2], resp[-1]
        if (sw1, sw2) != (0x91, 0xAF):
            return data, sw1, sw2
        ins = 0xAF
    raise NfcProtocol("the card never stopped asking for more")
