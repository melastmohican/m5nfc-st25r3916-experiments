"""Send an NDEF message to a peer-to-peer peer: the unit is the NFC-DEP initiator and the LLCP/SNEP client.

Run from python/:  uv run python tools/snep_send.py [text] [--uri URL] [seconds]

Peer: a PN532 in target mode running the Seeed library's p2p_raw (or p2p_with_ndef_library) example, in its
READ phase (a SNEP server; the sketch alternates write and read phases 3 s apart). Hold the two antennas
together. The unit
  1. selects the peer and sends ATR_REQ (NFC-DEP, LLCP general bytes),
  2. sends a SYMM PDU to tell the phases apart: a peer in its read phase answers SYMM, a peer in its write
     phase (it is a SNEP client too) answers CONNECT, and the unit then releases and retries,
  3. sends CONNECT to the SNEP service (urn:nfc:sn:snep, SAP 4) and waits for CC,
  4. sends the SNEP Put (one I PDU) and waits for the RR that acknowledges it,
  5. sends SYMM until the peer's I PDU with SNEP Success arrives, acknowledges it with RR and prints the result.

It reuses the PDU helpers of tools/snep_receive.py and the DEP helpers of tools/dep_probe.py; it changes no
existing code. The message must fit in one I PDU (the PN532 sketch reads 128 bytes).
"""
import argparse
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import dep_probe as dp  # noqa: E402
import snep_receive as sr  # noqa: E402
from nfc import ndef  # noqa: E402
from nfc.nfca import select_card  # noqa: E402
from nfc.st25r3916 import NfcError, St25r3916  # noqa: E402
from nfc.transport import FtdiTransport  # noqa: E402

OUR_SAP = 0x20  # an arbitrary source SAP for our data link connection


def exchange(rf, state: dict, out: bytes, wt_ms: float) -> tuple[int, int, int, bytes]:
    """One DEP exchange: send `out`, return the peer's parsed PDU (dsap, type, ssap, body)."""
    if out[:2] != sr.pdu(0, sr.SYMM, 0):
        print(f"  -> {dp.llcp_text(out)}")
    payload = dp.dep_exchange(rf, state["pni"], out, wt_ms, verbose=False)
    state["pni"] = (state["pni"] + 1) & 3
    if len(payload) < 2:
        raise NfcError("short PDU " + dp.h(payload))
    pdu = sr.parse(payload)
    if pdu[1] != sr.SYMM:
        print(f"  <- {dp.llcp_text(payload)}")
    return pdu


def session(rf, message: bytes, deadline: float) -> str:
    """One NFC-DEP link. Returns "done", "wrongphase" (the peer is a SNEP client too) or "error"."""
    card = select_card(rf)
    print(f"peer: UID {card.uid_hex}  SAK 0x{card.sak:02X}")
    if not card.sak & 0x40:
        print("SAK bit 6 is not set: the peer does not advertise NFC-DEP.")
        return "error"
    resp = rf.transceive(dp.atr_req(), tx_crc=True, rx_crc=True, timeout_ms=200.0)
    if not dp.show_atr_res(resp):
        return "error"
    wt_ms = 0.302 * 2 ** (resp[17] & 0x0F)
    print("LLCP link, SYMM exchanges are not printed:")
    state = {"pni": 0}

    # 1. SYMM: a server answers SYMM, a client with a CONNECT of its own answers CONNECT
    _, ptype, _, _ = exchange(rf, state, sr.pdu(0, sr.SYMM, 0), wt_ms)
    if ptype == sr.CONNECT:
        print("  the peer is in its write phase (a SNEP client): releasing to try again")
        return "wrongphase"
    if ptype != sr.SYMM:
        print("  unexpected answer to SYMM")
        return "error"

    # 2. CONNECT to the SNEP service by name, SAP 4
    name_tlv = bytes([0x06, len(sr.SNEP_NAME)]) + sr.SNEP_NAME
    _, ptype, peer_sap, _ = exchange(rf, state, sr.pdu(sr.SNEP_SAP, sr.CONNECT, OUR_SAP, name_tlv), wt_ms)
    for _ in range(5):  # a peer may answer SYMM first
        if ptype != sr.SYMM:
            break
        _, ptype, peer_sap, _ = exchange(rf, state, sr.pdu(0, sr.SYMM, 0), wt_ms)
    if ptype != sr.CC:
        print(f"  no CC (got type {ptype:X})")
        return "error"

    # 3. SNEP Put in one I PDU, N(S) 0, N(R) 0
    vs, vr = 0, 0
    put = sr.pdu(peer_sap, sr.I, OUR_SAP, bytes([(vs << 4) | vr]) + sr.snep(sr.SNEP_PUT, message))
    vs += 1
    _, ptype, _, body = exchange(rf, state, put, wt_ms)

    # 4. the RR acknowledges the Put; then poll with SYMM until the SNEP response arrives
    success = False
    for _ in range(40):
        if time.perf_counter() > deadline:
            break
        if ptype == sr.RR:
            print(f"  Put acknowledged, N(R) {body[0] & 0xF if body else '?'}")
        elif ptype == sr.I:
            ns, info = body[0] >> 4, body[1:]
            if ns == vr:
                vr = (vr + 1) & 0xF
                code = info[1] if len(info) > 1 else None
                print(f"  SNEP response {code:02X}" + (" = Success" if code == sr.SNEP_SUCCESS else ""))
                success = code == sr.SNEP_SUCCESS
                exchange(rf, state, sr.pdu(peer_sap, sr.RR, OUR_SAP, bytes([vr])), wt_ms)
                break
        elif ptype in (sr.DM, sr.DISC):
            print("  peer closed the link")
            return "error"
        time.sleep(0.02)
        _, ptype, _, body = exchange(rf, state, sr.pdu(0, sr.SYMM, 0), wt_ms)
    return "done" if success else "error"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("text", nargs="?", default="hello from the unit", help="text of the NDEF Text record to send")
    ap.add_argument("--uri", help="send a URI record instead of text")
    ap.add_argument("--seconds", type=float, default=60.0, help="give up after this long (default 60)")
    args = ap.parse_args()

    rec = ndef.uri_record(args.uri) if args.uri else ndef.text_record(args.text)
    message = ndef.encode_message([rec])
    print(f"sending NDEF message ({len(message)} bytes): {dp.h(message)}")

    t = FtdiTransport(freq=400_000)
    rf = St25r3916(t)
    result = "error"
    try:
        rf.init()
        deadline = time.perf_counter() + args.seconds
        attempt = 0
        while time.perf_counter() < deadline and result != "done":
            attempt += 1
            print(f"\n--- attempt {attempt} ---")
            rf.field_reset()
            try:
                result = session(rf, message, deadline)
            except NfcError as e:
                print(f"error: {e}")
                result = "error"
            if result != "done":
                sr.release(rf)
                time.sleep(0.5 if result == "wrongphase" else 1.0)
        if result != "done":
            print("\nSNEP Put not acknowledged by the peer")
            return 2
        print("\nthe peer accepted the NDEF message (SNEP Success)")
        return 0
    finally:
        try:
            rf.field_off()
        except Exception:
            pass
        t.close()


if __name__ == "__main__":
    sys.exit(main())
