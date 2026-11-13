"""Gate test: a stub USB CCID (smart-card reader) device.

Reports one slot with no card present. Success = the Mac's PC/SC lists a reader
(`uv run --with pyscard python host_check.py`). Copy to the board and run:

    mpremote cp ccid_stub.py :ccid_stub.py
    mpremote exec "import ccid_stub"      # USB re-enumerates; the REPL port drops briefly

The built-in USB serial (REPL) stays enabled alongside the CCID interface.
"""
import struct

import machine
from micropython import const
from usb.device import get as usb_get
from usb.device.core import Interface

_CLASS_CCID = const(0x0B)
_EP_SIZE = const(64)

# PC_to_RDR message types
_ICC_POWER_ON = const(0x62)
_ICC_POWER_OFF = const(0x63)
_GET_SLOT_STATUS = const(0x65)
# RDR_to_PC message types
_DATA_BLOCK = const(0x80)
_SLOT_STATUS = const(0x81)
_NOTIFY_SLOT_CHANGE = const(0x50)

# bStatus: bits 0-1 = ICC status (2 = no ICC present), bits 6-7 = command status (1 = failed)
_ICC_ABSENT = const(0x02)
_FAILED = const(0x40)
_ERR_NOT_SUPPORTED = const(0x00)
_ERR_ICC_MUTE = const(0xFE)


def _ccid_class_descriptor():
    return struct.pack(
        "<BBHBBIIIBIIBIIIIIBBHBB",
        0x36,  # bLength
        0x21,  # bDescriptorType (CCID functional descriptor)
        0x0110,  # bcdCCID 1.10
        0,  # bMaxSlotIndex: one slot
        0x01,  # bVoltageSupport: 5.0 V
        0x00000003,  # dwProtocols: T=0 and T=1
        4000,  # dwDefaultClock (kHz)
        4000,  # dwMaximumClock
        0,  # bNumClockSupported
        10752,  # dwDataRate
        344064,  # dwMaxDataRate
        0,  # bNumDataRatesSupported
        254,  # dwMaxIFSD
        0,  # dwSynchProtocols
        0,  # dwMechanical
        0x00010030,  # dwFeatures: auto clock + auto baud, TPDU-level exchange
        271,  # dwMaxCCIDMessageLength (10 header + 261)
        0xFF,  # bClassGetResponse
        0xFF,  # bClassEnvelope
        0,  # wLcdLayout
        0,  # bPINSupport
        1,  # bMaxCCIDBusySlots
    )


class CcidStub(Interface):
    def __init__(self):
        super().__init__()
        self.ep_out = self.ep_in = self.ep_int = None
        self.rx = bytearray(_EP_SIZE)
        self.tx = bytearray(_EP_SIZE)
        self.notify = bytearray(2)

    def num_itfs(self):
        return 1

    def num_eps(self):
        # endpoint number N carries bulk OUT + bulk IN; N+1 carries interrupt IN
        return 2

    def desc_cfg(self, desc, itf_num, ep_num, strs):
        self.ep_out = ep_num
        self.ep_in = ep_num | 0x80
        self.ep_int = (ep_num + 1) | 0x80
        desc.interface(itf_num, 3, _CLASS_CCID, 0, 0)
        desc.extend(_ccid_class_descriptor())
        desc.endpoint(self.ep_out, "bulk", _EP_SIZE, 0)
        desc.endpoint(self.ep_in, "bulk", _EP_SIZE, 0)
        desc.endpoint(self.ep_int, "interrupt", 8, 16)

    def on_open(self):
        super().on_open()
        # RDR_to_PC_NotifySlotChange: slot 0 = no card, no change
        self.notify[0] = _NOTIFY_SLOT_CHANGE
        self.notify[1] = 0x00
        self.submit_xfer(self.ep_int, self.notify)
        self.submit_xfer(self.ep_out, self.rx, self._on_rx)

    def _on_rx(self, ep, result, n):
        if n >= 10:
            self._handle(bytes(self.rx[:n]))
        if self.is_open():
            self.submit_xfer(self.ep_out, self.rx, self._on_rx)

    def _handle(self, msg):
        mtype, _length, slot, seq = struct.unpack_from("<BIBB", msg)
        if mtype == _GET_SLOT_STATUS or mtype == _ICC_POWER_OFF:
            resp = struct.pack("<BIBBBBB", _SLOT_STATUS, 0, slot, seq, _ICC_ABSENT, 0, 0)
        elif mtype == _ICC_POWER_ON:
            resp = struct.pack(
                "<BIBBBBB", _DATA_BLOCK, 0, slot, seq, _FAILED | _ICC_ABSENT, _ERR_ICC_MUTE, 0
            )
        else:
            resp = struct.pack(
                "<BIBBBBB", _SLOT_STATUS, 0, slot, seq, _FAILED | _ICC_ABSENT, _ERR_NOT_SUPPORTED, 0
            )
        self.tx[: len(resp)] = resp
        self.submit_xfer(self.ep_in, memoryview(self.tx)[: len(resp)])


stub = CcidStub()
usb_get().init(stub, builtin_driver=True, product_str="NFC CCID stub", manufacturer_str="Experiment")
