"""Command line: uv run python -m nfc.cli <command>"""
from __future__ import annotations

import argparse
import sys

from . import ndef
from .nfca import select_card
from .ntag215 import PAGE_SIZE, Ntag215
from .st25r3916 import NfcError, St25r3916
from .transport import DEFAULT_URL, FtdiTransport


def _open(args) -> tuple[St25r3916, FtdiTransport]:
    transport = FtdiTransport(url=args.url, freq=args.freq)
    rf = St25r3916(transport)
    rf.init()
    rf.field_reset()
    return rf, transport


def _hexdump(data: bytes) -> None:
    for page in range(len(data) // PAGE_SIZE):
        chunk = data[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
        text = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        print(f"{page:3d}  {chunk.hex(' ').upper()}  {text}")


def cmd_chip(rf, args) -> None:
    ic_type, rev = rf.identify()
    print(f"ST25R3916 present: type=0x{ic_type:02X} revision={rev}")


def cmd_detect(rf, args) -> None:
    card = select_card(rf)
    print(f"UID:  {card.uid_hex}")
    print(f"ATQA: {card.atqa.hex().upper()}  SAK: 0x{card.sak:02X}")
    print("Type: NTAG215" if Ntag215(rf).is_ntag215() else "Type: not an NTAG215 (CC differs)")


def cmd_dump(rf, args) -> None:
    select_card(rf)
    _hexdump(Ntag215(rf).dump())


def cmd_read_ndef(rf, args) -> None:
    select_card(rf)
    records = Ntag215(rf).read_ndef()
    if not records:
        print("no NDEF message")
    for rec in records:
        print(rec.describe())


def cmd_write_ndef(rf, args) -> None:
    records = []
    if args.text:
        records.append(ndef.text_record(args.text, args.lang))
    if args.uri:
        records.append(ndef.uri_record(args.uri))
    if not records:
        raise NfcError("give --text and/or --uri")
    select_card(rf)
    n = Ntag215(rf).write_ndef(records)
    print(f"wrote {n} page(s)")


def cmd_write_page(rf, args) -> None:
    data = bytes.fromhex(args.hex)
    select_card(rf)
    Ntag215(rf).write_page(args.page, data, force=args.force)
    print(f"page {args.page} = {data.hex().upper()} (verified where readable)")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="nfc")
    p.add_argument("--url", default=DEFAULT_URL, help="pyftdi device URL")
    p.add_argument("--freq", type=int, default=100_000, help="I2C clock in Hz")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("chip", help="check the ST25R3916 answers").set_defaults(fn=cmd_chip)
    sub.add_parser("detect", help="find a card, print UID/ATQA/SAK").set_defaults(fn=cmd_detect)
    sub.add_parser("dump", help="dump all 135 pages").set_defaults(fn=cmd_dump)
    sub.add_parser("read-ndef", help="decode the NDEF message").set_defaults(fn=cmd_read_ndef)
    w = sub.add_parser("write-ndef", help="write text and/or URI records")
    w.add_argument("--text")
    w.add_argument("--lang", default="en")
    w.add_argument("--uri")
    w.set_defaults(fn=cmd_write_ndef)
    wp = sub.add_parser("write-page", help="write one 4-byte page")
    wp.add_argument("page", type=int)
    wp.add_argument("hex", help="8 hex digits")
    wp.add_argument("--force", action="store_true", help="allow lock/config/CC pages (one-way!)")
    wp.set_defaults(fn=cmd_write_page)

    args = p.parse_args(argv)
    rf, transport = _open(args)
    try:
        args.fn(rf, args)
        return 0
    except NfcError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        try:
            rf.field_off()
        finally:
            transport.close()


if __name__ == "__main__":
    raise SystemExit(main())
