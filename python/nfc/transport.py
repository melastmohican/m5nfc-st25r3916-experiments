"""FT232H (MPSSE) I2C transport for the ST25R3916 driver.

Wiring: D0=SCL, D1+D2 tied together=SDA, GND, unit 5V from the FT232H USB pin.
"""
from __future__ import annotations

from .st25r3916 import I2C_ADDR

DEFAULT_URL = "ftdi://ftdi:232h/1"


class FtdiTransport:
    def __init__(self, url: str = DEFAULT_URL, addr: int = I2C_ADDR, freq: int = 100_000):
        from pyftdi.i2c import I2cController  # imported lazily so tests need no libusb

        self._ctrl = I2cController()
        self._ctrl.configure(url, frequency=freq)
        # The FTDI latency timer defaults to 16 ms, and every I2C operation waits for one tick.
        # At 1 ms a register read takes about 1 ms instead of 18 ms, and a full dump 0.5 s instead of 7 s.
        self._ctrl.ftdi.set_latency_timer(1)
        self._port = self._ctrl.get_port(addr)

    def write(self, data: bytes) -> None:
        self._port.write(data)

    def exchange(self, out: bytes, read_len: int) -> bytes:
        return bytes(self._port.exchange(out, read_len))

    def close(self) -> None:
        self._ctrl.terminate()
