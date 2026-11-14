"""Boot: start the CCID reader after a short window where the REPL still works.

Hold the BOOTSEL button during the 4 s window (LED on the Feather stays as is) to
skip the reader and keep the USB serial REPL for mpremote. The reader itself turns
the REPL port off, so this window is how you get back in.
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
