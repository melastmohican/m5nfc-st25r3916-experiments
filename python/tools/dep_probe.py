"""Diagnostic: can the unit act as an NFC-DEP initiator (peer-to-peer) against a PN532 target?

Run from python/:  uv run python tools/dep_probe.py [tries]

The peer is a PN532 sitting in target mode (the Seeed PN532 library's p2p_raw example: it calls
TgInitAsTarget, which answers WUPA/anticollision itself, advertises SEL_RES 0x40 = "NFC-DEP", and then
waits for ATR_REQ). Hold the two antennas together.

Steps, each printed with the raw frames:
  1. select the peer like any Type A card (UID starts 08 for a PN532 target, SAK 0x40)
  2. ATR_REQ  (F0 LEN D4 00 ...) with LLCP general bytes, expect ATR_RES (F0 LEN D5 01 ...)
  3. DEP_REQ carrying an LLCP SYMM PDU (00 00), a few times with PNI 0,1,2,..., to see whether the link
     stays alive
  4. RLS_REQ (F0 03 D4 0A), expect RLS_RES

It changes no existing code; it only uses St25r3916.transceive() and nfca.select_card().
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from nfc.nfca import select_card  # noqa: E402
from nfc.st25r3916 import NfcError, St25r3916  # noqa: E402
from nfc.transport import FtdiTransport  # noqa: E402

NFCID3 = bytes(range(0x30, 0x3A))  # 10 arbitrary bytes identifying us
# LLCP general bytes: magic 46 66 6D, VERSION 1.0 (01 01 10), LTO 1.5 s (04 01 96)
GB = bytes.fromhex("46666D" "010110" "040196")
LR = {0: 64, 1: 128, 2: 192, 3: 254}


def h(data: bytes) -> str:
    return data.hex(" ").upper() if data else "(none)"


def atr_req() -> bytes:
    pp = 0x30 | 0x02  # LR=3 (254 byte payload), general bytes present
    body = bytes([0xD4, 0x00]) + NFCID3 + bytes([0x00, 0x00, 0x00, pp]) + GB  # DID, BS, BR, PP, Gb
    return bytes([0xF0, len(body) + 1]) + body


def dep_req(pni: int, data: bytes) -> bytes:
    body = bytes([0xD4, 0x06, pni & 0x03]) + data  # PFB: information PDU, no chaining, PNI
    return bytes([0xF0, len(body) + 1]) + body


LLCP_TYPES = {0x0: "SYMM", 0x1: "PAX", 0x2: "AGF", 0x3: "UI", 0x4: "CONNECT", 0x5: "DISC", 0x6: "CC", 0x7: "DM",
              0x8: "FRMR", 0x9: "SNL", 0xC: "I", 0xD: "RR", 0xE: "RNR"}


def llcp_text(pdu: bytes) -> str:
    if len(pdu) < 2:
        return h(pdu)
    ptype = ((pdu[0] & 0x03) << 2) | (pdu[1] >> 6)
    return (f"{LLCP_TYPES.get(ptype, '?')} dsap {pdu[0] >> 2} ssap {pdu[1] & 0x3F}"
            f"{'  data ' + h(pdu[2:]) if len(pdu) > 2 else ''}   [{h(pdu)}]")


def dep_exchange(rf, pni: int, data: bytes, wt_ms: float, max_turns: int = 8, verbose: bool = True) -> bytes:
    """Send one information PDU and return the peer's information payload.

    Answers the peer's S-PDUs on the way: RTOX (timeout extension: echo the value, wait RTOX times
    longer) and ATN (echo). An R-PDU or anything else raises NfcError.
    """
    frame, timeout = dep_req(pni, data), max(2 * wt_ms, 100.0)
    for _ in range(max_turns):
        t0 = time.perf_counter()
        resp = rf.transceive(frame, tx_crc=True, rx_crc=True, timeout_ms=timeout)
        if verbose:
            print(f"  -> {h(frame)}\n  <- {h(resp)}   ({(time.perf_counter() - t0) * 1000:.1f} ms)")
        if len(resp) < 5 or resp[0] != 0xF0 or resp[2:4] != b"\xD5\x07":
            raise NfcError("not a DEP_RES")
        pfb = resp[4]
        kind = pfb >> 5
        if kind == 0b100:  # supervisory
            if pfb & 0x10:  # RTOX
                value = resp[5] & 0x3F
                if verbose:
                    print(f"  peer asks for a timeout extension x{value}: answering and waiting {value * wt_ms:.0f} ms")
                frame = bytes([0xF0, 0x05, 0xD4, 0x06, 0x90, value])
                timeout = max(value * wt_ms * 1.5, 100.0)
            else:  # ATN
                if verbose:
                    print("  peer sent ATN: echoing")
                frame, timeout = bytes([0xF0, 0x04, 0xD4, 0x06, 0x80]), max(2 * wt_ms, 100.0)
            continue
        if kind == 0b000:  # information
            return bytes(resp[5:])
        raise NfcError(f"unexpected PFB {pfb:02X} (R-PDU or unknown)")
    raise NfcError("too many S-PDU turns")


def show_atr_res(frame: bytes) -> bool:
    if len(frame) < 19 or frame[0] != 0xF0 or frame[2:4] != b"\xD5\x01":
        print("  not an ATR_RES")
        return False
    to, pp = frame[16], frame[17]
    print(f"  ATR_RES: length byte {frame[1]}, NFCID3 {h(frame[4:14])}")
    print(f"           DID {frame[14]:02X} BS {frame[15]:02X} BR {frame[16]:02X} TO {frame[17]:02X} PP {frame[18]:02X}"
          if len(frame) >= 19 else "")
    print(f"           WT {frame[17] & 0x0F} (response timeout about {0.302 * 2 ** (frame[17] & 0x0F):.1f} ms), "
          f"max payload {LR[(frame[18] >> 4) & 3]}, general bytes {h(frame[19:])}")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("tries", nargs="?", type=int, default=3, help="LLCP SYMM exchanges after the ATR (default 3)")
    args = ap.parse_args()

    t = FtdiTransport(freq=400_000)
    rf = St25r3916(t)
    try:
        rf.init()
        rf.field_reset()
        card = select_card(rf)
        print(f"UID:  {card.uid_hex}   ATQA: {card.atqa.hex().upper()}   SAK: 0x{card.sak:02X}")
        if not card.sak & 0x40:
            print("SAK bit 6 is not set: the peer does not advertise NFC-DEP.")
            return 1
        print("peer advertises NFC-DEP (SAK bit 6)")

        frame = atr_req()
        print(f"\nATR_REQ  -> {h(frame)}")
        t0 = time.perf_counter()
        resp = rf.transceive(frame, tx_crc=True, rx_crc=True, timeout_ms=200.0)
        print(f"ATR_RES  <- {h(resp)}   ({(time.perf_counter() - t0) * 1000:.1f} ms)")
        if not show_atr_res(resp):
            return 1

        wt_ms = 0.302 * 2 ** (resp[17] & 0x0F)  # the peer's response timeout from the ATR_RES
        for pni in range(args.tries):
            print(f"\nDEP_REQ  PNI {pni & 3}: LLCP SYMM 00 00")
            try:
                payload = dep_exchange(rf, pni, b"\x00\x00", wt_ms)
            except NfcError as e:
                print(f"  error: {e}")
                break
            print(f"  peer's PDU: {llcp_text(payload)}")
            time.sleep(0.05)

        frame = bytes([0xF0, 0x03, 0xD4, 0x0A])
        print(f"\nRLS_REQ  -> {h(frame)}")
        try:
            resp = rf.transceive(frame, tx_crc=True, rx_crc=True, timeout_ms=200.0)
            print(f"RLS_RES  <- {h(resp)}")
        except NfcError as e:
            print(f"RLS_RES  <- error: {e}")
        return 0
    except NfcError as e:
        print(f"error: {e}")
        return 1
    finally:
        try:
            rf.field_off()
        except Exception:
            pass
        t.close()


if __name__ == "__main__":
    sys.exit(main())
