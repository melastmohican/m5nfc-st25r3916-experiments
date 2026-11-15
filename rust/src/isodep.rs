//! Persistent ISO-DEP session for PC/SC pass-through.
//!
//! A DESFire card keeps state between commands (pending "additional frame" replies, the selected
//! application, authentication), so the card must stay activated across APDUs. The `rnfc`
//! ISO-DEP object borrows the chip, so one task owns it and the CCID backend talks to that task
//! over channels. While a session is open the task holds the chip lock: the serial command line
//! waits, and the session closes by itself after `IDLE` without commands.
use core::sync::atomic::{AtomicBool, Ordering};

use embassy_futures::select::{select, Either};
use embassy_sync::blocking_mutex::raw::CriticalSectionRawMutex;
use embassy_sync::channel::Channel;
use embassy_time::{Duration, Timer};
use heapless::Vec;
use m5nfc::ccid::{BeError, MAX_DATA};
use rnfc::iso14443a::Poller;
use rnfc::iso_dep::IsoDepA;
use rnfc::traits::iso14443a::Reader as A;
use rnfc::traits::iso_dep::Reader as DepReader;

use crate::backend::SharedRf;
use crate::nfc::Rf;

pub enum Cmd {
    Open,
    Transmit(Vec<u8, MAX_DATA>),
    Close,
}

pub struct Opened {
    pub ats: Vec<u8, 32>,
}

pub enum Resp {
    Opened(Result<Opened, BeError>),
    Data(Result<Vec<u8, MAX_DATA>, BeError>),
}

pub static CMD: Channel<CriticalSectionRawMutex, Cmd, 1> = Channel::new();
pub static RESP: Channel<CriticalSectionRawMutex, Resp, 1> = Channel::new();
/// True while a session holds the chip. Lets other tasks avoid locking it.
pub static OPEN: AtomicBool = AtomicBool::new(false);

const IDLE: Duration = Duration::from_secs(20);

#[embassy_executor::task]
pub async fn task(rf: &'static SharedRf) -> ! {
    loop {
        match CMD.receive().await {
            Cmd::Open => {}
            Cmd::Transmit(_) => {
                RESP.send(Resp::Data(Err(BeError::Absent))).await;
                continue;
            }
            Cmd::Close => continue,
        }
        let mut guard = rf.lock().await;
        session(&mut guard).await;
        OPEN.store(false, Ordering::Release);
    }
}

async fn session(rf: &mut Rf) {
    let Ok(mut ll) = rf.start_iso14443a().await else {
        RESP.send(Resp::Opened(Err(BeError::Hw))).await;
        return;
    };
    let mut poller = Poller::new(&mut ll);
    let Ok(card) = poller.select_any().await else {
        RESP.send(Resp::Opened(Err(BeError::Absent))).await;
        return;
    };
    let _ = card.uid(); // the stateless probe before this already reported the UID
    let Ok(mut dep) = IsoDepA::new(card).await else {
        RESP.send(Resp::Opened(Err(BeError::Absent))).await;
        return;
    };
    let ats = Vec::from_slice(dep.ats()).unwrap_or_default();
    OPEN.store(true, Ordering::Release);
    RESP.send(Resp::Opened(Ok(Opened { ats }))).await;

    loop {
        match select(CMD.receive(), Timer::after(IDLE)).await {
            Either::First(Cmd::Transmit(apdu)) => {
                let mut rx = [0u8; MAX_DATA];
                match dep.transceive(&apdu, &mut rx).await {
                    Ok(n) => {
                        let v = Vec::from_slice(&rx[..n]).unwrap_or_default();
                        RESP.send(Resp::Data(Ok(v))).await;
                    }
                    Err(_) => {
                        // The card is gone or the link broke: end the session.
                        RESP.send(Resp::Data(Err(BeError::Absent))).await;
                        return;
                    }
                }
            }
            Either::First(Cmd::Close) => {
                let _ = dep.deselect().await;
                return;
            }
            Either::First(Cmd::Open) => {
                RESP.send(Resp::Opened(Err(BeError::Hw))).await; // already open: not expected
                return;
            }
            Either::Second(()) => return, // idle too long: release the chip and the card
        }
    }
}
