# HogShowDecoder

A reader / "decompiler" for **Hog 4 OS (`.hog4show`) and Hog 5 (`.hogshow`)
show files**. It lets other tools use the fixtures, patch, groups, palettes,
cue lists and cues from a show.

Both formats are a **gzip'd tar** with two files: `hog_ident.json` and a
**SQLite** database (`hogdatabase.mk2`). Each database row holds one object
serialised in a binary TLV format called **XTTI**. Cue and palette values
sit inside that as packed little-endian records. See
[docs/FORMAT.md](docs/FORMAT.md) for the full write-up.

## Usage

The reader needs Python 3.8+ and has no dependencies.

```bash
python -m hogshow MyShow.hog4show                 # readable summary
python -m hogshow MyShow.hog4show --json          # full decoded show as JSON on stdout
python -m hogshow MyShow.hog4show -o out/         # write the export files below
python -m hogshow MyShow.hogshow                  # Hog 5 show files work the same way
python -m hogshow MyShow.hogshow --include-attic  # also show deleted objects (undo history)
```

Or install it (wheels are attached to each [GitHub release](https://github.com/FrankDeRuiterNL/HogShowExtractor/releases)) and use the `hogshow` command:

```bash
pip install hogshowdecoder-0.1.0-py3-none-any.whl
hogshow MyShow.hogshow -o out/
```

`-o out/` writes:

| file | content |
|------|---------|
| `show.json` | decoded show: fixture types, fixtures + patch, groups + members, palettes + values, scenes, cue lists + cues + values, pages |
| `raw.json`  | every row of every table as a generic XTTI tree (nothing lost) |
| `dump.txt`  | the same tree as indented text, handy for `diff` |
| `hogdatabase.mk2`, `hog_ident.json` | the extracted original files |

From Python:

```python
from hogshow import Show
show = Show.load("MyShow.hog4show")
for g in show.groups():
    print(g["number"], g["name"], [m["fixture_number"] for m in g["members"]])
```

## Status

| area | status |
|------|--------|
| container (gzip/tar/SQLite) | done |
| XTTI generic decoder | done (structs, arrays, maps, pointers, leaves) |
| fixture types, fixtures, user numbers, DMX address | done (universe numbering not confirmed) |
| groups + members (in selection order) | done |
| palettes / cues / scenes: function, target, level %, palette references | done |
| cue fade times (cue level and per value) | done, confirmed with a test file |
| decimal object numbers (12.5) | done |
| Hog 5 differences (`xver` column, selection section, attic rows) | done |
| cue delay/path, in vs out split, effect parameters, "kind 7" entries | partly done; raw values are exported |

## Tests

```bash
python -m unittest discover -s tests
HOG_SAMPLE=/path/a.hog4show:/path/b.hogshow python -m unittest discover -s tests   # also real shows
```
