"""PC/SC round trip against the Feather CCID reader (uv run --with pyscard python pcsc_check.py)."""
from smartcard.System import readers
from smartcard.util import toHexString

r = readers()[0]
print("reader:", r)
conn = r.createConnection()
conn.connect()
print("ATR:", toHexString(conn.getATR()))


def apdu(label, data):
    resp, sw1, sw2 = conn.transmit(data)
    print("%-14s %-14s -> %s | %02X %02X" % (label, toHexString(data), toHexString(resp), sw1, sw2))
    return resp, sw1, sw2


apdu("get UID", [0xFF, 0xCA, 0x00, 0x00, 0x00])
apdu("read pg 3", [0xFF, 0xB0, 0x00, 0x03, 0x04])
apdu("read pg 4-7", [0xFF, 0xB0, 0x00, 0x04, 0x10])
apdu("read pg 0-11", [0xFF, 0xB0, 0x00, 0x00, 0x30])
apdu("lock page w", [0xFF, 0xD6, 0x00, 0x03, 0x04, 0, 0, 0, 0])  # must be refused
