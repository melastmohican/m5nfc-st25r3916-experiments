"""Applications for the ISO-DEP target (isodeptarget.py). No hardware, host-tested.

    Type4NdefApp  an NFC Forum Type 4 tag: SELECT the NDEF application (D2760000850101), the
                  capability container E103 and the NDEF file E104, READ BINARY and UPDATE BINARY.
    SniffApp      records every APDU in full. Class 00 commands (SELECT, READ BINARY) are served
                  by a real Type 4 NDEF tag, so the phone's own background tag reader finishes
                  instead of looping; everything else gets 90 00, so an app that sends a vendor
                  protocol (for example F0 D2 image packets) keeps talking.
"""
NDEF_FILE_SIZE = 1024  # NLEN (2 bytes) + message
MAX_READ = 0xF0  # MLe / MLc announced in the capability container
CAPTURE_SIZE = 16384
# After a screen refresh (F0 D4) the first BUSY_POLLS busy queries (F0 DE) answer "busy" (01), like a
# real panel that needs a second or two; then "idle" (00).
BUSY_POLLS = 3
# A real e-paper board answers the refresh command (F0 D4) with S(WTX) "wait longer" requests, five of
# them in the Flipper Zero app's model of a Good Display board, then 90 00.
REFRESH_WTX_REQUESTS = 5

_NDEF_AID = b"\xD2\x76\x00\x00\x85\x01\x01"
_NONE, _APP, _CC, _NDEF = 0, 1, 2, 3


def _sw(sw1, sw2):
    return bytes([sw1, sw2])


class Type4NdefApp:
    def __init__(self):
        self.cc = bytes([0x00, 0x0F,  # CCLEN
                         0x20,  # mapping version 2.0
                         0x00, MAX_READ, 0x00, MAX_READ,  # MLe, MLc
                         0x04, 0x06, 0xE1, 0x04,  # NDEF file control TLV: file E104
                         NDEF_FILE_SIZE >> 8, NDEF_FILE_SIZE & 0xFF,  # maximum NDEF file size
                         0x00, 0x00])  # read and write access: open
        self.ndef = bytearray(NDEF_FILE_SIZE)  # NLEN = 0: empty message
        self.updates = 0
        self._selected = _NONE
        self._app_selected = False

    def reset(self):
        self._selected = _NONE

    def take_wtx(self):
        return 0

    def apdu(self, c):
        n = len(c)
        if n < 4:
            return _sw(0x67, 0x00)
        if c[0] != 0x00:
            return _sw(0x6E, 0x00)
        ins, p1, p2 = c[1], c[2], c[3]

        if ins == 0xA4:  # SELECT
            lc = c[4] if n > 4 else 0
            if n >= 5 + lc and p1 == 0x04:  # by name: the NDEF application
                if lc == len(_NDEF_AID) and bytes(c[5:5 + lc]) == _NDEF_AID:
                    self._app_selected = True
                    self._selected = _APP
                    return _sw(0x90, 0x00)
                return _sw(0x6A, 0x82)
            if p1 == 0x00 and lc == 2 and n >= 7 and self._app_selected:  # by file id
                fid = c[5] << 8 | c[6]
                if fid == 0xE103:
                    self._selected = _CC
                    return _sw(0x90, 0x00)
                if fid == 0xE104:
                    self._selected = _NDEF
                    return _sw(0x90, 0x00)
                return _sw(0x6A, 0x82)
            return _sw(0x6A, 0x86)

        if ins == 0xB0:  # READ BINARY
            if self._selected != _CC and self._selected != _NDEF:
                return _sw(0x69, 0x86)
            if p1 & 0x80:
                return _sw(0x6B, 0x00)
            off = p1 << 8 | p2
            le = c[4] if n > 4 else 0
            if le == 0 or le > MAX_READ:
                le = MAX_READ
            data = self.cc if self._selected == _CC else self.ndef
            if off > len(data):
                return _sw(0x6B, 0x00)
            if off + le > len(data):
                le = len(data) - off
            return bytes(data[off:off + le]) + b"\x90\x00"

        if ins == 0xD6:  # UPDATE BINARY
            if self._selected != _NDEF:
                return _sw(0x69, 0x82) if self._selected == _CC else _sw(0x69, 0x86)
            if p1 & 0x80:
                return _sw(0x6B, 0x00)
            off = p1 << 8 | p2
            lc = c[4] if n > 4 else 0
            if n != 5 + lc or lc == 0 or lc > MAX_READ:
                return _sw(0x67, 0x00)
            if off + lc > NDEF_FILE_SIZE:
                return _sw(0x6B, 0x00)
            self.ndef[off:off + lc] = c[5:5 + lc]
            self.updates += 1
            return _sw(0x90, 0x00)
        return _sw(0x6D, 0x00)


class SniffApp:
    """Records every APDU. `records` is a list of (kind, bytes): kind 1 = command, 2 = response."""

    def __init__(self):
        self._ndef = Type4NdefApp()
        self.records = []
        self.used = 0
        self.apdus = 0
        self.sessions = 0
        self.full = False
        self._busy_left = 0
        self._wtx = 0

    def clear(self):
        self.records = []
        self.used = self.apdus = self.sessions = 0
        self._busy_left = self._wtx = 0
        self.full = False

    def reset(self):
        self.sessions += 1
        self._ndef.reset()
        self._wtx = 0

    def take_wtx(self):
        w, self._wtx = self._wtx, 0
        return w

    def _record(self, kind, data):
        if self.used + 3 + len(data) > CAPTURE_SIZE:
            self.full = True
            return
        self.records.append((kind, bytes(data)))
        self.used += 3 + len(data)

    def apdu(self, c):
        self.apdus += 1
        self._record(1, c)
        n = len(c)
        if n >= 4 and c[0] == 0x00:  # class 00: a real NDEF tag, so the phone's background reader stops asking
            resp = self._ndef.apdu(c)
            self._record(2, resp)
            return resp
        if n >= 4 and c[0] == 0xF0 and c[1] == 0xD4:  # screen refresh starts
            self._busy_left = BUSY_POLLS
            self._wtx = REFRESH_WTX_REQUESTS
        if n >= 4 and c[0] == 0xF0 and c[1] == 0xDE:  # vendor "get busy": 01 busy, 00 idle
            busy = self._busy_left > 0
            if busy:
                self._busy_left -= 1
            resp = bytes([0x01 if busy else 0x00, 0x90, 0x00])
        else:
            resp = b"\x90\x00"
        self._record(2, resp)
        return resp
