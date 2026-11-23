"""Receive an NDEF message from a peer-to-peer peer: the unit is the NFC-DEP initiator and the LLCP/SNEP server.

Run from python/:  uv run python tools/snep_receive.py [seconds]

Peer: a PN532 in target mode running the Seeed library's p2p_raw (or p2p_with_ndef_library) example, which
is a SNEP client. Hold the two antennas together. The unit
  1. selects the peer and sends ATR_REQ (NFC-DEP, LLCP general bytes),
  2. keeps the LLCP link alive with SYMM PDUs, each carried in one DEP_REQ,
  3. answers CONNECT to the SNEP service (urn:nfc:sn:snep, SAP 4) with CC,
  4. receives the SNEP Put (acknowledging each I PDU, answering a fragment with Continue), and answers
     it with SNEP Success,
  5. answers DISC with DM, releases the link with RLS_REQ and prints the NDEF records it received.

It reuses select_card(), transceive(), nfc.ndef and the DEP helpers of tools/dep_probe.py; it changes no
existing code.
"""
import argparse
import sys
import time
from collections import deque
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import dep_probe as dp  # noqa: E402
from nfc import ndef  # noqa: E402
from nfc.nfca import select_card  # noqa: E402
from nfc.st25r3916 import NfcError, St25r3916  # noqa: E402
from nfc.transport import FtdiTransport  # noqa: E402

SYMM, PAX, UI, CONNECT, DISC, CC, DM, FRMR, I, RR, RNR = 0x0, 0x1, 0x3, 0x4, 0x5, 0x6, 0x7, 0x8, 0xC, 0xD, 0xE
SNEP_SAP = 4
SNEP_NAME = b"urn:nfc:sn:snep"
SNEP_PUT, SNEP_CONTINUE, SNEP_SUCCESS, SNEP_REJECT = 0x02, 0x80, 0x81, 0xFF


def pdu(dsap: int, ptype: int, ssap: int, body: bytes = b"") -> bytes:
    return bytes([(dsap << 2) | (ptype >> 2), ((ptype & 3) << 6) | ssap]) + body


def parse(p: bytes) -> tuple[int, int, int, bytes]:
    return p[0] >> 2, ((p[0] & 3) << 2) | (p[1] >> 6), p[1] & 0x3F, p[2:]


def snep(code: int, payload: bytes = b"") -> bytes:
    return bytes([0x10, code]) + len(payload).to_bytes(4, "big") + payload


class Server:
    """LLCP data link connection to our SNEP server, one peer at a time."""

    def __init__(self) -> None:
        self.queue: deque = deque()  # PDUs to send; ("I", info) is built at send time with the live counters
        self.connected = False
        self.peer_sap = 0
        self.vs = self.vr = 0
        self.buf = b""
        self.continue_sent = False
        self.message: bytes | None = None
        self.closed = False

    def next_out(self) -> bytes:
        if not self.queue:
            return pdu(0, SYMM, 0)
        item = self.queue.popleft()
        if isinstance(item, tuple):  # an I PDU: add the sequence byte
            seq = (self.vs << 4) | self.vr
            self.vs = (self.vs + 1) & 0xF
            return pdu(self.peer_sap, I, SNEP_SAP, bytes([seq]) + item[1])
        return item

    def on_pdu(self, p: bytes) -> str:
        if len(p) < 2:
            return "short PDU " + dp.h(p)
        dsap, ptype, ssap, body = parse(p)
        if ptype == SYMM:
            return ""
        if ptype == CONNECT:
            wants_snep = dsap == SNEP_SAP or SNEP_NAME in body
            if wants_snep and not self.connected:
                self.connected, self.peer_sap, self.vs, self.vr, self.buf = True, ssap, 0, 0, b""
                self.queue.append(pdu(ssap, CC, SNEP_SAP))
                return f"CONNECT ssap {ssap} -> dsap {dsap}: accepting, answering CC"
            self.queue.append(pdu(ssap, DM, dsap, b"\x03"))  # no such service
            return f"CONNECT dsap {dsap}: refusing with DM"
        if ptype == I and self.connected and ssap == self.peer_sap:
            ns, nr, info = body[0] >> 4, body[0] & 0xF, body[1:]
            if ns != self.vr:
                self.queue.append(pdu(self.peer_sap, RR, SNEP_SAP, bytes([self.vr])))
                return f"I with N(S) {ns}, expected {self.vr}: re-acknowledging"
            self.vr = (self.vr + 1) & 0xF
            self.buf += info
            return f"I N(S) {ns} N(R) {nr}, {len(info)} bytes: {self.on_snep()}"
        if ptype == RR:
            return f"RR N(R) {body[0] & 0xF if body else '?'}"
        if ptype == DISC:
            self.queue.append(pdu(ssap, DM, dsap, b"\x00"))
            self.connected, self.closed = False, True
            return "DISC: answering DM"
        if ptype == DM:
            self.connected, self.closed = False, True
            return f"DM reason {body[0] if body else '?'}"
        return f"ptype {ptype:X}: ignored  [{dp.h(p)}]"

    def on_snep(self) -> str:
        if len(self.buf) < 6:
            self.queue.append(pdu(self.peer_sap, RR, SNEP_SAP, bytes([self.vr])))
            return "SNEP header incomplete, RR"
        _, code, length = self.buf[0], self.buf[1], int.from_bytes(self.buf[2:6], "big")
        if code != SNEP_PUT:
            self.queue.append(("I", snep(SNEP_REJECT)))
            return f"SNEP request {code:02X} is not Put: rejecting"
        if len(self.buf) < 6 + length:
            if not self.continue_sent:
                self.continue_sent = True
                self.queue.append(("I", snep(SNEP_CONTINUE)))
                return f"SNEP Put, {len(self.buf) - 6}/{length} bytes: Continue"
            self.queue.append(pdu(self.peer_sap, RR, SNEP_SAP, bytes([self.vr])))
            return f"SNEP Put, {len(self.buf) - 6}/{length} bytes: RR"
        self.message = self.buf[6:6 + length]
        self.queue.append(("I", snep(SNEP_SUCCESS)))
        return f"SNEP Put complete ({length} bytes): answering Success"


def session(rf, srv: Server, idle_s: float, deadline: float) -> str:
    """One NFC-DEP link. Returns "done", "idle" (peer sent only SYMM for idle_s) or "error"."""
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

    pni, turns, last_news, done_at = 0, 0, time.perf_counter(), None
    while time.perf_counter() < deadline:
        out = srv.next_out()
        if out[:2] != pdu(0, SYMM, 0):
            print(f"  -> {dp.llcp_text(out)}")
        try:
            payload = dp.dep_exchange(rf, pni, out, wt_ms, verbose=False)
        except NfcError as e:
            if srv.message is not None:  # the peer finished its SNEP write and dropped the link
                print("  peer left the link after the SNEP Success")
                return "done"
            print(f"  DEP error: {e}")
            return "error"
        pni, turns = (pni + 1) & 3, turns + 1
        what = srv.on_pdu(payload)
        if what:
            print(f"  <- {what}")
            last_news = time.perf_counter()
        if srv.message is not None and done_at is None:
            done_at = time.perf_counter()
        if srv.closed or (done_at and not srv.queue and time.perf_counter() - done_at > 2.0):
            return "done"
        if not srv.connected and time.perf_counter() - last_news > idle_s:
            print(f"  peer silent for {idle_s:.0f} s ({turns} exchanges): releasing to try again")
            return "idle"
        time.sleep(0.02)
    return "idle"


def release(rf) -> None:
    try:
        rls = rf.transceive(bytes([0xF0, 0x03, 0xD4, 0x0A]), tx_crc=True, rx_crc=True, timeout_ms=200.0)
        print(f"RLS_RES <- {dp.h(rls)}")
    except NfcError as e:
        print(f"RLS_RES <- error: {e}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("seconds", nargs="?", type=float, default=60.0, help="give up after this long (default 60)")
    ap.add_argument("--idle", type=float, default=6.0,
                    help="release and retry when the peer sends only SYMM for this long (default 6)")
    args = ap.parse_args()

    t = FtdiTransport(freq=400_000)
    rf = St25r3916(t)
    srv = Server()
    try:
        rf.init()
        deadline = time.perf_counter() + args.seconds
        attempt = 0
        while time.perf_counter() < deadline:
            attempt += 1
            print(f"\n--- attempt {attempt} ---")
            rf.field_reset()
            srv = Server()
            try:
                result = session(rf, srv, args.idle, deadline)
            except NfcError as e:
                print(f"error: {e}")
                result = "error"
            if srv.message is None:
                release(rf)
            if srv.message is not None:
                break
            time.sleep(0.5 if result == "idle" else 1.0)

        if srv.message is None:
            print("\nno SNEP message received")
            return 2
        print(f"\nreceived NDEF message ({len(srv.message)} bytes): {dp.h(srv.message)}")
        for rec in ndef.decode_message(srv.message):
            if rec.tnf == 2:  # media type record: show the MIME type and a text rendering
                print(f"  media {rec.type.decode('ascii', 'replace')}: {rec.payload.decode('utf-8', 'replace')!r}")
            else:
                print(f"  {rec.describe()}")
        return 0
    finally:
        try:
            rf.field_off()
        except Exception:
            pass
        t.close()


if __name__ == "__main__":
    sys.exit(main())
