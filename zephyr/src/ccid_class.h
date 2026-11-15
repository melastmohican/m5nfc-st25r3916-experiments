/* USB CCID (smart card reader) class for Zephyr's new USB device stack (usbd).
 *
 * Zephyr has no CCID class, so this registers one next to CDC-ACM (sample_usbd_init_device()
 * registers every class linked into the image). The USB callbacks only record that a message
 * arrived; the NFC (I2C) work runs in the application thread through the calls below.
 */
#ifndef CCID_CLASS_H_
#define CCID_CLASS_H_

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define CCID_MAX_MESSAGE 271 /* header + 261, as announced in the class descriptor */

/* The host configured the device and the CCID interface is usable. */
bool ccid_class_is_open(void);

/* True once after every (re)configuration by the host: report the current slot state again. */
bool ccid_class_take_opened(void);

/* Copy a received PC_to_RDR message into buf. Returns its length, or 0 if none is waiting. */
size_t ccid_class_poll_command(uint8_t *buf, size_t cap);

/* Send a bulk IN reply (adds a zero-length packet after a multiple of 64 bytes) and wait for it
 * to finish. Returns false on timeout or error.
 */
bool ccid_class_send(const uint8_t *data, size_t len);

/* Allow the next command: call after the reply to the previous one was sent. */
void ccid_class_arm_out(void);

/* Queue a 2-byte RDR_to_PC_NotifySlotChange on the interrupt IN endpoint without blocking.
 * Returns false if the endpoint is still busy.
 */
bool ccid_class_notify(const uint8_t msg[2]);

#ifdef __cplusplus
}
#endif

#endif
