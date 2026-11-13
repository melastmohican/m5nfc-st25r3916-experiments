"""Step 1 check: does the FT232H see the NFC unit on I2C (expect 0x50)?

Wiring: D0=SCL, D1+D2 tied together=SDA, GND, unit 5V from FT232H USB pin.
Run: uv run python -m nfc.scan
"""
from pyftdi.i2c import I2cController

URL = "ftdi://ftdi:232h/1"


def main() -> None:
    i2c = I2cController()
    i2c.configure(URL, frequency=100_000)
    found = []
    for addr in range(0x08, 0x78):
        try:
            i2c.get_port(addr).read(1)
            found.append(addr)
        except Exception:
            pass
    i2c.terminate()
    print("I2C devices:", [hex(a) for a in found] or "none")
    print("OK: NFC unit at 0x50" if 0x50 in found else "NOT FOUND: check wiring/5V")


if __name__ == "__main__":
    main()
