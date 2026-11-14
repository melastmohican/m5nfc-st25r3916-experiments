// USB CCID (smart-card reader) class driver for Arduino-Pico.
//
// The core's USB stack has no CCID class, so this registers
//   1. an interface descriptor through USB.registerInterface() (the core builds the configuration
//      descriptor from these entries, next to the USB serial one), and
//   2. a TinyUSB class driver through usbd_app_driver_get_cb(), which the core only defines when
//      ENABLE_PICOTOOL_USB is set (it is not in this build; if it ever is, the two would clash).
//
// ccidBegin() must run from initVariant(). The core's main() runs USB.begin() and Serial.begin()
// before setup(), so by then the configuration descriptor is already built and a late
// registration is ignored (alternative: USB.disconnect(), register, USB.connect()).
//
// Threading: USB callbacks run in the TinyUSB task and only record that a message arrived. All NFC
// (I2C) work happens in ccidService(), which must be called from loop(), under the core's USB lock.
#pragma once
#include "CcidProto.h"

namespace nfc {

void ccidBegin();
// Call from loop(): answers a pending host command, and sends a slot-change notification when a
// card appears or disappears (polled every 300 ms while idle).
void ccidService(ccid::Slot& slot, ccid::Backend& backend);

}  // namespace nfc
