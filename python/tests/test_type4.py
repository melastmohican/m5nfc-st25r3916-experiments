"""Type 4 tag NDEF read (nfc/type4.py and `read-ndef` on an ISO-DEP card) against a scripted tag."""
import pytest

from nfc import ndef, type4
from nfc.st25r3916 import NfcProtocol, NfcRefused

URI = bytes.fromhex("D1 01 19 55 00") + b"http://www.elechouse.com"  # the PN532 emulate_tag_ndef message, 29 bytes


class FakeType4:
    """Answers the Type 4 command set at the APDU level; records every command."""

    def __init__(self, message=URI, mle=0x54, read_access=0x00, fail_on=None):
        self.file = len(message).to_bytes(2, "big") + message
        self.cc = (bytes.fromhex("00 0F 20") + mle.to_bytes(2, "big") + bytes.fromhex("00 FF 04 06 E1 04 00 80")
                   + bytes([read_access, 0x00]))
        self.fail_on = fail_on
        self.selected = None
        self.sent = []

    def transceive(self, apdu):
        self.sent.append(bytes(apdu))
        ins = apdu[1]
        if ins == 0xA4:
            data = apdu[5:5 + apdu[4]]
            self.selected = data
            ok = data in (type4.NDEF_APP, b"\xE1\x03", b"\xE1\x04")
            return b"\x90\x00" if ok and ins != self.fail_on else b"\x6A\x82"
        if ins == 0xB0:
            offset, le = (apdu[2] << 8) | apdu[3], apdu[4]
            if self.fail_on == 0xB0:
                return b"\x69\x82"
            source = self.cc if self.selected == b"\xE1\x03" else self.file
            return source[offset:offset + le] + b"\x90\x00"
        return b"\x6D\x00"


def test_reads_the_message_the_pn532_emulates():
    tag = FakeType4()
    msg = type4.read_ndef_message(tag)
    assert msg == URI
    assert [r.describe() for r in ndef.decode_message(msg)] == ["uri: http://www.elechouse.com"]
    # the exact command sequence: application, capability container, its 15 bytes, NDEF file, NLEN, the message
    assert tag.sent == [
        bytes.fromhex("00A4040007D276000085010100"), bytes.fromhex("00A4000C02E103"), bytes.fromhex("00B000000F"),
        bytes.fromhex("00A4000C02E104"), bytes.fromhex("00B0000002"), bytes.fromhex("00B000021D")]


def test_message_longer_than_one_read_chunk_is_read_in_pieces():
    long_msg = bytes(range(200))
    tag = FakeType4(message=long_msg, mle=0x54)  # 84-byte reads: 84 + 84 + 32
    assert type4.read_ndef_message(tag) == long_msg
    reads = [a for a in tag.sent if a[1] == 0xB0][2:]  # after the CC and NLEN reads
    assert [(a[2] << 8 | a[3], a[4]) for a in reads] == [(2, 84), (86, 84), (170, 32)]


def test_chunk_is_capped_at_255_when_mle_is_larger_or_zero():
    msg = bytes(300)
    for mle in (0x0400, 0):
        tag = FakeType4(message=msg, mle=mle)
        assert type4.read_ndef_message(tag) == msg
        assert max(a[4] for a in tag.sent if a[1] == 0xB0) == 255


def test_empty_file_gives_an_empty_message():
    assert type4.read_ndef_message(FakeType4(message=b"")) == b""


def test_status_word_other_than_9000_is_refused():
    with pytest.raises(NfcRefused, match="select NDEF application: status 6A 82"):
        type4.read_ndef_message(FakeType4(fail_on=0xA4))
    tag = FakeType4(fail_on=0xB0)
    with pytest.raises(NfcRefused, match="read capability container: status 69 82"):
        type4.read_ndef_message(tag)


def test_closed_read_access_is_refused():
    with pytest.raises(NfcRefused, match="read access 0xFF"):
        type4.read_ndef_message(FakeType4(read_access=0xFF))


def test_capability_container_without_the_ndef_tlv_is_a_protocol_error():
    tag = FakeType4()
    tag.cc = bytes(15)
    with pytest.raises(NfcProtocol):
        type4.read_ndef_message(tag)


def test_cmd_read_ndef_on_an_iso_dep_card(monkeypatch, capsys):
    from nfc import cli
    from nfc.nfca import Card
    from nfc.st25r3916 import St25r3916
    from tests.fakechip import FakeChip, FakeNtag215

    tag = FakeType4()
    events = []

    class Dep:
        def __init__(self, link):
            pass

        def activate(self):
            events.append("activate")

        def transceive(self, apdu):
            return tag.transceive(apdu)

        def deselect(self):
            events.append("deselect")

    monkeypatch.setattr(cli, "select_card", lambda rf: Card(bytes([8, 0x12, 0x34, 0x56]), bytes([4, 0]), 0x20))
    monkeypatch.setattr(cli, "IsoDep", Dep)
    rf = St25r3916(FakeChip(FakeNtag215()), sleep=lambda s: None)
    cli.cmd_read_ndef(rf, None, link=lambda rf: None)
    assert capsys.readouterr().out == "Type 4 tag (ISO-DEP)\nuri: http://www.elechouse.com\n"
    assert events == ["activate", "deselect"]
