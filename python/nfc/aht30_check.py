"""Sanity check of the FT232H I2C path using an AHT30 (addr 0x38).

Run: uv run python -m nfc.aht30_check
"""
import time

from pyftdi.i2c import I2cController

URL = "ftdi://ftdi:232h/1"
ADDR = 0x38


def crc8(data: bytes) -> int:
    crc = 0xFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = ((crc << 1) ^ 0x31) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def main() -> None:
    i2c = I2cController()
    i2c.configure(URL, frequency=100_000)
    dev = i2c.get_port(ADDR)

    time.sleep(0.1)
    status = dev.read(1)[0]
    print(f"status: 0x{status:02X} calibrated={bool(status & 0x08)}")
    if not status & 0x08:
        dev.write(b"\xBE\x08\x00")
        time.sleep(0.01)

    dev.write(b"\xAC\x33\x00")  # trigger measurement
    time.sleep(0.08)
    for _ in range(10):
        data = bytes(dev.read(7))
        if not data[0] & 0x80:  # busy bit clear
            break
        time.sleep(0.01)
    else:
        raise SystemExit("sensor stayed busy")

    raw_h = (data[1] << 12) | (data[2] << 4) | (data[3] >> 4)
    raw_t = ((data[3] & 0x0F) << 16) | (data[4] << 8) | data[5]
    hum = raw_h / 2**20 * 100
    temp = raw_t / 2**20 * 200 - 50
    crc_ok = crc8(data[:6]) == data[6]
    print(f"raw: {data.hex()}  crc_ok={crc_ok}")
    print(f"temp: {temp:.1f} C ({temp * 9 / 5 + 32:.1f} F)  humidity: {hum:.1f} %")
    i2c.terminate()


if __name__ == "__main__":
    main()
