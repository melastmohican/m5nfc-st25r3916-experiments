"""NFC Forum Type 4 tag: read the NDEF message over ISO-DEP (NFC Forum Type 4 Tag spec, version 2).

`dep` is an activated `IsoDep`; its `transceive()` takes one APDU and returns the answer with the status word.
"""
from .st25r3916 import NfcProtocol, NfcRefused

NDEF_APP = bytes.fromhex("D2760000850101")  # NDEF tag application name
CC_FILE = b"\xE1\x03"  # capability container file id
SW_OK = b"\x90\x00"


def _apdu(dep, apdu: bytes, what: str) -> bytes:
    resp = dep.transceive(apdu)
    if len(resp) < 2:
        raise NfcProtocol(f"{what}: answer too short")
    if resp[-2:] != SW_OK:
        raise NfcRefused(f"{what}: status {resp[-2:].hex(' ').upper()}")
    return bytes(resp[:-2])


def read_ndef_message(dep) -> bytes:
    """SELECT the NDEF application, read the capability container, then read the NDEF file. Returns the
    NDEF message bytes (empty when the tag holds none)."""
    _apdu(dep, b"\x00\xA4\x04\x00" + bytes([len(NDEF_APP)]) + NDEF_APP + b"\x00", "select NDEF application")
    _apdu(dep, b"\x00\xA4\x00\x0C\x02" + CC_FILE, "select capability container")
    cc = _apdu(dep, b"\x00\xB0\x00\x00\x0F", "read capability container")
    # CCLEN(2) version(1) MLe(2) MLc(2), then the NDEF file control TLV: T=04 L=06 fid(2) max size(2) read write
    if len(cc) < 15 or cc[7] != 0x04 or cc[8] != 0x06:
        raise NfcProtocol("capability container has no NDEF file control TLV")
    fid, read_access = cc[9:11], cc[13]
    if read_access != 0x00:
        raise NfcRefused(f"NDEF file read access 0x{read_access:02X}, not open")
    mle = int.from_bytes(cc[3:5], "big")
    chunk = min(mle, 255) if mle else 255  # largest read the tag accepts; Le is one byte

    _apdu(dep, b"\x00\xA4\x00\x0C\x02" + fid, "select NDEF file")
    nlen_bytes = _apdu(dep, b"\x00\xB0\x00\x00\x02", "read NLEN")
    if len(nlen_bytes) != 2:
        raise NfcProtocol("NLEN is not two bytes")
    nlen = int.from_bytes(nlen_bytes, "big")
    message = b""
    while len(message) < nlen:
        offset = 2 + len(message)
        want = min(chunk, nlen - len(message))
        part = _apdu(dep, bytes([0x00, 0xB0, offset >> 8, offset & 0xFF, want]), f"read at offset {offset}")
        if not part:
            raise NfcProtocol(f"empty read at offset {offset}")
        message += part
    return message[:nlen]
