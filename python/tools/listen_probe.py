"""Act as an NFC-A tag for a while and print everything a reader (phone) sends.

    uv run python tools/listen_probe.py [seconds] [--uid 04112233445566]

Hold a phone to the unit. The chip answers anticollision by itself; this prints the states and the
frames the phone sends after it has selected us. Experiment: see nfc/listen.py.
"""
import argparse
import sys
import time

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))

from nfc.listen import Listener  # noqa: E402
from nfc.st25r3916 import St25r3916  # noqa: E402
from nfc.transport import FtdiTransport  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("seconds", nargs="?", type=float, default=60)
    ap.add_argument("--uid", default="04112233445566")
    args = ap.parse_args()
    t = FtdiTransport(freq=400_000)
    rf = St25r3916(t)
    lis = Listener(rf, bytes.fromhex(args.uid))
    lis.start()
    print(f"listening as UID {args.uid} for {args.seconds:.0f} s: hold the phone to the unit", flush=True)
    end = time.perf_counter() + args.seconds
    try:
        while time.perf_counter() < end:
            lis.poll()
    except KeyboardInterrupt:
        pass
    finally:
        t.close()


if __name__ == "__main__":
    main()
