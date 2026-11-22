//! Host-testable parts (no hardware access): command-line parsing and, later, the CCID
//! message layer.
#![cfg_attr(not(test), no_std)]

pub mod ccid;
pub mod emu;
pub mod ndef;
pub mod parse;
pub mod tag;
