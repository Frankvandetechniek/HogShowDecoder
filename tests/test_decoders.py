import os
import struct
import unittest

from hogshow import xtti
from hogshow.values import parse_value_blob, decode_level

T, E = b"\xd7\x31", b"\xd7\x33"


def field(t, f, payload):
    return T + struct.pack(">HHI", t, f, len(payload) + 4) + payload


def obj(t, payload):
    return T + struct.pack(">HI", t, len(payload) + 4) + payload


def string(s):
    enc = s.encode("utf-16-be")
    return struct.pack(">I", len(s)) + enc


class XttiTests(unittest.TestCase):
    def test_struct_with_fields(self):
        ident = field(0xBA, 0x1E8, field(0x22, 0x65, string("All Fixtures"))
                      + field(0x21, 0x64, struct.pack(">HH", 3, 0)) + E)
        blob = obj(0x1BE, field(0x12, 0x1E5, b"\x00") + ident + E)
        root = xtti.parse(blob)
        self.assertEqual(root.kind, xtti.STRUCT)
        self.assertEqual(root.type, 0x1BE)
        self.assertEqual(root.path(0x1E8, 0x65).string(), "All Fixtures")
        self.assertEqual(root.get(0x1E5).boolean(), False)

    def test_array_and_pointer(self):
        arr = field(0x621, 0xC33, struct.pack(">I", 2)
                    + obj(0x64, struct.pack(">II", 3, 1))
                    + obj(0x64, struct.pack(">II", 3, 2)) + E)
        ptr = field(0x1B3, 0x209, T + b"\x00\x00")
        root = xtti.parse(obj(0x1AD, arr + ptr + E))
        a = root.get(0xC33)
        self.assertEqual(a.kind, xtti.ARRAY)
        self.assertEqual([c.raw for c in a.children],
                         [struct.pack(">II", 3, 1), struct.pack(">II", 3, 2)])
        p = root.get(0x209)
        self.assertEqual(p.kind, xtti.POINTER)
        self.assertEqual(p.children[0].kind, xtti.NULL)

    def test_inline_struct_array(self):
        elem = field(0x16, 0xC6, struct.pack(">I", 7)) + E
        root = xtti.parse(obj(0xF3, field(0xFC, 0xB9, struct.pack(">I", 1) + elem + E) + E))
        a = root.get(0xB9)
        self.assertEqual(a.kind, xtti.ARRAY)
        self.assertEqual(a.children[0].kind, xtti.INLINE)
        self.assertEqual(a.children[0].get(0xC6).u32(), 7)


class ValueBlobTests(unittest.TestCase):
    def test_palette_value_record(self):
        # real record from an intensity palette ("10%")
        raw = bytes.fromhex("0001d73101c50107fe0300004401000"
                            "50a00409a99999999 19e03fff00d733".replace(" ", ""))
        out = parse_value_blob(raw)
        self.assertEqual(len(out["records"]), 1)
        r = out["records"][0]
        self.assertEqual(r.target, 0x3FE)
        self.assertEqual(r.function_id, 1)
        self.assertEqual(r.entry_kind, 4)
        self.assertAlmostEqual(r.percent, 10.0, places=6)

    def test_cue_palette_reference(self):
        rec = bytes.fromhex("001314000000019444ffffffff37000000ffffffff020120090202 20ff00".replace(" ", ""))
        out = parse_value_blob(b"\x01\x00" + T + b"\x01\xc5" + rec + E)
        r = out["records"][0]
        self.assertEqual(r.target, 20)
        self.assertEqual(r.function_id, 0x494)
        self.assertEqual(r.palette_id, 0x37)

    def test_fade_short_and_long_form(self):
        # Hog 5 cue 1 after changing its fade to 5 s: short form TLV 02 = 0x50
        short = bytes.fromhex("010700000000240140020150050a00c0000000000000e03fff00")
        # 1.5 s cue: long form TLV 03 = 01 00 + u32 LE 300 (5 ms units)
        long_ = bytes.fromhex("010700000000240180030601002c010000050a04c0cdcccccccccce03fff00")
        out = parse_value_blob(b"\x00\x00" + T + b"\x01\xc5" + short + T + b"\x01\xc5" + long_ + E)
        self.assertEqual([r.fade_seconds for r in out["records"]], [5.0, 1.5])
        self.assertEqual(out["errors"], [])

    def test_hog5_selection_section(self):
        rec = bytes.fromhex("010700000000240140020100050a00c0000000000000e03fff00")
        sel = bytes.fromhex(
            "00000002"
            "d7310014000000060000d73105460000001a00000010030000000400000005000000060000 00d733"
            "d7310014000000060001d73105460000001a00000010010000000200000007000000080000 00d733"
            "d733".replace(" ", ""))
        out = parse_value_blob(b"\x00\x00" + T + b"\x01\xc5" + rec + E + sel)
        self.assertEqual(out["errors"], [])
        self.assertEqual(out["selections"], [{"index": 0, "ids": [3, 4, 5, 6]},
                                             {"index": 1, "ids": [1, 2, 7, 8]}])

    def test_empty_hog5_blob(self):
        out = parse_value_blob(bytes.fromhex("0000d73300000000d733"))
        self.assertEqual((out["records"], out["selections"], out["errors"]), ([], [], []))

    def test_object_number(self):
        from hogshow.model import hog_number
        self.assertEqual(hog_number(struct.pack(">HH", 12, 5000)), "12.5")
        self.assertEqual(hog_number(struct.pack(">HH", 1, 0)), "1")
        self.assertIsNone(hog_number(struct.pack(">HH", 0xFFFF, 0)))

    def test_level_encoding(self):
        self.assertEqual(decode_level(0.5), 0.0)
        self.assertEqual(decode_level(0.53125), 100.0)
        self.assertIsNone(decode_level(168.0))


SAMPLES = [p for p in os.environ.get("HOG_SAMPLE", "").split(os.pathsep) if p]


@unittest.skipUnless(SAMPLES, "set HOG_SAMPLE=/path/a.hog4show:/path/b.hogshow")
class SampleShowTests(unittest.TestCase):
    def test_load(self):
        from hogshow import Show
        for path in SAMPLES:
            with self.subTest(path=path):
                d = Show.load(path).to_dict()
                self.assertTrue(d["fixtures"])
                self.assertTrue(d["groups"])
                undecoded = [v for cl in d["cuelists"] for c in cl["cues"]
                             for v in c.get("values", []) if "undecoded" in v]
                undecoded += [v for p in d["palettes"] for v in p.get("values", [])
                              if "undecoded" in v]
                self.assertEqual(undecoded, [])


if __name__ == "__main__":
    unittest.main()
