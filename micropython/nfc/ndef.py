"""Minimal NDEF encode/decode for Type 2 tags (text and URI records)."""
URI_PREFIXES = [
    "", "http://www.", "https://www.", "http://", "https://", "tel:", "mailto:",
    "ftp://anonymous:anonymous@", "ftp://ftp.", "ftps://", "sftp://", "smb://",
    "nfs://", "ftp://", "dav://", "news:", "telnet://", "imap:", "rtsp://",
    "urn:", "pop:", "sip:", "sips:", "tftp:", "btspp://", "btl2cap://",
    "btgoep://", "tcpobex://", "irdaobex://", "file://", "urn:epc:id:",
    "urn:epc:tag:", "urn:epc:pat:", "urn:epc:raw:", "urn:epc:", "urn:nfc:",
]

TLV_NDEF = 0x03
TLV_TERMINATOR = 0xFE
TLV_NULL = 0x00


class NdefRecord:
    def __init__(self, tnf, type, payload):
        self.tnf = tnf
        self.type = type
        self.payload = payload

    def describe(self) -> str:
        if self.tnf == 1 and self.type == b"T" and self.payload:
            lang_len = self.payload[0] & 0x3F
            lang = self.payload[1:1 + lang_len].decode("ascii", "replace")
            text = self.payload[1 + lang_len:].decode("utf-8", "replace")
            return f"text[{lang}]: {text}"
        if self.tnf == 1 and self.type == b"U" and self.payload:
            code = self.payload[0]
            prefix = URI_PREFIXES[code] if code < len(URI_PREFIXES) else ""
            return f"uri: {prefix}{self.payload[1:].decode('utf-8', 'replace')}"
        return f"tnf={self.tnf} type={self.type!r} payload={self.payload.hex()}"


def text_record(text: str, lang: str = "en") -> NdefRecord:
    lang_b = lang.encode("ascii")
    return NdefRecord(1, b"T", bytes([len(lang_b)]) + lang_b + text.encode("utf-8"))


def uri_record(uri: str) -> NdefRecord:
    best = 0
    for i, prefix in enumerate(URI_PREFIXES):
        if prefix and uri.startswith(prefix) and len(prefix) > len(URI_PREFIXES[best]):
            best = i
    return NdefRecord(1, b"U", bytes([best]) + uri[len(URI_PREFIXES[best]):].encode("utf-8"))


def encode_message(records: list[NdefRecord]) -> bytes:
    out = b""
    for i, rec in enumerate(records):
        short = len(rec.payload) < 256
        header = 0x00
        header |= 0x80 if i == 0 else 0  # MB
        header |= 0x40 if i == len(records) - 1 else 0  # ME
        header |= 0x10 if short else 0  # SR
        header |= rec.tnf & 0x07
        out += bytes([header, len(rec.type)])
        out += bytes([len(rec.payload)]) if short else len(rec.payload).to_bytes(4, "big")
        out += rec.type + rec.payload
    return out


def decode_message(data: bytes) -> list[NdefRecord]:
    records, i = [], 0
    while i < len(data):
        header = data[i]
        sr, il = bool(header & 0x10), bool(header & 0x08)
        type_len = data[i + 1]
        i += 2
        if sr:
            payload_len = data[i]
            i += 1
        else:
            payload_len = int.from_bytes(data[i:i + 4], "big")
            i += 4
        id_len = data[i] if il else 0
        i += 1 if il else 0
        rec_type = data[i:i + type_len]
        i += type_len + id_len
        payload = data[i:i + payload_len]
        i += payload_len
        records.append(NdefRecord(header & 0x07, rec_type, payload))
        if header & 0x40:  # ME
            break
    return records


def wrap_tlv(message: bytes) -> bytes:
    """NDEF TLV + terminator, as stored from page 4 of a Type 2 tag."""
    if len(message) < 0xFF:
        length = bytes([len(message)])
    else:
        length = b"\xFF" + len(message).to_bytes(2, "big")
    return bytes([TLV_NDEF]) + length + message + bytes([TLV_TERMINATOR])


def find_message(user_data: bytes) -> bytes | None:
    """Locate the NDEF TLV in raw user memory; None if there is none."""
    i = 0
    while i < len(user_data):
        tag = user_data[i]
        if tag == TLV_NULL:
            i += 1
        elif tag == TLV_TERMINATOR:
            return None
        else:
            if i + 1 >= len(user_data):
                return None
            length = user_data[i + 1]
            start = i + 2
            if length == 0xFF:
                length = int.from_bytes(user_data[i + 2:i + 4], "big")
                start = i + 4
            if tag == TLV_NDEF:
                return user_data[start:start + length]
            i = start + length
    return None
