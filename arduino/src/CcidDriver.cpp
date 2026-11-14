#include "CcidDriver.h"

#include <Arduino.h>
#include <device/usbd_pvt.h>
#include <string.h>
#include <tusb.h>

#include "CoreMutex.h"
#include "USB.h"

namespace nfc {

namespace {

// CCID functional descriptor (54 bytes, rev 1.10, short-APDU level); identical to
// class_descriptor() in ../micropython/ccid_proto.py.
const uint8_t kClassDesc[54] = {
    0x36, 0x21, 0x10, 0x01, 0x00, 0x07, 0x03, 0x00, 0x00, 0x00, 0xA0, 0x0F, 0x00, 0x00,
    0xA0, 0x0F, 0x00, 0x00, 0x00, 0x00, 0x2A, 0x00, 0x00, 0x00, 0x40, 0x05, 0x00, 0x00,
    0xFE, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0xBA, 0x00,
    0x02, 0x00, 0x0F, 0x01, 0x00, 0x00, 0xFF, 0xFF, 0x00, 0x00, 0x00, 0x01};

constexpr uint8_t CLASS_SMART_CARD = 0x0B;
constexpr uint16_t EP_SIZE = 64;
constexpr uint16_t DESC_LEN = 9 + sizeof(kClassDesc) + 3 * 7;  // interface + class + 3 endpoints
constexpr uint32_t POLL_MS = 300;

uint8_t epOut, epIn, epInt;  // endpoint addresses, allocated by ccidBegin()
uint8_t rhport = 0;
volatile bool opened = false;
uint8_t rxBuf[ccid::MAX_MESSAGE + 1];
uint8_t txBuf[ccid::MAX_MESSAGE + 1];
uint8_t notifyBuf[2];
volatile size_t pendingLen = 0;  // length of a command received in rxBuf, not yet answered
volatile uint16_t lastTxLen = 0;
volatile bool resetPresence = false;  // set on open: send the current state at the next poll

// ---- configuration descriptor, written by the core when USB connects --------------------
void putEndpoint(uint8_t*& p, uint8_t addr, uint8_t attr, uint16_t size, uint8_t interval) {
  *p++ = 7;
  *p++ = TUSB_DESC_ENDPOINT;
  *p++ = addr;
  *p++ = attr;
  *p++ = size & 0xFF;
  *p++ = size >> 8;
  *p++ = interval;
}

void descriptorCb(int itf, uint8_t* dst, int len, void* /*param*/) {
  (void)len;
  uint8_t* p = dst;
  *p++ = 9;
  *p++ = TUSB_DESC_INTERFACE;
  *p++ = (uint8_t)itf;
  *p++ = 0;  // alternate setting
  *p++ = 3;  // endpoints
  *p++ = CLASS_SMART_CARD;
  *p++ = 0;
  *p++ = 0;
  *p++ = 0;  // no interface string
  memcpy(p, kClassDesc, sizeof(kClassDesc));
  p += sizeof(kClassDesc);
  putEndpoint(p, epOut, TUSB_XFER_BULK, EP_SIZE, 0);
  putEndpoint(p, epIn, TUSB_XFER_BULK, EP_SIZE, 0);
  putEndpoint(p, epInt, TUSB_XFER_INTERRUPT, 8, 16);
}

// ---- TinyUSB class driver (runs in the TinyUSB task: no NFC / I2C work here) ---------------
void ccidInit() {}

void ccidReset(uint8_t /*rhport*/) {
  opened = false;
  pendingLen = 0;
  lastTxLen = 0;
}

uint16_t ccidOpen(uint8_t port, tusb_desc_interface_t const* itf, uint16_t maxLen) {
  TU_VERIFY(itf->bInterfaceClass == CLASS_SMART_CARD, 0);
  TU_VERIFY(maxLen >= DESC_LEN, 0);
  uint8_t const* p = (uint8_t const*)itf + 9 + sizeof(kClassDesc);  // first endpoint descriptor
  for (int i = 0; i < 3; i++, p += 7) {
    TU_ASSERT(usbd_edpt_open(port, (tusb_desc_endpoint_t const*)p), 0);
  }
  rhport = port;
  pendingLen = 0;
  lastTxLen = 0;
  resetPresence = true;
  opened = true;
  TU_ASSERT(usbd_edpt_xfer(port, epOut, rxBuf, sizeof(rxBuf)), 0);
  return DESC_LEN;
}

bool ccidControl(uint8_t /*rhport*/, uint8_t stage, tusb_control_request_t const* /*request*/) {
  return stage != CONTROL_STAGE_SETUP;  // no class requests needed: stall them
}

bool ccidXfer(uint8_t port, uint8_t epAddr, xfer_result_t result, uint32_t n) {
  if (epAddr == epOut) {
    if (result == XFER_RESULT_SUCCESS && n > 0) {
      pendingLen = n;  // ccidService() answers it and arms the next OUT transfer
    } else {
      usbd_edpt_xfer(port, epOut, rxBuf, sizeof(rxBuf));
    }
  } else if (epAddr == epIn) {
    uint16_t sent = lastTxLen;
    lastTxLen = 0;
    if (sent > 0 && sent % EP_SIZE == 0) usbd_edpt_xfer(port, epIn, txBuf, 0);  // end with a ZLP
  }
  return true;
}

const usbd_class_driver_t ccidDriver = {
#if CFG_TUSB_DEBUG >= 2
    .name = "CCID",
#endif
    .init = ccidInit,
    .reset = ccidReset,
    .open = ccidOpen,
    .control_xfer_cb = ccidControl,
    .xfer_cb = ccidXfer,
    .sof = NULL};

// ---- called from loop(), under the core's USB lock --------------------------------------
bool sendReply(size_t len) {
  for (int tries = 0; tries < 200; tries++) {
    {
      CoreMutex m(&USB.mutex, false);
      if (m && !usbd_edpt_busy(rhport, epIn)) {
        lastTxLen = (uint16_t)len;
        return usbd_edpt_xfer(rhport, epIn, txBuf, (uint16_t)len);
      }
    }
    delay(1);  // wait outside the lock: the USB task needs it to make progress
  }
  return false;
}

void armOut() {
  for (int tries = 0; tries < 200; tries++) {
    {
      CoreMutex m(&USB.mutex, false);
      if (m && !usbd_edpt_busy(rhport, epOut)) {
        usbd_edpt_xfer(rhport, epOut, rxBuf, sizeof(rxBuf));
        return;
      }
    }
    delay(1);
  }
}

bool trySendNotify(bool present) {
  CoreMutex m(&USB.mutex, false);
  if (!m || usbd_edpt_busy(rhport, epInt)) return false;
  ccid::slotChangeMessage(present, true, notifyBuf);
  return usbd_edpt_xfer(rhport, epInt, notifyBuf, sizeof(notifyBuf));
}

int lastPresent = -1;  // -1 = unknown
bool notifyPending = false;
bool notifyValue = false;
uint32_t lastPoll = 0;

}  // namespace

void ccidBegin() {
  USB.setManufacturer("Experiment");
  USB.setProduct("NFC CCID reader");
  epOut = USB.registerEndpointOut();
  epIn = USB.registerEndpointIn();
  epInt = USB.registerEndpointIn();
  USB.registerInterface(1, descriptorCb, nullptr, DESC_LEN, 20, 0);  // after the serial interface
}

void ccidService(ccid::Slot& slot, ccid::Backend& backend) {
  if (!opened) return;

  size_t n = pendingLen;
  if (n) {
    size_t rl = slot.handle(rxBuf, n, txBuf, sizeof(txBuf));
    if (rl) sendReply(rl);
    pendingLen = 0;
    armOut();
    return;
  }

  if (resetPresence) {  // host just (re)configured the device: report the current state
    resetPresence = false;
    lastPresent = -1;
    notifyPending = false;
  }

  if (notifyPending) {  // a notification could not be queued earlier
    if (trySendNotify(notifyValue)) notifyPending = false;
    return;
  }

  uint32_t now = millis();
  if (now - lastPoll < POLL_MS) return;
  lastPoll = now;
  int st = backend.status();
  bool present = st >= 0 && st != ccid::ICC_ABSENT;
  if ((int)present != lastPresent) {
    lastPresent = present;
    notifyValue = present;
    notifyPending = !trySendNotify(present);
  }
}

}  // namespace nfc

// TinyUSB calls this to find application class drivers (weak in TinyUSB; the Arduino-Pico core
// defines it only under ENABLE_PICOTOOL_USB).
extern "C" usbd_class_driver_t const* usbd_app_driver_get_cb(uint8_t* driverCount) {
  *driverCount = 1;
  return &nfc::ccidDriver;
}
