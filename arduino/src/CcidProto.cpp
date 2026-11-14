#include "CcidProto.h"

#include <string.h>

namespace nfc {
namespace ccid {

size_t contactlessAtr(uint8_t* out, size_t cap) {
  static const uint8_t body[18] = {0x8F, 0x80, 0x01, 0x80, 0x4F, 0x0C, 0xA0, 0x00, 0x00,
                                   0x03, 0x06, 0x03, 0x00, 0x03, 0x00, 0x00, 0x00, 0x00};
  if (cap < 1 + sizeof(body) + 1) return 0;
  uint8_t tck = 0;
  out[0] = 0x3B;
  for (size_t i = 0; i < sizeof(body); i++) {
    out[1 + i] = body[i];
    tck ^= body[i];
  }
  out[1 + sizeof(body)] = tck;
  return 1 + sizeof(body) + 1;
}

void slotChangeMessage(bool present, bool changed, uint8_t out[2]) {
  out[0] = NOTIFY_SLOT_CHANGE;
  out[1] = (present ? 1 : 0) | (changed ? 2 : 0);
}

size_t Slot::reply(uint8_t type, uint8_t slot, uint8_t seq, uint8_t status, uint8_t error,
                   uint8_t specific, const uint8_t* data, size_t dlen, uint8_t* out, size_t cap) {
  if (HEADER_LEN + dlen > cap) return 0;
  out[0] = type;
  out[1] = dlen & 0xFF;
  out[2] = (dlen >> 8) & 0xFF;
  out[3] = 0;
  out[4] = 0;
  out[5] = slot;
  out[6] = seq;
  out[7] = status;
  out[8] = error;
  out[9] = specific;
  if (dlen && data != out + HEADER_LEN) memcpy(out + HEADER_LEN, data, dlen);  // may already be in place
  return HEADER_LEN + dlen;
}

size_t Slot::failed(uint8_t type, uint8_t slot, uint8_t seq, uint8_t icc, uint8_t error,
                    uint8_t* out, size_t cap) {
  return reply(type, slot, seq, CMD_FAILED | icc, error, 0, nullptr, 0, out, cap);
}

uint8_t Slot::iccStatus() {
  int s = be_.status();
  return s < 0 ? ICC_ABSENT : (uint8_t)s;
}

size_t Slot::handle(const uint8_t* msg, size_t n, uint8_t* out, size_t cap) {
  if (n < HEADER_LEN) return 0;
  uint8_t mtype = msg[0], slot = msg[5], seq = msg[6];
  size_t length = msg[1] | ((size_t)msg[2] << 8) | ((size_t)msg[3] << 16) | ((size_t)msg[4] << 24);
  const uint8_t* payload = msg + HEADER_LEN;
  if (length > n - HEADER_LEN) length = n - HEADER_LEN;

  uint8_t dataType = SLOT_STATUS;
  if (mtype == ICC_POWER_ON || mtype == XFR_BLOCK) dataType = DATA_BLOCK;
  else if (mtype == ESCAPE) dataType = ESCAPE_RESP;
  else if (mtype == GET_PARAMETERS || mtype == SET_PARAMETERS || mtype == RESET_PARAMETERS)
    dataType = PARAMETERS;

  if (slot != 0) return failed(dataType, slot, seq, ICC_ABSENT, ERR_SLOT, out, cap);

  if (mtype == GET_SLOT_STATUS) {
    int s = be_.status();
    if (s < 0) return failed(SLOT_STATUS, slot, seq, ICC_ABSENT, ERR_HW, out, cap);
    return reply(SLOT_STATUS, slot, seq, CMD_OK | s, 0, 0, nullptr, 0, out, cap);
  }

  if (mtype == ICC_POWER_ON) {
    uint8_t atr[32];
    size_t atrLen = 0;
    int rc = be_.powerOn(atr, sizeof(atr), &atrLen);
    if (rc == BE_ABSENT) return failed(DATA_BLOCK, slot, seq, ICC_ABSENT, ERR_ICC_MUTE, out, cap);
    if (rc != BE_OK) return failed(DATA_BLOCK, slot, seq, iccStatus(), ERR_HW, out, cap);
    return reply(DATA_BLOCK, slot, seq, CMD_OK | ICC_ACTIVE, 0, 0, atr, atrLen, out, cap);
  }

  if (mtype == ICC_POWER_OFF) {
    be_.powerOff();
    return reply(SLOT_STATUS, slot, seq, CMD_OK | iccStatus(), 0, 0, nullptr, 0, out, cap);
  }

  if (mtype == XFR_BLOCK) {
    int s = be_.status();
    if (s == ICC_ABSENT) return failed(DATA_BLOCK, slot, seq, ICC_ABSENT, ERR_ICC_MUTE, out, cap);
    if (s < 0) return failed(DATA_BLOCK, slot, seq, ICC_ABSENT, ERR_HW, out, cap);
    // Answer into `out` after the header so no copy is needed; the header is filled afterwards.
    if (cap < HEADER_LEN) return 0;
    size_t respLen = 0;
    int rc = be_.transmit(payload, length, out + HEADER_LEN, cap - HEADER_LEN, &respLen);
    if (rc == BE_ABSENT) return failed(DATA_BLOCK, slot, seq, ICC_ABSENT, ERR_ICC_MUTE, out, cap);
    if (rc != BE_OK) return failed(DATA_BLOCK, slot, seq, iccStatus(), ERR_HW, out, cap);
    return reply(DATA_BLOCK, slot, seq, CMD_OK | ICC_ACTIVE, 0, 0, out + HEADER_LEN, respLen, out,
                 cap);
  }

  if (mtype == GET_PARAMETERS || mtype == SET_PARAMETERS || mtype == RESET_PARAMETERS) {
    // T=1 defaults; contactless cards ignore them
    static const uint8_t params[7] = {0x11, 0x10, 0x00, 0x45, 0x00, 0xFE, 0x00};
    return reply(PARAMETERS, slot, seq, CMD_OK | iccStatus(), 0, 1, params, sizeof(params), out,
                 cap);
  }

  return failed(dataType, slot, seq, iccStatus(), ERR_CMD_NOT_SUPPORTED, out, cap);
}

}  // namespace ccid
}  // namespace nfc
