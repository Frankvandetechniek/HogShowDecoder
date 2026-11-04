"""Decoder for the packed *programming data* blob (XTTI type 0x1c7, field 0x397).

Cue, palette and scene bodies don't store their values as XTTI fields.  Instead
they hold one leaf with a compact, little-endian record stream::

    header:u16                      (0x0001 in palettes, 0x0100 in cues)
    record*                         each record = D7 31 01 C5 <record body>
    D7 33

Record body (reverse engineered, see docs/FORMAT.md)::

    rtype:u16 LE      0x0701 = programmed value, 0x1300 = palette reference
    target:u32 LE     fixture id (cues) / fixture *type* id (palettes) / 0x3FE = any fixture
    ftag:u8           low nibble = entry kind, bit 0x40 = flag
    function:u16 LE   function id (low 14 bits); bits 0xC000 are flags
    [kind 1 only]     u32, palette_id:u32, u32      (reference to a palette)
    TLV*              tag:u8 len:u8 data[len]
    FF 00             end of record

Known TLVs:
    0x05 (len 10)  value     : flags:u16, value:f64 LE
    0x07 (len 28)  effect    : u32, effect_table:u32, f64, u16, u16, f64
    0x08 (len 2)   unknown 16-bit word (seen with entry kind 7)
    0x02 (len 1)   unknown byte (always 0x20 so far; only in cues)
    0x09 (len 2)   unknown 16-bit word (with palette references)

Value encoding: percentage-type values are stored as ``0.5 + fraction/32`` so
0 %% == 0.5 (0x3FE0000000000000) and 100 %% == 0.53125 (0x3FE1000000000000).
Values in real-world units (degrees, CIE x/y, colour wheel slots ...) are stored
as plain doubles.  ``decode_level`` applies that heuristic.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import List, Optional

REC = b"\xd7\x31\x01\xc5"
TARGET_ANY = 0x3FE

ENTRY_KINDS = {
    1: "palette_ref",
    4: "value",
    5: "effect",
    7: "kind7",
}


def decode_level(d: float):
    """Return (percent or None, raw) for a stored value double."""
    if d is None:
        return None
    if 0.5 <= d <= 0.53125:
        return round((d - 0.5) * 3200.0, 6)
    return None


@dataclass
class ValueRecord:
    rtype: int
    target: int
    entry_kind: int
    entry_flags: int
    function_id: int
    function_flags: int
    value: Optional[float] = None          # raw double
    value_flags: Optional[int] = None
    percent: Optional[float] = None        # decoded percentage if applicable
    palette_id: Optional[int] = None
    palette_ref_extra: Optional[tuple] = None
    effect: Optional[dict] = None
    tlvs: dict = field(default_factory=dict)
    raw: str = ""

    def to_dict(self):
        d = {
            "record_type": f"0x{self.rtype:04x}",
            "target": self.target,
            "entry": ENTRY_KINDS.get(self.entry_kind, f"kind{self.entry_kind}"),
            "function_id": self.function_id,
        }
        if self.value is not None:
            d["value"] = self.value
            d["value_flags"] = f"0x{self.value_flags:04x}"
            if self.percent is not None:
                d["percent"] = self.percent
        if self.palette_id is not None:
            d["palette_id"] = self.palette_id
        if self.effect is not None:
            d["effect"] = self.effect
        other = {k: v for k, v in self.tlvs.items() if k not in ("05", "07")}
        if other:
            d["extra"] = other
        d["raw"] = self.raw
        return d


def _parse_record(body: bytes, p: int = 0):
    start = p
    rtype = struct.unpack_from("<H", body, p)[0]; p += 2
    target = struct.unpack_from("<I", body, p)[0]; p += 4
    ftag = body[p]; p += 1
    fword = struct.unpack_from("<H", body, p)[0]; p += 2
    rec = ValueRecord(rtype=rtype, target=target, entry_kind=ftag & 0x0F,
                      entry_flags=ftag & 0xF0, function_id=fword & 0x3FFF,
                      function_flags=fword & 0xC000)
    if rec.entry_kind == 1:
        a, pal, c = struct.unpack_from("<iIi", body, p); p += 12
        rec.palette_id = pal
        rec.palette_ref_extra = (a, c)
    while p + 2 <= len(body):
        tag, ln = body[p], body[p + 1]
        if tag == 0xFF:
            p += 2
            break
        data = body[p + 2:p + 2 + ln]
        p += 2 + ln
        if tag == 0x05 and ln == 10:
            rec.value_flags = struct.unpack_from("<H", data, 0)[0]
            rec.value = struct.unpack_from("<d", data, 2)[0]
            rec.percent = decode_level(rec.value)
        elif tag == 0x07 and ln == 28:
            a, table, x, w1, w2, y = struct.unpack("<IIdHHd", data)
            rec.effect = {"u0": a, "effect_table_id": table, "d0": x,
                          "w0": w1, "w1": w2, "d1": y, "raw": data.hex()}
        rec.tlvs[f"{tag:02x}"] = data.hex()
    rec.raw = body[start:p].hex()
    return rec, p


def parse_value_blob(raw: bytes) -> dict:
    """Parse the 0x1c7 leaf payload into header + list of records."""
    if raw is None or len(raw) < 4:
        return {"header": None, "records": []}
    header = struct.unpack_from(">H", raw, 0)[0]
    body = raw[2:]
    if body.endswith(b"\xd7\x33"):
        body = body[:-2]
    records: List[ValueRecord] = []
    errors = []
    p = 0
    while p < len(body):
        if body[p:p + 4] != REC:
            # lost sync: resynchronise on the next record marker
            nxt = body.find(REC, p + 1)
            errors.append({"offset": p, "raw": body[p:nxt if nxt >= 0 else len(body)].hex()})
            if nxt < 0:
                break
            p = nxt
            continue
        try:
            rec, p = _parse_record(body, p + 4)
            records.append(rec)
        except (struct.error, IndexError) as e:
            nxt = body.find(REC, p + 4)
            errors.append({"offset": p, "error": str(e)})
            if nxt < 0:
                break
            p = nxt
    return {"header": header, "records": records, "errors": errors}
