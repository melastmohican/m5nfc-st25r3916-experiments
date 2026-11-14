#include "CcidDriver.h"

#include <Arduino.h>
#include <device/usbd_pvt.h>
#include <string.h>
#include <tusb.h>

#include "USB.h"

namespace nfc {

namespace {

// CCID functional descriptor (54 bytes, rev 1.10, short-APDU level). Same bytes as
// micropython/ccid_proto.py class_descriptor().
const uint8_t kClassDesc[54] = {
    0x36, 0x21, 0x10, 0x01, 0x00, 0x07, 0x03, 0x00, 0x00, 0x00, 0xA0, 0x0F, 0x00, 0x00,
    0xA0, 0x0F, 0x00, 0x00, 0x00, 0x00, 0x2A, 0x00, 0x00, 0x00, 0x40, 0x05, 0x00, 0x00,
    0xFE, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0xBA, 0x00,
    0x02, 0x00, 0x0F, 0x01, 0x00, 0x00, 0xFF, 0xFF, 0x00, 0x00, 0x00, 0x01};

constexpr uint8_t CLASS_SMART_CARD = 0x0B;
constexpr uint16_t EP_SIZE = 64;
constexpr uint16_t DESC_LEN = 9 + sizeof(kClassDesc) + 3 * 7;  // interface + class + 3 endpoints

uint8_t epOut, epIn, epInt;  // endpoint addresses, allocated by ccidBegin()
bool opened = false;
uint8_t rxBuf[EP_SIZE];
uint8_t txBuf[EP_SIZE];
uint8_t notifyBuf[2] = {0x50, 0x00};  // RDR_to_PC_NotifySlotChange: slot 0 absent, no change

// ---- the (stub) message handler: slot status only -------------------------------------
constexpr uint8_t ICC_ABSENT = 0x02, CMD_FAILED = 0x40;

size_t handleMessage(const uint8_t* msg, size_t n, uint8_t* out) {
  if (n < 10) return 0;
  uint8_t type = msg[0], slot = msg[5], seq = msg[6];
  uint8_t respType = 0x81, status = ICC_ABSENT, error = 0;
  if (type == 0x65 || type == 0x63) {
    // GetSlotStatus / IccPowerOff: ok, card absent
  } else if (type == 0x62) {  // IccPowerOn: no card
    respType = 0x80;
    status = CMD_FAILED | ICC_ABSENT;
    error = 0xFE;  // ICC mute
  } else {
    status = CMD_FAILED | ICC_ABSENT;  // command not supported
  }
  out[0] = respType;
  out[1] = out[2] = out[3] = out[4] = 0;  // dwLength = 0
  out[5] = slot;
  out[6] = seq;
  out[7] = status;
  out[8] = error;
  out[9] = 0;
  return 10;
}

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

// ---- TinyUSB class driver ------------------------------------------------------------
void ccidInit() {}

void ccidReset(uint8_t /*rhport*/) { opened = false; }

uint16_t ccidOpen(uint8_t rhport, tusb_desc_interface_t const* itf, uint16_t maxLen) {
  TU_VERIFY(itf->bInterfaceClass == CLASS_SMART_CARD, 0);
  TU_VERIFY(maxLen >= DESC_LEN, 0);
  uint8_t const* p = (uint8_t const*)itf + 9 + sizeof(kClassDesc);  // first endpoint descriptor
  for (int i = 0; i < 3; i++, p += 7) {
    TU_ASSERT(usbd_edpt_open(rhport, (tusb_desc_endpoint_t const*)p), 0);
  }
  opened = true;
  usbd_edpt_xfer(rhport, epInt, notifyBuf, sizeof(notifyBuf));
  TU_ASSERT(usbd_edpt_xfer(rhport, epOut, rxBuf, sizeof(rxBuf)), 0);
  return DESC_LEN;
}

bool ccidControl(uint8_t /*rhport*/, uint8_t stage, tusb_control_request_t const* /*request*/) {
  return stage != CONTROL_STAGE_SETUP;  // no class requests needed: stall them
}

bool ccidXfer(uint8_t rhport, uint8_t epAddr, xfer_result_t result, uint32_t n) {
  if (epAddr == epOut) {
    if (result == XFER_RESULT_SUCCESS) {
      size_t len = handleMessage(rxBuf, n, txBuf);
      if (len) usbd_edpt_xfer(rhport, epIn, txBuf, len);
    }
    usbd_edpt_xfer(rhport, epOut, rxBuf, sizeof(rxBuf));  // arm for the next message
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

}  // namespace

void ccidBegin() {
  epOut = USB.registerEndpointOut();
  epIn = USB.registerEndpointIn();
  epInt = USB.registerEndpointIn();
  USB.registerInterface(1, descriptorCb, nullptr, DESC_LEN, 20, 0);  // after the serial interface
}

}  // namespace nfc

// TinyUSB calls this to find application class drivers (weak in TinyUSB; the Arduino-Pico core
// defines it only under ENABLE_PICOTOOL_USB).
extern "C" usbd_class_driver_t const* usbd_app_driver_get_cb(uint8_t* driverCount) {
  *driverCount = 1;
  return &nfc::ccidDriver;
}
