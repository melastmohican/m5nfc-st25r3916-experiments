"""Start the CCID reader (and the serial command line) now, from RAM (no main.py). A power cycle brings the REPL back."""
from nfc.st25r3916 import St25r3916
from nfc.transport import MachineI2CTransport

import ccid_reader

rf = St25r3916(MachineI2CTransport())
rf.init()
rf.field_reset()
ccid_reader.run(rf)
