mod i2c;
mod spi;

use core::fmt::Debug;

pub use i2c::I2cInterface;
pub use spi::SpiInterface;

pub trait Interface {
    type Error: Debug;

    fn do_command(&mut self, cmd: u8) -> Result<(), Self::Error>;
    fn read_reg(&mut self, reg: u8) -> Result<u8, Self::Error>;
    fn write_reg(&mut self, reg: u8, val: u8) -> Result<(), Self::Error>;
    fn read_fifo(&mut self, data: &mut [u8]) -> Result<(), Self::Error>;
    fn write_fifo(&mut self, data: &[u8]) -> Result<(), Self::Error>;

    /// Local patch (tag emulation): reads `out.len()` consecutive registers starting at `reg`. The
    /// default reads them one by one; the I2C interface does it in one transfer for space A.
    fn read_regs(&mut self, reg: u8, out: &mut [u8]) -> Result<(), Self::Error> {
        for (i, b) in out.iter_mut().enumerate() {
            *b = self.read_reg(reg + i as u8)?;
        }
        Ok(())
    }

    /// Local patch (tag emulation): loads the passive target memory A (UID, ATQA, SAK) with the
    /// "load PT memory A config" opcode 0xA0.
    fn write_pt_memory_a(&mut self, data: &[u8]) -> Result<(), Self::Error>;
}
