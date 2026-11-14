"""NDEF write/read round trip through PC/SC (FF D6 / FF B0), then restore the tag.

    uv run --with pyscard python pcsc_ndef_roundtrip.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from nfc import ndef  # noqa: E402
from smartcard.System import readers  # noqa: E402

FIRST_PAGE, N_PAGES = 4, 16  # 64 bytes of user memory: plenty for the test message

c = readers()[0].createConnection()
c.connect()


def read(page, n):
    data, sw1, sw2 = c.transmit([0xFF, 0xB0, 0x00, page, n])
    assert (sw1, sw2) == (0x90, 0x00), "read p%d SW %02X%02X" % (page, sw1, sw2)
    return bytes(data)


def write_pages(first, blob):
    for i in range(0, len(blob), 4):
        _, sw1, sw2 = c.transmit([0xFF, 0xD6, 0x00, first + i // 4, 0x04] + list(blob[i:i + 4]))
        assert (sw1, sw2) == (0x90, 0x00), "write p%d SW %02X%02X" % (first + i // 4, sw1, sw2)


original = read(FIRST_PAGE, N_PAGES * 4)
print("original:", [r.describe() for r in ndef.decode_message(ndef.find_message(original) or b"")])

try:
    records = [ndef.text_record("via PC/SC"), ndef.uri_record("https://example.org/pcsc")]
    blob = ndef.wrap_tlv(ndef.encode_message(records))
    blob += b"\x00" * (-len(blob) % 4)
    write_pages(FIRST_PAGE, blob)

    back = read(FIRST_PAGE, N_PAGES * 4)
    found = ndef.decode_message(ndef.find_message(back) or b"")
    for rec in found:
        print("read back:", rec.describe())
    ok = back[:len(blob)] == blob and [r.describe() for r in found] == [r.describe() for r in records]
    print("ROUND TRIP", "OK" if ok else "FAILED")
finally:
    write_pages(FIRST_PAGE, original)
    restored = read(FIRST_PAGE, N_PAGES * 4)
    print("restored original:", restored == original)
