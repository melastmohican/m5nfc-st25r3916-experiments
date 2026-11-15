// Feather RP2350 + M5Stack NFC unit on Zephyr: the same serial CLI as arduino/ (copied, no Arduino dependency) (see Cli.h).
#include <zephyr/device.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/drivers/i2c.h>
#include <zephyr/drivers/uart.h>
#include <zephyr/kernel.h>

#include "CcidProto.h"
#include "CcidService.h"
#include "Cli.h"
#include "Io.h"
#include "NfcBackend.h"
#include "St25r3916.h"
#include "ZephyrTransport.h"
#include <zephyr/usb/usbd.h>  // before the extern "C" block: it pulls in C++-aware headers
extern "C" {
#include "sample_usbd.h"
}

namespace {

// Io over the CDC-ACM UART.
class UsbSerial : public nfc::Io {
 public:
  explicit UsbSerial(const struct device* uart) : uart_(uart) {}
  int available() override {
    if (peek_ < 0) {
      unsigned char c;
      if (uart_poll_in(uart_, &c) == 0) peek_ = c;
    }
    return peek_ >= 0 ? 1 : 0;
  }
  int read() override {
    if (!available()) return -1;
    int c = peek_;
    peek_ = -1;
    return c;
  }
  size_t write(const uint8_t* data, size_t len) override {
    for (size_t i = 0; i < len; i++) {
      if (data[i] == '\n') uart_poll_out(uart_, '\r');
      uart_poll_out(uart_, data[i]);
    }
    return len;
  }

 private:
  const struct device* uart_;
  int peek_ = -1;
};

}  // namespace

// Debug LED (GP7): 3 quick blinks at boot, steady on once USB is up, slow blink forever if USB init failed.
static const struct gpio_dt_spec dbgLed = GPIO_DT_SPEC_GET(DT_NODELABEL(dbg_led), gpios);

static void blink(int n, int ms) {
  for (int i = 0; i < n; i++) {
    gpio_pin_set_dt(&dbgLed, 1);
    k_msleep(ms);
    gpio_pin_set_dt(&dbgLed, 0);
    k_msleep(ms);
  }
}

int main() {
  gpio_pin_configure_dt(&dbgLed, GPIO_OUTPUT_INACTIVE);
  blink(3, 100);
  const struct device* uart = DEVICE_DT_GET(DT_NODELABEL(cdc_acm_uart0));
  const struct device* i2c = DEVICE_DT_GET(DT_NODELABEL(i2c1));

  struct usbd_context* usb = sample_usbd_init_device(NULL);
  if (usb == NULL) {
    while (true) blink(1, 1000);
  }
  // sample_usbd_init_device() only initialises: without VBUS detection the device must be enabled here.
  if (!usbd_can_detect_vbus(usb) && usbd_enable(usb) != 0) {
    while (true) blink(2, 300);
  }
  gpio_pin_set_dt(&dbgLed, 1);

  static UsbSerial serial(uart);
  static nfc::ZephyrTransport transport(i2c);
  static nfc::St25r3916 rf(transport);
  static nfc::Cli cli(serial, rf);
  static nfc::NfcBackend ccidBackend(rf);
  static nfc::ccid::Slot ccidSlot(ccidBackend);

  k_msleep(3000);  // time to attach a serial monitor
  int rc = rf.init();
  serial.printf("init: %s\n", nfc::statusName(rc));
  serial.println("type 'help' for commands");

  while (true) {
    if (rc == nfc::OK) {
      cli.poll();
      nfc::ccidService(ccidSlot, ccidBackend);
    }
    k_msleep(1);
  }
}
