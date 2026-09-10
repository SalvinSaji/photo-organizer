#!/usr/bin/env python3
"""Repair files placed under UnknownCountry/UnknownCity by transient geocode failures.

Re-reads GPS from the moved files (EXIF survives move), re-geocodes with
retries, moves to the correct Country/City/YYYY-MM folder.

Usage:
  python3 repair_unknown.py DST
  e.g. python3 repair_unknown.py "/Volumes/ExFAT Part/NothingBkup/organized"
"""
import csv
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

import organize


def main():
    if len(sys.argv) < 2:
        raise SystemExit("usage: python3 repair_unknown.py DST")
    dst = Path(sys.argv[1])
    if not dst.is_dir():
        raise SystemExit(f"DST not found: {dst}")

    targets = sorted(
        p for p in dst.rglob("*")
        if p.is_file() and not p.name.startswith(".")
        and p.suffix.lower() in organize.EXTS
        and ("UnknownCountry" in p.parts or "UnknownCity" in p.parts)
    )
    print(f"found {len(targets)} misplaced files")
    if not targets:
        return

    cache = {}
    if Path(organize.CACHE_FILE).exists():
        try:
            cache = {k: tuple(v) for k, v in json.load(open(organize.CACHE_FILE)).items()}
        except Exception:
            cache = {}
    # Drop previously-cached Unknowns (from throttled/banned runs) for a fresh lookup.
    cache = {k: v for k, v in cache.items() if "UnknownCountry" not in v and "UnknownCity" not in v}

    def save_cache():
        json.dump({k: list(v) for k, v in cache.items()}, open(organize.CACHE_FILE, "w"), indent=1)

    log = open("repair_log.csv", "a", newline="")
    wr = csv.writer(log)
    if Path("repair_log.csv").stat().st_size == 0:
        wr.writerow(["src", "dst", "lat", "lon", "country", "city"])

    fixed = still_unknown = no_gps = 0
    try:
        for i, p in enumerate(targets, 1):
            print(f"[{i}/{len(targets)}] {p.name} ... ", end="", flush=True)
            try:
                lat, lon, d = organize.run_mdls(p)
            except Exception:
                lat = lon = d = None
            if lat is None or lon is None:
                print("no GPS in file, leaving")
                wr.writerow([str(p), "", "", "", "", "NO-GPS"])
                no_gps += 1
                continue
            if d is None:
                try:
                    d = organize.stat_oldest(p)
                except Exception:
                    d = datetime.now()
            country, city = organize.reverse_geocode(lat, lon, cache)
            if country == "UnknownCountry" or city == "UnknownCity":
                print(f"still unknown ({lat},{lon}), leaving")
                wr.writerow([str(p), "", lat, lon, country, city])
                still_unknown += 1
                continue
            raw = dst / country / city / d.strftime(organize.DATE_FMT) / p.name
            if raw == p:
                print("already in right place")
                wr.writerow([str(p), str(raw), lat, lon, country, city + " (already-correct)"])
                continue
            dest = organize.unique_dest(raw)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(p), str(dest))
            wr.writerow([str(p), str(dest), lat, lon, country, city])
            print(f"-> {country}/{city}/")
            fixed += 1
            save_cache()
    finally:
        save_cache()
        log.close()
    # remove emptied Unknown dirs
    for d in sorted((dst / "UnknownCountry").rglob("*"), reverse=True):
        try:
            if d.is_dir() and not any(d.iterdir()):
                d.rmdir()
        except OSError:
            pass
    print(f"done: fixed={fixed} still_unknown={still_unknown} no_gps={no_gps}")


if __name__ == "__main__":
    main()
