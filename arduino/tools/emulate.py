"""Run the Feather as an NTAG215 tag and save what a phone writes to it.

    uv run --with pyserial python tools/emulate.py [--port /dev/cu.usbmodemXXXX] [--seconds 300] [--out capture]

Starts `emulate` on the Feather, then waits. Hold a phone to the unit and write a tag with NFC Tools
(or any app). Press Enter here when the phone is done (or wait for the timeout): the script ends the
emulation, reads the event log and the emulated memory back, and writes

    <out>.bin    the 540 bytes of tag memory
    <out>.txt    the event log, the memory in `dump` format and the decoded NDEF message
"""
import argparse
import glob
import re
import sys
import threading
import time

import serial


def read_until(ser, ok_markers, timeout):
    out = b""
    end = time.time() + timeout
    while time.time() < end:
        out += ser.read(4096)
        text = out.rstrip()
        if text.endswith(b"ok") or b"error:" in out:
            break
    return out.decode(errors="replace")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default=None)
    ap.add_argument("--seconds", type=int, default=300)
    ap.add_argument("--out", default="capture")
    ap.add_argument("--stop-file", default=None, help="also stop when this file appears (for scripts)")
    args = ap.parse_args()
    port = args.port or (sorted(glob.glob("/dev/cu.usbmodem*")) or [None])[0]
    if not port:
        print("no serial port found", file=sys.stderr)
        return 1
    ser = serial.Serial(port, 115200, timeout=0.2)
    ser.dtr = True
    time.sleep(0.3)
    ser.reset_input_buffer()
    ser.write(f"emulate {args.seconds}\n".encode())
    time.sleep(0.5)
    first = ser.read(512).decode(errors="replace")
    if "emulating" not in first:
        print("the Feather did not start emulating:\n" + first, file=sys.stderr)
        return 1
    print(first.strip())
    print(f"Hold the phone to the unit and write the tag. Press Enter when done (timeout {args.seconds} s)...", flush=True)

    import os

    done = threading.Event()
    if args.stop_file is None:
        threading.Thread(target=lambda: (sys.stdin.readline(), done.set()), daemon=True).start()
    t0 = time.time()
    while not done.is_set() and time.time() - t0 < args.seconds:
        if args.stop_file and os.path.exists(args.stop_file):
            break
        time.sleep(0.1)
    ser.write(b"x")  # any byte ends the emulation
    log = read_until(ser, (b"ok",), 15)
    ser.write(b"emu-dump\n")
    dump = read_until(ser, (b"ok",), 10)

    mem = bytearray(540)
    seen = 0
    for line in dump.splitlines():
        m = re.match(r"^\s*(\d+)\s+((?:[0-9A-F]{2} ){3}[0-9A-F]{2})", line)
        if m and int(m.group(1)) < 135:
            page = int(m.group(1))
            mem[page * 4:page * 4 + 4] = bytes.fromhex(m.group(2))
            seen += 1
    if seen != 135:
        print(f"warning: read back {seen} of 135 pages", file=sys.stderr)
    with open(args.out + ".bin", "wb") as f:
        f.write(mem)
    with open(args.out + ".txt", "w") as f:
        f.write("== event log ==\n" + log.strip() + "\n\n== memory and NDEF ==\n" + dump.strip() + "\n")
    written = [l for l in dump.splitlines() if "(written)" in l]
    print(f"saved {args.out}.bin and {args.out}.txt ({len(written)} pages written by the phone)")
    for l in dump.splitlines():
        if re.match(r"^(text|uri|tnf)", l) or l.startswith("no NDEF"):
            print(l)
    return 0


if __name__ == "__main__":
    sys.exit(main())
