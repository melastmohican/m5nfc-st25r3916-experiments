"""Command line: uv run python -m nfc.cli <command>"""
from __future__ import annotations

import argparse
import sys
import time

from . import ndef
from .ident import GET_VERSION, READ_CNT, READ_SIG, counter_value, ident, ntag_type_name
from .isodep import IsoDep, RfIsoDepLink, desfire_command
from .nfca import select_card
from .ntag215 import PAGE_SIZE, Ntag215
from .st25r3916 import NfcError, NfcRefused, St25r3916
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
    if card.sak & 0x20:  # SAK bit 6: the card speaks ISO 14443-4 and does not answer Type 2 READ
        print("Type: ISO-DEP card (ISO 14443-4); try `desfire`")
        return
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


def cmd_ident(rf, args) -> None:
    """NTAG identification: GET_VERSION, READ_SIG and READ_CNT."""
    select_card(rf)
    version = ident(rf, GET_VERSION)
    print(f"Version: {version.hex(' ').upper()}")
    print(f"Type: {ntag_type_name(version)}")
    print(f"Signature: {ident(rf, READ_SIG, 0x00).hex().upper()}")
    try:
        count = counter_value(ident(rf, READ_CNT, 0x02))
    except NfcRefused:
        print("Counter: not readable (NAK)")
    else:
        if count is None:
            raise NfcError("bad response")
        print(f"Counter: {count}")


def cmd_desfire(rf, args, link=RfIsoDepLink) -> None:
    """ISO-DEP plus DESFire GET_VERSION and GET_APPLICATION_IDS: what a card reveals without keys."""
    card = select_card(rf)
    print(f"UID:  {card.uid_hex}")
    print(f"ATQA: {card.atqa.hex().upper()}  SAK: 0x{card.sak:02X}")
    if not card.sak & 0x20:
        raise NfcRefused("the tag refused the command or does not support it")  # not an ISO-DEP card
    dep = IsoDep(link(rf))
    dep.activate()
    print(f"ATS:  {dep.ats.hex(' ').upper()}")
    version, vsw1, vsw2 = desfire_command(dep, 0x60)
    aids, asw1, asw2 = desfire_command(dep, 0x6A)
    try:
        dep.deselect()
    except NfcError:
        pass  # the card is back at idle either way, or gone
    if len(version) >= 28:
        storage = 1 << (version[5] >> 1) if version[5] & 1 == 0 else 0
        print(f"Version: HW {version[3]}.{version[4]} SW {version[10]}.{version[11]} "
              f"vendor 0x{version[0]:02X} type 0x{version[1]:02X} storage {storage} bytes")
        uid = ":".join(f"{b:02X}" for b in version[14:21])
        print(f"Card UID: {uid}  production week {version[26]:02X} year {version[27]:02X}")
    print(f"GET_VERSION status: {vsw1:02X} {vsw2:02X}")
    print(f"GET_APPLICATION_IDS status: {asw1:02X} {asw2:02X}")
    for i in range(0, len(aids) - 2, 3):
        print(f"  AID {aids[i:i + 3].hex(' ').upper()}")


def cmd_cycles(rf, args) -> None:
    """N rounds of select and full dump, each compared with the first."""
    n = args.n
    if not 1 <= n <= 1000:
        raise NfcError("bad parameter")
    first = None
    ok = mismatches = errors = 0
    last_err = None
    total = worst = 0
    for _ in range(n):
        t0 = time.monotonic()
        try:
            select_card(rf)  # no field reset here: the retry inside select_card handles a stuck tag
            data = Ntag215(rf).dump()
        except NfcError as exc:
            errors += 1
            last_err = exc
            continue
        if first is None:
            first = data
        elif data != first:
            mismatches += 1
            continue
        ok += 1
        dt = int((time.monotonic() - t0) * 1000)
        total += dt
        worst = max(worst, dt)
    line = f"{ok}/{n} cycles OK"
    if ok:
        line += f"; per cycle avg {total // ok} ms, max {worst} ms"
    print(line)
    if mismatches:
        print(f"  {mismatches} dump mismatch(es)")
    if errors:
        print(f"  {errors} error(s), last: {last_err}")
    if ok != n:
        raise NfcError("not all cycles succeeded")


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
    sub.add_parser("ident", help="NTAG GET_VERSION, READ_SIG, READ_CNT").set_defaults(fn=cmd_ident)
    sub.add_parser("desfire", help="identify a DESFire card over ISO-DEP (no keys)").set_defaults(fn=cmd_desfire)
    cy = sub.add_parser("cycles", help="N rounds of select and full dump, compared with the first")
    cy.add_argument("n", nargs="?", type=int, default=50)
    cy.set_defaults(fn=cmd_cycles)
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
