#include "IsoDep.h"

#include <string.h>

namespace nfc {

namespace {
constexpr uint32_t RATS_TIMEOUT_1FC = 65536;
constexpr size_t FSC_MAX = 256;
// The chip loads at most 64 bytes into its FIFO for a transmit, so a frame without CRC is capped.
constexpr size_t TX_FRAME_MAX = 64;
constexpr int MAX_RETRIES = 10;
constexpr int RATS_RETRIES = 4;
const uint16_t FS_TABLE[9] = {16, 24, 32, 40, 48, 64, 96, 128, 256};

bool lost(int rc) { return rc == ERR_TIMEOUT || rc == ERR_FRAME || rc == ERR_COLLISION; }
}  // namespace

int IsoDep::activate() {
  const uint8_t req[2] = {0xE0, 0x80};  // RATS: FSDI = 8 (256 bytes), CID = 0
  uint8_t res[ATS_MAX + 2];
  size_t n = 0;
  for (int tries = 1;; tries++) {
    int rc = link_.transceive(req, sizeof(req), res, sizeof(res), &n, RATS_TIMEOUT_1FC);
    if (rc == OK) break;
    if (!lost(rc)) return rc;
    if (tries >= RATS_RETRIES) return ERR_TIMEOUT;
  }
  if (n > ATS_MAX) n = ATS_MAX;
  memcpy(ats_, res, n);
  atsLen_ = n;

  uint8_t fsci = 2, sfgi = 0, fwi = 4;
  if (n >= 2) {
    uint8_t t0 = res[1];
    fsci = t0 & 0x0F;
    if (t0 & 0x20) {  // TB(1) present: SFGI and FWI
      size_t tb = (t0 & 0x10) ? 3 : 2;
      if (tb < n) {
        sfgi = res[tb] & 0x0F;
        fwi = res[tb] >> 4;
      }
    }
  }
  if (fsci >= sizeof(FS_TABLE) / sizeof(FS_TABLE[0])) return ERR_PROTOCOL;
  if (fwi > 14) fwi = 4;  // 15 is reserved
  if (sfgi > 14) sfgi = 0;
  fsc_ = FS_TABLE[fsci];
  sfgt1fc_ = (256u * 16u) << sfgi;  // SFGT = 256 x 16 / fc x 2^SFGI
  fwt1fc_ = (256u * 16u) << fwi;    // FWT  = 256 x 16 / fc x 2^FWI
  blockNum_ = 0;
  link_.sleepMs((sfgt1fc_ + 13559) / 13560 + 1);  // the card needs SFGT before it takes the next frame
  return OK;
}

int IsoDep::deselect() {
  const uint8_t tx = 0xC2;
  uint8_t rx[4];
  size_t n = 0;
  int rc = link_.transceive(&tx, 1, rx, sizeof(rx), &n, fwt1fc_);
  if (rc != OK) return rc;
  return (n == 1 && rx[0] == 0xC2) ? OK : ERR_PROTOCOL;
}

int IsoDep::transceive(const uint8_t* tx, size_t txLen, uint8_t* rx, size_t cap, size_t* rxLen) {
  enum class Send { Data, Ack, Nak, Wtx };
  uint8_t txBuf[FSC_MAX];
  uint8_t rxBuf[FSC_MAX + 2];  // two spare bytes: the chip may leave the CRC in the frame
  Send send = Send::Data;
  uint8_t wtxMul = 0;
  // INF bytes per I-block: FSC less PCB and CRC, and no more than the chip can transmit.
  size_t maxN = fsc_ >= 3 ? fsc_ - 3 : 0;
  if (maxN > TX_FRAME_MAX - 1) maxN = TX_FRAME_MAX - 1;
  if (maxN == 0) return ERR_PROTOCOL;
  size_t rxTotal = 0;
  bool rxChaining = false;
  int retries = 0;
  const uint8_t* txp = tx;
  size_t txRemain = txLen;

  for (;;) {
    uint32_t fwt = fwt1fc_;
    size_t frameLen = 0;
    switch (send) {
      case Send::Data: {
        size_t n = txRemain < maxN ? txRemain : maxN;
        bool more = n != txRemain;
        txBuf[0] = 0x02 | blockNum_ | (more ? 0x10 : 0);
        memcpy(txBuf + 1, txp, n);
        frameLen = 1 + n;
        break;
      }
      case Send::Wtx:
        fwt *= wtxMul;
        txBuf[0] = 0xF2;
        txBuf[1] = wtxMul;
        frameLen = 2;
        break;
      case Send::Ack:
        txBuf[0] = 0xA2 | blockNum_;
        frameLen = 1;
        break;
      case Send::Nak:
        txBuf[0] = 0xB2 | blockNum_;
        frameLen = 1;
        break;
    }

    size_t got = 0;
    int rc = link_.transceive(txBuf, frameLen, rxBuf, sizeof(rxBuf), &got, fwt);
    if (rc != OK) {
      if (!lost(rc)) return rc;
      if (++retries >= MAX_RETRIES) return ERR_TIMEOUT;
      send = rxChaining ? Send::Ack : Send::Nak;
      continue;
    }
    if (got == 0) return ERR_PROTOCOL;
    retries = 0;

    uint8_t pcb = rxBuf[0];
    switch (pcb) {
      case 0x02: case 0x03: case 0x12: case 0x13: {  // I-block
        size_t inf = got - 1;
        if (rxTotal + inf > cap) return ERR_PARAM;
        memcpy(rx + rxTotal, rxBuf + 1, inf);
        rxTotal += inf;
        blockNum_ ^= 1;
        if (!(pcb & 0x10)) {  // last block of the response
          *rxLen = rxTotal;
          return OK;
        }
        rxChaining = true;
        send = Send::Ack;
        break;
      }
      case 0xA2: case 0xA3:  // R(ACK) for our chained block
        if ((pcb & 1) == blockNum_) {
          if (txRemain <= maxN) return ERR_PROTOCOL;  // ACK on the last block of a chain
          txp += maxN;
          txRemain -= maxN;
          blockNum_ ^= 1;
        }
        send = Send::Data;
        break;
      case 0xF2:  // S(WTX): the card asks for more time
        if (got != 2) return ERR_PROTOCOL;
        wtxMul = rxBuf[1] & 0x3F;
        send = Send::Wtx;
        break;
      default:
        return ERR_PROTOCOL;
    }
  }
}

}  // namespace nfc
