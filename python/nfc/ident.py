"""NTAG identification: GET_VERSION (0x60), READ_SIG (0x3C 0x00), READ_CNT (0x39 0x02).

NTAG21x datasheet 10.1, 10.8 and 10.6. Port of ../arduino/src/Ident.h and Ntag215::identCmd.
"""
from .nfca import select_card
from .st25r3916 import NfcError, NfcFrameError, NfcRefused, NfcTimeout

GET_VERSION = 0x60
READ_SIG = 0x3C
READ_CNT = 0x39

_REPLY_LEN = {GET_VERSION: 8, READ_SIG: 32, READ_CNT: 3}


def ntag_type_name(version: bytes) -> str:
    """Family name from a GET_VERSION reply (datasheet Table 28)."""
    if len(version) < 8 or version[1] != 0x04:
        return "unknown"
    if version[2] != 0x04:
        return "NXP, not an NTAG"
    return {0x0F: "NTAG213 (NXP)", 0x11: "NTAG215 (NXP)", 0x13: "NTAG216 (NXP)"}.get(
        version[6], "NTAG, unknown size (NXP)")


def counter_value(data: bytes):
    """READ_CNT returns the 24-bit counter, low byte first; None if it is not 3 bytes."""
    if len(data) != 3:
        return None
    return data[0] | data[1] << 8 | data[2] << 16


def ident(rf, which: int, addr=None) -> bytes:
    """Run one identification command. If the tag does not answer it is selected again: a tag that
    still answers refused the command (NfcRefused), otherwise the original error (no card)."""
    if which == GET_VERSION:
        tx = bytes([which])
    else:
        tx = bytes([which, addr if addr is not None else (0x00 if which == READ_SIG else 0x02)])
    want = _REPLY_LEN[which]
    try:
        resp = rf.transceive(tx, tx_crc=True, rx_crc=True, timeout_ms=5)
        if len(resp) == want:
            return resp
        err = NfcError("bad response")
    except (NfcTimeout, NfcFrameError) as exc:  # silence, or a 4-bit NAK seen as a receive error
        err = exc
    try:
        select_card(rf)
    except NfcError:
        raise err  # the card is gone
    raise NfcRefused("the tag refused the command or does not support it")
