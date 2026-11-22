"""Act as an NFC-A tag for a while, then print what a reader (a phone) did to it.

    uv run python tools/listen_probe.py [seconds] [t2|t4|sniff]

t2 (default) is an NTAG215 the phone can read and write, t4 an NFC Forum Type 4 NDEF tag (ISO-DEP), sniff an
ISO-DEP card that logs every APDU. UID 04112233445566. Ctrl-C ends the run early.

The bus runs at 400 kHz and the hot path goes through FtdiTransport.batch(): about 1.7 ms for a whole Type 2
command instead of 11 ms (tools/batch_timing.py). See nfc/listen.py.
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nfc import ndef  # noqa: E402
from nfc.apduapps import SniffApp, Type4NdefApp  # noqa: E402
from nfc.isodeptarget import IsoDepTarget  # noqa: E402
from nfc.listen import Listener  # noqa: E402
from nfc.st25r3916 import St25r3916  # noqa: E402
from nfc.transport import FtdiTransport  # noqa: E402
from nfc.type2tag import BYTES, PAGES, Type2Tag  # noqa: E402

UID = bytes.fromhex("04112233445566")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("seconds", nargs="?", type=float, default=40)
    ap.add_argument("mode", nargs="?", choices=("t2", "t4", "sniff"), default="t2")
    args = ap.parse_args()

    app = None
    if args.mode == "t2":
        target, sak, what = Type2Tag(UID), 0x00, "an NTAG215"
    elif args.mode == "t4":
        app = Type4NdefApp()
        target, sak, what = IsoDepTarget(app), 0x20, "a Type 4 NDEF tag"  # SAK 20: the reader sends RATS
    else:
        app = SniffApp()
        target, sak, what = IsoDepTarget(app), 0x20, "an ISO-DEP card that logs every APDU"

    t = FtdiTransport(freq=400_000)
    rf = St25r3916(t)
    lis = Listener(rf, target)
    lis.start(UID, 0x0044, sak)
    print(f"emulating {what}, UID {UID.hex()}, for {args.seconds:.0f} s: hold the phone to the unit", flush=True)
    end = time.perf_counter() + args.seconds
    try:
        while time.perf_counter() < end:
            lis.poll()
    except KeyboardInterrupt:
        pass
    finally:
        lis.stop()
        t.close()

    for line in lis.log_lines():
        print(line)
    text = f"fields seen: {lis.fields}, frames answered: {lis.frames}"
    if args.mode == "t2":
        text += f", tag writes: {target.writes}"
    elif args.mode == "t4":
        text += f", NDEF file updates: {app.updates}"
    else:
        text += f", APDUs: {app.apdus}, sessions: {app.sessions}"
    print(text)

    if args.mode == "t2":
        mem = target.memory()
        for page in range(PAGES):
            if target.is_changed(page):
                c = mem[page * 4:page * 4 + 4]
                print(f"{page:3d}  {c.hex(' ')}  (written)")
        msg = ndef.find_message(bytes(mem[16:BYTES]))
    elif args.mode == "t4":
        f = app.ndef
        nlen = f[0] << 8 | f[1]
        msg = bytes(f[2:2 + nlen]) if 0 < nlen <= len(f) - 2 else None
    else:
        for kind, data in app.records:
            print(("C> " if kind == 1 else "R> ") + data.hex(" "))
        msg = None
    for rec in ndef.decode_message(msg) if msg else []:
        print(rec.describe())


if __name__ == "__main__":
    main()
