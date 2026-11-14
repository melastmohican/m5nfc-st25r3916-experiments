"""Boot script: wait 4 s, then start the CCID reader.

During the 4 s window the USB serial REPL is still up, so `mpremote` can interrupt this script.
Holding BOOTSEL (after plugging in, not while plugging in) skips the reader and leaves the REPL.
Once the reader runs, the REPL port is off.
"""
import time

import rp2

_WINDOW_MS = 4000

start = time.ticks_ms()
skip = False
while time.ticks_diff(time.ticks_ms(), start) < _WINDOW_MS:
    if rp2.bootsel_button():
        skip = True
        break
    time.sleep_ms(20)

if not skip:
    from nfc.st25r3916 import St25r3916
    from nfc.transport import MachineI2CTransport

    import ccid_reader

    rf = St25r3916(MachineI2CTransport())
    rf.init()
    rf.field_reset()
    ccid_reader.run(rf)
