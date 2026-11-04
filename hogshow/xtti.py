"""Decoder for the XTTI serialisation used in the ``xtti`` BLOB columns.

Everything is big-endian.  The grammar (reverse engineered, see docs/FORMAT.md):

    object      := D7 31  type:u16  length:u32  payload            (top level / array element / boxed)
    field       := D7 31  type:u16  field:u16  length:u32  payload  (member of a struct)
    null        := D7 31  00 00
    end         := D7 33

``length`` counts itself (4 bytes) plus the payload, so
``payload_len = length - 4``.

A payload is one of

* **struct**   - ``field* end``
* **array**    - ``count:u32`` then ``count`` elements then ``end``; an element is either
  an ``object`` (no field id) or an *inline struct* ``field* end``
* **map**      - ``count:u32`` then ``2*count`` key/value items then ``end``
* **pointer**  - exactly one ``object``/``null`` (a boxed, possibly polymorphic value)
* **leaf**     - raw bytes (ints, bools, doubles, UTF-16BE strings, packed blobs ...)

There is no schema in the file, so the decoder tries those interpretations in order
and keeps the first one that consumes the payload exactly.  Type ids and field ids are
numeric; ``schema.py`` maps the ones we understand to names.
"""
from __future__ import annotations

import struct
from typing import Iterator, Optional

TAG = b"\xd7\x31"
END = b"\xd7\x33"

STRUCT, ARRAY, MAP, POINTER, LEAF, NULL, INLINE = (
    "struct", "array", "map", "pointer", "leaf", "null", "inline")


class Node:
    __slots__ = ("kind", "type", "field", "raw", "children", "count")

    def __init__(self, kind, type_=0, field=None, raw=None, children=None, count=None):
        self.kind = kind
        self.type = type_
        self.field = field
        self.raw = raw
        self.children = children if children is not None else []
        self.count = count

    # ---- navigation helpers -------------------------------------------------
    def get(self, field_id: int) -> Optional["Node"]:
        for c in self.children:
            if c.field == field_id:
                return c
        return None

    def path(self, *field_ids: int) -> Optional["Node"]:
        n = self
        for fid in field_ids:
            if n is None:
                return None
            if n.kind == POINTER and n.children:
                n = n.children[0]
            n = n.get(fid)
        return n

    def walk(self) -> Iterator["Node"]:
        yield self
        for c in self.children:
            yield from c.walk()

    def find_type(self, type_id: int) -> Iterator["Node"]:
        for n in self.walk():
            if n.type == type_id and n.kind != INLINE:
                yield n

    def find_field(self, field_id: int) -> Iterator["Node"]:
        for n in self.walk():
            if n.field == field_id:
                yield n

    # ---- leaf accessors ----------------------------------------------------
    def u8(self):
        return self.raw[0] if self.raw else None

    def u32(self):
        return struct.unpack(">I", self.raw[:4])[0] if self.raw and len(self.raw) >= 4 else None

    def i32(self):
        return struct.unpack(">i", self.raw[:4])[0] if self.raw and len(self.raw) >= 4 else None

    def f64(self):
        return struct.unpack(">d", self.raw[:8])[0] if self.raw and len(self.raw) >= 8 else None

    def boolean(self):
        return bool(self.raw[0]) if self.raw else None

    def string(self):
        return decode_string(self.raw) if self.raw is not None else None

    def __repr__(self):
        f = "" if self.field is None else f" f=0x{self.field:x}"
        return f"<{self.kind} t=0x{self.type:x}{f} n={len(self.children)}>"


def decode_string(raw: bytes) -> Optional[str]:
    """Strings are ``u32 char_count`` followed by UTF-16BE code units."""
    if raw is None or len(raw) < 4:
        return None
    n = struct.unpack(">I", raw[:4])[0]
    if len(raw) != 4 + 2 * n:
        return None
    try:
        return raw[4:].decode("utf-16-be")
    except UnicodeDecodeError:
        return None


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------

def _item(b: bytes, pos: int, end: int):
    """Parse one tagged item (with or without a field id) at ``pos``."""
    if pos + 4 > end or b[pos:pos + 2] != TAG:
        return None
    t = struct.unpack_from(">H", b, pos + 2)[0]
    if t == 0:
        return Node(NULL), pos + 4
    if pos + 10 <= end:  # with field id
        f, ln = struct.unpack_from(">HI", b, pos + 4)
        e = pos + 6 + ln
        if ln >= 4 and e <= end:
            v = _value(b, pos + 10, e, t, f)
            if v is not None:
                return v, e
    if pos + 8 <= end:  # without field id
        ln = struct.unpack_from(">I", b, pos + 4)[0]
        e = pos + 4 + ln
        if ln >= 4 and e <= end:
            v = _value(b, pos + 8, e, t, None)
            if v is not None:
                return v, e
    return None


def _items(b: bytes, pos: int, end: int):
    """``field* END`` -> (children, position after END)."""
    out = []
    while pos < end:
        if b[pos:pos + 2] == END:
            return out, pos + 2
        r = _item(b, pos, end)
        if r is None:
            return None
        out.append(r[0])
        pos = r[1]
    return None


def _value(b: bytes, s: int, e: int, t: int, f):
    n = e - s
    if n >= 2 and b[e - 2:e] == END:
        # struct
        r = _items(b, s, e)
        if r is not None and r[1] == e:
            return Node(STRUCT, t, f, children=r[0])
        if n >= 6:
            cnt = struct.unpack_from(">I", b, s)[0]
            if cnt < 1_000_000:
                # array of objects / inline structs
                p, out, ok = s + 4, [], True
                for _ in range(cnt):
                    r = _item(b, p, e)
                    if r is not None and (r[0].kind == NULL or r[0].field is None):
                        out.append(r[0]); p = r[1]; continue
                    r = _items(b, p, e)
                    if r is None:
                        ok = False; break
                    out.append(Node(INLINE, t, None, children=r[0])); p = r[1]
                if ok and p == e - 2:
                    return Node(ARRAY, t, f, children=out, count=cnt)
                # map: count, then key/value pairs
                p, out, ok = s + 4, [], True
                for _ in range(2 * cnt):
                    r = _item(b, p, e)
                    if r is None:
                        ok = False; break
                    out.append(r[0]); p = r[1]
                if ok and p == e - 2:
                    return Node(MAP, t, f, children=out, count=cnt)
                # array with an extra u32 header (count, capacity?)
                if n >= 10:
                    p, out, ok = s + 8, [], True
                    for _ in range(cnt):
                        r = _item(b, p, e)
                        if r is not None and r[0].field is None:
                            out.append(r[0]); p = r[1]; continue
                        r = _items(b, p, e)
                        if r is None:
                            ok = False; break
                        out.append(Node(INLINE, t, None, children=r[0])); p = r[1]
                    if ok and p == e - 2:
                        node = Node(ARRAY, t, f, children=out, count=cnt)
                        node.raw = b[s + 4:s + 8]  # keep the extra header
                        return node
    if n >= 4 and b[s:s + 2] == TAG:
        r = _item(b, s, e)
        if r is not None and r[1] == e and (r[0].kind == NULL or r[0].field is None):
            return Node(POINTER, t, f, children=[r[0]])
    return Node(LEAF, t, f, raw=bytes(b[s:e]))


def parse(blob: bytes) -> Node:
    """Parse a complete ``xtti`` column value."""
    if not blob or blob[:2] != TAG:
        raise ValueError("not an XTTI blob")
    t, ln = struct.unpack_from(">HI", blob, 2)
    node = _value(blob, 8, min(4 + ln, len(blob)), t, None)
    return node


# ---------------------------------------------------------------------------
# generic JSON conversion (lossless enough to re-inspect anything)
# ---------------------------------------------------------------------------

DOUBLE_TYPES = {0x6cf, 0x672}


def leaf_value(n: Node):
    raw = n.raw
    s = decode_string(raw)
    if s is not None and len(raw) > 4:
        return s
    if n.type in DOUBLE_TYPES and len(raw) == 8:
        return struct.unpack(">d", raw)[0]
    if len(raw) == 1:
        return raw[0]
    if len(raw) == 2:
        return struct.unpack(">H", raw)[0]
    if len(raw) == 4:
        return struct.unpack(">i", raw)[0]
    return raw.hex()


def to_json(n: Node, names=None) -> dict:
    names = names or {}
    d = {"type": f"0x{n.type:x}"} if n.kind not in (NULL, INLINE) else {}
    if n.field is not None:
        d["field"] = f"0x{n.field:x}"
        if n.field in names:
            d["name"] = names[n.field]
    if n.kind == NULL:
        return {"null": True}
    if n.kind == LEAF:
        d["value"] = leaf_value(n)
        return d
    if n.kind in (ARRAY, MAP):
        d["kind"] = n.kind
    elif n.kind == POINTER:
        d["kind"] = "pointer"
    d["children"] = [to_json(c, names) for c in n.children]
    return d


def dump_text(n: Node, indent: int = 0, out=None) -> list:
    """Human readable tree dump (used for exploring unknown fields)."""
    out = [] if out is None else out
    pre = "  " * indent
    if n.kind == NULL:
        out.append(pre + "NULL")
        return out
    if n.kind == INLINE:
        out.append(pre + "{elem}")
    else:
        fs = "" if n.field is None else f" f=0x{n.field:x}"
        if n.kind == LEAF:
            v = leaf_value(n)
            hx = n.raw.hex() if len(n.raw) <= 32 else n.raw[:32].hex() + f"..({len(n.raw)}B)"
            shown = "" if (isinstance(v, str) and v == n.raw.hex()) else f"  = {v!r}"
            out.append(f"{pre}(0x{n.type:x}){fs} {hx}{shown}")
            return out
        extra = {ARRAY: f" ARRAY n={n.count}", MAP: f" MAP n={n.count}", POINTER: " PTR"}.get(n.kind, "")
        out.append(f"{pre}[0x{n.type:x}]{fs}{extra}")
    for c in n.children:
        dump_text(c, indent + 1, out)
    return out
