# Hog 4 / Hog OS show file format (`.hog4show`)

This is reverse engineered from three show files. There is no public spec.
Items marked **(?)** are educated guesses and need confirming against more
show files.

| file | software | archive `xtti_version` | row `xver` |
|------|----------|------------------------|------------|
| `UE5_Music03_bck.hog4show` | Hog 4 PC | 29 | no column |
| `Music03_Test_Latest.hogshow` | Hog 5 PC | 30 | 28 and 30 mixed |
| `Music03_Test_Latest_cue1_fade_different.hogshow` | Hog 5 PC | 30 | same file with cue 1 fade 0 → 5 s |

## 0. Hog 4 vs Hog 5

Both versions use the same container, the same SQLite tables and the same XTTI
encoding. One reader handles both. The differences found:

| | Hog 4 (`.hog4show`) | Hog 5 (`.hogshow`) |
|---|---|---|
| file extension | `.hog4show` | `.hogshow` |
| `hog_ident.json` `xtti_version` | 29 | 30 |
| per-row `xver` column | absent | `xver INTEGER NOT NULL DEFAULT 28` on every table. It holds the XTTI version the row was written with, so one show can mix 28 and 30 rows. |
| programming data blob (§3) | records + `D7 33` | records + `D7 33` + a *selection section* + `D7 33` (rows with xver 30) |
| per-value fade TLV in cues | short form `02` only seen | short form `02` and long form `03` |
| entry byte high nibble in cues | 0x00 | 0x20 |

Rows with `attic = 1` are **deleted objects** kept for undo. The Hog 5 test show
has a deleted cue list 1 (row 1) next to the live one (row 2), and 23 deleted
fixtures. The reader skips attic rows unless you pass `--include-attic`.
Cues can still hold values for deleted fixtures; those values are flagged
`fixture_deleted`.

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
| `attic`    | 1 = deleted object kept in the "attic" (undo history). Skip these for the live show. |
| `xver`     | Hog 5 only: XTTI version of this row (28, 30, …) |

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
| 0x21 | Hog object number: `major:u16 minor:u16`, minor in 1/10000. `ffff` = unnumbered. 3 = `00030000`, 12.5 = `000c1388` |
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
header:u16                 0x0001 / 0x0000 (palettes), 0x0100 / 0x0000 (cues)
record*                    D7 31 01 C5 <record>
D7 33
[xver 30 / Hog 5 only]
count:u32
count x ( D7 31 00 14 len:u32 index:u16
          D7 31 05 46 len:u32 nbytes:u32 BE  fixture_id:u32 LE * (nbytes/4)  D7 33 )
D7 33

record :=
  rtype:u16                0x0701 = value entry, 0x1300 = palette reference
  target:u32               cues/scenes: fixture id. palettes: fixture TYPE id. 0x3FE = any fixture.
                           Fixture and type ids overlap, so the context decides which.
  entry:u8                 low nibble: 1 = palette ref, 4 = value, 5 = effect, 7 = ?
                           high nibble: 0x40 palettes, 0x00 Hog 4 cues, 0x20 Hog 5 cues (?)
  function:u16             function id in the low 14 bits
                           bit 0x4000 = short fade TLV 02 follows, bit 0x8000 = long fade TLV 03 follows
  [entry==1] u32(-1) palette_id:u32 u32(-1)
  TLV*                     tag:u8 len:u8 data
  FF 00
```

| TLV | len | content |
|-----|-----|---------|
| 0x05 | 10 | value: `flags:u16, value:f64` |
| 0x07 | 28 | effect: `u32, effect_table:u32, f64, u16, u16, f64` **(?)** |
| 0x08 | 2  | unknown, used with entry kind 7 |
| 0x02 | 1  | per-value fade, short form: seconds × 16 (0x00 = 0 s, 0x10 = 1 s, 0x20 = 2 s, 0x50 = 5 s). Cues only. **Confirmed** by the fade test file. |
| 0x03 | 6  | per-value fade, long form: `u16 (=1), time:u32 LE` in 5 ms units (20 = 0.1 s, 300 = 1.5 s). Used when the fade isn't a whole second. |
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
* Cue timing is under `0x1C4`. Each time is a `0x152` struct holding field
  `0x15D` (type 0x33, milliseconds).

  | field | meaning | status |
  |-------|---------|--------|
  | `0x166` | fade in | **confirmed**: 0 → 5000 when the cue fade was set to 5 s |
  | `0x167` | fade out | **confirmed**: changed together with 0x166 |
  | `0x64F[]` | fade, as `{0x15E: in, 0x15F: out}` | **confirmed**: both changed 0 → 5000 |
  | `0x650[]` | delay, as `{0x15E: in, 0x15F: out}` | **(?)**: stayed 0 in the test |
  | `0x651[]` | path | **(?)** |

  Which of `0x166` and `0x167` is "in" and which is "out" still needs a split
  fade to prove it (e.g. `Fade 3/5`). The same fade is also copied into every
  value in the cue body (TLV 02/03 above).
* Cue list option `0x747` = number of cues.
* Hog 5 cue bodies have a *selection section* after the records: a list of
  `(index, [fixture ids])`. In the test show it holds sub-selections such as
  `[1,8] [2,7] [3,6] [4,5]`, which look like the fan/buddy order the cue was
  programmed with **(?)**.

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
