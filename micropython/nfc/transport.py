"""machine.I2C transport for the ST25R3916 driver on a Feather RP2350.

Wiring: Qwiic/STEMMA QT port = I2C1, SDA=GP2, SCL=GP3 (3.3V). The unit's I2C
pull-ups are on its own 3.3V rail, so no level shifter is needed.
"""
from machine import I2C, Pin

I2C_ADDR = 0x50


class MachineI2CTransport:
    def __init__(self, i2c=None, addr=I2C_ADDR):
        self._i2c = i2c or I2C(1, sda=Pin(2), scl=Pin(3), freq=400_000)
        self._addr = addr

    def write(self, data):
        self._i2c.writeto(self._addr, data)

    def exchange(self, out, read_len):
        # repeated start between the write and the read, as pyftdi's exchange does
        self._i2c.writeto(self._addr, out, False)
        return self._i2c.readfrom(self._addr, read_len)
