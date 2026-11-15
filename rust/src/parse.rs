//! Command-line helpers shared by the serial CLI.
use heapless::Vec;

pub const MAX_ARGS: usize = 10;

/// Splits on whitespace; double quotes group words.
pub fn tokenize(line: &str) -> Vec<&str, MAX_ARGS> {
    let mut out = Vec::new();
    let b = line.as_bytes();
    let mut i = 0;
    while i < b.len() {
        match b[i] {
            b' ' | b'\t' => i += 1,
            b'"' => {
                let start = i + 1;
                let end = line[start..].find('"').map(|j| start + j).unwrap_or(b.len());
                if out.push(&line[start..end]).is_err() {
                    break;
                }
                i = end + 1;
            }
            _ => {
                let start = i;
                while i < b.len() && b[i] != b' ' && b[i] != b'\t' {
                    i += 1;
                }
                if out.push(&line[start..i]).is_err() {
                    break;
                }
            }
        }
    }
    out
}

/// Exactly 8 hex digits into 4 bytes.
pub fn parse_hex4(s: &str) -> Option<[u8; 4]> {
    if s.len() != 8 {
        return None;
    }
    let mut out = [0u8; 4];
    for (i, o) in out.iter_mut().enumerate() {
        *o = u8::from_str_radix(s.get(2 * i..2 * i + 2)?, 16).ok()?;
    }
    Some(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tokenizes_quotes() {
        let t = tokenize(r#"write-ndef --text "hello world" --lang en"#);
        assert_eq!(t.as_slice(), &["write-ndef", "--text", "hello world", "--lang", "en"]);
    }

    #[test]
    fn hex4() {
        assert_eq!(parse_hex4("0A1b2C3d"), Some([0x0A, 0x1B, 0x2C, 0x3D]));
        assert_eq!(parse_hex4("0A1b2C"), None);
        assert_eq!(parse_hex4("zz000000"), None);
    }
}
