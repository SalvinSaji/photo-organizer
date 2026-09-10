#!/usr/bin/env python3
"""Repair misplaced files: UnknownCountry/UnknownCity (geocode failures) and
NoGPS/NoGPS (mdls misses mp4 GPS on ExFAT; stat dates use copy time).

Re-reads GPS/date from the moved files (ffprobe for videos, camera-filename
dates), re-geocodes with retries, moves to the correct Country/City/YYYY-MM
folder. True-NoGPS files stay, possibly re-dated.

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
        and ("UnknownCountry" in p.parts or "UnknownCity" in p.parts
             or "NoGPS" in p.parts)
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

    fixed = still_unknown = correct = 0
    try:
        for i, p in enumerate(targets, 1):
            print(f"[{i}/{len(targets)}] {p.name} ... ", end="", flush=True)
            try:
                lat, lon, d = organize.run_mdls(p)
            except Exception:
                lat = lon = d = None
            probe_d = None
            if p.suffix.lower() in organize.VIDEO_EXTS:
                try:
                    probe_lat, probe_lon, probe_d = organize.probe_video(p)
                except Exception:
                    probe_lat = probe_lon = None
                if lat is None:
                    lat = probe_lat
                if lon is None:
                    lon = probe_lon
            d = organize.best_date(p, d, probe_d)
            if lat is None or lon is None:
                raw = dst / "NoGPS" / "NoGPS" / d.strftime(organize.DATE_FMT) / p.name
                if raw == p:
                    print("true NoGPS, already placed")
                    correct += 1
                    continue
                dest = organize.unique_dest(raw)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(p), str(dest))
                wr.writerow([str(p), str(dest), "", "", "NoGPS", "NoGPS-redated"])
                print(f"true NoGPS, re-dated -> NoGPS/{d.strftime(organize.DATE_FMT)}/")
                fixed += 1
                continue
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
    # remove emptied Unknown/NoGPS dirs
    for top in ("UnknownCountry", "NoGPS"):
        root = dst / top
        if not root.is_dir():
            continue
        for d in sorted(root.rglob("*"), reverse=True):
            try:
                if d.is_dir() and not any(d.iterdir()):
                    d.rmdir()
            except OSError:
                pass
    print(f"done: fixed={fixed} already_correct={correct} still_unknown={still_unknown}")


if __name__ == "__main__":
    main()
