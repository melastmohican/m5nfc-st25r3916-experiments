"""Rebuild the image an NFC e-paper app sent, from a `tools/emulate.py --mode sniff` capture.

    uv run --with pillow python tools/gd_image.py capture.txt [out.png]

Reads the `C>` lines (the reader's APDUs), joins the `F0 D2` image packets (250 bytes each, P2 =
packet number) and draws them as a 1 bit per pixel bitmap, rows of width/8 bytes, MSB first,
1 = white. Width and height come from the `A0` entry of the `F0 DB 00 00` screen setup, or from
--width/--height. Also prints every other APDU, shortened.
"""
import argparse
import sys


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("capture")
    ap.add_argument("out", nargs="?", default="image.png")
    ap.add_argument("--width", type=int)
    ap.add_argument("--height", type=int)
    args = ap.parse_args()

    cmds = []
    for line in open(args.capture):
        line = line.strip()
        if line.startswith("C>"):
            cmds.append(bytes.fromhex(line[2:].replace(" ", "")))
    if not cmds:
        print("no 'C>' lines in the capture", file=sys.stderr)
        return 1

    packets = {}
    w, h = args.width, args.height
    for c in cmds:
        if c[:2] == b"\xF0\xD2" and len(c) >= 5:
            packets[(c[2], c[3])] = c[5:5 + c[4]]
        elif c[:2] == b"\xF0\xDB" and c[2] == 0 and len(c) > 12 and c[5] == 0xA0 and c[6] >= 6 and w is None:
            w, h = (c[5 + 4] << 8 | c[5 + 5]), (c[5 + 6] << 8 | c[5 + 7])  # A0 06 <2 bytes> <w> <h>
        shown = " ".join(f"{b:02X}" for b in c[:12]) + (" ..." if len(c) > 12 else "")
        print(f"{len(c):3d} bytes  {shown}")

    index = sorted({k[0] for k in packets})
    print(f"image(s): {index}; packets: {len(packets)}")
    for idx in index:
        data = b"".join(packets[k] for k in sorted(packets) if k[0] == idx)
        print(f"image {idx}: {len(data)} bytes")
        if not (w and h):
            print("width and height unknown: pass --width and --height")
            continue
        row = (w + 7) // 8
        if len(data) < row * h:
            print(f"only {len(data)} of {row * h} bytes: image incomplete")
            continue
        from PIL import Image

        im = Image.new("1", (w, h))
        px = im.load()
        for y in range(h):
            for x in range(w):
                px[x, y] = (data[y * row + x // 8] >> (7 - x % 8)) & 1
        out = args.out if len(index) == 1 else f"{args.out.rsplit('.', 1)[0]}_{idx}.png"
        im.save(out)
        print(f"saved {out} ({w}x{h})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
