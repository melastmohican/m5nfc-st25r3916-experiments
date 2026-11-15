// Glue between the USB CCID class (ccid_class.c) and the CCID message layer (CcidProto, NfcBackend).
#pragma once
#include "CcidProto.h"

namespace nfc {

// Call from the main loop: answers a pending host command, and sends a slot-change notification
// when a card appears or disappears (polled every 300 ms while idle).
void ccidService(ccid::Slot& slot, ccid::Backend& backend);

}  // namespace nfc
