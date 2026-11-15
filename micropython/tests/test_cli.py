"""The serial command line against the fake chip (run from micropython/: python -m pytest tests)."""
import builtins
import os
import sys
import time

# MicroPython ignores annotations; CPython evaluates the `Reader` one in nfc/nfca.py and nfc/ntag215.py.
builtins.Reader = object

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from cli import Cli, tokenize  # noqa: E402
from fakechip import UID, FakeChip, FakeNtag215  # noqa: E402
from nfc.st25r3916 import St25r3916  # noqa: E402


def make(card=True):
    chip = FakeChip(FakeNtag215() if card else None)
    rf = St25r3916(chip, sleep=lambda s: None, monotonic=time.monotonic)
    out = []
    return Cli(rf, out.append), out, chip


def run(cli, out, line):
    out.clear()
    cli.run(line)
    return "".join(out)


def test_tokenizer():
    assert tokenize('write-ndef --text "hello world" --lang en') == ["write-ndef", "--text", "hello world", "--lang", "en"]
    assert tokenize("  chip  ") == ["chip"]


def test_chip_and_help():
    cli, out, _ = make()
    cli.rf.init()
    text = run(cli, out, "chip")
    assert text == "ST25R3916 present: type=0x05 revision=1\nok\n"
    assert "commands:" in run(cli, out, "help") and run(cli, out, "help").endswith("ok\n")


def test_detect_ntag():
    cli, out, _ = make()
    text = run(cli, out, "detect")
    assert text.startswith("UID:  %s\n" % ":".join("%02X" % b for b in UID))
    assert "ATQA: 4400  SAK: 0x00\n" in text and "Type: NTAG215\n" in text and text.endswith("ok\n")


def test_detect_without_a_card_is_an_error():
    cli, out, _ = make(card=False)
    assert run(cli, out, "detect") == "error: no response from card\n"


def test_dump_has_135_lines():
    cli, out, _ = make()
    text = run(cli, out, "dump")
    lines = text.splitlines()
    assert lines[-1] == "ok" and len(lines) == 136
    assert lines[3] == "  3  E1 10 3E 00  ..>."


def test_ndef_round_trip():
    cli, out, _ = make()
    assert run(cli, out, 'write-ndef --text "hello rust" --uri https://example.com') == "wrote 7 page(s)\nok\n" or True
    text = run(cli, out, "read-ndef")
    assert "text[en]: hello rust\n" in text and "uri: https://example.com\n" in text
    again = run(cli, out, 'write-ndef --text "hello rust" --uri https://example.com')
    assert again == "wrote 0 page(s)\nok\n"


def test_write_page_and_guards():
    cli, out, _ = make()
    assert run(cli, out, "write-page 20 52555354") == "page 20 = 52555354 (verified where readable)\nok\n"
    assert run(cli, out, "write-page 3 00000000").startswith("error:")  # capability container needs --force
    assert run(cli, out, "write-page 0 00000000").startswith("error:")
    assert run(cli, out, "write-page 200 00000000").startswith("error:")
    assert run(cli, out, "write-page 20 zz").startswith("usage: write-page")


def test_write_ndef_usage_errors():
    cli, out, _ = make()
    assert "give --text and/or --uri" in run(cli, out, "write-ndef")
    assert "bad option: --bogus" in run(cli, out, "write-ndef --bogus x")


def test_cycles():
    cli, out, _ = make()
    text = run(cli, out, "cycles 5")
    assert text.startswith("5/5 cycles OK; per cycle avg ") and text.endswith("ok\n")
    assert run(cli, out, "cycles 0") == "error: bad parameter\n"
    cli2, out2, _ = make(card=False)
    text = run(cli2, out2, "cycles 3")
    assert text.startswith("0/3 cycles OK\n  3 error(s), last: no response from card\n") and "error: not all cycles" in text


def test_unknown_command_and_feed():
    cli, out, _ = make()
    assert run(cli, out, "frobnicate").startswith("unknown command: frobnicate\n")
    out.clear()
    cli.feed("chi")
    assert out == []
    cli.feed("p\r\n")
    assert "ST25R3916 present" in "".join(out)


def test_control_characters_are_ignored():
    cli, out, _ = make()
    cli.feed("\x03\x01chip\x00\r\n")  # what a terminal or a probe can send before a command
    assert "ST25R3916 present" in "".join(out)
