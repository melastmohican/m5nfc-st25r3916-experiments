//! Minimal NDEF text and URI records for Type 2 tags, no heap.
//!
//! No existing crate fits: Foundation Devices' `ndef` is GPL-3.0 and has no URI record type,
//! and `ndef-rs` (MIT) needs `std`. This is a port of `../tinygo/nfc/ndef.go`.
use core::fmt::Write;

use heapless::Vec;

pub const TLV_NDEF: u8 = 0x03;
pub const TLV_TERMINATOR: u8 = 0xFE;
pub const MAX_PAYLOAD: usize = 256;
pub const MAX_RECORDS: usize = 4;

const URI_PREFIXES: [&str; 36] = [
    "", "http://www.", "https://www.", "http://", "https://", "tel:", "mailto:",
    "ftp://anonymous:anonymous@", "ftp://ftp.", "ftps://", "sftp://", "smb://", "nfs://", "ftp://",
    "dav://", "news:", "telnet://", "imap:", "rtsp://", "urn:", "pop:", "sip:", "sips:", "tftp:",
    "btspp://", "btl2cap://", "btgoep://", "tcpobex://", "irdaobex://", "file://", "urn:epc:id:",
    "urn:epc:tag:", "urn:epc:pat:", "urn:epc:raw:", "urn:epc:", "urn:nfc:",
];

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Record {
    pub tnf: u8,
    pub typ: Vec<u8, 8>,
    pub payload: Vec<u8, MAX_PAYLOAD>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum NdefError {
    TooLarge,
    Malformed,
}

fn well_known(typ: &[u8], payload: Vec<u8, MAX_PAYLOAD>) -> Record {
    Record { tnf: 1, typ: Vec::from_slice(typ).unwrap(), payload }
}

/// Well-known "T" record: status byte (UTF-8, language length), language, text.
pub fn text_record(text: &str, lang: &str) -> Result<Record, NdefError> {
    if lang.len() > 0x3F {
        return Err(NdefError::TooLarge);
    }
    let mut p: Vec<u8, MAX_PAYLOAD> = Vec::new();
    p.push(lang.len() as u8).map_err(|_| NdefError::TooLarge)?;
    p.extend_from_slice(lang.as_bytes()).map_err(|_| NdefError::TooLarge)?;
    p.extend_from_slice(text.as_bytes()).map_err(|_| NdefError::TooLarge)?;
    Ok(well_known(b"T", p))
}

/// Well-known "U" record using the longest matching prefix code.
pub fn uri_record(uri: &str) -> Result<Record, NdefError> {
    let mut best = 0;
    for (i, p) in URI_PREFIXES.iter().enumerate().skip(1) {
        if uri.starts_with(p) && p.len() > URI_PREFIXES[best].len() {
            best = i;
        }
    }
    let mut p: Vec<u8, MAX_PAYLOAD> = Vec::new();
    p.push(best as u8).map_err(|_| NdefError::TooLarge)?;
    p.extend_from_slice(uri[URI_PREFIXES[best].len()..].as_bytes()).map_err(|_| NdefError::TooLarge)?;
    Ok(well_known(b"U", p))
}

/// Encodes the records as one message. Fails if it does not fit in `N` bytes.
pub fn encode_message<const N: usize>(recs: &[Record]) -> Result<Vec<u8, N>, NdefError> {
    let mut out: Vec<u8, N> = Vec::new();
    let ok = |r: Result<(), u8>| r.map_err(|_| NdefError::TooLarge);
    for (i, r) in recs.iter().enumerate() {
        let short = r.payload.len() < 256;
        let mut h = r.tnf & 7;
        if i == 0 {
            h |= 0x80; // MB
        }
        if i == recs.len() - 1 {
            h |= 0x40; // ME
        }
        if short {
            h |= 0x10; // SR
        }
        ok(out.push(h))?;
        ok(out.push(r.typ.len() as u8))?;
        if short {
            ok(out.push(r.payload.len() as u8))?;
        } else {
            let n = r.payload.len();
            out.extend_from_slice(&[0, 0, (n >> 8) as u8, n as u8]).map_err(|_| NdefError::TooLarge)?;
        }
        out.extend_from_slice(&r.typ).map_err(|_| NdefError::TooLarge)?;
        out.extend_from_slice(&r.payload).map_err(|_| NdefError::TooLarge)?;
    }
    Ok(out)
}

/// Decodes records up to and including the one with the ME flag.
pub fn decode_message(data: &[u8]) -> Result<Vec<Record, MAX_RECORDS>, NdefError> {
    let mut recs = Vec::new();
    let mut i = 0;
    while i < data.len() {
        if i + 2 > data.len() {
            return Err(NdefError::Malformed);
        }
        let h = data[i];
        let (sr, il) = (h & 0x10 != 0, h & 0x08 != 0);
        let type_len = data[i + 1] as usize;
        i += 2;
        let plen = if sr {
            let v = *data.get(i).ok_or(NdefError::Malformed)? as usize;
            i += 1;
            v
        } else {
            let b = data.get(i..i + 4).ok_or(NdefError::Malformed)?;
            i += 4;
            u32::from_be_bytes([b[0], b[1], b[2], b[3]]) as usize
        };
        let id_len = if il {
            let v = *data.get(i).ok_or(NdefError::Malformed)? as usize;
            i += 1;
            v
        } else {
            0
        };
        if type_len > 8 || plen > MAX_PAYLOAD || i + type_len + id_len + plen > data.len() {
            return Err(NdefError::Malformed);
        }
        let typ = Vec::from_slice(&data[i..i + type_len]).unwrap();
        i += type_len + id_len;
        let payload = Vec::from_slice(&data[i..i + plen]).unwrap();
        i += plen;
        recs.push(Record { tnf: h & 7, typ, payload }).map_err(|_| NdefError::TooLarge)?;
        if h & 0x40 != 0 {
            break;
        }
    }
    Ok(recs)
}

/// NDEF TLV plus terminator, as stored from page 4. Fails if it exceeds `N` bytes.
pub fn wrap_tlv<const N: usize>(msg: &[u8]) -> Result<Vec<u8, N>, NdefError> {
    let mut out: Vec<u8, N> = Vec::new();
    let r = |x: Result<(), u8>| x.map_err(|_| NdefError::TooLarge);
    r(out.push(TLV_NDEF))?;
    if msg.len() < 0xFF {
        r(out.push(msg.len() as u8))?;
    } else {
        out.extend_from_slice(&[0xFF, (msg.len() >> 8) as u8, msg.len() as u8]).map_err(|_| NdefError::TooLarge)?;
    }
    out.extend_from_slice(msg).map_err(|_| NdefError::TooLarge)?;
    r(out.push(TLV_TERMINATOR))?;
    Ok(out)
}

/// Locates the NDEF TLV in raw user memory.
pub fn find_message(user: &[u8]) -> Option<&[u8]> {
    let mut i = 0;
    while i < user.len() {
        let tag = user[i];
        if tag == 0x00 {
            i += 1;
            continue;
        }
        if tag == TLV_TERMINATOR || i + 1 >= user.len() {
            return None;
        }
        let (mut len, mut start) = (user[i + 1] as usize, i + 2);
        if len == 0xFF {
            let b = user.get(i + 2..i + 4)?;
            len = (b[0] as usize) << 8 | b[1] as usize;
            start = i + 4;
        }
        if tag == TLV_NDEF {
            return user.get(start..start + len);
        }
        i = start + len;
    }
    None
}

impl Record {
    /// "text[en]: hello", "uri: https://example.com", or "tnf=.. type=.. payload=..".
    pub fn describe(&self, out: &mut impl Write) {
        let p = &self.payload;
        if self.tnf == 1 && self.typ.as_slice() == b"T" && !p.is_empty() {
            let l = ((p[0] & 0x3F) as usize).min(p.len() - 1);
            let _ = write!(
                out,
                "text[{}]: {}",
                core::str::from_utf8(&p[1..1 + l]).unwrap_or("?"),
                core::str::from_utf8(&p[1 + l..]).unwrap_or("?")
            );
        } else if self.tnf == 1 && self.typ.as_slice() == b"U" && !p.is_empty() {
            let prefix = URI_PREFIXES.get(p[0] as usize).copied().unwrap_or("");
            let _ = write!(out, "uri: {}{}", prefix, core::str::from_utf8(&p[1..]).unwrap_or("?"));
        } else {
            let _ = write!(out, "tnf={} type=", self.tnf);
            for b in &self.typ {
                let _ = write!(out, "{:02x}", b);
            }
            let _ = write!(out, " payload=");
            for b in p {
                let _ = write!(out, "{:02x}", b);
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use heapless::String;

    fn desc(r: &Record) -> String<300> {
        let mut s = String::new();
        r.describe(&mut s);
        s
    }

    #[test]
    fn round_trip() {
        let t = text_record("hello", "en").unwrap();
        let u = uri_record("https://example.com/x").unwrap();
        assert_eq!(u.payload[0], 4);
        let msg = encode_message::<504>(&[t, u]).unwrap();
        let blob = wrap_tlv::<504>(&msg).unwrap();
        let found = find_message(&blob).unwrap();
        assert_eq!(found, msg.as_slice());
        let recs = decode_message(found).unwrap();
        assert_eq!(recs.len(), 2);
        assert_eq!(desc(&recs[0]).as_str(), "text[en]: hello");
        assert_eq!(desc(&recs[1]).as_str(), "uri: https://example.com/x");
    }

    #[test]
    fn long_payload_uses_long_record() {
        let long = core::str::from_utf8(&[b'a'; 253]).unwrap();
        let r = text_record(long, "en").unwrap(); // 256 payload bytes
        assert_eq!(r.payload.len(), 256);
        let msg = encode_message::<504>(&[r]).unwrap();
        assert_eq!(msg[0] & 0x10, 0); // SR clear
        assert_eq!(decode_message(&msg).unwrap()[0].payload.len(), 256);
    }

    #[test]
    fn rejects_truncated() {
        let msg = encode_message::<64>(&[text_record("hello", "en").unwrap()]).unwrap();
        assert_eq!(decode_message(&msg[..msg.len() - 1]), Err(NdefError::Malformed));
    }

    #[test]
    fn too_large_for_tag() {
        let long = core::str::from_utf8(&[b'a'; 200]).unwrap();
        let recs = [text_record(long, "en").unwrap(), text_record(long, "en").unwrap(), text_record(long, "en").unwrap()];
        assert_eq!(encode_message::<504>(&recs), Err(NdefError::TooLarge));
    }
}
