# Hog 4 / Hog OS show file format (`.hog4show`)

This is reverse engineered from a show saved by Hog OS **v5.2.0 (build 212)**
(`xtti_version` 29). There is no public spec. Items marked **(?)** are
educated guesses and need confirming against more show files.

## 1. Layers

```
UE5_Music03_bck.hog4show      gzip
└── tar (ustar)
    ├── hog_ident.json        JSON header
    └── hogdatabase.mk2       SQLite 3 database   <- all show data
        └── table rows: id | sequence | xtti BLOB | attic
                                         └── XTTI binary tree (big-endian TLV)
                                             └── some leaves: packed little-endian
                                                 programming data (cue/palette values)
```

To get at the database by hand:

```bash
gzip -dc show.hog4show | tar x        # -> hog_ident.json, hogdatabase.mk2
sqlite3 hogdatabase.mk2 .tables
```

### hog_ident.json
```json
{"description":"Hog 5 Show","library_version":{"majorRevision":5,"minorRevision":2,...},
 "show_type":"show","show_version":{...},"timestamp":1785994163,"xtti_version":29}
```

### SQLite database
There are 52 tables, and almost all of them have the same layout:

| column     | meaning |
|------------|---------|
| `id`       | object id. The ids for fixtures, types and functions are referenced from other objects. |
| `sequence` | change counter / revision |
| `xtti`     | the object, serialised as XTTI (see §2) |
| `attic`    | non-zero = object is in the "attic" (deleted / undo history) **(?)** |

`types`, `metatypes` and `compound_types` also have plain columns
(`name`, `mfr`, `uuid`, `origin`, `footprint`, `product` …). `versioning`
holds one JSON row per table with the software version.

An object is usually split over two tables: a *header* (name, number,
flags) and a *body* (the content). Both use the **same `id`**:

| header       | body             | content |
|--------------|------------------|---------|
| `groups`     | `groupbodies`    | fixture membership |
| `palettes`   | `palettebodies`  | programmed values |
| `scenes`     | `scenebodies`    | programmed values |
| `cues`       | `cuebodies`      | programmed values |
| `cuelists`   | –                | cue list options and the list of cue refs |
| `types`      | `typebodies`     | fixture library definition |
| `preferences`| `preferencesbodies` | |
| `viewitems`  | `viewitembodies` | |

Other useful tables: `fixtures` (patch), `functions` (function names like
Intensity/Pan/Red…), `parameters`, `pages`, `directories`, `showusers`,
`macros`/`macrosteps`, `effecttables`, `curves`, `crossfadepaths`.

## 2. XTTI encoding (big-endian)

```
object  := D7 31  type:u16             length:u32  payload     top level, array element, boxed value
field   := D7 31  type:u16  field:u16  length:u32  payload     member of a struct
null    := D7 31  00 00
end     := D7 33
```

`length` counts itself plus the payload (`payload_len = length - 4`). `type`
is a class/type id and `field` is a member id. Both are global numbers and
the file has no names for them. A payload is one of:

| kind    | layout |
|---------|--------|
| struct  | `field* end` |
| array   | `count:u32` + `count` × (`object` or inline struct `field* end`) + `end` |
| map     | `count:u32` + `2×count` items (key, value) + `end` |
| pointer | exactly one `object` or `null` (boxed / polymorphic value) |
| leaf    | raw bytes |

There is no schema, so a decoder tries the payload kinds in that order and
keeps the first one that uses up the payload exactly. `hogshow/xtti.py` does
this.

Leaf encodings that were identified:

| type | meaning |
|------|---------|
| 0x12 | bool (1 byte) |
| 0x11 | u8 |
| 0x16, 0x15, 0x20, 0x36, … | u32 / i32 (`ffffffff` = none) |
| 0x33 | time in **milliseconds** (i32), e.g. `0x7d0` = 2.0 s |
| 0x22, 0x23 | string: `count:u32` + UTF-16BE |
| 0x21 | Hog object number: `major:u16 minor:u16`. `ffff` = unnumbered. 3 = `00030000` |
| 0x6cf, 0x672 | IEEE double |
| 0x64 | object reference `(class:u32, id:u32)`. Class 2 = cue list, 3 = cue **(?)** |

### Common sub-structures

* **Identity** `[0xBA]`: `0x65` name, `0x64` number (type 0x21), `0x66` note.
  It appears in every user-visible object.
* **Editor kind** `[0xBD]` → `0x6B` u32 bitmask: 1 = Intensity, 2 = Position,
  4 = Colour, 8 = Beam, 16 = Effect, 32 = Time **(?)**, 64 = Control.
  It is used on functions and palettes.
* **Value** `[0xC9]`: `0x531` unit/kind u8, `0x532` u8, `0x77` double. It is
  used for function defaults and ranges.

## 3. Objects

### Fixture types (`types`, `metatypes`, `typebodies`)
`types.name`, `metatypes.origin` (manufacturer), `metatypes.product`,
`metatypes.footprint` (DMX channels). The full library definition is in
`typebodies`.

### Fixtures (`fixtures`, root type 0xF2)
| path | meaning |
|------|---------|
| `0xAD/0x41` | fixture type id (→ `types.id`) |
| `0xB0/0xC34` | array of u32: the **user fixture number** (e.g. 110) |
| `0xAF[]` | patch points: `0xD1` node/DP **(?)**, `0xD2` universe index **(?)**, `0xD3` 0-based DMX offset (address = offset + 1) |
| `0xAE/0xC2[]` | per-function → parameter mapping |

### Functions (`functions`, 0xD6)
`0x87` name ("Intensity", "Pan", "Red", "v-Hue" …), `0x88/0x6B` editor kind,
`0x8D[]` ranges (name, default value, …). The function **id** is what
programming data refers to.

### Groups (`groups` 0x1BE + `groupbodies` 0x1C0)
The membership is in leaf `0x1EA/0x218` (type 0x1E4). It is a run of fixed
14-byte entries followed by `D7 33`:

```
D7 31 01 E6   unk:u16(=1)   order:u32   fixture_id:u32
```
`order` is the selection order inside the group.

### Palettes, scenes, cues: programming data
The body holds one leaf (type **0x1C7**, field 0x397) in a packed
**little-endian** format:

```
header:u16                 0x0001 (palettes) / 0x0100 (cues)
record*                    D7 31 01 C5 <record>
D7 33

record :=
  rtype:u16                0x0701 = value entry, 0x1300 = palette reference
  target:u32               fixture id (cues) | fixture TYPE id (palettes) | 0x3FE = any fixture
  entry:u8                 low nibble: 1 = palette ref, 4 = value, 5 = effect, 7 = ? ; bit 0x40 = flag
  function:u16             function id in the low 14 bits; bits 0xC000 = flags
  [entry==1] u32(-1) palette_id:u32 u32(-1)
  TLV*                     tag:u8 len:u8 data
  FF 00
```

| TLV | len | content |
|-----|-----|---------|
| 0x05 | 10 | value: `flags:u16, value:f64` |
| 0x07 | 28 | effect: `u32, effect_table:u32, f64, u16, u16, f64` **(?)** |
| 0x08 | 2  | unknown, used with entry kind 7 |
| 0x02 | 1  | unknown (always 0x20, cues only) |
| 0x09 | 2  | unknown (on palette references) |

**Value scaling.** Percentage values are stored as `0.5 + fraction/32`:

| stored double | hex | meaning |
|---|---|---|
| 0.5      | 3FE0000000000000 | 0 % |
| 0.503125 | 3FE019999999999A | 10 % |
| 0.515625 | 3FE0800000000000 | 50 % |
| 0.53125  | 3FE1000000000000 | 100 % |

Values in real units are plain doubles: CIE x = 0.3324, a colour-wheel slot
or angle = 168.0 **(?)**. Function ranges in `parameters` use the same
encoding: a 0..100 % range is stored as min `3FE0…` and max `3FE1…`.

### Cue lists (`cuelists` 0x1AD) and cues (`cues` 0x1A8 + `cuebodies` 0x19F)
* The cue list's cues are the object refs `(3, cue_id)` under field `0x1D1`.
* Cue list options are under `0x5CD`. The default times there are type 0x33
  values in ms.
* Cue timing is under `0x1C4`. Fields `0x166` and `0x167` are probably
  fade and delay **(?)**. Arrays `0x64F` and `0x650` hold per-category
  times **(?)**.

### Pages (`pages` 0x1D7)
`0x203[]` master slots. Each slot holds an object ref (class 2 = cue list)
and several u32 fields (master number etc. **(?)**).

## 4. How to map the remaining unknowns

Save two show files on the console (or Hog 4 PC) that differ in **one**
setting, for example a cue fade time of 3 s instead of 2 s, or fixture
universe 2 instead of 1. Then diff the two text dumps:

```bash
python -m hogshow a.hog4show -o a_out
python -m hogshow b.hog4show -o b_out
diff a_out/dump.txt b_out/dump.txt
```

The field id that changes is the one that holds that setting. Add it to
`hogshow/model.py` and to this document.
