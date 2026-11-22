//! Feather RP2350 + M5Stack NFC unit, Rust (Embassy). Step 1: chip identity over USB serial.
#![no_std]
#![no_main]

use embassy_executor::Spawner;
use embassy_rp::bind_interrupts;
use embassy_rp::i2c::{self, I2c};
use embassy_rp::peripherals::USB;
use embassy_rp::usb::{Driver, InterruptHandler};
use embassy_sync::mutex::Mutex;
use embassy_time::Timer;
use embassy_usb::class::cdc_acm::{CdcAcmClass, State};
use embassy_usb::UsbDevice;
use panic_halt as _;
use rnfc_st25r39::{I2cInterface, Interface, St25r39};

mod backend;
mod class;
mod cli;
mod emulate;
mod isodep;
mod nfc;
use static_cell::StaticCell;

bind_interrupts!(struct Irqs {
    USBCTRL_IRQ => InterruptHandler<USB>;
});

type UsbDriver = Driver<'static, USB>;

#[embassy_executor::task]
async fn usb_task(mut usb: UsbDevice<'static, UsbDriver>) -> ! {
    usb.run().await
}

type Ccid = class::CcidClass<'static, UsbDriver>;

#[embassy_executor::task]
async fn ccid_task(mut ccid: Ccid, rf: &'static backend::SharedRf) -> ! {
    let mut be = backend::NfcBackend::new(rf);
    ccid.run(&mut be, async || backend::card_present(rf).await).await
}

#[embassy_executor::main]
async fn main(spawner: Spawner) {
    let p = embassy_rp::init(Default::default());

    // Qwiic port: I2C1, SDA GP2, SCL GP3.
    let mut cfg = i2c::Config::default();
    cfg.frequency = 400_000;
    let bus = I2c::new_blocking(p.I2C1, p.PIN_3, p.PIN_2, cfg);
    let mut iface = I2cInterface::new(bus, 0x50);

    let driver = Driver::new(p.USB, Irqs);
    let mut config = embassy_usb::Config::new(0x2e8a, 0x000a);
    config.manufacturer = Some("Experiment");
    config.product = Some("NFC reader (Rust)");
    config.serial_number = Some("rust-1");
    config.max_power = 100;
    config.max_packet_size_0 = 64;
    config.composite_with_iads = true;
    config.device_class = 0xEF;
    config.device_sub_class = 2;
    config.device_protocol = 1;

    static CONFIG_DESC: StaticCell<[u8; 256]> = StaticCell::new();
    static BOS_DESC: StaticCell<[u8; 256]> = StaticCell::new();
    static CONTROL_BUF: StaticCell<[u8; 64]> = StaticCell::new();
    static CDC_STATE: StaticCell<State> = StaticCell::new();
    let mut builder = embassy_usb::Builder::new(
        driver,
        config,
        CONFIG_DESC.init([0; 256]),
        BOS_DESC.init([0; 256]),
        &mut [],
        CONTROL_BUF.init([0; 64]),
    );
    let cdc = CdcAcmClass::new(&mut builder, CDC_STATE.init(State::new()), 64);
    let ccid = class::CcidClass::new(&mut builder);
    spawner.spawn(usb_task(builder.build()).unwrap());

    Timer::after_secs(3).await;
    let ident = iface.read_reg(0x3F).ok();
    static RF: StaticCell<backend::SharedRf> = StaticCell::new();
    let rf: &'static backend::SharedRf = RF.init(Mutex::new(St25r39::new(iface, nfc::NoIrq).await.unwrap()));
    spawner.spawn(ccid_task(ccid, rf).unwrap());
    spawner.spawn(isodep::task(rf).unwrap());
    let mut cli = cli::Cli::new(cdc, ident, rf);
    cli.run().await
}
