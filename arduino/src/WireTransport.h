// Arduino Wire transport for the ST25R3916 driver.
// On the Feather RP2350 `Wire` is I2C1 on GP2 (SDA) / GP3 (SCL), the Qwiic port.
// (`Wire1` is I2C0 on an unconnected pin: do not use it.)
#pragma once
#include <Arduino.h>
#include <Wire.h>

#include "St25r3916.h"

namespace nfc {

class WireTransport : public Transport {
 public:
  explicit WireTransport(TwoWire& wire = Wire, uint8_t addr = 0x50) : wire_(wire), addr_(addr) {}

  bool write(const uint8_t* data, size_t len) override {
    wire_.beginTransmission(addr_);
    wire_.write(data, len);
    return wire_.endTransmission() == 0;
  }

  bool exchange(const uint8_t* out, size_t outLen, uint8_t* in, size_t inLen) override {
    wire_.beginTransmission(addr_);
    wire_.write(out, outLen);
    if (wire_.endTransmission(false) != 0) return false;  // repeated start
    if (wire_.requestFrom((int)addr_, (int)inLen) != (int)inLen) return false;
    for (size_t i = 0; i < inLen; i++) in[i] = wire_.read();
    return true;
  }

  void sleepMs(uint32_t ms) override { delay(ms); }
  uint32_t nowMs() override { return millis(); }

 private:
  TwoWire& wire_;
  uint8_t addr_;
};

}  // namespace nfc
