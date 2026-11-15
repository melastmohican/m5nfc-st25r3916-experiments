// Zephyr I2C transport for the ST25R3916 driver (counterpart of arduino/src/WireTransport.h).
#pragma once
#include <zephyr/device.h>
#include <zephyr/drivers/i2c.h>
#include <zephyr/kernel.h>

#include "St25r3916.h"

namespace nfc {

class ZephyrTransport : public Transport {
 public:
  explicit ZephyrTransport(const struct device* bus, uint16_t addr = 0x50) : bus_(bus), addr_(addr) {}

  bool write(const uint8_t* data, size_t len) override {
    return i2c_write(bus_, data, len, addr_) == 0;
  }
  bool exchange(const uint8_t* out, size_t outLen, uint8_t* in, size_t inLen) override {
    return i2c_write_read(bus_, addr_, out, outLen, in, inLen) == 0;  // repeated start
  }
  void sleepMs(uint32_t ms) override { k_msleep(ms); }
  uint32_t nowMs() override { return k_uptime_get_32(); }

 private:
  const struct device* bus_;
  uint16_t addr_;
};

}  // namespace nfc
