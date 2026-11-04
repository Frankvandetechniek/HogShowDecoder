"""Command line interface::

    python -m hogshow SHOW.hog4show                 # summary to stdout (Hog 5 .hogshow works too)
    python -m hogshow SHOW.hog4show -o outdir       # write show.json, raw.json, dump.txt, database
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from .model import Show


def _summary(show: dict) -> str:
    L = []
    ident = show.get("ident") or {}
    fmt = show.get("format", {})
    L.append(f"Show: {ident.get('description', '?')}  software={show.get('software')}  "
             f"archive xtti_version={fmt.get('archive_xtti_version')}  row xver={fmt.get('row_xver_counts')}")
    L.append(f"\nFixture types ({len(show['fixture_types'])}):")
    for t in show["fixture_types"]:
        L.append(f"  [{t['id']}] {t['name']}  ({t['manufacturer']}, footprint {t['footprint']})")
    L.append(f"\nFixtures ({len(show['fixtures'])}):")
    for f in show["fixtures"]:
        patch = ", ".join(f"U{p['universe_index']}:{p['dmx_address']}" for p in f["patch"])
        L.append(f"  #{f['number']:>6}  {f['type']}  patch {patch}")
    L.append(f"\nGroups ({len(show['groups'])}):")
    for g in show["groups"]:
        nums = ",".join(str(m["fixture_number"]) for m in g["members"])
        L.append(f"  {g.get('number')!s:>5}  {g.get('name')}  -> [{nums}]")
    L.append(f"\nPalettes ({len(show['palettes'])}):")
    for p in show["palettes"]:
        L.append(f"  {'/'.join(p['kind']) or '-':<16} {p.get('number')!s:>5}  {p.get('name')}"
                 f"  ({len(p.get('values', []))} values)")
    L.append("\nCue lists:")
    for cl in show["cuelists"]:
        L.append(f"  {cl.get('number')}  {cl.get('name')}  ({len(cl['cues'])} cues)")
        for c in cl["cues"]:
            t = c.get("timing", {})
            L.append(f"    cue {c.get('number'):>6}  fade {t.get('fade_in')}/{t.get('fade_out')}s"
                     f"  {c.get('name')}  ({len(c.get('values', []))} values)")
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Read / decompile Hog 4 OS (.hog4show) and Hog 5 (.hogshow) show files")
    ap.add_argument("show", help=".hog4show / .hogshow file (or an extracted hogdatabase.mk2)")
    ap.add_argument("-o", "--out", help="output directory")
    ap.add_argument("--json", action="store_true", help="print the decoded show as JSON to stdout")
    ap.add_argument("--include-attic", action="store_true",
                    help="also include deleted objects kept in the attic (undo history)")
    args = ap.parse_args(argv)

    s = Show.load(args.show, include_attic=args.include_attic)
    data = s.to_dict()
    if args.json:
        json.dump(data, sys.stdout, indent=2, default=str)
        print()
    else:
        print(_summary(data))
    if args.out:
        os.makedirs(args.out, exist_ok=True)
        with open(os.path.join(args.out, "show.json"), "w") as f:
            json.dump(data, f, indent=2, default=str)
        with open(os.path.join(args.out, "raw.json"), "w") as f:
            json.dump(s.raw_dump(), f, indent=1, default=str)
        with open(os.path.join(args.out, "dump.txt"), "w") as f:
            f.write(s.text_dump())
        with open(os.path.join(args.out, "hogdatabase.mk2"), "wb") as f:
            f.write(s.container.db_bytes)
        with open(os.path.join(args.out, "hog_ident.json"), "w") as f:
            json.dump(s.container.ident, f, indent=2)
        print(f"\nwrote {args.out}/show.json, raw.json, dump.txt, hogdatabase.mk2", file=sys.stderr)


if __name__ == "__main__":
    main()
