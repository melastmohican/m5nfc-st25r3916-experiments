//! NTAG215 memory model: 135 pages x 4 bytes, user memory = pages 4-129 (504 bytes).

pub const PAGE_SIZE: usize = 4;
pub const TOTAL_PAGES: usize = 135;
pub const DUMP_BYTES: usize = TOTAL_PAGES * PAGE_SIZE;
pub const USER_FIRST: u8 = 4;
pub const USER_LAST: u8 = 129;
pub const USER_BYTES: usize = (USER_LAST as usize - USER_FIRST as usize + 1) * PAGE_SIZE; // 504
pub const USER_READ_BYTES: usize = 512; // 32 READs of 16 bytes cover pages 4..131
pub const PAGE_CC: u8 = 3;
pub const PAGE_PWD: u8 = 133;
pub const PAGE_PACK: u8 = 134;
pub const CC_NTAG215: [u8; 4] = [0xE1, 0x10, 0x3E, 0x00];

/// 4-bit ACK a Type 2 tag sends after a WRITE (NXP NTAG21x datasheet, section 9.3, Table 23).
pub const ACK: u8 = 0x0A;

/// Meaning of a 4-bit NAK code (NTAG21x Table 23; other Type 2 chips may add codes).
pub fn nack_text(code: u8) -> &'static str {
    match code {
        0x0 => "tag refused the write: invalid argument (bad page address)",
        0x1 => "tag refused the write: parity or CRC error in the command",
        0x4 => "tag refused the write: authentication counter overflow",
        0x5 => "tag refused the write: EEPROM write error",
        _ => "tag refused the write: unknown NAK code",
    }
}

/// NTAG family name from a `GET_VERSION` reply (NTAG21x datasheet, Table 28).
pub fn ntag_type(version: &[u8]) -> &'static str {
    match version {
        [_, 0x04, 0x04, _, _, _, 0x0F, _] => "NTAG213 (NXP)",
        [_, 0x04, 0x04, _, _, _, 0x11, _] => "NTAG215 (NXP)",
        [_, 0x04, 0x04, _, _, _, 0x13, _] => "NTAG216 (NXP)",
        [_, 0x04, 0x04, ..] => "NTAG, unknown size (NXP)",
        [_, 0x04, ..] => "NXP, not an NTAG",
        _ => "unknown",
    }
}

/// `READ_CNT` returns the 24-bit counter low byte first.
pub fn counter_value(b: &[u8]) -> Option<u32> {
    match b {
        [a, b, c] => Some(*a as u32 | (*b as u32) << 8 | (*c as u32) << 16),
        _ => None,
    }
}

/// Lock/config/CC pages (outside 4..=129) need `force`, and their bits are one-way. UID pages
/// 0-1 and out-of-range pages are never writable.
pub fn may_write(page: u8, force: bool) -> bool {
    if page < 2 || page as usize >= TOTAL_PAGES {
        return false;
    }
    (USER_FIRST..=USER_LAST).contains(&page) || force
}

/// Pages that cannot be read back to verify a write (PWD/PACK read as zeros, 2 and 130 hold
/// lock bits that may not match what was written).
pub fn verifiable(page: u8) -> bool {
    !matches!(page, PAGE_PWD | PAGE_PACK | 2 | 130)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ntag_names_and_counter() {
        assert_eq!(ntag_type(&[0, 4, 4, 2, 1, 0, 0x11, 3]), "NTAG215 (NXP)");
        assert_eq!(ntag_type(&[0, 4, 4, 2, 1, 0, 0x0F, 3]), "NTAG213 (NXP)");
        assert_eq!(ntag_type(&[0, 4, 4, 2, 1, 0, 0x13, 3]), "NTAG216 (NXP)");
        assert_eq!(ntag_type(&[0, 4, 4, 2, 1, 0, 0x55, 3]), "NTAG, unknown size (NXP)");
        assert_eq!(ntag_type(&[0, 4, 1, 1, 1, 0, 0x18, 5]), "NXP, not an NTAG");
        assert_eq!(ntag_type(&[1, 2]), "unknown");
        assert_eq!(counter_value(&[0x01, 0x02, 0x03]), Some(0x030201));
        assert_eq!(counter_value(&[1, 2]), None);
    }

    #[test]
    fn nack_codes() {
        assert!(nack_text(0x5).contains("EEPROM"));
        assert!(nack_text(0x0).contains("invalid argument"));
        assert!(nack_text(0x7).contains("unknown"));
    }

    #[test]
    fn guards() {
        assert!(!may_write(0, true));
        assert!(!may_write(1, true));
        assert!(!may_write(3, false));
        assert!(may_write(3, true));
        assert!(may_write(4, false));
        assert!(may_write(129, false));
        assert!(!may_write(130, false));
        assert!(!may_write(135, true));
        assert!(!verifiable(133));
    }
}
