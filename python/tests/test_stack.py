import pytest

from nfc import ndef
from nfc.nfca import crc_a, select_card
from nfc.ntag215 import Ntag215
from nfc.st25r3916 import NfcError, NfcTimeout, St25r3916
from tests.fakechip import UID, FakeChip, FakeNtag215


def make(card=True):
    chip = FakeChip(FakeNtag215() if card else None)
    rf = St25r3916(chip, sleep=lambda s: None)
    return chip, rf


def test_crc_a_known_vectors():
    assert crc_a(bytes([0x00, 0x00])) == bytes([0xA0, 0x1E])
    assert crc_a(bytes([0x12, 0x34])) == bytes([0x26, 0xCF])


def test_frame_formats():
    chip, rf = make()
    rf.read_reg(0x1A)
    rf.write_reg(0x02, 0x48)
    rf.write_space_b(0x2A, 0x80)
    rf.read_space_b(0x2A)
    rf.command(0xDB)
    rf.load_fifo(b"\x01\x02")
    assert chip.log == [
        ("x", b"\x5A", 1),
        ("w", b"\x02\x48"),
        ("w", b"\xFB\x2A\x80"),
        ("x", b"\xFB\x6A", 1),
        ("w", b"\xDB"),
        ("w", b"\x80\x01\x02"),
    ]


def test_identify_rejects_missing_chip():
    chip, rf = make()
    chip.regs[0x3F] = 0xFF
    with pytest.raises(NfcError):
        rf.identify()


def test_init_runs_and_enables_field():
    chip, rf = make()
    rf.init()
    assert chip.regs[0x02] & 0x48 == 0x48


def test_oscillator_failure_is_reported():
    chip, rf = make()
    chip.regs[0x02] = 0  # forces oscillator enable path
    t = iter(range(1000))
    rf = St25r3916(chip, sleep=lambda s: None, monotonic=lambda: next(t) * 0.01)
    chip.write = lambda data, orig=chip.write: None if data[0] == 0x02 else orig(data)
    with pytest.raises(NfcError, match="oscillator"):
        rf.init()


def test_select_and_dump():
    chip, rf = make()
    rf.init()
    card = select_card(rf)
    assert card.uid == UID and card.sak == 0x00 and card.atqa == b"\x44\x00"
    tag = Ntag215(rf)
    assert tag.is_ntag215()
    assert len(tag.dump()) == 135 * 4


def test_no_card_times_out():
    chip, rf = make(card=False)
    rf.init()
    with pytest.raises(NfcTimeout):
        select_card(rf)


def test_select_retries_after_field_reset():
    chip, rf = make()
    rf.init()
    calls = {"short": 0, "reset": 0}
    real_short, real_reset = rf.send_short, rf.field_reset

    def flaky_short(*a, **k):
        calls["short"] += 1
        if calls["short"] == 1:  # tag stuck in ACTIVE: first WUPA unanswered
            raise NfcTimeout("no response from card")
        return real_short(*a, **k)

    def counting_reset(*a, **k):
        calls["reset"] += 1
        return real_reset(*a, **k)

    rf.send_short, rf.field_reset = flaky_short, counting_reset
    assert select_card(rf).uid == UID
    assert calls["reset"] == 1


def test_ndef_roundtrip_through_stack():
    chip, rf = make()
    rf.init()
    select_card(rf)
    tag = Ntag215(rf)
    records = [ndef.text_record("hello NFC"), ndef.uri_record("https://example.com/x")]
    assert tag.write_ndef(records) > 0
    got = tag.read_ndef()
    assert [r.describe() for r in got] == [
        "text[en]: hello NFC",
        "uri: https://example.com/x",
    ]


def test_rewriting_same_ndef_writes_nothing():
    chip, rf = make()
    rf.init()
    select_card(rf)
    tag = Ntag215(rf)
    recs = [ndef.text_record("same")]
    tag.write_ndef(recs)
    assert tag.write_ndef(recs) == 0


def test_write_guard_on_config_pages():
    chip, rf = make()
    rf.init()
    select_card(rf)
    tag = Ntag215(rf)
    for page in (0, 1, 3, 130, 133):
        with pytest.raises(NfcError):
            tag.write_page(page, b"\x00" * 4)
    with pytest.raises(NfcError):
        tag.write_page(0, b"\x00" * 4, force=True)  # UID never writable
    tag.write_page(3, bytes([0xE1, 0x10, 0x3E, 0x00]), force=True)
    assert chip.card.writes == [3]


def test_ndef_large_message_uses_long_tlv():
    msg = ndef.encode_message([ndef.text_record("x" * 300)])
    blob = ndef.wrap_tlv(msg)
    assert blob[:2] == b"\x03\xFF"
    assert ndef.decode_message(ndef.find_message(blob))[0].payload.endswith(b"x" * 300)


def test_ndef_too_big_for_tag():
    chip, rf = make()
    rf.init()
    select_card(rf)
    with pytest.raises(NfcError, match="holds"):
        Ntag215(rf).write_ndef([ndef.text_record("x" * 600)])
