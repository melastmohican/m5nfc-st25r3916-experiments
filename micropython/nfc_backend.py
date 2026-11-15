"""NFC backend for ccid_proto: maps PC/SC pseudo-APDUs onto an NTAG/Ultralight tag.

    FF CA 00 00 Le          -> UID (Le=0: whole UID)
    FF B0 00 <page> <Le>    -> read Le bytes starting at <page>
    FF D6 00 <page> 04 <d>  -> write one 4-byte page

    FF 60 00 00 00         -> NTAG GET_VERSION (8 bytes)
    FF 3C 00 00 00         -> READ_SIG (32 bytes)
    FF 39 00 02 00         -> READ_CNT (3 bytes), 6A81 if the tag refuses

Lock/config pages are refused (Ntag215.write_page guard) with SW 6982.
"""
from ccid_proto import CardAbsent, ICC_ABSENT, ICC_ACTIVE, ICC_INACTIVE, contactless_atr
from nfc.nfca import select_card, t2_read
from nfc.ntag215 import TOTAL_PAGES, Ntag215
from nfc.ident import GET_VERSION, READ_CNT, READ_SIG, ident
from nfc.st25r3916 import NfcError, NfcNak, NfcRefused, NfcTimeout

SW_OK = b"\x90\x00"
SW_WRONG_LENGTH = b"\x67\x00"
SW_SECURITY = b"\x69\x82"
SW_MEMORY = b"\x65\x81"
SW_BAD_P1P2 = b"\x6b\x00"
SW_INS = b"\x6d\x00"
SW_CLA = b"\x6e\x00"
SW_NOT_FOUND = b"\x6a\x82"
SW_NOT_SUPPORTED = b"\x6a\x81"


class NfcBackend:
    def __init__(self, rf):
        self.rf = rf
        self.card = None  # set while the slot is powered

    def _probe(self):
        """Is a card in the field? (does not keep it selected for the host)"""
        try:
            select_card(self.rf)
            return True
        except NfcError:
            return False

    def status(self):
        if self.card is not None:
            try:
                t2_read(self.rf, 0)
                return ICC_ACTIVE
            except NfcError:
                self.card = None
                return ICC_ABSENT
        return ICC_INACTIVE if self._probe() else ICC_ABSENT

    def power_on(self):
        self.rf.field_reset()
        try:
            self.card = select_card(self.rf)
        except NfcError:
            self.card = None
            raise CardAbsent()
        return contactless_atr()

    def power_off(self):
        self.card = None
        self.rf.field_reset()

    def transmit(self, apdu):
        if self.card is None:
            raise CardAbsent()
        if len(apdu) < 4:
            return SW_WRONG_LENGTH
        cla, ins, p1, p2 = apdu[0], apdu[1], apdu[2], apdu[3]
        if cla != 0xFF:
            return SW_CLA
        try:
            if ins == 0xCA:
                return self._get_data(p1, apdu)
            if ins == 0xB0:
                return self._read_binary(p2, apdu)
            if ins == 0xD6:
                return self._update_binary(p2, apdu)
            if ins in (GET_VERSION, READ_SIG, READ_CNT):  # FF 60/3C 00 00 00, FF 39 00 02 00
                return self._ident(ins, p2)
            return SW_INS
        except NfcTimeout:
            self.card = None
            raise CardAbsent()

    def _ident(self, ins, addr):
        """NTAG GET_VERSION, READ_SIG, READ_CNT; P2 is the address byte of READ_SIG and READ_CNT."""
        try:
            return ident(self.rf, ins, addr) + SW_OK
        except NfcRefused:
            return SW_NOT_SUPPORTED  # not supported, or refused with a NAK

    def _get_data(self, p1, apdu):
        if p1 != 0x00:
            return SW_BAD_P1P2
        uid = self.card.uid
        le = apdu[4] if len(apdu) > 4 else 0
        return (uid[:le] if le else uid) + SW_OK

    def _read_binary(self, page, apdu):
        le = apdu[4] if len(apdu) > 4 else 0
        if le == 0:
            le = 16
        if page >= TOTAL_PAGES:
            return SW_NOT_FOUND
        out = b""
        p = page
        while len(out) < le and p < TOTAL_PAGES:
            out += t2_read(self.rf, p)
            p += 4
        return out[:le] + SW_OK

    def _update_binary(self, page, apdu):
        if len(apdu) < 9 or apdu[4] != 4:
            return SW_WRONG_LENGTH
        try:
            Ntag215(self.rf).write_page(page, bytes(apdu[5:9]))
        except NfcTimeout:
            raise
        except NfcNak as e:
            return SW_NOT_FOUND if e.code == 0 else SW_MEMORY  # NAK 0: the tag refused the page address
        except NfcError as e:
            return SW_SECURITY if "one-way" in str(e) or "read-only" in str(e) else SW_MEMORY
        return SW_OK
