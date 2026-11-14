# Phase C: Arduino-Pico + PlatformIO + USB CCID

Same goal as Phase B (Feather RP2350 as a CCID reader for the NFC unit), written in C++ with the Arduino-Pico core under PlatformIO.

Status: toolchain works (Feather RP2350 builds and flashes, prints over USB serial). Driver and CCID not started. See `../.agents/notes/PLAN.md` for the plan.

## Notes
- TinyUSB in the Pico SDK has no mainline CCID class; add a custom class driver through the app-driver hook.
- References: RevK's TinyUSB CCID driver, EspUsbDevice SmartCardReader example.
- Port register logic from `../python/nfc/st25r3916.py`; M5's official library targets M5Unified.
- Same gate test as Phase B: `pcsc_scan` must list the device.

## Build and flash
Board: `adafruit_feather_rp2350_hstx` (the Feather RP2350 with PSRAM; CircuitPython calls it `adafruit_feather_rp2350`), Arduino-Pico core via the maxgerhardt PlatformIO platform.

    pio run                  # build (first build downloads the toolchain, about 2 minutes)
    pio run -t upload        # needs the board in the bootloader (RP2350 drive)

Coming from MicroPython: `mpremote exec "import machine; machine.bootloader()"`, wait for the `RP2350` drive, then upload. Once Arduino firmware is on the board, Arduino-Pico's 1200-baud touch reboots it for uploads.
Qwiic I2C is I2C1: SDA GP2, SCL GP3.
