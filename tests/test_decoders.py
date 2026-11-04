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

    def test_level_encoding(self):
        self.assertEqual(decode_level(0.5), 0.0)
        self.assertEqual(decode_level(0.53125), 100.0)
        self.assertIsNone(decode_level(168.0))


SAMPLE = os.environ.get("HOG_SAMPLE")


@unittest.skipUnless(SAMPLE and os.path.exists(SAMPLE), "set HOG_SAMPLE=/path/to/show.hog4show")
class SampleShowTests(unittest.TestCase):
    def test_load(self):
        from hogshow import Show
        d = Show.load(SAMPLE).to_dict()
        self.assertTrue(d["fixtures"])
        self.assertTrue(d["groups"])


if __name__ == "__main__":
    unittest.main()
