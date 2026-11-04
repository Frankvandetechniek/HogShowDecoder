"""High level model: turns the decoded XTTI trees into show objects.

Only fields whose meaning was established are given names here; every object
also carries its row id, so anything else can be looked up in the full raw dump
(``--raw``).  Field / type ids are documented in docs/FORMAT.md.
"""
from __future__ import annotations

import struct
from typing import Dict, List, Optional

from . import xtti
from .container import read_container
from .values import TARGET_ANY, parse_value_blob

# --- field ids ---------------------------------------------------------------
F_IDENT_NAME = 0x65      # inside the 0xBA "identity" struct
F_IDENT_NOTE = 0x66
F_IDENT_NUMBER = 0x64

T_IDENT = 0xBA           # identity struct (name / number / comment)
T_KIND = 0xBD            # editor-kind bitmask wrapper -> field 0x6B
F_KIND_VALUE = 0x6B
T_VALUE_BLOB = 0x1C7     # packed programming data
T_OBJREF = 0x64          # (class:u32, id:u32)

KIND_BITS = {1: "intensity", 2: "position", 4: "colour", 8: "beam",
             16: "effect", 32: "time", 64: "control"}

OBJREF_CLASS = {2: "cuelist", 3: "cue"}  # tentative


def hog_number(raw: bytes) -> Optional[str]:
    """Hog object numbers are u16 major + u16 minor/10000 (0xFFFF major = unnumbered)."""
    if raw is None or len(raw) != 4:
        return None
    major, minor = struct.unpack(">HH", raw)
    if major == 0xFFFF:
        return None
    if minor == 0:
        return f"{major}"
    # minor is in 1/10000 units: 12.5 is stored as (12, 5000)
    return f"{major}.{minor:04d}".rstrip("0")


def kind_names(mask: Optional[int]) -> List[str]:
    if not mask:
        return []
    return [n for b, n in KIND_BITS.items() if mask & b] or [f"0x{mask:x}"]


def identity(root: xtti.Node) -> dict:
    """Find the first 0xBA identity struct at the top level of an object."""
    for c in root.children:
        if c.type == T_IDENT and c.kind == xtti.STRUCT:
            out = {}
            n = c.get(F_IDENT_NAME)
            if n is not None:
                out["name"] = n.string() or ""
            n = c.get(F_IDENT_NUMBER)
            if n is not None:
                out["number"] = hog_number(n.raw)
            n = c.get(F_IDENT_NOTE)
            if n is not None and n.string():
                out["note"] = n.string()
            return out
    return {}


def kind_of(root: xtti.Node) -> Optional[int]:
    for c in root.children:
        if c.type == T_KIND and c.kind == xtti.STRUCT:
            v = c.get(F_KIND_VALUE)
            if v is not None:
                return v.u32()
    return None


def objrefs(node: xtti.Node):
    for n in node.walk():
        if n.type == T_OBJREF and n.kind == xtti.LEAF and len(n.raw) == 8:
            cls, oid = struct.unpack(">II", n.raw)
            yield cls, oid


class Show:
    """Loaded show file. ``Show.load(path).to_dict()`` gives everything as JSON."""

    def __init__(self, container, conn, include_attic: bool = False):
        self.container = container
        self.conn = conn
        self.include_attic = include_attic
        self.tables: Dict[str, Dict[int, dict]] = {}
        self._load_tables()

    # ------------------------------------------------------------------ loading
    @classmethod
    def load(cls, path: str, include_attic: bool = False) -> "Show":
        """Load a .hog4show (Hog 4 OS) or .hogshow (Hog 5) file.

        Rows with ``attic != 0`` are deleted objects kept for undo; they are
        skipped unless ``include_attic`` is set.
        """
        cont = read_container(path)
        return cls(cont, cont.open_db(), include_attic)

    def _load_tables(self):
        names = [r[0] for r in self.conn.execute(
            "select name from sqlite_master where type='table'")]
        for t in names:
            cols = [r[1] for r in self.conn.execute(f'pragma table_info("{t}")')]
            if "xtti" not in cols:
                continue
            rows = {}
            for row in self.conn.execute(f'select * from "{t}"'):
                d = dict(zip(cols, row))
                blob = d.pop("xtti")
                try:
                    d["tree"] = xtti.parse(blob) if blob else None
                except Exception as e:  # pragma: no cover - keep going on odd rows
                    d["tree"], d["error"] = None, str(e)
                rows[d["id"]] = d
            self.tables[t] = rows

    def versioning(self) -> dict:
        import json
        out = {}
        try:
            for name, js in self.conn.execute("select tablename, json from versioning"):
                out[name] = json.loads(js)
        except Exception:
            pass
        return out

    def rows(self, table):
        for rid, d in sorted(self.tables.get(table, {}).items()):
            if d.get("tree") is None:
                continue
            if d.get("attic") and not self.include_attic:
                continue
            yield rid, d

    def live(self, table, rid):
        """Row ``rid`` of ``table`` if it exists and is not in the attic."""
        d = self.tables.get(table, {}).get(rid)
        if d is None or d.get("tree") is None:
            return None
        if d.get("attic") and not self.include_attic:
            return None
        return d

    def format_info(self) -> dict:
        """Container / serialisation versions (Hog 4 OS vs Hog 5 differences)."""
        xver = {}
        for t, rows in self.tables.items():
            for d in rows.values():
                v = d.get("xver")
                key = "none (Hog 4 OS layout, no xver column)" if v is None else str(v)
                xver[key] = xver.get(key, 0) + 1
        attic = sum(1 for rows in self.tables.values() for d in rows.values() if d.get("attic"))
        ident = self.container.ident or {}
        return {
            "archive_xtti_version": ident.get("xtti_version"),
            "show_version": ident.get("show_version"),
            "row_xver_counts": xver,
            "attic_rows": attic,
        }

    # ------------------------------------------------------------- lookups
    def function_names(self) -> Dict[int, str]:
        out = {}
        for rid, d in self.rows("functions"):
            n = d["tree"].get(0x87)
            out[rid] = n.string() if n is not None else None
        return out

    def fixture_types(self) -> Dict[int, dict]:
        out = {}
        meta = {r[0]: r for r in self.conn.execute(
            "select id, origin, footprint, bypass, product from metatypes")} \
            if "metatypes" in self.tables else {}
        for r in self.conn.execute("select id, name, mfr, rev, status, uuid, protocols, attic from types"):
            tid, name, mfr, rev, status, uuid, proto, attic = r
            if attic and not self.include_attic:
                continue
            m = meta.get(tid)
            out[tid] = {
                "id": tid, "name": name, "manufacturer": m[1] if m else None,
                "product": m[4] if m else None, "footprint": m[2] if m else None,
                "revision": rev, "uuid": uuid, "protocols": proto,
            }
        return out

    def fixtures(self) -> Dict[int, dict]:
        types = self.fixture_types()
        out = {}
        for rid, d in self.rows("fixtures"):
            t = d["tree"]
            type_id = None
            tn = t.path(0xAD, 0x41)
            if tn is not None:
                type_id = tn.u32()
            numbers = []
            num_arr = t.path(0xB0, 0xC34)
            if num_arr is not None:
                numbers = [c.u32() for c in num_arr.children if c.kind == xtti.LEAF]
            patch = []
            parr = t.get(0xAF)
            if parr is not None:
                for p in parr.children:
                    if p.kind == xtti.NULL:
                        continue
                    node = p.children[0] if p.kind == xtti.POINTER else p
                    off = node.get(0xD3)
                    uni = node.get(0xD2)
                    dp = node.get(0xD1)
                    patch.append({
                        "universe_index": uni.u32() if uni else None,
                        "dmx_offset": off.u32() if off else None,
                        "dmx_address": off.u32() + 1 if off else None,
                        "node": dp.u32() if dp else None,
                    })
            ft = types.get(type_id, {})
            out[rid] = {
                "id": rid,
                "number": ".".join(str(n) for n in numbers) if numbers else None,
                "type_id": type_id,
                "type": ft.get("name"),
                "patch": patch,
            }
        return out

    def groups(self, fixtures=None) -> List[dict]:
        fixtures = fixtures or self.fixtures()
        out = []
        for rid, d in self.rows("groups"):
            g = {"id": rid, **identity(d["tree"]), "members": []}
            body = self.live("groupbodies", rid)
            if body:
                g["members"] = self._group_members(body["tree"], fixtures)
            out.append(g)
        return out

    @staticmethod
    def _group_members(tree: xtti.Node, fixtures) -> List[dict]:
        """Group membership is a packed leaf (type 0x1E4) of fixed 14-byte elements::

            D7 31 01 E6  u16 (=1)  order:u32  fixture_id:u32     (big-endian), then D7 33
        """
        members = []
        for n in tree.find_type(0x1E4):
            raw = n.raw or b""
            p = 0
            while p + 14 <= len(raw) and raw[p:p + 4] == b"\xd7\x31\x01\xe6":
                unk, order, fid = struct.unpack_from(">HII", raw, p + 4)
                fx = fixtures.get(fid, {})
                members.append({"fixture_id": fid, "fixture_number": fx.get("number"),
                                "fixture_type": fx.get("type"), "order": order})
                p += 14
        members.sort(key=lambda m: m["order"])
        return members

    def _all_fixtures(self) -> Dict[int, dict]:
        """Fixture id -> {number, type, deleted}, including attic (deleted) fixtures.

        Cues can still hold values for a fixture that was deleted from the show.
        """
        if getattr(self, "_fx_all", None) is None:
            prev, self.include_attic = self.include_attic, True
            try:
                allfx = self.fixtures()
            finally:
                self.include_attic = prev
            for fid, f in allfx.items():
                f["deleted"] = bool(self.tables["fixtures"][fid].get("attic"))
            self._fx_all = allfx
        return self._fx_all

    def _programming(self, tree: xtti.Node, fixtures, types, fnames, sel_out=None,
                     scope: str = "fixture") -> List[dict]:
        """Decode a body's value records.

        ``scope`` says what a record's target id refers to: ``"fixture"`` for cues and
        scenes, ``"type"`` for palettes (fixture and type ids overlap, so this can't be
        inferred from the id).  0x3FE is always "any fixture".
        """
        allfx = self._all_fixtures()
        out = []
        for n in tree.find_type(T_VALUE_BLOB):
            parsed = parse_value_blob(n.raw)
            for rec in parsed["records"]:
                d = rec.to_dict()
                tgt = rec.target
                if tgt == TARGET_ANY:
                    d["target_kind"] = "any"
                elif scope == "type":
                    d["target_kind"] = "fixture_type"
                    d["fixture_type"] = types.get(tgt, {}).get("name")
                else:
                    d["target_kind"] = "fixture"
                    fx = allfx.get(tgt)
                    if fx:
                        d["fixture_number"] = fx["number"]
                        d["fixture_type"] = fx["type"]
                        if fx["deleted"]:
                            d["fixture_deleted"] = True
                d["function"] = fnames.get(rec.function_id)
                out.append(d)
            for e in parsed["errors"]:
                out.append({"undecoded": e})
            if sel_out is not None:
                for sel in parsed.get("selections", []):
                    ids = sel.get("ids", [])
                    sel_out.append({**sel, "fixture_numbers": [
                        allfx[i]["number"] if i in allfx else None for i in ids]})
        return out

    def palettes(self, fixtures=None) -> List[dict]:
        fixtures = fixtures or self.fixtures()
        types, fnames = self.fixture_types(), self.function_names()
        names = {r: d for r, d in self.rows("palettes")}
        out = []
        for rid, d in names.items():
            ident = identity(d["tree"])
            if not ident:
                continue
            mask = kind_of(d["tree"])
            p = {"id": rid, **ident, "kind_mask": mask, "kind": kind_names(mask)}
            body = self.live("palettebodies", rid)
            if body:
                p["values"] = self._programming(body["tree"], fixtures, types, fnames, scope="type")
            out.append(p)
        return out

    def scenes(self, fixtures=None) -> List[dict]:
        fixtures = fixtures or self.fixtures()
        types, fnames = self.fixture_types(), self.function_names()
        out = []
        for rid, d in self.rows("scenes"):
            ident = identity(d["tree"])
            if not ident:
                continue
            s = {"id": rid, **ident}
            body = self.live("scenebodies", rid)
            if body:
                s["values"] = self._programming(body["tree"], fixtures, types, fnames)
            out.append(s)
        return out

    @staticmethod
    def _times(tree: xtti.Node) -> dict:
        """Collect all time values (type 0x33 = milliseconds) keyed by their field path."""
        out = {}

        def rec(n, path):
            for c in n.children:
                p = path + ([f"0x{c.field:x}"] if c.field is not None else [])
                if c.kind == xtti.LEAF and c.type == 0x33 and len(c.raw) == 4:
                    out["/".join(p)] = struct.unpack(">i", c.raw)[0] / 1000.0
                elif c.children:
                    rec(c, p)
        rec(tree, [])
        return out

    @staticmethod
    def cue_timing(tree: xtti.Node) -> dict:
        """Cue timing block (field 0x1C4).

        Confirmed by diffing two Hog 5 shows where only cue 1's fade changed 0 -> 5 s:
        ``0x166``, ``0x167`` and both halves of ``0x64F[0]`` changed 0 -> 5000 ms.
        ``0x650`` stayed 0 and is taken to be the delay (not yet confirmed).
        Times are type 0x33 (milliseconds) wrapped in a 0x152 struct (field 0x15D).
        """
        tm = tree.get(0x1C4)
        if tm is None:
            return {}

        def ms(n):
            v = n.get(0x15D) if n is not None else None
            return v.i32() / 1000.0 if v is not None and v.raw is not None else None

        def pairs(fid):
            a = tm.get(fid)
            if a is None:
                return None
            return [{"in": ms(e.get(0x15E)), "out": ms(e.get(0x15F))} for e in a.children]

        out = {"fade_in": ms(tm.get(0x166)), "fade_out": ms(tm.get(0x167))}
        f, dl = pairs(0x64F), pairs(0x650)
        if f is not None:
            out["fade"] = f
        if dl is not None:
            out["delay"] = dl
        return out

    def cuelists(self, fixtures=None) -> List[dict]:
        fixtures = fixtures or self.fixtures()
        types, fnames = self.fixture_types(), self.function_names()
        cue_rows = dict(self.rows("cues"))
        out = []
        for rid, d in self.rows("cuelists"):
            ident = identity(d["tree"])
            if not ident:
                continue
            cl = {"id": rid, **ident, "cue_ids": [], "cues": [],
                  "times_seconds": self._times(d["tree"])}
            content = d["tree"].get(0x1D1)
            if content is not None:
                cl["cue_ids"] = [oid for cls, oid in objrefs(content) if cls == 3]
            for cid in cl["cue_ids"]:
                crow = cue_rows.get(cid)
                if not crow:
                    continue
                cue = {"id": cid, **identity(crow["tree"]),
                       "timing": self.cue_timing(crow["tree"]),
                       "times_seconds": self._times(crow["tree"])}
                body = self.live("cuebodies", cid)
                if body:
                    sels = []
                    cue["values"] = self._programming(body["tree"], fixtures, types, fnames, sels)
                    if sels:
                        cue["selections"] = sels
                cl["cues"].append(cue)
            out.append(cl)
        return out

    def pages(self) -> List[dict]:
        out = []
        for rid, d in self.rows("pages"):
            ident = identity(d["tree"])
            if not ident:
                continue
            masters = []
            arr = d["tree"].get(0x203)
            if arr is not None:
                for m in arr.children:
                    node = m.children[0] if m.kind == xtti.POINTER else m
                    refs = [{"class": OBJREF_CLASS.get(c, c), "id": i} for c, i in objrefs(node)]
                    f = {f"0x{c.field:x}": c.u32() for c in node.children
                         if c.kind == xtti.LEAF and c.raw is not None and len(c.raw) == 4}
                    masters.append({"objects": refs, "fields": f})
            out.append({"id": rid, **ident, "masters": masters})
        return out

    # ------------------------------------------------------------ export
    def to_dict(self) -> dict:
        fixtures = self.fixtures()
        return {
            "ident": self.container.ident,
            "format": self.format_info(),
            "software": next(iter(self.versioning().values()), {}).get("software_version"),
            "table_counts": {t: sum(1 for d in r.values() if self.include_attic or not d.get("attic"))
                             for t, r in sorted(self.tables.items())},
            "fixture_types": list(self.fixture_types().values()),
            "functions": [{"id": k, "name": v} for k, v in sorted(self.function_names().items())],
            "fixtures": list(fixtures.values()),
            "groups": self.groups(fixtures),
            "palettes": self.palettes(fixtures),
            "scenes": self.scenes(fixtures),
            "cuelists": self.cuelists(fixtures),
            "pages": self.pages(),
        }

    def raw_dump(self) -> dict:
        """Every row of every table as a generic JSON tree."""
        out = {}
        for t, rows in sorted(self.tables.items()):
            out[t] = []
            for rid, d in sorted(rows.items()):
                meta = {k: v for k, v in d.items() if k not in ("tree",)}
                meta["xtti"] = xtti.to_json(d["tree"]) if d.get("tree") is not None else None
                out[t].append(meta)
        return out

    def text_dump(self) -> str:
        lines = []
        for t, rows in sorted(self.tables.items()):
            for rid, d in sorted(rows.items()):
                meta = {k: v for k, v in d.items() if k not in ("tree",)}
                lines.append(f"===== {t} {meta}")
                if d.get("tree") is not None:
                    xtti.dump_text(d["tree"], 0, lines)
        return "\n".join(lines) + "\n"
