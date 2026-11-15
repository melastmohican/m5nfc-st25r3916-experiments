"""USB CCID reader on the Feather RP2350: PC/SC <-> M5Stack NFC unit (ST25R3916).

All NFC/I2C work happens in the main loop (run()), never in USB callbacks, so the
chip is only ever touched from one place. Callbacks just hand over messages.

The board's USB serial port (the console CDC, builtin_driver=True) carries the serial command
line from cli.py while this runs; the REPL is not available then, and Ctrl-C is turned off so a
stray character cannot stop the reader. Errors are appended to /ccid.log.
"""
import sys
import time

import micropython
import select

from usb.device import get as usb_get
from usb.device.core import Interface

import ccid_proto as cp
from cli import Cli

_EP_SIZE = 64
_POLL_MS = 300


def _log(text):
    try:
        with open("/ccid.log", "a") as f:
            f.write(text + "\n")
    except OSError:
        pass


class CcidInterface(Interface):
    def __init__(self, slot):
        super().__init__()
        self.slot = slot
        self.ep_out = self.ep_in = self.ep_int = None
        self.rx = bytearray(_EP_SIZE)
        self.tx = bytearray(_EP_SIZE)
        self.notify = bytearray(2)
        self.pending = None
        self.notify_wanted = None  # (present, changed) still to be sent
        self.last_present = None

    def num_itfs(self):
        return 1

    def num_eps(self):
        return 2  # N: bulk OUT + bulk IN, N+1: interrupt IN

    def desc_cfg(self, desc, itf_num, ep_num, strs):
        self.ep_out = ep_num
        self.ep_in = ep_num | 0x80
        self.ep_int = (ep_num + 1) | 0x80
        desc.interface(itf_num, 3, 0x0B, 0, 0)
        desc.extend(cp.class_descriptor())
        desc.endpoint(self.ep_out, "bulk", _EP_SIZE, 0)
        desc.endpoint(self.ep_in, "bulk", _EP_SIZE, 0)
        desc.endpoint(self.ep_int, "interrupt", 8, 16)

    def on_open(self):
        super().on_open()
        self.last_present = None  # forces a notify with the current state
        self._arm_rx()

    def _arm_rx(self):
        try:
            self.submit_xfer(self.ep_out, self.rx, self._on_rx)
        except RuntimeError:
            pass

    def _on_rx(self, ep, result, n):
        if n >= 10:
            self.pending = bytes(self.rx[:n])
        else:
            self._arm_rx()

    # ---- called from the main loop only ----
    def service(self):
        msg, self.pending = self.pending, None
        if msg is not None:
            resp = None
            try:
                resp = self.slot.handle(msg)
            except Exception as e:  # noqa: BLE001
                _log("handle: %r" % (e,))
            if resp:
                self.tx[: len(resp)] = resp
                try:
                    self.submit_xfer(self.ep_in, memoryview(self.tx)[: len(resp)])
                except RuntimeError as e:
                    _log("tx: %r" % (e,))
            self._arm_rx()
        if self.notify_wanted is not None and not self.xfer_pending(self.ep_int):
            present, changed = self.notify_wanted
            self.notify_wanted = None
            m = cp.slot_change_message(present, changed)
            self.notify[0], self.notify[1] = m[0], m[1]
            try:
                self.submit_xfer(self.ep_int, self.notify)
            except RuntimeError as e:
                _log("notify: %r" % (e,))

    def poll_card(self, backend):
        """Update the slot state and queue a notification if presence changed."""
        present = backend.status() != cp.ICC_ABSENT
        if present != self.last_present:
            self.last_present = present
            self.notify_wanted = (present, True)


def run(rf):
    from nfc_backend import NfcBackend

    backend = NfcBackend(rf)
    dev = CcidInterface(cp.CcidSlot(backend))
    cli = Cli(rf, sys.stdout.write)
    console = select.poll()
    console.register(sys.stdin, select.POLLIN)
    micropython.kbd_intr(-1)  # Ctrl-C arrives as a character instead of raising KeyboardInterrupt
    usb_get().init(
        dev,
        builtin_driver=True,
        manufacturer_str="Experiment",
        product_str="NFC CCID reader",
    )
    last_poll = time.ticks_ms()
    while True:
        try:
            dev.service()
            for _ in range(64):  # typed characters for the serial command line
                if not console.poll(0):
                    break
                cli.feed(sys.stdin.read(1))
            now = time.ticks_ms()
            if dev.is_open() and dev.pending is None and time.ticks_diff(now, last_poll) >= _POLL_MS:
                last_poll = now
                dev.poll_card(backend)
        except Exception as e:  # noqa: BLE001
            _log("loop: %r" % (e,))
        time.sleep_ms(2)
