"""Tag emulation without hardware (run from python/: uv run --with pytest python -m pytest -q): the Type 2 tag,
the ISO-DEP target with the Type 4 NDEF and sniffer apps (same checks as arduino/test), and the listener state
machine on a scripted chip. The listener's hot path goes through rf.batch()."""
from nfc import apduapps, isodeptarget, listen as L, type2tag
from nfc.apduapps import SniffApp, Type4NdefApp
from nfc.isodeptarget import ATS, IsoDepTarget
from nfc.type2tag import Type2Tag

UID = bytes([0x04, 0x11, 0x22, 0x33, 0x44, 0x55, 0x66])


def h(s):
    return bytes.fromhex(s.replace(" ", ""))


# ---- Type 2 tag --------------------------------------------------------------------------------
def test_type2_tag():
    tag = Type2Tag(UID)
    r = tag.handle(h("30 00"))
    out = r[0]
    assert len(out) == 16 and r[1] == -1
    assert out[0] == 0x04 and out[3] == 0x88 ^ 0x04 ^ 0x11 ^ 0x22
    assert out[4] == 0x33 and out[8] == 0x33 ^ 0x44 ^ 0x55 ^ 0x66
    assert out[12:15] == h("E1 10 3E")
    # a blank NTAG215: AUTH0 = FF (no password protection), PWD = FF FF FF FF
    out = tag.handle(h("30 83"))[0]
    assert out[3] == 0xFF and out[8] == 0xFF and out[11] == 0xFF
    # READ wraps past the last page
    out = tag.handle(h("30 86"))[0]
    assert out[4:16] == bytes(tag.memory()[:12])
    # READ out of range
    assert tag.handle(h("30 87")) == (None, 0x0, False)
    # FAST_READ pages 3..5
    out = tag.handle(h("3A 03 05"))[0]
    assert len(out) == 12 and out[0] == 0xE1
    assert tag.handle(h("3A 05 03")) == (None, 0x0, False)
    # WRITE: ACK, stored, marked changed
    assert not tag.is_changed(4)
    assert tag.handle(h("A2 04 03 0A D1 01")) == (None, 0xA, False) and tag.writes == 1
    assert tag.memory()[16] == 0x03 and tag.memory()[19] == 0x01 and tag.is_changed(4) and not tag.is_changed(5)
    # writing the same data again is still ACKed but does not mark the page again
    tag.clear_changed()
    assert tag.handle(h("A2 04 03 0A D1 01")) == (None, 0xA, False) and not tag.is_changed(4)
    # UID and lock pages are refused
    assert tag.handle(h("A2 00 01 02 03 04")) == (None, 0x0, False) and tag.memory()[0] == 0x04
    assert tag.handle(h("A2 02 01 02 03 04")) == (None, 0x0, False)
    assert tag.handle(h("A2 04 01 02 03")) == (None, 0x1, False)  # wrong length
    # GET_VERSION, READ_SIG
    out = tag.handle(h("60"))[0]
    assert len(out) == 8 and out[2] == 0x04 and out[6] == 0x11
    assert len(tag.handle(h("3C 00"))[0]) == 32
    # HLTA ends the session without a reply
    assert tag.handle(h("50 00")) == (None, -1, True)
    # unknown command: NAK
    assert tag.handle(h("99")) == (None, 0x0, False)


# ---- ISO-DEP target with the Type 4 NDEF app -----------------------------------------------------
def tx(t, hexs):
    reply, nibble, halt = t.handle(h(hexs))
    return reply or b""


SELECT_APP = "02 00 A4 04 00 07 D2 76 00 00 85 01 01"


def test_type4_through_isodep():
    app = Type4NdefApp()
    t = IsoDepTarget(app)
    assert tx(t, "02 00 A4 04 00") == b""  # before RATS: silent
    assert tx(t, "E0 80") == ATS and t.activated
    assert tx(t, SELECT_APP + " 00") == h("02 90 00")
    assert tx(t, "03 00 A4 00 0C 02 E1 03") == h("03 90 00")  # block number 1 is mirrored
    r = tx(t, "02 00 B0 00 00 0F")  # READ BINARY of the CC
    assert len(r) == 1 + 15 + 2 and r[:4] == h("02 00 0F 20") and r[-2] == 0x90
    assert tx(t, "03 00 A4 00 0C 02 E1 04") == h("03 90 00")  # the NDEF file
    assert tx(t, "02 00 B0 00 00 02") == h("02 00 00 90 00")
    # UPDATE BINARY: NLEN = 0 first, then the data, then the real NLEN (the usual order)
    assert tx(t, "03 00 D6 00 00 02 00 00") == h("03 90 00")
    assert tx(t, "02 00 D6 00 02 05 D1 01 01 54 00") == h("02 90 00")
    assert tx(t, "03 00 D6 00 00 02 00 05") == h("03 90 00")
    assert app.ndef[1] == 0x05 and app.ndef[2] == 0xD1 and app.updates == 3
    # an R(NAK) repeats the last I-block; an R(NAK) with a fresh block number just acknowledges
    assert tx(t, "B3") == h("03 90 00")
    assert tx(t, "B2") == h("A2")
    # chaining: two I-blocks with the chain bit set, then the last one
    app2 = Type4NdefApp()
    t2 = IsoDepTarget(app2)
    tx(t2, "E0 80")
    tx(t2, SELECT_APP)
    tx(t2, "03 00 A4 00 0C 02 E1 04")
    assert tx(t2, "12 00 D6 00 00") == h("A2")  # first part of a 4-byte UPDATE BINARY
    assert tx(t2, "13 04 AA BB") == h("A3")
    assert tx(t2, "02 CC DD") == h("02 90 00")
    assert app2.ndef[0] == 0xAA and app2.ndef[3] == 0xDD
    # errors: unknown file, wrong class, unknown instruction
    assert tx(t2, "03 00 A4 00 0C 02 3F 00") == h("03 6A 82")
    assert tx(t2, "02 80 00 00 00") == h("02 6E 00")
    assert tx(t2, "03 00 88 00 00") == h("03 6D 00")
    # DESELECT answers and halts; afterwards the layer is silent until the next RATS
    assert t2.handle(h("C2")) == (h("C2"), -1, True) and not t2.activated
    assert tx(t2, "02 00 B0 00 00 02") == b""
    assert tx(t2, "E0 80") == ATS


def test_sniffer():
    sniff = SniffApp()
    t = IsoDepTarget(sniff)
    tx(t, "E0 80")
    assert tx(t, "02 F0 D2 00 00 03 11 22 33") == h("02 90 00")
    assert tx(t, "03 F0 DE 00 00 01") == h("03 00 90 00")
    assert sniff.apdus == 2 and sniff.sessions == 1
    # a refresh (F0 D4) is answered with five S(WTX) requests, then the I-block (same block number)
    assert tx(t, "02 F0 D4 05 80 00") == h("F2 01")
    for _ in range(1, apduapps.REFRESH_WTX_REQUESTS):
        assert tx(t, "F2 01") == h("F2 01")
    assert tx(t, "F2 01") == h("02 90 00")
    # after a refresh the first busy polls answer busy (01), then idle (00)
    for i in range(apduapps.BUSY_POLLS):
        r = tx(t, "%02X F0 DE 00 00 01" % (0x03 ^ (i & 1)))
        assert len(r) == 4 and r[1] == 0x01
    r = tx(t, "03 F0 DE 00 00 01")
    assert len(r) == 4 and r[1] == 0x00
    assert sniff.apdus == 2 + 1 + apduapps.BUSY_POLLS + 1
    # class 00 commands are served by a real NDEF tag: SELECT the CC, READ BINARY gives its 15 bytes
    tx(t, SELECT_APP)
    tx(t, "03 00 A4 00 0C 02 E1 03")
    r = tx(t, "02 00 B0 00 00 0F")
    assert len(r) == 1 + 15 + 2 and r[1] == 0x00 and r[2] == 0x0F and r[-2] == 0x90
    # the capture keeps commands and answers in order
    sniff.clear()
    tx(t, "02 F0 D2 00 00 03 11 22 33")
    assert sniff.records == [(1, h("F0 D2 00 00 03 11 22 33")), (2, h("90 00"))]


# ---- listener on a scripted chip -------------------------------------------------------------------
class FakeRf:
    """Just the driver calls the Listener makes: registers in an array, interrupts from a queue."""

    def __init__(self):
        self.regs = bytearray(0x40)
        self.space_b = bytearray(0x40)
        self.regs[L.REG_AUX_DISPLAY] = L.AUX_EFD_O | L.AUX_OSC_OK  # reader field present, oscillator up
        self.irqs = []
        self.fifo_in = b""
        self.fifo_out = b""
        self.pt_mem = b""
        self.commands = []
        self.writes = []  # what the batched hot path wrote
        self.batch_calls = 0  # USB round trips on the FT232H
        self.t = self  # the listener writes the PT memory through rf.t.write

    def write(self, data):
        assert data[0] == L.OP_LOAD_PT_A
        self.pt_mem = bytes(data[1:])

    def init(self):
        pass

    def field_off(self):
        self.commands.append("field_off")

    def command(self, cmd, payload=b"", delay=0.0):
        self.commands.append(cmd)

    def read_reg(self, reg):
        return self.regs[reg]

    def write_reg(self, reg, val):
        self.regs[reg] = val & 0xFF

    def set_bits(self, reg, mask):
        self.regs[reg] |= mask

    def clear_bits(self, reg, mask):
        self.regs[reg] &= ~mask & 0xFF

    def read_space_b(self, reg):
        return self.space_b[reg]

    def write_space_b(self, reg, val):
        self.space_b[reg] = val

    def read_irqs(self):
        return self.irqs.pop(0) if self.irqs else 0

    def batch(self, ops):
        """What St25r3916.batch does on a transport without its own: the listener's hot path uses it."""
        self.batch_calls += 1
        out = []
        for op in ops:
            if op[0] == "w":
                self.writes.append(bytes(op[1]))
                if len(op[1]) == 2 and op[1][0] < 0x40:  # a register write
                    self.regs[op[1][0]] = op[1][1]
                out.append(None)
            elif op[1][0] == L.REG_IRQ_MAIN | L.OP_READ:
                out.append(self.read_irqs().to_bytes(4, "little"))
            elif op[1][0] == L.REG_FIFO_STATUS1 | L.OP_READ:
                n = len(self.fifo_in)
                out.append(bytes([n & 0xFF, (n >> 8 & 0x03) << 6]))
            elif op[1][0] == L.OP_FIFO_READ:
                data = self.read_fifo(op[2])
                out.append(data + bytes(op[2] - len(data)))  # a blind read past the end returns filler
            elif op[1][0] & L.OP_READ and op[1][0] < 0x80:  # any other register read
                reg = op[1][0] & 0x3F
                out.append(bytes(self.regs[reg:reg + op[2]]))
            else:
                raise AssertionError(op)
        return out

    def _write_mask(self, value):
        pass

    def fifo_count(self):
        return len(self.fifo_in)

    def read_fifo(self, n):
        data, self.fifo_in = self.fifo_in[:n], self.fifo_in[n:]
        return data

    def load_fifo(self, data):
        self.fifo_out = bytes(data)


def select_by_reader(rf, listener):
    rf.regs[L.REG_BITRATE_DETECT] = 0x00  # 106 kbit/s
    rf.irqs.append(L.I_NFCT)
    listener.poll()
    rf.regs[L.REG_PT_DISPLAY] = 0x02  # beyond idle: the chip answered the anticollision
    rf.irqs.append(L.I_RXE_PTA)
    listener.poll()
    rf.irqs.append(L.I_WU_A)
    listener.poll()


def names(listener):
    return [line.split("  ", 1)[1] for line in listener.log_lines()]


def test_listener_selected_by_a_reader_and_answers():
    rf = FakeRf()
    tag = Type2Tag(UID)
    lis = L.Listener(rf, tag)
    lis.start(UID, 0x0044, 0x00)
    # PT memory A: UID padded to 10 bytes, ATQA little endian, SAK of the three cascade levels
    assert rf.pt_mem == h("04 11 22 33 44 55 66 00 00 00 44 00 04 00 00")
    assert rf.regs[L.REG_MODE] == 0xC8  # target mode with bit rate detection
    assert lis.state == L.IDLE  # the reader field is already there

    select_by_reader(rf, lis)
    assert lis.state == L.ACTIVE
    # READ page 0 (30 00 + CRC_A that the chip leaves in the FIFO)
    rf.fifo_in = h("30 00 02 A8")
    rf.irqs.append(L.I_RXE)
    rf.writes.clear()
    lis.poll()
    assert lis.frames == 1
    # the reply, as one batch: clear the FIFO, load 16 bytes, the length in bits, transmit with CRC
    assert [w[0] for w in rf.writes] == [L.CMD_CLEAR_FIFO, L.OP_FIFO_LOAD, L.REG_NUM_TX1, L.REG_NUM_TX2, L.CMD_TX_WITH_CRC]
    assert len(rf.writes[1]) == 17 and rf.writes[1][1:5] == bytes([0x04, 0x11, 0x22, 0x88 ^ 0x04 ^ 0x11 ^ 0x22])
    assert rf.writes[2][1] == 0x00 and rf.writes[3][1] == 128  # 16 bytes = 128 bits
    # a WRITE is acknowledged with a 4-bit frame without CRC
    rf.fifo_in = h("A2 04 03 0A D1 01 00 00")
    rf.irqs.append(L.I_RXE)
    rf.writes.clear()
    lis.poll()
    assert rf.writes == [bytes([L.CMD_CLEAR_FIFO]), bytes([L.OP_FIFO_LOAD, 0x0A]), bytes([L.REG_NUM_TX1, 0]),
                         bytes([L.REG_NUM_TX2, 4]), bytes([L.CMD_TX_WITHOUT_CRC])]
    assert tag.writes == 1
    # HLTA: no reply, the listener halts
    rf.fifo_in = h("50 00 00 00")
    rf.irqs.append(L.I_RXE)
    lis.poll()
    assert lis.state == L.HALT
    # the reader switches its field off: back to OFF (the fake keeps a field, so it goes on to IDLE)
    rf.irqs.append(L.I_EOF)
    lis.poll()
    assert [n for n in names(lis) if n.endswith("OFF")]


def test_listener_drops_a_frame_with_a_bad_crc():
    rf = FakeRf()
    tag = Type2Tag(UID)
    lis = L.Listener(rf, tag)
    lis.start(UID)
    select_by_reader(rf, lis)
    rf.fifo_in = h("30 00 00 00")
    rf.irqs.append(L.I_RXE | L.I_CRC)
    rf.writes.clear()
    lis.poll()
    assert lis.frames == 0 and rf.writes == []
    assert any("rx error" in line for line in lis.log_lines())


def test_listener_reads_a_frame_longer_than_the_blind_read():
    """A frame longer than the blind FIFO read (a 16-byte WRITE-like frame) needs a second read."""
    rf = FakeRf()
    tag = Type2Tag(UID)
    lis = L.Listener(rf, tag)
    lis.start(UID)
    select_by_reader(rf, lis)
    frame = h("A2 04 03 0A D1 01")  # 6 bytes + 2 CRC = 8: inside the blind read
    long_frame = bytes([0x3A, 0x03, 0x05]) + bytes(12)  # 15 bytes + 2 CRC = 17 > 12: FAST_READ with junk
    rf.fifo_in = long_frame + b"\x00\x00"
    rf.irqs.append(L.I_RXE)
    lis.poll()
    assert lis.frames == 1  # read in full: the target saw all 15 bytes (and NAKed the odd FAST_READ)
    assert rf.fifo_in == b""


def test_select_to_active_takes_few_usb_round_trips():
    """A phone sends its first command about 5 ms after the select; each FT232H round trip is about 0.3 ms."""
    rf = FakeRf()
    lis = L.Listener(rf, Type2Tag(UID))
    lis.start(UID)
    before = rf.batch_calls
    select_by_reader(rf, lis)
    assert lis.state == L.ACTIVE
    assert rf.batch_calls - before <= 10
