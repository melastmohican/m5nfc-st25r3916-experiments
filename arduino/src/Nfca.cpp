#include "Nfca.h"

namespace nfc {

namespace {
constexpr uint8_t CT = 0x88;  // cascade tag
constexpr uint8_t T2_READ = 0x30;
constexpr uint8_t T2_WRITE = 0xA2;
constexpr uint8_t T2_ACK = 0x0A;

uint8_t bcc(const uint8_t* d, size_t n) {
  uint8_t out = 0;
  for (size_t i = 0; i < n; i++) out ^= d[i];
  return out;
}

int anticollision(St25r3916& rf, uint8_t sel, uint8_t cl[5]) {
  const uint8_t cmd[2] = {sel, 0x20};
  uint8_t resp[8];
  size_t n = 0;
  int rc = rf.transceive(cmd, 2, resp, sizeof(resp), &n, /*txCrc=*/false, /*rxCrc=*/false);
  if (rc != OK) return rc;
  if (n != 5 || bcc(resp, 4) != resp[4]) return ERR_BAD_RESPONSE;
  for (int i = 0; i < 5; i++) cl[i] = resp[i];
  return OK;
}

int selectLevel(St25r3916& rf, uint8_t sel, const uint8_t cl[5], uint8_t* sak) {
  uint8_t cmd[7] = {sel, 0x70, cl[0], cl[1], cl[2], cl[3], cl[4]};
  uint8_t resp[8];
  size_t n = 0;
  int rc = rf.transceive(cmd, 7, resp, sizeof(resp), &n, /*txCrc=*/true, /*rxCrc=*/true);
  if (rc != OK) return rc;
  if (n != 1) return ERR_BAD_RESPONSE;
  *sak = resp[0];
  return OK;
}
}  // namespace

void uidToHex(const Card& card, char* out, size_t cap) {
  static const char* hex = "0123456789ABCDEF";
  size_t o = 0;
  for (uint8_t i = 0; i < card.uidLen && o + 3 <= cap; i++) {
    if (i) out[o++] = ':';
    out[o++] = hex[card.uid[i] >> 4];
    out[o++] = hex[card.uid[i] & 0x0F];
  }
  if (o < cap) out[o] = 0;
}

void crcA(const uint8_t* data, size_t len, uint8_t out[2]) {
  uint16_t crc = 0x6363;
  for (size_t i = 0; i < len; i++) {
    uint8_t ch = data[i] ^ (crc & 0xFF);
    ch ^= (uint8_t)(ch << 4);
    crc = (crc >> 8) ^ ((uint16_t)ch << 8) ^ ((uint16_t)ch << 3) ^ (ch >> 4);
  }
  out[0] = crc & 0xFF;
  out[1] = crc >> 8;
}

int selectCard(St25r3916& rf, Card& card) {
  uint8_t atqa[4];
  size_t n = 0;
  int rc = rf.sendShort(CMD_TX_WUPA, atqa, sizeof(atqa), &n);
  if (rc == ERR_TIMEOUT) {
    rf.fieldReset();
    rc = rf.sendShort(CMD_TX_WUPA, atqa, sizeof(atqa), &n);
  }
  if (rc != OK) return rc;
  if (n != 2) return ERR_BAD_RESPONSE;
  card.uidLen = 0;
  card.atqa[0] = atqa[0];
  card.atqa[1] = atqa[1];
  static const uint8_t levels[3] = {0x93, 0x95, 0x97};
  for (uint8_t sel : levels) {
    uint8_t cl[5];
    rc = anticollision(rf, sel, cl);
    if (rc != OK) return rc;
    uint8_t sak = 0;
    rc = selectLevel(rf, sel, cl, &sak);
    if (rc != OK) return rc;
    if (cl[0] == CT && (sak & 0x04)) {  // UID continues at the next cascade level
      for (int i = 1; i < 4; i++) card.uid[card.uidLen++] = cl[i];
      continue;
    }
    for (int i = 0; i < 4; i++) card.uid[card.uidLen++] = cl[i];
    card.sak = sak;
    return OK;
  }
  return ERR_BAD_RESPONSE;  // UID longer than 10 bytes
}

int t2Read(St25r3916& rf, uint8_t page, uint8_t out[16]) {
  const uint8_t cmd[2] = {T2_READ, page};
  uint8_t buf[16 + 2];  // the chip leaves the CRC_A in the FIFO; room for it before it is stripped
  size_t n = 0;
  int rc = rf.transceive(cmd, 2, buf, sizeof(buf), &n, /*txCrc=*/true, /*rxCrc=*/true);
  if (rc != OK) return rc;
  if (n != 16) return ERR_BAD_RESPONSE;
  for (int i = 0; i < 16; i++) out[i] = buf[i];
  return OK;
}

int t2Write(St25r3916& rf, uint8_t page, const uint8_t data[4]) {
  uint8_t cmd[6] = {T2_WRITE, page, data[0], data[1], data[2], data[3]};
  uint8_t resp[4];
  size_t n = 0;
  // The ACK is a 4-bit frame without CRC, so receive-side CRC checking is off.
  int rc = rf.transceive(cmd, 6, resp, sizeof(resp), &n, /*txCrc=*/true, /*rxCrc=*/false,
                         /*timeoutMs=*/10, /*shortRx=*/true);
  if (rc != OK) return rc;
  if (n == 0) return ERR_NO_ACK;
  // 4-bit ACK/NAK (NTAG21x datasheet 9.3, Table 23)
  switch (resp[0] & 0x0F) {
    case T2_ACK: return OK;
    case 0x0: return ERR_NAK_ARG;
    case 0x1: return ERR_NAK_CRC;
    case 0x4: return ERR_NAK_AUTH;
    case 0x5: return ERR_NAK_EEPROM;
    default: return ERR_NAK_OTHER;
  }
}

}  // namespace nfc
