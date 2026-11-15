"""PC/SC pass-through to an ISO-DEP card, and the ATR built from its ATS."""
import builtins
import os
import sys
import time

builtins.Reader = object  # MicroPython ignores annotations; CPython evaluates this one

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import nfc_backend  # noqa: E402
import pytest  # noqa: E402
from ccid_proto import (CardAbsent, ICC_ABSENT, ICC_ACTIVE, CcidSlot, GET_SLOT_STATUS,  # noqa: E402
                        ICC_POWER_ON, XFR_BLOCK, atr_from_ats)
from fakechip import FakeChip, FakeNtag215  # noqa: E402
from nfc.nfca import Card  # noqa: E402
from nfc.st25r3916 import St25r3916  # noqa: E402
from test_isodep import Script  # noqa: E402

DESFIRE_ATS = "06 75 77 81 02 80"
ORCA = Card(bytes([4, 0x82, 0x81, 0xFA, 0xE2, 0x40, 0x80]), bytes([0x44, 0x03]), 0x20)


def test_atr_from_ats():
    assert atr_from_ats(bytes.fromhex(DESFIRE_ATS.replace(" ", ""))) == bytes([0x3B, 0x81, 0x80, 0x01, 0x80, 0x80])
    assert atr_from_ats(bytes([0x02, 0x00])) == bytes([0x3B, 0x80, 0x80, 0x01, 0x01])
    assert atr_from_ats(b"\x06") is None and atr_from_ats(b"") is None


def backend(monkeypatch, script, card=ORCA):
    rf = St25r3916(FakeChip(FakeNtag215()), sleep=lambda s: None, monotonic=time.monotonic)
    monkeypatch.setattr(nfc_backend, "select_card", lambda rf: card)
    return nfc_backend.NfcBackend(rf, link=lambda rf: script)


def test_power_on_builds_the_atr_from_the_ats(monkeypatch):
    script = Script().ok("e0 80", DESFIRE_ATS)
    be = backend(monkeypatch, script)
    assert be.power_on() == bytes([0x3B, 0x81, 0x80, 0x01, 0x80, 0x80])
    assert be.status() == ICC_ACTIVE  # an activated ISO-DEP card is not probed
    assert script.done()


def test_apdus_pass_through_and_ff_ca_is_answered_by_the_reader(monkeypatch):
    script = (Script().ok("e0 80", DESFIRE_ATS)
              .ok("02 90 60 00 00 00", "02 04 01 01 01 00 18 05 91 af")
              .ok("03 90 af 00 00 00", "03 04 01 01 01 04 18 05 91 af")
              .ok("02 90 af 00 00 00", "02 04 82 81 fa e2 40 80 ba 64 95 c6 40 25 15 91 00"))
    be = backend(monkeypatch, script)
    be.power_on()
    assert be.transmit(bytes.fromhex("FFCA000000")) == bytes([4, 0x82, 0x81, 0xFA, 0xE2, 0x40, 0x80]) + b"\x90\x00"
    assert be.transmit(bytes.fromhex("FFCA000004")) == bytes([4, 0x82, 0x81, 0xFA]) + b"\x90\x00"  # Le = 4
    assert be.transmit(bytes.fromhex("9060000000")) == bytes.fromhex("04010101001805") + b"\x91\xaf"
    assert be.transmit(bytes.fromhex("90AF000000")) == bytes.fromhex("04010101041805") + b"\x91\xaf"
    assert be.transmit(bytes.fromhex("90AF000000")).endswith(b"\x91\x00")
    assert script.done()  # the FF CA APDUs never reached the card


def test_a_lost_card_ends_the_session(monkeypatch):
    script = Script().ok("e0 80", DESFIRE_ATS).timeout("02 90 60 00 00 00")
    for _ in range(9):
        script.timeout("b2")
    be = backend(monkeypatch, script)
    be.power_on()
    with pytest.raises(CardAbsent):
        be.transmit(bytes.fromhex("9060000000"))
    assert be.card is None and be.dep is None


def test_power_off_deselects(monkeypatch):
    script = Script().ok("e0 80", DESFIRE_ATS).ok("c2", "c2")
    be = backend(monkeypatch, script)
    be.power_on()
    be.power_off()
    assert be.dep is None and script.done()


def test_through_the_ccid_slot(monkeypatch):
    script = Script().ok("e0 80", DESFIRE_ATS).ok("02 90 6a 00 00 00", "02 ff ff ff 91 00")
    slot = CcidSlot(backend(monkeypatch, script))

    def msg(t, payload=b""):
        return bytes([t]) + len(payload).to_bytes(4, "little") + bytes([0, 7, 0, 0, 0]) + payload

    on = slot.handle(msg(ICC_POWER_ON))
    assert on[10:] == bytes([0x3B, 0x81, 0x80, 0x01, 0x80, 0x80])
    resp = slot.handle(msg(XFR_BLOCK, bytes.fromhex("906A000000")))
    assert resp[10:] == bytes([0xFF, 0xFF, 0xFF, 0x91, 0x00])
    assert slot.handle(msg(GET_SLOT_STATUS))[7] == ICC_ACTIVE


def test_type2_tags_still_get_the_ultralight_atr(monkeypatch):
    rf = St25r3916(FakeChip(FakeNtag215()), sleep=lambda s: None, monotonic=time.monotonic)
    be = nfc_backend.NfcBackend(rf)
    atr = be.power_on()
    assert atr[0] == 0x3B and len(atr) == 20 and be.dep is None
    assert be.transmit(bytes.fromhex("FFCA000000")).endswith(b"\x90\x00")
    assert be.status() in (0, 1) and ICC_ABSENT == 2
