"""ISO14443-A activation (REQA/WUPA, anticollision, select) and Type 2 commands."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .st25r3916 import CMD_TX_WUPA, NfcError, NfcNak, NfcTimeout

CT = 0x88  # cascade tag
T2_READ = 0x30
T2_WRITE = 0xA2
T2_ACK = 0x0A


class Reader(Protocol):
    def send_short(self, cmd: int, timeout_ms: float = ...) -> bytes: ...

    def transceive(self, data: bytes, *, tx_crc: bool = ..., rx_crc: bool = ...,
                   timeout_ms: float = ..., short_rx: bool = ...) -> bytes: ...


def crc_a(data: bytes) -> bytes:
    """ISO14443-A CRC, returned low byte first."""
    crc = 0x6363
    for byte in data:
        ch = byte ^ (crc & 0xFF)
        ch ^= (ch << 4) & 0xFF
        crc = (crc >> 8) ^ (ch << 8) ^ (ch << 3) ^ (ch >> 4)
        crc &= 0xFFFF
    return bytes([crc & 0xFF, crc >> 8])


def bcc(data: bytes) -> int:
    out = 0
    for b in data:
        out ^= b
    return out


@dataclass(frozen=True)
class Card:
    uid: bytes
    atqa: bytes
    sak: int

    @property
    def uid_hex(self) -> str:
        return ":".join(f"{b:02X}" for b in self.uid)


def _anticollision(rf: Reader, sel: int) -> bytes:
    resp = rf.transceive(bytes([sel, 0x20]), tx_crc=False, rx_crc=False)
    if len(resp) != 5 or bcc(resp[:4]) != resp[4]:
        raise NfcError(f"bad anticollision response: {resp.hex()}")
    return resp


def _select(rf: Reader, sel: int, uid_cl: bytes) -> int:
    resp = rf.transceive(bytes([sel, 0x70]) + uid_cl, tx_crc=True, rx_crc=True)
    if len(resp) != 1:
        raise NfcError(f"bad SAK response: {resp.hex()}")
    return resp[0]


def select_card(rf: Reader) -> Card:
    """WUPA + cascade-level anticollision/select. Supports 4- and 7-byte UIDs.

    A tag left in ACTIVE state by an earlier select ignores WUPA until its
    field is power-cycled, so on a timeout reset the field once and retry.
    """
    try:
        atqa = rf.send_short(CMD_TX_WUPA)
    except NfcTimeout:
        reset = getattr(rf, "field_reset", None)
        if reset is None:
            raise
        reset()
        atqa = rf.send_short(CMD_TX_WUPA)
    if len(atqa) != 2:
        raise NfcError(f"bad ATQA: {atqa.hex()}")
    uid = b""
    for sel in (0x93, 0x95, 0x97):
        cl = _anticollision(rf, sel)
        sak = _select(rf, sel, cl)
        if cl[0] == CT and sak & 0x04:
            uid += cl[1:4]
            continue
        return Card(uid + cl[:4], atqa, sak)
    raise NfcError("UID longer than 10 bytes not supported")


def t2_read(rf: Reader, page: int) -> bytes:
    """READ returns 4 pages (16 bytes) starting at `page`."""
    resp = rf.transceive(bytes([T2_READ, page]), tx_crc=True, rx_crc=True)
    if len(resp) != 16:
        raise NfcError(f"READ page {page}: expected 16 bytes, got {resp.hex() or 'nothing'}")
    return resp


NAK_TEXT = {
    0x0: "tag refused the write: invalid argument (bad page address)",
    0x1: "tag refused the write: parity or CRC error in the command",
    0x4: "tag refused the write: authentication counter overflow",
    0x5: "tag refused the write: EEPROM write error",
}


def nak_text(code: int) -> str:
    """Meaning of the 4-bit NAK a tag sends after a refused WRITE (NTAG21x datasheet Table 23)."""
    return NAK_TEXT.get(code, "tag refused the write: unknown NAK code")


def t2_write(rf: Reader, page: int, data: bytes) -> None:
    if len(data) != 4:
        raise ValueError("a page is 4 bytes")
    # The ACK is a 4-bit frame without CRC, so receive-side CRC checking is off.
    resp = rf.transceive(bytes([T2_WRITE, page]) + data, tx_crc=True, rx_crc=False,
                         short_rx=True)
    if not resp:
        raise NfcError(f"WRITE page {page}: no ACK (got nothing)")
    code = resp[0] & 0x0F
    if code != T2_ACK:
        raise NfcNak(code, nak_text(code))
