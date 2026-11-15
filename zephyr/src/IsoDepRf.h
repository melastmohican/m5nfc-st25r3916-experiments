// IsoDepLink on the ST25R3916: frames go through St25r3916::transceive with CRC_A handled by the chip.
#pragma once
#include <zephyr/kernel.h>

#include "IsoDep.h"
#include "St25r3916.h"

namespace nfc {

class RfIsoDepLink : public IsoDepLink {
 public:
  explicit RfIsoDepLink(St25r3916& rf) : rf_(rf) {}

  int transceive(const uint8_t* tx, size_t txLen, uint8_t* rx, size_t cap, size_t* rxLen,
                 uint32_t timeout1fc) override {
    uint32_t ms = (timeout1fc + 13559) / 13560;  // carrier clocks to milliseconds, rounded up
    if (ms < 1) ms = 1;
    return rf_.transceive(tx, txLen, rx, cap, rxLen, /*txCrc=*/true, /*rxCrc=*/true, ms);
  }
  void sleepMs(uint32_t ms) override { k_msleep(ms); }

 private:
  St25r3916& rf_;
};

}  // namespace nfc
