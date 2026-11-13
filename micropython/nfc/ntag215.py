"""NTAG215 memory model: 135 pages x 4 bytes, user memory = pages 4-129 (504 bytes)."""
from . import ndef
from .nfca import t2_read, t2_write
from .st25r3916 import NfcError

PAGE_SIZE = 4
TOTAL_PAGES = 135
USER_FIRST, USER_LAST = 4, 129
USER_BYTES = (USER_LAST - USER_FIRST + 1) * PAGE_SIZE  # 504
PAGE_CC = 3
CC_NTAG215 = bytes([0xE1, 0x10, 0x3E, 0x00])
PAGE_PWD, PAGE_PACK = 133, 134
READ_PROTECTED = {PAGE_PWD, PAGE_PACK}  # always read back as zeros


class Ntag215:
    def __init__(self, rf: Reader) -> None:
        self.rf = rf

    def read_page(self, page: int) -> bytes:
        return t2_read(self.rf, page)[:PAGE_SIZE]

    def dump(self) -> bytes:
        """All 135 pages (540 bytes). PWD/PACK pages read as zeros."""
        out = b""
        for page in range(0, 132, 4):
            out += t2_read(self.rf, page)
        out += t2_read(self.rf, 131)[PAGE_SIZE:]  # pages 132-134
        return out[: TOTAL_PAGES * PAGE_SIZE]

    def is_ntag215(self) -> bool:
        return self.read_page(PAGE_CC) == CC_NTAG215

    def write_page(self, page: int, data: bytes, *, force: bool = False, verify: bool = True) -> None:
        if page < 2 or page >= TOTAL_PAGES:
            raise NfcError(f"page {page} is read-only or out of range")
        if not USER_FIRST <= page <= USER_LAST and not force:
            raise NfcError(
                f"page {page} is lock/config/CC: bits are one-way. Pass --force if you mean it."
            )
        t2_write(self.rf, page, data)
        if verify and page not in READ_PROTECTED and page not in (2, 130):
            got = self.read_page(page)
            if got != data:
                raise NfcError(f"verify failed on page {page}: wrote {data.hex()} read {got.hex()}")

    # ---- NDEF --------------------------------------------------------
    def read_user_memory(self) -> bytes:
        out = b""
        for page in range(USER_FIRST, USER_LAST + 1, 4):
            out += t2_read(self.rf, page)
        return out[:USER_BYTES]

    def read_ndef(self) -> list[ndef.NdefRecord]:
        msg = ndef.find_message(self.read_user_memory())
        return ndef.decode_message(msg) if msg else []

    def write_ndef(self, records: list[ndef.NdefRecord]) -> int:
        """Write an NDEF message; returns the number of pages actually written."""
        if self.read_page(PAGE_CC)[0] != 0xE1:
            raise NfcError(
                "capability container missing; initialise with: write-page 3 E1103E00 --force"
            )
        blob = ndef.wrap_tlv(ndef.encode_message(records))
        if len(blob) > USER_BYTES:
            raise NfcError(f"message is {len(blob)} bytes, tag holds {USER_BYTES}")
        blob += b"\x00" * (-len(blob) % PAGE_SIZE)
        current = self.read_user_memory()
        written = 0
        for i in range(0, len(blob), PAGE_SIZE):
            chunk = blob[i:i + PAGE_SIZE]
            if current[i:i + PAGE_SIZE] != chunk:  # skip identical pages to save wear
                self.write_page(USER_FIRST + i // PAGE_SIZE, chunk)
                written += 1
        return written
