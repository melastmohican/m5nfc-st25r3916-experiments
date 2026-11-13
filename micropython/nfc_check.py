"""Bring-up check on the Feather: chip ID, tag detect, NDEF read, first pages.

    mpremote cp -r nfc :            # once
    mpremote run nfc_check.py
"""
from nfc.nfca import select_card, t2_read
from nfc.ntag215 import Ntag215
from nfc.st25r3916 import St25r3916
from nfc.transport import MachineI2CTransport

rf = St25r3916(MachineI2CTransport())
rf.init()
rf.field_reset()
ic_type, rev = rf.identify()
print("ST25R3916 present: type=0x%02X revision=%d" % (ic_type, rev))

card = select_card(rf)
print("UID:  " + card.uid_hex)
print("ATQA: %s  SAK: 0x%02X" % (card.atqa.hex().upper(), card.sak))
tag = Ntag215(rf)
print("Type: NTAG215" if tag.is_ntag215() else "Type: not an NTAG215")
for rec in tag.read_ndef():
    print(rec.describe())
for page in range(0, 12, 4):
    print(page, t2_read(rf, page).hex(" "))
