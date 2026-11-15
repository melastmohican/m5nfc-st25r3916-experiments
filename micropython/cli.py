"""Serial command line for the MicroPython reader: the same commands as the other variants.

One command per line on the board's USB serial port (the console CDC). Every command ends with a
line "ok" or "error: <reason>". The commands share the chip with the PC/SC reader, so a long one
such as `cycles` delays PC/SC answers while it runs, and a command that selects the card ends the
PC/SC session of a card held by the host.

Pure Python on top of the nfc package, so it also runs on the Mac against the fake chip
(tests/test_cli.py).
"""
import time

from nfc import ndef
from nfc.isodep import IsoDep, RfIsoDepLink, desfire_command
from nfc.ident import GET_VERSION, READ_CNT, READ_SIG, counter_value, ident, ntag_type_name
from nfc.nfca import select_card
from nfc.ntag215 import PAGE_SIZE, TOTAL_PAGES, Ntag215
from nfc.st25r3916 import NfcError, NfcRefused

try:
    _ticks, _diff = time.ticks_ms, time.ticks_diff
except AttributeError:  # CPython (host tests)
    def _ticks():
        return int(time.monotonic() * 1000)

    def _diff(a, b):
        return a - b

HELP = (
    "commands: chip | detect | dump | read-ndef | write-ndef [--text T] [--lang L] [--uri U] |\n"
    "          write-page <page> <8 hex> [--force] | ident | desfire | cycles [N] | help\n"
)
LINE_MAX = 200


def tokenize(line):
    """Split on whitespace; double quotes group words."""
    out = []
    i, n = 0, len(line)
    while i < n:
        c = line[i]
        if c in " \t":
            i += 1
        elif c == '"':
            j = line.find('"', i + 1)
            if j < 0:
                j = n
            out.append(line[i + 1:j])
            i = j + 1
        else:
            j = i
            while j < n and line[j] not in " \t":
                j += 1
            out.append(line[i:j])
            i = j
    return out


def _hex_bytes(data, sep=" "):
    return sep.join("%02X" % b for b in data)


def _parse_hex4(text):
    if len(text) != 8:
        return None
    try:
        return bytes(int(text[i:i + 2], 16) for i in range(0, 8, 2))
    except ValueError:
        return None


class Cli:
    def __init__(self, rf, write, link=RfIsoDepLink):
        self.rf = rf
        self.write = write  # write(str)
        self._link = link  # builds the ISO-DEP link from the driver (replaceable in tests)
        self._line = ""

    # ---- input -------------------------------------------------------
    def feed(self, text):
        """Take typed characters; runs each complete line."""
        for c in text:
            if c in "\r\n":
                if self._line:
                    line, self._line = self._line, ""
                    self.run(line)
            elif (c >= " " or c == "\t") and len(self._line) < LINE_MAX - 1:  # ignore control characters
                self._line += c

    def run(self, line):
        argv = tokenize(line)
        if not argv:
            return
        cmd, args = argv[0], argv[1:]
        handler = {
            "chip": self._chip,
            "detect": self._detect,
            "dump": self._dump,
            "read-ndef": self._read_ndef,
            "write-ndef": self._write_ndef,
            "write-page": self._write_page,
            "ident": self._ident,
            "desfire": self._desfire,
            "cycles": self._cycles,
            "help": self._help,
        }.get(cmd)
        try:
            if handler is None:
                self.write("unknown command: %s\n" % cmd)
                self.write(HELP)
                raise NfcError("bad parameter")
            handler(args)
            self.write("ok\n")
        except NfcError as exc:
            self.write("error: %s\n" % exc)
        except Exception as exc:  # noqa: BLE001  keep the reader alive whatever happens
            self.write("error: %r\n" % (exc,))

    # ---- commands ----------------------------------------------------
    def _help(self, args):
        self.write(HELP)

    def _chip(self, args):
        ic_type, rev = self.rf.identify()
        self.write("ST25R3916 present: type=0x%02X revision=%d\n" % (ic_type, rev))

    def _detect(self, args):
        card = select_card(self.rf)
        self.write("UID:  %s\n" % card.uid_hex)
        self.write("ATQA: %s  SAK: 0x%02X\n" % (_hex_bytes(card.atqa, ""), card.sak))
        if card.sak & 0x20:  # SAK bit 6: the card speaks ISO 14443-4 and does not answer Type 2 READ
            self.write("Type: ISO-DEP card (ISO 14443-4); try `desfire`\n")
        elif Ntag215(self.rf).is_ntag215():
            self.write("Type: NTAG215\n")
        else:
            self.write("Type: not an NTAG215 (CC differs)\n")

    def _dump(self, args):
        select_card(self.rf)
        data = Ntag215(self.rf).dump()
        for page in range(TOTAL_PAGES):
            chunk = data[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
            text = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
            self.write("%3d  %s  %s\n" % (page, _hex_bytes(chunk), text))

    def _read_ndef(self, args):
        select_card(self.rf)
        records = Ntag215(self.rf).read_ndef()
        if not records:
            self.write("no NDEF message\n")
        for rec in records:
            self.write(rec.describe() + "\n")

    def _write_ndef(self, args):
        text = uri = None
        lang = "en"
        i = 0
        while i < len(args):
            opt = args[i]
            if opt not in ("--text", "--lang", "--uri") or i + 1 >= len(args):
                self.write("bad option: %s\n" % opt)
                raise NfcError("bad parameter")
            if opt == "--text":
                text = args[i + 1]
            elif opt == "--lang":
                lang = args[i + 1]
            else:
                uri = args[i + 1]
            i += 2
        if text is None and uri is None:
            self.write("give --text and/or --uri\n")
            raise NfcError("bad parameter")
        records = []
        if text is not None:
            records.append(ndef.text_record(text, lang))
        if uri is not None:
            records.append(ndef.uri_record(uri))
        select_card(self.rf)
        n = Ntag215(self.rf).write_ndef(records)
        self.write("wrote %d page(s)\n" % n)

    def _write_page(self, args):
        force = "--force" in args
        pos = [a for a in args if a != "--force"]
        data = _parse_hex4(pos[1]) if len(pos) == 2 else None
        try:
            page = int(pos[0]) if pos else -1
        except ValueError:
            page = -1
        if data is None or not 0 <= page <= 255:
            self.write("usage: write-page <page> <8 hex digits> [--force]\n")
            raise NfcError("bad parameter")
        select_card(self.rf)
        Ntag215(self.rf).write_page(page, data, force=force)
        self.write("page %d = %s (verified where readable)\n" % (page, _hex_bytes(data, "")))

    def _ident(self, args):
        select_card(self.rf)
        version = ident(self.rf, GET_VERSION)
        self.write("Version: %s\n" % _hex_bytes(version))
        self.write("Type: %s\n" % ntag_type_name(version))
        self.write("Signature: %s\n" % _hex_bytes(ident(self.rf, READ_SIG, 0x00), ""))
        try:
            count = counter_value(ident(self.rf, READ_CNT, 0x02))
        except NfcRefused:
            self.write("Counter: not readable (NAK)\n")
        else:
            if count is None:
                raise NfcError("bad response")
            self.write("Counter: %d\n" % count)

    def _desfire(self, args):
        """ISO-DEP plus DESFire GET_VERSION and GET_APPLICATION_IDS: what a card reveals without keys."""
        card = select_card(self.rf)
        self.write("UID:  %s\n" % card.uid_hex)
        self.write("ATQA: %s  SAK: 0x%02X\n" % (_hex_bytes(card.atqa, ""), card.sak))
        if not card.sak & 0x20:
            raise NfcRefused("the tag refused the command or does not support it")  # not an ISO-DEP card
        dep = IsoDep(self._link(self.rf))
        dep.activate()
        self.write("ATS:  %s\n" % _hex_bytes(dep.ats))
        version, vsw1, vsw2 = desfire_command(dep, 0x60)
        aids, asw1, asw2 = desfire_command(dep, 0x6A)
        try:
            dep.deselect()
        except NfcError:
            pass  # the card is back at idle either way, or gone
        if len(version) >= 28:
            storage = 1 << (version[5] >> 1) if version[5] & 1 == 0 else 0
            self.write("Version: HW %d.%d SW %d.%d vendor 0x%02X type 0x%02X storage %d bytes\n" % (
                version[3], version[4], version[10], version[11], version[0], version[1], storage))
            self.write("Card UID: %s  production week %02X year %02X\n" % (
                _hex_bytes(version[14:21], ":"), version[26], version[27]))
        self.write("GET_VERSION status: %02X %02X\n" % (vsw1, vsw2))
        self.write("GET_APPLICATION_IDS status: %02X %02X\n" % (asw1, asw2))
        for i in range(0, len(aids) - 2, 3):
            self.write("  AID %s\n" % _hex_bytes(aids[i:i + 3]))

    def _cycles(self, args):
        try:
            n = int(args[0]) if args else 50
        except ValueError:
            n = 0
        if not 1 <= n <= 1000:
            raise NfcError("bad parameter")
        first = None
        ok = mismatches = errors = 0
        last_err = None
        total = worst = 0
        for _ in range(n):
            t0 = _ticks()
            try:
                select_card(self.rf)  # no field reset here: the retry inside select_card handles a stuck tag
                data = Ntag215(self.rf).dump()
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
            dt = _diff(_ticks(), t0)
            total += dt
            worst = max(worst, dt)
        line = "%d/%d cycles OK" % (ok, n)
        if ok:
            line += "; per cycle avg %d ms, max %d ms" % (total // ok, worst)
        self.write(line + "\n")
        if mismatches:
            self.write("  %d dump mismatch(es)\n" % mismatches)
        if errors:
            self.write("  %d error(s), last: %s\n" % (errors, last_err))
        if ok != n:
            raise NfcError("not all cycles succeeded")
