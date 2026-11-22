"""The FT232H batch call without hardware: pyftdi's real MPSSE builders, a stubbed USB handle."""
import pytest

pyftdi_i2c = pytest.importorskip("pyftdi.i2c")

from nfc.st25r3916 import St25r3916  # noqa: E402
from nfc.transport import FtdiTransport  # noqa: E402
from tests.fakechip import FakeChip, FakeNtag215  # noqa: E402

SEND_IMMEDIATE = 0x87


class StubFtdi:
    """Records what is written and answers with canned bytes, as one USB write and one read."""

    def __init__(self, answer):
        self.answer = bytes(answer)
        self.writes = []
        self.reads = []

    def write_data(self, data):
        self.writes.append(bytes(data))

    def read_data_bytes(self, size, attempt=1, request_gen=None):
        self.reads.append(size)
        return bytearray(self.answer[:size])


def make_transport(answer):
    tr = FtdiTransport.__new__(FtdiTransport)  # no device: only the batch builder is under test
    tr._addr = 0x50
    ctrl = pyftdi_i2c.I2cController()
    ctrl._ftdi = StubFtdi(answer)
    tr._ctrl = ctrl
    return tr, ctrl._ftdi


def test_batch_is_one_usb_write_and_one_read_with_the_right_answer_count():
    # write [DB]: address + 1 byte = 2 acks. exchange [5A] + read 4: address, 1 byte, address(R) = 3 acks, then 4 data bytes.
    acks_then_data = [0, 0] + [0, 0, 0] + [0xAA, 0xBB, 0xCC, 0xDD]
    tr, ftdi = make_transport(acks_then_data)
    res = tr.batch([("w", bytes([0xDB])), ("x", bytes([0x5A]), 4)])
    assert res == [None, bytes([0xAA, 0xBB, 0xCC, 0xDD])]
    assert len(ftdi.writes) == 1 and ftdi.writes[0][-1] == SEND_IMMEDIATE
    assert ftdi.reads == [9]
    # the 7-bit address 0x50 shows up as the write byte 0xA0 and the read byte 0xA1 in the command stream
    assert bytes([0xA0]) in ftdi.writes[0] and bytes([0xA1]) in ftdi.writes[0]


def test_a_nack_anywhere_in_the_batch_raises_after_it_ran():
    tr, ftdi = make_transport([0, 0, 0, 1, 0, 0, 0])  # 4 + 3 acknowledgements; the 4th is a NACK
    with pytest.raises(OSError, match="NACK"):
        tr.batch([("w", bytes([1, 2, 3])), ("w", bytes([4, 5]))])
    assert len(ftdi.writes) == 1  # the whole batch was sent first


def test_a_short_answer_raises():
    class Short(StubFtdi):
        def read_data_bytes(self, size, attempt=1, request_gen=None):
            return bytearray(b"\x00")

    tr, _ = make_transport([])
    tr._ctrl._ftdi = Short([])
    with pytest.raises(OSError, match="answered"):
        tr.batch([("w", bytes([1]))])


def test_driver_batch_falls_back_to_one_operation_at_a_time():
    chip = FakeChip(FakeNtag215())
    rf = St25r3916(chip, sleep=lambda s: None)
    ident = rf.read_regs(0x3F, 1)
    res = rf.batch([("w", bytes([0xDB])), ("x", bytes([0x3F | 0x40]), 1)])
    assert res == [None, ident]
