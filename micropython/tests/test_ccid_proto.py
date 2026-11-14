import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ccid_proto as cp  # noqa: E402


class FakeBackend:
    def __init__(self):
        self.st = cp.ICC_INACTIVE
        self.sent = []

    def status(self):
        return self.st

    def power_on(self):
        if self.st == cp.ICC_ABSENT:
            raise cp.CardAbsent()
        self.st = cp.ICC_ACTIVE
        return cp.contactless_atr()

    def power_off(self):
        if self.st == cp.ICC_ACTIVE:
            self.st = cp.ICC_INACTIVE

    def transmit(self, apdu):
        self.sent.append(bytes(apdu))
        return b"\x01\x02\x90\x00"


def msg(mtype, seq=7, payload=b"", slot=0):
    return struct.pack("<BIBBBBB", mtype, len(payload), slot, seq, 0, 0, 0) + payload


def parse(resp):
    mtype, length, slot, seq, status, error, spec = struct.unpack_from("<BIBBBBB", resp)
    return mtype, slot, seq, status, error, spec, resp[10:10 + length]


def test_descriptor_is_54_bytes():
    d = cp.class_descriptor()
    assert len(d) == 54 and d[0] == 0x36 and d[1] == 0x21


def test_atr_checksum_matches_pcsc_ultralight_atr():
    atr = cp.contactless_atr()
    assert atr.hex() == "3b8f8001804f0ca0000003060300030000000068"
    x = 0
    for b in atr[1:]:
        x ^= b
    assert x == 0


def test_slot_status_reports_icc_state():
    be = FakeBackend()
    slot = cp.CcidSlot(be)
    for st in (cp.ICC_ACTIVE, cp.ICC_INACTIVE, cp.ICC_ABSENT):
        be.st = st
        mtype, _, seq, status, err, _, data = parse(slot.handle(msg(cp.GET_SLOT_STATUS, seq=9)))
        assert (mtype, seq, status, err, data) == (cp.SLOT_STATUS, 9, st, 0, b"")


def test_power_on_returns_atr_and_activates():
    be = FakeBackend()
    mtype, _, seq, status, err, _, data = parse(cp.CcidSlot(be).handle(msg(cp.ICC_POWER_ON, seq=3)))
    assert mtype == cp.DATA_BLOCK and seq == 3 and status == cp.ICC_ACTIVE and err == 0
    assert data == cp.contactless_atr()


def test_power_on_without_card_fails_icc_mute():
    be = FakeBackend()
    be.st = cp.ICC_ABSENT
    mtype, _, _, status, err, _, _ = parse(cp.CcidSlot(be).handle(msg(cp.ICC_POWER_ON)))
    assert mtype == cp.DATA_BLOCK and status == cp.CMD_FAILED | cp.ICC_ABSENT and err == cp.ERR_ICC_MUTE


def test_xfr_block_passes_apdu_and_returns_data():
    be = FakeBackend()
    be.st = cp.ICC_ACTIVE
    apdu = bytes.fromhex("FFCA000000")
    mtype, _, _, status, _, _, data = parse(cp.CcidSlot(be).handle(msg(cp.XFR_BLOCK, payload=apdu)))
    assert be.sent == [apdu] and data == b"\x01\x02\x90\x00" and mtype == cp.DATA_BLOCK


def test_xfr_block_with_card_gone_fails():
    be = FakeBackend()
    be.st = cp.ICC_ABSENT
    _, _, _, status, err, _, _ = parse(cp.CcidSlot(be).handle(msg(cp.XFR_BLOCK, payload=b"\xff\xca\0\0\0")))
    assert status == cp.CMD_FAILED | cp.ICC_ABSENT and err == cp.ERR_ICC_MUTE


def test_unknown_command_and_bad_slot():
    slot = cp.CcidSlot(FakeBackend())
    _, _, _, status, err, _, _ = parse(slot.handle(msg(0x7F)))
    assert status & cp.CMD_FAILED and err == cp.ERR_CMD_NOT_SUPPORTED
    _, s, _, status, err, _, _ = parse(slot.handle(msg(cp.GET_SLOT_STATUS, slot=3)))
    assert s == 3 and status & cp.CMD_FAILED and err == cp.ERR_SLOT


def test_backend_exception_becomes_hardware_error():
    class Broken(FakeBackend):
        def status(self):
            raise OSError("i2c")
    _, _, _, status, err, _, _ = parse(cp.CcidSlot(Broken()).handle(msg(cp.GET_SLOT_STATUS)))
    assert status & cp.CMD_FAILED and err == cp.ERR_HW


def test_get_parameters_returns_t1_block():
    mtype, _, _, _, _, spec, data = parse(cp.CcidSlot(FakeBackend()).handle(msg(cp.GET_PARAMETERS)))
    assert mtype == cp.PARAMETERS and spec == 1 and len(data) == 7


def test_short_message_ignored_and_notify_format():
    assert cp.CcidSlot(FakeBackend()).handle(b"\x65\x00") is None
    assert cp.slot_change_message(True, True) == b"\x50\x03"
    assert cp.slot_change_message(False, True) == b"\x50\x02"
