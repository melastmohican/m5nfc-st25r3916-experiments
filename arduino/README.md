# Phase C: Arduino-Pico + PlatformIO + USB CCID

Same goal as Phase B (Feather RP2350 as a CCID reader for the NFC unit), written in C++ with the Arduino-Pico core under PlatformIO.

Status: not started. See `../.agents/notes/PLAN.md` for the plan.

## Notes
- TinyUSB in the Pico SDK has no mainline CCID class; add a custom class driver through the app-driver hook.
- References: RevK's TinyUSB CCID driver, EspUsbDevice SmartCardReader example.
- Port register logic from `../python/nfc/st25r3916.py`; M5's official library targets M5Unified.
- Same gate test as Phase B: `pcsc_scan` must list the device.
