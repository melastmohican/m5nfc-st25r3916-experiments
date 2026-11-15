"""NTAG identification, NAK decoding and the PC/SC pseudo-APDUs against the fake chip."""
import builtins
import os
import sys
import time

builtins.Reader = object  # MicroPython ignores annotations; CPython evaluates this one

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import pytest  # noqa: E402
from cli import Cli  # noqa: E402
from fakechip import FakeChip, FakeNtag215  # noqa: E402
from nfc.ident import (GET_VERSION, READ_CNT, READ_SIG, counter_value, ident,  # noqa: E402
                       ntag_type_name)
from nfc.nfca import nak_text, select_card, t2_write  # noqa: E402
from nfc.st25r3916 import NfcNak, NfcRefused, NfcTimeout, St25r3916  # noqa: E402
from nfc_backend import NfcBackend  # noqa: E402


def make(card=True):
    tag = FakeNtag215() if card else None
    chip = FakeChip(tag)
    rf = St25r3916(chip, sleep=lambda s: None, monotonic=time.monotonic)
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


def run(cli, out, line):
    out.clear()
    cli.run(line)
    return "".join(out)


def test_cli_ident():
    rf, _, tag = make()
    out = []
    cli = Cli(rf, out.append)
    text = run(cli, out, "ident")
    sig = "".join("%02X" % b for b in range(0xA0, 0xC0))
    assert text == ("Version: 00 04 04 02 01 00 11 03\nType: NTAG215 (NXP)\nSignature: %s\n"
                    "Counter: not readable (NAK)\nok\n" % sig)
    tag.counter_nak = False
    assert "Counter: 5\n" in run(cli, out, "ident")
    tag.ident = False
    assert run(cli, out, "ident") == "error: the tag refused the command or does not support it\n"
    rf2, _, _ = make(card=False)
    assert run(Cli(rf2, out.append), out, "ident") == "error: no response from card\n"


def apdu(backend, data):
    return backend.transmit(bytes.fromhex(data))


def test_backend_ident_apdus_and_nak_status_words():
    rf, _, tag = make()
    backend = NfcBackend(rf)
    backend.power_on()
    assert apdu(backend, "FF60000000") == bytes([0, 4, 4, 2, 1, 0, 0x11, 3]) + b"\x90\x00"
    assert apdu(backend, "FF3C000000") == bytes(range(0xA0, 0xC0)) + b"\x90\x00"
    assert apdu(backend, "FF39000200") == b"\x6a\x81"  # counter refused
    tag.counter_nak = False
    assert apdu(backend, "FF39000200") == bytes([5, 0, 0]) + b"\x90\x00"
    tag.ident = False
    assert apdu(backend, "FF60000000") == b"\x6a\x81"  # tag still answers WUPA: not supported
    # a refused write: NAK 0 is a bad page address, any other NAK a memory failure
    tag.write_nak = 0
    assert apdu(backend, "FFD600140400000000") == b"\x6a\x82"
    tag.write_nak = 5
    assert apdu(backend, "FFD600140400000000") == b"\x65\x81"
