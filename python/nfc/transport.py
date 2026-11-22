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
        self._addr = addr
        self._port = self._ctrl.get_port(addr)

    def write(self, data: bytes) -> None:
        self._port.write(data)

    def exchange(self, out: bytes, read_len: int) -> bytes:
        return bytes(self._port.exchange(out, read_len))

    def batch(self, ops: list[tuple]) -> list[bytes | None]:
        """Run several I2C transfers as ONE USB write and ONE read.

        ops: ("w", bytes) or ("x", out_bytes, read_len), one I2C transaction each (START, address, data,
        STOP). Returns one entry per op: None for a write, the bytes read for an exchange.

        pyftdi's own write() and exchange() do one USB round trip per byte, about 1 ms per operation,
        which is too slow for tag emulation (a phone gives a tag about 5 ms to answer). This builds the
        same MPSSE command stream pyftdi would send, but sends it in one piece and checks the slave's
        acknowledgements afterwards: a NACK anywhere raises after the whole batch ran. The bus must run
        at 400 kHz: at 100 kHz the bytes alone take too long (see tools/batch_timing.py).

        It reaches into pyftdi's private I2cController helpers (the stream builders and `_lock`); the
        version is pinned in uv.lock (0.57.2) and tests/test_batch.py checks the command shape.
        """
        ctrl = self._ctrl
        fake = ctrl._fake_tristate
        cmd = bytearray()
        expect: list[tuple[str, int]] = []  # one entry per MPSSE reply byte: ("ack" | "data", op index)

        def ack(i: int) -> None:
            cmd.extend(ctrl._clk_lo_data_input if fake else ctrl._clk_lo_data_hi)
            cmd.extend(ctrl._read_bit)
            expect.append(("ack", i))

        def prolog(a: int, i: int) -> None:
            cmd.extend(ctrl._idle * ctrl._ck_delay)
            cmd.extend(ctrl._start)
            cmd.extend(ctrl._write_byte)
            cmd.append(a)
            ack(i)

        def write_bytes(data: bytes, i: int) -> None:
            for b in data:
                if fake:
                    cmd.extend(ctrl._clk_lo_data_hi)
                cmd.extend(ctrl._write_byte)
                cmd.append(b)
                ack(i)

        def read_bytes(n: int, i: int) -> None:
            if fake:
                rb = ctrl._clk_lo_data_input + ctrl._read_byte + ctrl._clk_lo_data_hi
                not_last = rb + ctrl._ack + ctrl._clk_lo_data_lo * ctrl._ck_delay
                last = rb + ctrl._nack + ctrl._clk_lo_data_hi * ctrl._ck_delay
            else:
                not_last = ctrl._read_byte + ctrl._ack + ctrl._clk_lo_data_hi * ctrl._ck_delay
                last = ctrl._read_byte + ctrl._nack + ctrl._clk_lo_data_hi * ctrl._ck_delay
            for _ in range(n - 1):
                cmd.extend(not_last)
                expect.append(("data", i))
            cmd.extend(last)
            expect.append(("data", i))

        a_w = (self._addr << 1) & 0xFF
        for i, op in enumerate(ops):
            prolog(a_w, i)
            write_bytes(op[1], i)
            if op[0] == "x":
                prolog(a_w | 1, i)
                read_bytes(op[2], i)
            cmd.extend(ctrl._stop)
            if fake:
                cmd.extend(ctrl._clk_input_data_input)
        cmd.extend(ctrl._immediate)
        with ctrl._lock:
            ctrl._ftdi.write_data(cmd)
            raw = ctrl._ftdi.read_data_bytes(len(expect), 4)
        if len(raw) != len(expect):
            raise OSError("FTDI answered %d of %d bytes" % (len(raw), len(expect)))
        results: list[bytearray | None] = [bytearray() if op[0] == "x" else None for op in ops]
        for (kind, i), b in zip(expect, raw):
            if kind == "ack":
                if b & 1:
                    raise OSError("NACK from the unit in operation %d of a batch" % i)
            else:
                results[i].append(b)
        return [bytes(r) if r is not None else None for r in results]

    def close(self) -> None:
        self._ctrl.terminate()
