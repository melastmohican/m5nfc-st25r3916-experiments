#include "NfcBackend.h"

#include <string.h>

namespace nfc {

namespace {
void sw(uint8_t* resp, size_t* n, uint8_t sw1, uint8_t sw2) {
  resp[0] = sw1;
  resp[1] = sw2;
  *n = 2;
}
}  // namespace

bool NfcBackend::probe() {
  Card c;
  return selectCard(rf_, c) == OK;
}

int NfcBackend::status() {
  if (powered_) {
    uint8_t buf[16];
    if (t2Read(rf_, 0, buf) == OK) return ccid::ICC_ACTIVE;
    powered_ = false;
    return ccid::ICC_ABSENT;
  }
  return probe() ? ccid::ICC_INACTIVE : ccid::ICC_ABSENT;
}

int NfcBackend::powerOn(uint8_t* atr, size_t cap, size_t* len) {
  rf_.fieldReset();
  powered_ = false;
  int rc = selectCard(rf_, card_);
  if (rc == ERR_TIMEOUT || rc == ERR_COLLISION || rc == ERR_FRAME || rc == ERR_BAD_RESPONSE)
    return ccid::BE_ABSENT;
  if (rc != OK) return ccid::BE_ERROR;
  *len = ccid::contactlessAtr(atr, cap);
  if (*len == 0) return ccid::BE_ERROR;
  powered_ = true;
  return ccid::BE_OK;
}

void NfcBackend::powerOff() {
  powered_ = false;
  rf_.fieldReset();
}

int NfcBackend::transmit(const uint8_t* apdu, size_t len, uint8_t* resp, size_t cap, size_t* respLen) {
  if (!powered_) return ccid::BE_ABSENT;
  if (cap < 2) return ccid::BE_ERROR;
  if (len < 4) {
    sw(resp, respLen, 0x67, 0x00);
    return ccid::BE_OK;
  }
  if (apdu[0] != 0xFF) {
    sw(resp, respLen, 0x6E, 0x00);  // class not supported
    return ccid::BE_OK;
  }
  int rc;
  switch (apdu[1]) {
    case 0xCA: rc = getData(apdu[2], apdu, len, resp, cap, respLen); break;
    case 0xB0: rc = readBinary(apdu[3], apdu, len, resp, cap, respLen); break;
    case 0xD6: rc = updateBinary(apdu[3], apdu, len, resp, cap, respLen); break;
    default:
      sw(resp, respLen, 0x6D, 0x00);  // instruction not supported
      return ccid::BE_OK;
  }
  if (rc == ERR_TIMEOUT) {  // the tag was lifted
    powered_ = false;
    return ccid::BE_ABSENT;
  }
  return rc == OK ? ccid::BE_OK : ccid::BE_ERROR;
}

int NfcBackend::getData(uint8_t p1, const uint8_t* apdu, size_t len, uint8_t* resp, size_t cap,
                        size_t* n) {
  if (p1 != 0x00) {
    sw(resp, n, 0x6B, 0x00);
    return OK;
  }
  size_t le = len > 4 ? apdu[4] : 0;
  size_t take = (le == 0 || le > card_.uidLen) ? card_.uidLen : le;
  if (take + 2 > cap) return ERR_PARAM;
  memcpy(resp, card_.uid, take);
  resp[take] = 0x90;
  resp[take + 1] = 0x00;
  *n = take + 2;
  return OK;
}

int NfcBackend::readBinary(uint8_t page, const uint8_t* apdu, size_t len, uint8_t* resp, size_t cap,
                           size_t* n) {
  size_t le = len > 4 ? apdu[4] : 0;
  if (le == 0) le = 16;
  if (page >= TOTAL_PAGES) {
    sw(resp, n, 0x6A, 0x82);
    return OK;
  }
  if (le + 2 > cap) return ERR_PARAM;
  size_t got = 0;
  uint8_t buf[16];
  for (uint16_t p = page; got < le && p < TOTAL_PAGES; p += 4) {
    int rc = t2Read(rf_, (uint8_t)p, buf);
    if (rc != OK) return rc;
    size_t chunk = le - got < 16 ? le - got : 16;
    memcpy(resp + got, buf, chunk);
    got += chunk;
  }
  resp[got] = 0x90;
  resp[got + 1] = 0x00;
  *n = got + 2;
  return OK;
}

int NfcBackend::updateBinary(uint8_t page, const uint8_t* apdu, size_t len, uint8_t* resp,
                             size_t cap, size_t* n) {
  (void)cap;
  if (len < 9 || apdu[4] != 4) {
    sw(resp, n, 0x67, 0x00);
    return OK;
  }
  int rc = tag_.writePage(page, apdu + 5);
  if (rc == OK) {
    sw(resp, n, 0x90, 0x00);
    return OK;
  }
  if (rc == ERR_TIMEOUT) return rc;
  if (rc == ERR_PROTECTED) sw(resp, n, 0x69, 0x82);
  else sw(resp, n, 0x65, 0x81);  // memory failure
  return OK;
}

}  // namespace nfc
