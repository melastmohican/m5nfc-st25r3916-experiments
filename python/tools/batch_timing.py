"""How fast can the Mac answer a phone through the FT232H? Times the traffic of one tag-emulation
reply the way the stack does it today (one pyftdi call per I2C operation) and as raw MPSSE batches
(one USB write for the whole reply, one blind read for the poll and the frame): FtdiTransport.batch().

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

from nfc.st25r3916 import St25r3916  # noqa: E402
from nfc.transport import FtdiTransport  # noqa: E402

REG_NUM_TX1, REG_NUM_TX2 = 0x22, 0x23
CMD_CLEAR_FIFO = 0xDB


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
        reply = bytes(range(16))

        def sequential_reply():  # one pyftdi call per operation, as the plain driver does it
            rf.command(CMD_CLEAR_FIFO)
            rf.load_fifo(reply)
            rf.write_reg(REG_NUM_TX1, 0)
            rf.write_reg(REG_NUM_TX2, 128)
            rf.command(CMD_CLEAR_FIFO)  # stands in for the transmit command

        def batched_reply():
            tr.batch([
                ("w", bytes([CMD_CLEAR_FIFO])),
                ("w", bytes([0x80]) + reply),
                ("w", bytes([REG_NUM_TX1, 0])),
                ("w", bytes([REG_NUM_TX2, 128])),
                ("w", bytes([CMD_CLEAR_FIFO])),
            ])

        def batched_poll():  # the four interrupt registers
            tr.batch([("x", bytes([0x1A | 0x40]), 4)])

        def batched_frame_read():  # FIFO count and the first 12 FIFO bytes, blind
            tr.batch([("x", bytes([0x1E | 0x40]), 2), ("x", bytes([0x9F]), 12)])

        # the batch must read what the normal path reads
        ident = rf.read_reg(0x3F)
        got = tr.batch([("x", bytes([0x3F | 0x40]), 1)])[0][0]
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
