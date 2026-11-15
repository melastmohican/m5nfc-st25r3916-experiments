/* USB CCID class for Zephyr usbd; see ccid_class.h. Modelled on subsys/usb/device_next/class/loopback.c. */
#include "ccid_class.h"

#include <string.h>

#include <zephyr/drivers/usb/udc.h>
#include <zephyr/kernel.h>
#include <zephyr/sys/atomic.h>
#include <zephyr/usb/usbd.h>

#define CLASS_SMART_CARD 0x0B
#define EP_SIZE          64
#define OUT_BUF_SIZE     320 /* >= CCID_MAX_MESSAGE, multiple of the endpoint size */

/* CCID functional descriptor (54 bytes, rev 1.10, short-APDU level); identical to
 * class_descriptor() in ../micropython/ccid_proto.py.
 */
#define CCID_CLASS_DESC { \
	0x36, 0x21, 0x10, 0x01, 0x00, 0x07, 0x03, 0x00, 0x00, 0x00, 0xA0, 0x0F, 0x00, 0x00, \
	0xA0, 0x0F, 0x00, 0x00, 0x00, 0x00, 0x2A, 0x00, 0x00, 0x00, 0x40, 0x05, 0x00, 0x00, \
	0xFE, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0xBA, 0x00, \
	0x02, 0x00, 0x0F, 0x01, 0x00, 0x00, 0xFF, 0xFF, 0x00, 0x00, 0x00, 0x01}

struct ccid_desc {
	struct usb_if_descriptor if0;
	uint8_t ccid[54]; /* starts with bLength / bDescriptorType like any descriptor */
	struct usb_ep_descriptor out_ep;
	struct usb_ep_descriptor in_ep;
	struct usb_ep_descriptor int_ep;
	struct usb_desc_header nil_desc;
};

/* The stack renumbers the interface and the endpoints when it builds the configuration, so the
 * addresses used below are always read back from this structure.
 */
static struct ccid_desc ccid_desc = {
	.if0 = {
		.bLength = sizeof(struct usb_if_descriptor),
		.bDescriptorType = USB_DESC_INTERFACE,
		.bInterfaceNumber = 0,
		.bAlternateSetting = 0,
		.bNumEndpoints = 3,
		.bInterfaceClass = CLASS_SMART_CARD,
		.bInterfaceSubClass = 0,
		.bInterfaceProtocol = 0,
		.iInterface = 0,
	},
	.ccid = CCID_CLASS_DESC,
	.out_ep = {
		.bLength = sizeof(struct usb_ep_descriptor),
		.bDescriptorType = USB_DESC_ENDPOINT,
		.bEndpointAddress = 0x01,
		.bmAttributes = USB_EP_TYPE_BULK,
		.wMaxPacketSize = sys_cpu_to_le16(EP_SIZE),
		.bInterval = 0,
	},
	.in_ep = {
		.bLength = sizeof(struct usb_ep_descriptor),
		.bDescriptorType = USB_DESC_ENDPOINT,
		.bEndpointAddress = 0x81,
		.bmAttributes = USB_EP_TYPE_BULK,
		.wMaxPacketSize = sys_cpu_to_le16(EP_SIZE),
		.bInterval = 0,
	},
	.int_ep = {
		.bLength = sizeof(struct usb_ep_descriptor),
		.bDescriptorType = USB_DESC_ENDPOINT,
		.bEndpointAddress = 0x82,
		.bmAttributes = USB_EP_TYPE_INTERRUPT,
		.wMaxPacketSize = sys_cpu_to_le16(8),
		.bInterval = USB_FS_INT_EP_INTERVAL(16000),
	},
	.nil_desc = {.bLength = 0, .bDescriptorType = 0},
};

static const struct usb_desc_header *const ccid_fs_desc[] = {
	(const struct usb_desc_header *)&ccid_desc.if0,
	(const struct usb_desc_header *)ccid_desc.ccid,
	(const struct usb_desc_header *)&ccid_desc.out_ep,
	(const struct usb_desc_header *)&ccid_desc.in_ep,
	(const struct usb_desc_header *)&ccid_desc.int_ep,
	(const struct usb_desc_header *)&ccid_desc.nil_desc,
};

/* ---- state shared between the USB thread and the application thread ---- */
static struct usbd_class_data *g_cdata;
static atomic_t g_open;       /* host configured the interface */
static atomic_t g_reopened;   /* set on every (re)configuration */
static atomic_t g_out_armed;  /* a bulk OUT transfer is queued */
static atomic_t g_int_busy;   /* an interrupt IN transfer is queued */
static atomic_t g_in_zlp;     /* the current IN transfer needs a trailing zero-length packet */
static uint8_t g_rx[CCID_MAX_MESSAGE + 1];
static volatile size_t g_rx_len; /* length of a received, not yet consumed command */
static K_SEM_DEFINE(g_in_done, 0, 1);

static int enqueue_out(struct usbd_class_data *c_data)
{
	struct net_buf *buf;
	int err;

	if (!atomic_get(&g_open) || atomic_set(&g_out_armed, 1)) {
		return -EBUSY;
	}
	buf = usbd_ep_buf_alloc(c_data, ccid_desc.out_ep.bEndpointAddress, OUT_BUF_SIZE);
	if (buf == NULL) {
		atomic_set(&g_out_armed, 0);
		return -ENOMEM;
	}
	err = usbd_ep_enqueue(c_data, buf);
	if (err) {
		net_buf_unref(buf);
		atomic_set(&g_out_armed, 0);
	}
	return err;
}

static int ccid_request(struct usbd_class_data *const c_data, struct net_buf *const buf, const int err)
{
	struct udc_buf_info *bi = (struct udc_buf_info *)net_buf_user_data(buf);
	const uint8_t ep = bi->ep;

	if (ep == ccid_desc.out_ep.bEndpointAddress) {
		atomic_set(&g_out_armed, 0);
		if (err == 0 && buf->len > 0 && buf->len <= sizeof(g_rx)) {
			memcpy(g_rx, buf->data, buf->len);
			g_rx_len = buf->len; /* the application thread answers it and re-arms OUT */
		}
		net_buf_unref(buf);
		if (err != 0 && err != -ECONNABORTED) {
			enqueue_out(c_data);
		}
	} else if (ep == ccid_desc.in_ep.bEndpointAddress) {
		net_buf_unref(buf);
		if (err == 0 && atomic_set(&g_in_zlp, 0)) {
			struct net_buf *z = usbd_ep_buf_alloc(c_data, ep, EP_SIZE);

			if (z != NULL && usbd_ep_enqueue(c_data, z) == 0) {
				return 0; /* the semaphore is given when the ZLP completes */
			}
			if (z != NULL) {
				net_buf_unref(z);
			}
		}
		k_sem_give(&g_in_done);
	} else if (ep == ccid_desc.int_ep.bEndpointAddress) {
		atomic_set(&g_int_busy, 0);
		net_buf_unref(buf);
	} else {
		net_buf_unref(buf);
	}
	return 0;
}

static void ccid_open(struct usbd_class_data *c_data)
{
	g_cdata = c_data;
	g_rx_len = 0;
	atomic_set(&g_out_armed, 0);
	atomic_set(&g_int_busy, 0);
	atomic_set(&g_in_zlp, 0);
	atomic_set(&g_reopened, 1);
	atomic_set(&g_open, 1);
	k_sem_reset(&g_in_done);
	enqueue_out(c_data);
}

/* The stack calls enable() when the host sets the configuration (default alternate 0 is used,
 * update() is only called for explicit SET_INTERFACE requests).
 */
static void ccid_enable(struct usbd_class_data *const c_data)
{
	ccid_open(c_data);
}

static void ccid_update(struct usbd_class_data *c_data, uint8_t iface, uint8_t alternate)
{
	if (iface == ccid_desc.if0.bInterfaceNumber && alternate == 0) {
		ccid_open(c_data);
	}
}

static void ccid_disable(struct usbd_class_data *const c_data)
{
	ARG_UNUSED(c_data);
	atomic_set(&g_open, 0);
	g_rx_len = 0;
	k_sem_give(&g_in_done); /* release a sender that is waiting */
}

static struct net_buf *ccid_control_to_host(struct usbd_class_data *c_data,
					    const struct usb_setup_packet *const setup)
{
	ARG_UNUSED(c_data);
	ARG_UNUSED(setup);
	return NULL; /* no class requests needed: stall them (ABORT, GET_CLOCK_FREQUENCIES, ...) */
}

static int ccid_control_to_dev(struct usbd_class_data *c_data, const struct usb_setup_packet *const setup,
			       const struct net_buf *const buf)
{
	ARG_UNUSED(c_data);
	ARG_UNUSED(setup);
	ARG_UNUSED(buf);
	return -ENOTSUP;
}

static const void *ccid_get_desc(struct usbd_class_data *const c_data, const enum usbd_speed speed)
{
	ARG_UNUSED(c_data);
	ARG_UNUSED(speed);
	return ccid_fs_desc;
}

static int ccid_init(struct usbd_class_data *c_data)
{
	g_cdata = c_data;
	return 0;
}

static const struct usbd_class_api ccid_api = {
	.update = ccid_update,
	.control_to_host = ccid_control_to_host,
	.control_to_dev = ccid_control_to_dev,
	.request = ccid_request,
	.get_desc = ccid_get_desc,
	.enable = ccid_enable,
	.disable = ccid_disable,
	.init = ccid_init,
};

USBD_DEFINE_CLASS(ccid_0, &ccid_api, NULL, NULL);

/* ---- application-thread API ---- */
bool ccid_class_is_open(void)
{
	return atomic_get(&g_open) != 0;
}

bool ccid_class_take_opened(void)
{
	return atomic_set(&g_reopened, 0) != 0;
}

size_t ccid_class_poll_command(uint8_t *buf, size_t cap)
{
	size_t n = g_rx_len;

	if (n == 0 || n > cap) {
		return 0;
	}
	memcpy(buf, g_rx, n);
	g_rx_len = 0;
	return n;
}

bool ccid_class_send(const uint8_t *data, size_t len)
{
	struct net_buf *buf;

	if (!atomic_get(&g_open) || g_cdata == NULL) {
		return false;
	}
	buf = usbd_ep_buf_alloc(g_cdata, ccid_desc.in_ep.bEndpointAddress, len > 0 ? len : EP_SIZE);
	if (buf == NULL) {
		return false;
	}
	net_buf_add_mem(buf, data, len);
	atomic_set(&g_in_zlp, len > 0 && (len % EP_SIZE) == 0);
	k_sem_reset(&g_in_done);
	if (usbd_ep_enqueue(g_cdata, buf) != 0) {
		net_buf_unref(buf);
		return false;
	}
	return k_sem_take(&g_in_done, K_MSEC(500)) == 0 && atomic_get(&g_open);
}

void ccid_class_arm_out(void)
{
	if (g_cdata != NULL) {
		enqueue_out(g_cdata);
	}
}

bool ccid_class_notify(const uint8_t msg[2])
{
	struct net_buf *buf;

	if (!atomic_get(&g_open) || g_cdata == NULL || atomic_set(&g_int_busy, 1)) {
		return false;
	}
	buf = usbd_ep_buf_alloc(g_cdata, ccid_desc.int_ep.bEndpointAddress, 8);
	if (buf == NULL) {
		atomic_set(&g_int_busy, 0);
		return false;
	}
	net_buf_add_mem(buf, msg, 2);
	if (usbd_ep_enqueue(g_cdata, buf) != 0) {
		net_buf_unref(buf);
		atomic_set(&g_int_busy, 0);
		return false;
	}
	return true;
}
