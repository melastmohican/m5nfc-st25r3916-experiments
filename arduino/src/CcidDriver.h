// USB CCID (smart-card reader) class driver for Arduino-Pico.
//
// The core's USB stack has no CCID class, so this registers
//   1. an interface descriptor through USB.registerInterface() (the core builds the configuration
//      descriptor from these entries, next to the USB serial one), and
//   2. a TinyUSB class driver through usbd_app_driver_get_cb(), which the core only defines when
//      ENABLE_PICOTOOL_USB is set (it is not in this build; if it ever is, the two would clash).
//
// Call ccidBegin() from initVariant(). The core's main() runs USB.begin() and Serial.begin()
// before setup(), so by then the configuration descriptor is already built and a late
// registration is ignored (alternative: USB.disconnect(), register, USB.connect()).
#pragma once

namespace nfc {

void ccidBegin();

}  // namespace nfc
