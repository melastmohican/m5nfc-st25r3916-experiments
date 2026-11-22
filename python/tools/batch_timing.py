"""How fast can the Mac answer a phone through the FT232H? Times the traffic of one tag-emulation
reply the way the stack does it today (one pyftdi call per I2C operation) and as raw MPSSE batches
(one USB write for the whole reply, one blind read for the poll and the frame).

Needs the unit on the FT232H (see README.md). Nothing is transmitted: the TX command of a real reply
is replaced by CLEAR FIFO, so the traffic is the same and the field stays quiet.

    uv run python tools/batch_timing.py

A phone gives a tag about 5 ms for its first reply; the time that counts is poll + frame read +
reply, the three exchanges of one Type 2 command.
"""
from __future__ import annotations

import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nfc.st25r3916 import I2C_ADDR, St25r3916  # noqa: E402
from nfc.transport import FtdiTransport  # noqa: E402

REG_NUM_TX1, REG_NUM_TX2 = 0x22, 0x23
CMD_CLEAR_FIFO = 0xDB


def i2c_batch(ctrl, addr, ops):
    """Run several I2C transfers as ONE USB write and ONE read, using pyftdi's own MPSSE building blocks.

    ops: ("w", bytes) or ("x", bytes, readlen). Returns one entry per op: None, or the bytes read.
    The bytes of a write are acknowledged one by one by the slave; the acknowledgements are collected
    and checked after the whole batch ran. This reaches into pyftdi's private I2cController helpers
    (pinned in uv.lock to 0.57.2); its own write() does one USB round trip per byte.
    """
    fake = ctrl._fake_tristate
    cmd = bytearray()
    expect = []  # per MPSSE reply byte: ("ack", op index) or ("data", op index)

    def ack(i):
        cmd.extend(ctrl._clk_lo_data_input if fake else ctrl._clk_lo_data_hi)
        cmd.extend(ctrl._read_bit)
        expect.append(("ack", i))

    def prolog(a, i):
        cmd.extend(ctrl._idle * ctrl._ck_delay)
        cmd.extend(ctrl._start)
        cmd.extend(ctrl._write_byte)
        cmd.append(a)
        ack(i)

    def write_bytes(data, i):
        for b in data:
            if fake:
                cmd.extend(ctrl._clk_lo_data_hi)
            cmd.extend(ctrl._write_byte)
            cmd.append(b)
            ack(i)

    def read_bytes(n, i):
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

    def epilog():
        cmd.extend(ctrl._stop)
        if fake:
            cmd.extend(ctrl._clk_input_data_input)

    a_w = (addr << 1) & 0xFF
    for i, op in enumerate(ops):
        prolog(a_w, i)
        write_bytes(op[1], i)
        if op[0] == "x":
            prolog(a_w | 1, i)
            read_bytes(op[2], i)
        epilog()
    cmd.extend(ctrl._immediate)
    ctrl._ftdi.write_data(cmd)
    raw = ctrl._ftdi.read_data_bytes(len(expect), 4)
    if len(raw) != len(expect):
        raise RuntimeError("short MPSSE answer: %d of %d bytes" % (len(raw), len(expect)))
    results = [bytearray() if op[0] == "x" else None for op in ops]
    for (kind, i), b in zip(expect, raw):
        if kind == "ack":
            if b & 1:
                raise RuntimeError("NACK in op %d" % i)
        else:
            results[i].append(b)
    return [bytes(r) if r is not None else None for r in results]


def bench(label, fn, n=200):
    fn()
    t = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        t.append((time.perf_counter() - t0) * 1000)
    t.sort()
    print("  %-46s median %6.2f ms   p95 %6.2f   max %6.2f" % (label, statistics.median(t), t[int(n * .95)], t[-1]))
    return statistics.median(t)


def main():
    for freq in (100_000, 400_000):
        print("\nI2C at %d kHz" % (freq // 1000))
        tr = FtdiTransport(freq=freq)
        rf = St25r3916(tr)
        rf.init()
        ctrl, port = tr._ctrl, tr._port
        reply = bytes(range(16))

        def sequential_reply():  # what listen.py does today for one reply
            rf.command(CMD_CLEAR_FIFO)
            rf.load_fifo(reply)
            rf.write_reg(REG_NUM_TX1, 0)
            rf.write_reg(REG_NUM_TX2, 128)
            rf.command(CMD_CLEAR_FIFO)  # stands in for the transmit command

        def batched_reply():
            i2c_batch(ctrl, I2C_ADDR, [
                ("w", bytes([CMD_CLEAR_FIFO])),
                ("w", bytes([0x80]) + reply),
                ("w", bytes([REG_NUM_TX1, 0])),
                ("w", bytes([REG_NUM_TX2, 128])),
                ("w", bytes([CMD_CLEAR_FIFO])),
            ])

        def batched_poll():  # the four interrupt registers
            i2c_batch(ctrl, I2C_ADDR, [("x", bytes([0x1A | 0x40]), 4)])

        def batched_frame_read():  # FIFO count and the first 12 FIFO bytes, blind
            i2c_batch(ctrl, I2C_ADDR, [("x", bytes([0x1E | 0x40]), 2), ("x", bytes([0x9F]), 12)])

        # the batch must read what the normal path reads
        ident = rf.read_reg(0x3F)
        got = i2c_batch(ctrl, I2C_ADDR, [("x", bytes([0x3F | 0x40]), 1)])[0][0]
        print("  identity register: normal path 0x%02X, batch 0x%02X %s" % (ident, got, "ok" if ident == got else "MISMATCH"))

        a_read = bench("today: read 4 interrupt registers", lambda: rf.read_irqs())
        a_reply = bench("today: one reply (5 operations)", sequential_reply)
        b_poll = bench("batched: poll (4 interrupt registers)", batched_poll)
        b_frame = bench("batched: frame read (count + 12 FIFO bytes)", batched_frame_read)
        b_reply = bench("batched: one reply (5 operations)", batched_reply)
        print("  --> one Type 2 command, today: poll + read + reply = %.1f ms (the stack also reads the FIFO count first)"
              % (a_read * 3 + a_reply))
        print("  --> one Type 2 command, batched: poll + frame read + reply = %.1f ms   (phone allows about 5)"
              % (b_poll + b_frame + b_reply))
        tr.close()


if __name__ == "__main__":
    main()
