"""Diagnostic: does a card couple to the reader, and does it answer on NFC-A or NFC-B?

Run from python/:  uv run python tools/probe_nfc_b.py [quick]

Phase A (60 s): NFC-A mode. Every 0.4 s it measures the antenna amplitude and phase (a card or an
antenna loop near the unit moves them) and tries WUPA. Phase B (60 s): NFC-B mode, using the register
recipe of M5Stack's configure_nfc_b() for this unit (src/unit/unit_ST25R3916_nfcb.cpp in
m5stack/M5Unit-NFC), with REQB and WUPB (05 00 00, 05 00 08). The Mac speaks cues ("say") for what to
put on the unit when; `quick` runs 4 s per phase without cues.

Written for the US-passport experiment (see the backlog): the NTAG215 answered in phase A and moved the
phase by about 16, the passport moved it by up to about 9 but answered in neither mode. There was no
Type B card to check the NFC-B half against, so a negative NFC-B result is weaker than the NFC-A one.
ST25TB/SRI512 cards use Type B signalling but answer INITIATE (06 00), not REQB.
"""
import subprocess, sys, time
import os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from nfc.st25r3916 import (CMD_CLEAR_FIFO, CMD_INITIAL_FIELD_ON, CMD_TX_WITH_CRC, CMD_TX_WUPA, IRQ_RXE,
                           REG_AUX, REG_NUM_TX1, REG_NUM_TX2, REG_OP_CTRL, NfcError, St25r3916)
from nfc.transport import DEFAULT_URL, FtdiTransport

CMD_MEASURE_AMPLITUDE, CMD_MEASURE_PHASE, REG_AD_RESULT = 0xD3, 0xD9, 0x25
# M5Unit-NFC configure_nfc_b(): register file for NFC-B at 106 kbit/s (A space, 0x00..0x2B)
A_SPACE = [0x07, 0x3C, 0xCB, 0x14, 0x00, 0x00, 0x00, 0x00, 0x5D, 0x38, 0x00, 0x04, 0x3D, 0x00, 0x00, 0x07,
           0x10, 0x0D, 0x23, 0x00, 0x58, 0x80, 0x85, 0xA6, 0x0F, 0x7B]  # 0x00..0x19 (0x1A.. are IRQ status)
A_SPACE_TX = {0x28: 0x70, 0x29: 0x5F, 0x2A: 0x13, 0x2B: 0x02, 0x2C: 0xD0}
B_SPACE = {0x05: 0xC4, 0x06: 0x14, 0x0B: 0x0C, 0x0C: 0x1B, 0x0D: 0x00, 0x0F: 0x00, 0x15: 0x00,
           0x28: 0x10, 0x29: 0x7C, 0x2A: 0x80}


def measure(rf, cmd):
    rf.read_irqs()
    rf.command(cmd)
    time.sleep(0.03)
    return rf.read_reg(REG_AD_RESULT)


def send(rf, frame, wait=0.03):
    rf.command(CMD_CLEAR_FIFO)
    rf.read_irqs()
    rf.clear_bits(REG_AUX, 0x80)  # check CRC on receive
    rf.write_reg(REG_NUM_TX1, (len(frame) * 8) >> 8)
    rf.write_reg(REG_NUM_TX2, (len(frame) * 8) & 0xFF)
    rf.load_fifo(frame)
    rf.command(CMD_TX_WITH_CRC)
    end, irq = time.monotonic() + wait, 0
    while time.monotonic() < end:
        irq |= rf.read_irqs()
        if irq & IRQ_RXE:
            break
    n = rf.fifo_count()
    return irq, (rf.read_fifo(n) if n else b"")


def to_nfc_b(rf):
    for reg, val in enumerate(A_SPACE):
        rf.write_reg(reg, val)
    for reg, val in A_SPACE_TX.items():
        rf.write_reg(reg, val)
    for reg, val in B_SPACE.items():
        rf.write_space_b(reg, val)
    rf.command(CMD_INITIAL_FIELD_ON, delay=0.005)
    rf.set_bits(REG_OP_CTRL, 0x48)
    print("NFC-B mode: MODE=0x%02X OP_CONTROL=0x%02X" % (rf.read_reg(0x03), rf.read_reg(REG_OP_CTRL)), flush=True)


def say(text):
    print("CUE:", text, flush=True)
    subprocess.Popen(["say", text])


def phase(rf, name, seconds, try_fn, cues=()):
    t0 = time.monotonic()
    print("== %s for %d s" % (name, seconds), flush=True)
    cues = list(cues)
    while time.monotonic() - t0 < seconds:
        while cues and time.monotonic() - t0 >= cues[0][0]:
            say(cues.pop(0)[1])
        amp, ph = measure(rf, CMD_MEASURE_AMPLITUDE), measure(rf, CMD_MEASURE_PHASE)
        got = try_fn(rf)
        print("%5.1fs amp=%3d phase=%3d %s" % (time.monotonic() - t0, amp, ph, got), flush=True)
        time.sleep(0.1)


def try_a(rf):
    try:
        return "WUPA -> ATQA %s" % rf.send_short(CMD_TX_WUPA).hex().upper()
    except NfcError:
        return ""


def try_b(rf):
    out = []
    for label, frame in (("REQB", b"\x05\x00\x00"), ("WUPB", b"\x05\x00\x08")):
        irq, data = send(rf, frame)
        if data:
            out.append("%s -> %s" % (label, data.hex().upper()))
    return " | ".join(out)


quick = len(sys.argv) > 1 and sys.argv[1] == "quick"
secs = 4 if quick else 60
t = FtdiTransport(url=DEFAULT_URL, freq=100_000)
rf = St25r3916(t)
rf.init()
CUES_A = [(0, "Phase A. Keep the unit empty."), (10, "Now put the N T A G tag flat on the unit."),
          (25, "Remove the tag. Unit empty."), (35, "Now put the open passport on the unit, back cover down, and slide it slowly all over."),
          (58, "Phase A ends.")]
CUES_B = [(0, "Phase B. Remove the passport. Unit empty."), (10, "Passport on the unit again, open, back cover down. Slide it slowly all over."),
          (58, "Test finished.")]
phase(rf, "Phase A, NFC-A", secs, try_a, [] if quick else CUES_A)
to_nfc_b(rf)
phase(rf, "Phase B, NFC-B", secs, try_b, [] if quick else CUES_B)
rf.field_off()
t.close()
print("done", flush=True)
