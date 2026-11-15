"""ISO 14443-4 Annex B scenarios (as in embassy-rs/rnfc's tests) against a scripted link."""
import builtins
import os
import sys

builtins.Reader = object  # MicroPython ignores annotations; CPython evaluates this one

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402
from nfc.isodep import IsoDep, desfire_command  # noqa: E402
from nfc.st25r3916 import NfcProtocol, NfcTimeout  # noqa: E402

ATS = "06 77 77 81 02 80"


def h(s):
    return bytes.fromhex(s.replace(" ", ""))


class Script:
    def __init__(self):
        self.steps = []
        self.pos = 0

    def ok(self, tx, rx):
        self.steps.append((h(tx), h(rx)))
        return self

    def timeout(self, tx):
        self.steps.append((h(tx), None))
        return self

    def transceive(self, tx, timeout_1fc):
        assert self.pos < len(self.steps), "unexpected extra frame %s" % tx.hex()
        want, rx = self.steps[self.pos]
        self.pos += 1
        assert tx == want, "step %d: sent %s, expected %s" % (self.pos - 1, tx.hex(), want.hex())
        if rx is None:
            raise NfcTimeout("no response from card")
        return rx

    def sleep_ms(self, ms):
        pass

    def done(self):
        return self.pos == len(self.steps)


def activated(script, fsc=None):
    d = IsoDep(script)
    d.activate()
    if fsc:
        d.fsc = fsc
    return d


def trx(d, tx, want):
    assert d.transceive(h(tx)) == h(want)


@pytest.mark.parametrize("ats,fsc,sfgt,fwt", [
    ("01", 32, 256 * 16, 256 * 16 * 16),
    ("02 05", 64, 256 * 16, 256 * 16 * 16),
    ("05 67 81 02 80", 128, 8192, 1048576),
    ("06 77 77 81 02 80", 128, 8192, 1048576),
    ("06 75 77 81 02 80", 64, 8192, 1048576),  # the DESFire EV1 ATS
])
def test_activation(ats, fsc, sfgt, fwt):
    d = activated(Script().ok("e0 80", ats))
    assert (d.fsc, d.sfgt_1fc, d.fwt_1fc) == (fsc, sfgt, fwt)
    assert d.ats == h(ats)


def test_no_ats_retries_then_fails():
    s = Script().timeout("e0 80").timeout("e0 80").timeout("e0 80").timeout("e0 80")
    with pytest.raises(NfcTimeout):
        IsoDep(s).activate()
    assert s.done()


def test_exchange_and_wtx():
    s = Script().ok("e0 80", ATS).ok("02 12 34", "02 56 78").ok("03 aa bb", "03 cc dd")
    d = activated(s)
    trx(d, "12 34", "56 78")
    trx(d, "aa bb", "cc dd")
    s = Script().ok("e0 80", ATS).ok("02 12 34", "f2 c1").ok("f2 01", "f2 c1").ok("f2 01", "02 56 78").ok("03 aa bb", "03 cc dd")
    d = activated(s)
    trx(d, "12 34", "56 78")
    trx(d, "aa bb", "cc dd")
    assert s.done()


def test_chaining_both_ways():
    s = (Script().ok("e0 80", ATS).ok("12 00 11 22 33 44 55 66", "a2").ok("13 77 88 99 aa bb cc dd", "a3")
         .ok("02 ee ff", "02 cc dd").ok("03 aa bb", "03 cc dd"))
    d = activated(s, fsc=10)
    trx(d, "00 11 22 33 44 55 66 77 88 99 aa bb cc dd ee ff", "cc dd")
    trx(d, "aa bb", "cc dd")
    s = (Script().ok("e0 80", ATS).ok("02 12 34", "12 00 11 22 33 44 55 66").ok("a3", "13 77 88 99 aa bb cc dd")
         .ok("a2", "02 ee ff").ok("03 aa bb", "03 cc dd"))
    d = activated(s, fsc=10)
    trx(d, "12 34", "00 11 22 33 44 55 66 77 88 99 aa bb cc dd ee ff")
    trx(d, "aa bb", "cc dd")
    assert s.done()


def test_iblock_errors_scenarios_6_to_9():
    scripts = [
        Script().ok("e0 80", ATS).timeout("02 11 22").ok("b2", "a3").ok("02 11 22", "02 33 44").ok("03 55 66", "03 77 88").ok("02 99 aa", "02 bb cc"),
        Script().ok("e0 80", ATS).ok("02 11 22", "02 33 44").timeout("03 55 66").ok("b3", "a2").ok("03 55 66", "03 77 88").ok("02 99 aa", "02 bb cc"),
        Script().ok("e0 80", ATS).timeout("02 11 22").ok("b2", "02 33 44").ok("03 55 66", "03 77 88").ok("02 99 aa", "02 bb cc"),
        Script().ok("e0 80", ATS).timeout("02 11 22").timeout("b2").ok("b2", "02 33 44").ok("03 55 66", "03 77 88").ok("02 99 aa", "02 bb cc"),
    ]
    for s in scripts:
        d = activated(s)
        trx(d, "11 22", "33 44")
        trx(d, "55 66", "77 88")
        trx(d, "99 aa", "bb cc")
        assert s.done()


def test_wtx_errors_scenarios_10_to_14():
    scripts = [
        Script().ok("e0 80", ATS).timeout("02 11 22").ok("b2", "f2 c1").ok("f2 01", "02 33 44").ok("03 55 66", "03 77 88"),
        Script().ok("e0 80", ATS).timeout("02 11 22").timeout("b2").ok("b2", "f2 c1").ok("f2 01", "02 33 44").ok("03 55 66", "03 77 88"),
        Script().ok("e0 80", ATS).ok("02 11 22", "f2 c1").timeout("f2 01").ok("b2", "f2 c1").ok("f2 01", "02 33 44").ok("03 55 66", "03 77 88"),
        Script().ok("e0 80", ATS).ok("02 11 22", "f2 c1").timeout("f2 01").ok("b2", "02 33 44").ok("03 55 66", "03 77 88"),
        Script().ok("e0 80", ATS).ok("02 11 22", "f2 c1").timeout("f2 01").timeout("b2").ok("b2", "02 33 44").ok("03 55 66", "03 77 88"),
    ]
    for s in scripts:
        d = activated(s)
        trx(d, "11 22", "33 44")
        trx(d, "55 66", "77 88")
        assert s.done()


def test_chaining_errors_scenarios_16_to_20():
    pcd = [
        Script().ok("e0 80", ATS).timeout("12 00 11 22 33 44 55 66").ok("b2", "a2").ok("13 77 88 99 aa bb cc dd", "a3").ok("02 ee ff", "02 cc dd").ok("03 aa bb", "03 cc dd"),
        Script().ok("e0 80", ATS).ok("12 00 11 22 33 44 55 66", "a2").timeout("13 77 88 99 aa bb cc dd").ok("b3", "a2").ok("13 77 88 99 aa bb cc dd", "a3").ok("02 ee ff", "02 cc dd").ok("03 aa bb", "03 cc dd"),
        Script().ok("e0 80", ATS).timeout("12 00 11 22 33 44 55 66").timeout("b2").ok("b2", "a2").ok("13 77 88 99 aa bb cc dd", "a3").ok("02 ee ff", "02 cc dd").ok("03 aa bb", "03 cc dd"),
    ]
    for s in pcd:
        d = activated(s, fsc=10)
        trx(d, "00 11 22 33 44 55 66 77 88 99 aa bb cc dd ee ff", "cc dd")
        trx(d, "aa bb", "cc dd")
        assert s.done()
    s = (Script().ok("e0 80", ATS).ok("02 12 34", "12 00 11 22 33 44 55 66").timeout("a3").ok("a3", "13 77 88 99 aa bb cc dd")
         .ok("a2", "02 ee ff").ok("03 aa bb", "03 cc dd"))
    d = activated(s, fsc=10)
    trx(d, "12 34", "00 11 22 33 44 55 66 77 88 99 aa bb cc dd ee ff")
    trx(d, "aa bb", "cc dd")
    assert s.done()


def test_card_that_never_answers_gives_up_on_the_tenth_failure():
    s = Script().ok("e0 80", ATS).timeout("02 12 34")
    for _ in range(9):
        s.timeout("b2")
    d = activated(s)
    with pytest.raises(NfcTimeout):
        d.transceive(h("12 34"))
    assert s.done()


@pytest.mark.parametrize("answer", ["c2", "f2 c1 00", "a2"])
def test_protocol_errors(answer):
    d = activated(Script().ok("e0 80", ATS).ok("02 12", answer))
    with pytest.raises(NfcProtocol):
        d.transceive(h("12"))


def test_deselect():
    activated(Script().ok("e0 80", ATS).ok("c2", "c2")).deselect()
    d = activated(Script().ok("e0 80", ATS).ok("c2", "02"))
    with pytest.raises(NfcProtocol):
        d.deselect()


def test_desfire_get_version_in_three_frames():
    s = (Script().ok("e0 80", "06 75 77 81 02 80")
         .ok("02 90 60 00 00 00", "02 04 01 01 01 00 18 05 91 af")
         .ok("03 90 af 00 00 00", "03 04 01 01 01 04 18 05 91 af")
         .ok("02 90 af 00 00 00", "02 04 82 81 fa e2 40 80 ba 64 95 c6 40 25 15 91 00"))
    d = activated(s)
    data, sw1, sw2 = desfire_command(d, 0x60)
    assert (sw1, sw2) == (0x91, 0x00) and len(data) == 28
    assert data[0] == 0x04 and data[1] == 0x01 and data[5] == 0x18 and data[26] == 0x25 and data[27] == 0x15
    assert s.done()


def test_cli_desfire(monkeypatch):
    import cli
    from fakechip import FakeChip, FakeNtag215
    from nfc.nfca import Card
    from nfc.st25r3916 import St25r3916

    script = (Script().ok("e0 80", "06 75 77 81 02 80")
              .ok("02 90 60 00 00 00", "02 04 01 01 01 00 18 05 91 af")
              .ok("03 90 af 00 00 00", "03 04 01 01 01 04 18 05 91 af")
              .ok("02 90 af 00 00 00", "02 04 82 81 fa e2 40 80 ba 64 95 c6 40 25 15 91 00")
              .ok("03 90 6a 00 00 00", "03 ff ff ff 30 10 f2 f0 13 f2 91 00")
              .ok("c2", "c2"))
    rf = St25r3916(FakeChip(FakeNtag215()), sleep=lambda s: None, monotonic=__import__("time").monotonic)
    monkeypatch.setattr(cli, "select_card",
                        lambda rf: Card(bytes([4, 0x82, 0x81, 0xFA, 0xE2, 0x40, 0x80]), bytes([0x44, 0x03]), 0x20))
    out = []
    c = cli.Cli(rf, out.append, link=lambda rf: script)
    c.run("desfire")
    assert "".join(out) == (
        "UID:  04:82:81:FA:E2:40:80\nATQA: 4403  SAK: 0x20\nATS:  06 75 77 81 02 80\n"
        "Version: HW 1.0 SW 1.4 vendor 0x04 type 0x01 storage 4096 bytes\n"
        "Card UID: 04:82:81:FA:E2:40:80  production week 25 year 15\n"
        "GET_VERSION status: 91 00\nGET_APPLICATION_IDS status: 91 00\n"
        "  AID FF FF FF\n  AID 30 10 F2\n  AID F0 13 F2\nok\n")
    assert script.done()
    # a Type 2 tag is refused
    monkeypatch.undo()
    out.clear()
    cli.Cli(rf, out.append).run("desfire")
    assert "".join(out).endswith("error: the tag refused the command or does not support it\n")
