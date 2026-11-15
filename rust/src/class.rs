//! USB CCID class on embassy-usb: one interface (class 0x0B) with bulk OUT, bulk IN and an
//! interrupt IN endpoint for slot-change notifications.
use embassy_futures::join::join;
use embassy_time::{Duration, Timer};
use embassy_usb::driver::{Driver, Endpoint, EndpointError, EndpointIn, EndpointOut};
use embassy_usb::Builder;
use m5nfc::ccid::{self, Backend, Slot, HEADER_LEN, MAX_MESSAGE};

/// CCID functional descriptor (54 bytes, rev 1.10, short-APDU level); identical to
/// `kClassDesc` in `../arduino/src/CcidDriver.cpp`.
const CLASS_DESC: [u8; 54] = [
    0x36, 0x21, 0x10, 0x01, 0x00, 0x07, 0x03, 0x00, 0x00, 0x00, 0xA0, 0x0F, 0x00, 0x00, 0xA0, 0x0F, 0x00, 0x00,
    0x00, 0x00, 0x2A, 0x00, 0x00, 0x00, 0x40, 0x05, 0x00, 0x00, 0xFE, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0xBA, 0x00, 0x02, 0x00, 0x0F, 0x01, 0x00, 0x00, 0xFF, 0xFF, 0x00, 0x00, 0x00, 0x01,
];

const PKT: usize = 64;
const POLL: Duration = Duration::from_millis(300);

pub struct CcidClass<'d, D: Driver<'d>> {
    ep_out: D::EndpointOut,
    ep_in: D::EndpointIn,
    ep_int: D::EndpointIn,
}

impl<'d, D: Driver<'d>> CcidClass<'d, D> {
    pub fn new(builder: &mut Builder<'d, D>) -> Self {
        let mut func = builder.function(0x0B, 0x00, 0x00);
        let mut iface = func.interface();
        let mut alt = iface.alt_setting(0x0B, 0x00, 0x00, None);
        alt.descriptor(CLASS_DESC[1], &CLASS_DESC[2..]);
        let ep_out = alt.endpoint_bulk_out(None, PKT as u16);
        let ep_in = alt.endpoint_bulk_in(None, PKT as u16);
        let ep_int = alt.endpoint_interrupt_in(None, 8, 16);
        Self { ep_out, ep_in, ep_int }
    }

    /// Serves the host forever. `present` reports whether a card is on the reader (used for
    /// slot-change notifications).
    pub async fn run<B: Backend, F: AsyncFnMut() -> bool>(&mut self, backend: &mut B, mut present: F) -> ! {
        let Self { ep_out, ep_in, ep_int } = self;
        loop {
            join(ep_out.wait_enabled(), ep_in.wait_enabled()).await;
            ep_int.wait_enabled().await;
            let _ = join(Self::bulk(ep_out, ep_in, backend), Self::notify(ep_int, &mut present)).await;
        }
    }

    async fn bulk<B: Backend>(
        ep_out: &mut D::EndpointOut,
        ep_in: &mut D::EndpointIn,
        be: &mut B,
    ) -> Result<(), EndpointError> {
        let mut slot = Slot;
        let mut rx = [0u8; MAX_MESSAGE + PKT];
        loop {
            // Reassemble one message: the header says how long it is.
            let mut n = 0;
            loop {
                let got = ep_out.read(&mut rx[n..n + PKT]).await?;
                n += got;
                if n >= HEADER_LEN {
                    let want = HEADER_LEN + (rx[1] as usize | (rx[2] as usize) << 8);
                    if n >= want || got < PKT {
                        break;
                    }
                } else if got < PKT {
                    break;
                }
                if n + PKT > rx.len() {
                    break;
                }
            }
            if let Some(resp) = slot.handle(be, &rx[..n]).await {
                for chunk in resp.chunks(PKT) {
                    ep_in.write(chunk).await?;
                }
                if resp.len() % PKT == 0 {
                    ep_in.write(&[]).await?; // zero-length packet ends the transfer
                }
            }
        }
    }

    async fn notify<F: AsyncFnMut() -> bool>(
        ep_int: &mut D::EndpointIn,
        present: &mut F,
    ) -> Result<(), EndpointError> {
        let mut last: Option<bool> = None;
        loop {
            let now = present().await;
            if last != Some(now) {
                ep_int.write(&ccid::slot_change_message(now, true)).await?;
                last = Some(now);
            }
            Timer::after(POLL).await;
        }
    }
}
