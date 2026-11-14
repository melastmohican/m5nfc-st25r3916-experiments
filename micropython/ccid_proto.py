"""CCID message handling, independent of USB and of the NFC chip.

CcidSlot.handle(msg) turns one PC_to_RDR bulk message into the RDR_to_PC reply.
It talks to a backend with these methods:

    status()          -> 0 card present+active, 1 present+inactive, 2 absent
    power_on()        -> ATR bytes; raises CardAbsent
    power_off()       -> None
    transmit(apdu)    -> response bytes incl. SW1 SW2; raises CardAbsent

Plain Python that runs unchanged on MicroPython and CPython (host tests).
"""
import struct

# PC_to_RDR
ICC_POWER_ON = 0x62
ICC_POWER_OFF = 0x63
GET_SLOT_STATUS = 0x65
XFR_BLOCK = 0x6F
GET_PARAMETERS = 0x6C
SET_PARAMETERS = 0x61
RESET_PARAMETERS = 0x6D
ESCAPE = 0x6B
# RDR_to_PC
DATA_BLOCK = 0x80
SLOT_STATUS = 0x81
PARAMETERS = 0x82
ESCAPE_RESP = 0x83
NOTIFY_SLOT_CHANGE = 0x50

ICC_ACTIVE = 0
ICC_INACTIVE = 1
ICC_ABSENT = 2
CMD_OK = 0x00
CMD_FAILED = 0x40

ERR_CMD_NOT_SUPPORTED = 0x00
ERR_SLOT = 0x05
ERR_ICC_MUTE = 0xFE
ERR_HW = 0xFB

MAX_MESSAGE = 271


class CardAbsent(Exception):
    pass


def class_descriptor():
    """54-byte CCID functional descriptor (CCID rev 1.10, short-APDU level)."""
    return struct.pack(
        "<BBHBBIIIBIIBIIIIIBBHBB",
        0x36,  # bLength
        0x21,  # bDescriptorType
        0x0110,  # bcdCCID
        0,  # bMaxSlotIndex: one slot
        0x07,  # bVoltageSupport: 5.0 / 3.0 / 1.8 V
        0x00000003,  # dwProtocols: T=0, T=1
        4000,  # dwDefaultClock (kHz)
        4000,  # dwMaximumClock
        0,  # bNumClockSupported
        10752,  # dwDataRate
        344064,  # dwMaxDataRate
        0,  # bNumDataRatesSupported
        254,  # dwMaxIFSD
        0,  # dwSynchProtocols
        0,  # dwMechanical
        0x000200BA,  # dwFeatures: auto param/voltage/clock/baud/PPS, short APDU level
        MAX_MESSAGE,  # dwMaxCCIDMessageLength
        0xFF,  # bClassGetResponse
        0xFF,  # bClassEnvelope
        0,  # wLcdLayout
        0,  # bPINSupport
        1,  # bMaxCCIDBusySlots
    )


def contactless_atr():
    """PC/SC part 3 ATR for an ISO 14443-A part 3 card (MIFARE Ultralight family)."""
    body = bytes(
        (0x8F, 0x80, 0x01, 0x80, 0x4F, 0x0C, 0xA0, 0x00, 0x00, 0x03, 0x06, 0x03, 0x00, 0x03)
    ) + bytes(4)
    tck = 0
    for b in body:
        tck ^= b
    return b"\x3b" + body + bytes((tck,))


def slot_change_message(present, changed):
    return bytes((NOTIFY_SLOT_CHANGE, (1 if present else 0) | (2 if changed else 0)))


class CcidSlot:
    def __init__(self, backend):
        self.backend = backend

    @staticmethod
    def _reply(mtype, slot, seq, status, error, specific, data=b""):
        return struct.pack("<BIBBBBB", mtype, len(data), slot, seq, status, error, specific) + data

    def _failed(self, mtype, slot, seq, icc, error):
        return self._reply(mtype, slot, seq, CMD_FAILED | icc, error, 0)

    def handle(self, msg):
        if len(msg) < 10:
            return None
        mtype, length, slot, seq = struct.unpack_from("<BIBB", msg)
        payload = msg[10 : 10 + length]
        data_type = DATA_BLOCK if mtype in (ICC_POWER_ON, XFR_BLOCK, ESCAPE) else SLOT_STATUS
        if mtype == ESCAPE:
            data_type = ESCAPE_RESP
        elif mtype in (GET_PARAMETERS, SET_PARAMETERS, RESET_PARAMETERS):
            data_type = PARAMETERS
        if slot != 0:
            return self._failed(data_type, slot, seq, ICC_ABSENT, ERR_SLOT)
        try:
            return self._dispatch(mtype, data_type, slot, seq, payload)
        except CardAbsent:
            return self._failed(data_type, slot, seq, ICC_ABSENT, ERR_ICC_MUTE)
        except Exception:  # noqa: BLE001  any chip/I2C failure must not kill the reader
            return self._failed(data_type, slot, seq, self._icc_status(), ERR_HW)

    def _icc_status(self):
        try:
            return self.backend.status()
        except Exception:  # noqa: BLE001
            return ICC_ABSENT

    def _dispatch(self, mtype, data_type, slot, seq, payload):
        be = self.backend
        if mtype == GET_SLOT_STATUS:
            return self._reply(SLOT_STATUS, slot, seq, CMD_OK | be.status(), 0, 0)
        if mtype == ICC_POWER_ON:
            return self._reply(DATA_BLOCK, slot, seq, CMD_OK | ICC_ACTIVE, 0, 0, be.power_on())
        if mtype == ICC_POWER_OFF:
            be.power_off()
            return self._reply(SLOT_STATUS, slot, seq, CMD_OK | be.status(), 0, 0)
        if mtype == XFR_BLOCK:
            if be.status() == ICC_ABSENT:
                raise CardAbsent()
            return self._reply(DATA_BLOCK, slot, seq, CMD_OK | ICC_ACTIVE, 0, 0, be.transmit(payload))
        if mtype in (GET_PARAMETERS, SET_PARAMETERS, RESET_PARAMETERS):
            # T=1 defaults; contactless cards ignore them
            params = bytes((0x11, 0x10, 0x00, 0x45, 0x00, 0xFE, 0x00))
            return self._reply(PARAMETERS, slot, seq, CMD_OK | be.status(), 0, 1, params)
        return self._failed(data_type, slot, seq, self._icc_status(), ERR_CMD_NOT_SUPPORTED)
