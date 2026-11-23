"""Diagnostic: is a MIFARE Classic 1K fob a "magic" (UID-changeable) clone?

Run from python/:  uv run python tools/probe_magic.py

Read-only. Tests for the Gen1a backdoor: 0x40 as a 7-bit frame, then 0x43, each answered with a
4-bit ACK (0xA) by Gen1a cards and ignored by genuine NXP cards. Gen2/CUID clones do not answer
this; they only differ by accepting a write to block 0, which this tool never attempts.
Also prints WUPA/ATQA and the SAK from a normal select for reference.
"""
import os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from nfc.nfca import select_card
from nfc.st25r3916 import (CMD_TX_WITHOUT_CRC, IRQ_CRC, IRQ_PAR, REG_NUM_TX1, REG_NUM_TX2,
                           CMD_TX_WUPA, NfcError, St25r3916)
from nfc.transport import DEFAULT_URL, FtdiTransport


def raw(rf, data: bytes, bits: int, timeout_ms=10.0):
    rf._prepare(rx_crc=False, timeout_ms=timeout_ms)
    rf.write_reg(REG_NUM_TX1, bits >> 8)
    rf.write_reg(REG_NUM_TX2, bits & 0xFF)
    rf.load_fifo(data)
    rf.command(CMD_TX_WITHOUT_CRC)
    return rf._collect(timeout_ms, IRQ_CRC | IRQ_PAR, False)


def attempt(rf, label, wake):
    rf.field_reset()
    time.sleep(0.01)
    try:
        if wake:
            rf._prepare(rx_crc=False, timeout_ms=5)
            rf.command(CMD_TX_WUPA)
            rf._collect(5)
        r1 = raw(rf, b"\x40", 7)
    except NfcError as e:
        print(f"{label}: 0x40 -> no answer ({e})")
        return False
    print(f"{label}: 0x40 -> {r1.hex()}")
    try:
        r2 = raw(rf, b"\x43", 8)
        print(f"{label}: 0x43 -> {r2.hex()}")
    except NfcError as e:
        print(f"{label}: 0x43 -> no answer ({e})")
        return False
    return True


def main():
    rf = St25r3916(FtdiTransport(url=DEFAULT_URL, freq=100000))
    rf.init()
    rf.field_reset()
    card = select_card(rf)
    print(f"UID {card.uid_hex} ATQA {card.atqa.hex()} SAK {card.sak:02X}")
    hit = attempt(rf, "cold", False)
    hit = attempt(rf, "after WUPA", True) or hit
    print("RESULT:", "Gen1a magic backdoor answered" if hit else "no Gen1a backdoor (genuine, Gen2/CUID or other clone)")


main()
