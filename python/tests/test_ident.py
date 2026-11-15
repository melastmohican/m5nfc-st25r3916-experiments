"""NTAG identification, NAK decoding and the PC/SC pseudo-APDUs against the fake chip."""
import pytest

from nfc import cli
from nfc.ident import GET_VERSION, READ_CNT, READ_SIG, counter_value, ident, ntag_type_name
from nfc.nfca import nak_text, select_card, t2_write
from nfc.st25r3916 import NfcNak, NfcRefused, NfcTimeout, St25r3916
from tests.fakechip import FakeChip, FakeNtag215


def make(card=True):
    tag = FakeNtag215() if card else None
    chip = FakeChip(tag)
    rf = St25r3916(chip, sleep=lambda s: None)
    return rf, chip, tag


def test_type_names_and_counter():
    assert ntag_type_name(bytes([0, 4, 4, 2, 1, 0, 0x11, 3])) == "NTAG215 (NXP)"
    assert ntag_type_name(bytes([0, 4, 4, 2, 1, 0, 0x0F, 3])) == "NTAG213 (NXP)"
    assert ntag_type_name(bytes([0, 4, 4, 2, 1, 0, 0x13, 3])) == "NTAG216 (NXP)"
    assert ntag_type_name(bytes([0, 4, 4, 2, 1, 0, 0x55, 3])) == "NTAG, unknown size (NXP)"
    assert ntag_type_name(bytes([0, 4, 1, 1, 1, 0, 0x18, 5])) == "NXP, not an NTAG"
    assert ntag_type_name(b"\x01\x02") == "unknown"
    assert counter_value(bytes([1, 2, 3])) == 0x030201
    assert counter_value(b"\x01\x02") is None


def test_nak_texts():
    assert "EEPROM" in nak_text(5) and "invalid argument" in nak_text(0) and "unknown" in nak_text(7)


def test_ident_commands():
    rf, _, tag = make()
    select_card(rf)
    assert ident(rf, GET_VERSION) == bytes([0, 4, 4, 2, 1, 0, 0x11, 3])
    assert ident(rf, READ_SIG, 0x00) == bytes(range(0xA0, 0xC0))
    with pytest.raises(NfcRefused):  # the counter is refused with a NAK; the tag still answers
        ident(rf, READ_CNT, 0x02)
    tag.counter_nak = False
    assert counter_value(ident(rf, READ_CNT, 0x02)) == 5


def test_ident_silent_tag_is_refused_and_missing_tag_is_absent():
    rf, _, tag = make()
    select_card(rf)
    tag.ident = False
    with pytest.raises(NfcRefused):  # silent, but it still answers WUPA
        ident(rf, GET_VERSION)
    rf2, _, _ = make(card=False)
    with pytest.raises(NfcTimeout):  # nothing there: the original error, not "refused"
        ident(rf2, GET_VERSION)


def test_write_nak_is_decoded():
    rf, _, tag = make()
    select_card(rf)
    for code, word in ((0, "invalid argument"), (1, "CRC"), (4, "authentication"), (5, "EEPROM"), (9, "unknown")):
        tag.write_nak = code
        with pytest.raises(NfcNak) as info:
            t2_write(rf, 20, bytes(4))
        assert info.value.code == code and word in str(info.value)
    tag.write_nak = None
    t2_write(rf, 20, bytes(4))  # a normal ACK still works


def test_cmd_ident(capsys):
    rf, _, tag = make()
    cli.cmd_ident(rf, None)
    sig = "".join("%02X" % b for b in range(0xA0, 0xC0))
    assert capsys.readouterr().out == (
        "Version: 00 04 04 02 01 00 11 03\nType: NTAG215 (NXP)\nSignature: %s\nCounter: not readable (NAK)\n" % sig)
    tag.counter_nak = False
    cli.cmd_ident(rf, None)
    assert "Counter: 5\n" in capsys.readouterr().out
    tag.ident = False
    with pytest.raises(NfcRefused):
        cli.cmd_ident(rf, None)
    rf2, _, _ = make(card=False)
    with pytest.raises(NfcTimeout):
        cli.cmd_ident(rf2, None)


def test_cmd_cycles_and_detect(capsys):
    rf, _, _ = make()
    class A:  # argparse namespace
        n = 5
    cli.cmd_cycles(rf, A)
    assert capsys.readouterr().out.startswith("5/5 cycles OK; per cycle avg ")
    cli.cmd_detect(rf, None)
    out = capsys.readouterr().out
    assert "ATQA: 4400  SAK: 0x00\n" in out and out.endswith("Type: NTAG215\n")
    rf2, _, _ = make(card=False)
    with pytest.raises(NfcTimeout):
        cli.cmd_detect(rf2, None)
    A.n = 3
    with pytest.raises(cli.NfcError):
        cli.cmd_cycles(rf2, A)
    assert capsys.readouterr().out.startswith("0/3 cycles OK\n  3 error(s), last: no response from card\n")
    A.n = 0
    with pytest.raises(cli.NfcError):
        cli.cmd_cycles(rf, A)
