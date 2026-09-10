#!/usr/bin/env python3
"""Organize photos/videos into Country/City/YYYY-MM-DD/ using mdls + BigDataCloud.

Usage:
  python3 organize.py SRC DST [--move] [--dry-run]

  SRC = e.g. "/Volumes/ExFAT Part/NothingBkup/camera310826"
  DST = e.g. "/Volumes/ExFAT Part/NothingBkup/organized" (must NOT be inside SRC)

Default is copy (safe, slow, needs extra space).
--move is instant on same drive (rename only) but destructive.
--dry-run touches nothing, only prints + writes CSV.
"""
import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.parse import urlencode

DATE_FMT = "%Y-%m-%d"  # sortable, as suggested (was DDMMYY)
CACHE_FILE = "geocache.json"

EXTS = {
    ".jpg", ".jpeg", ".heic", ".heif", ".png", ".dng", ".cr2", ".nef",
    ".arw", ".rw2", ".tif", ".tiff", ".webp", ".bmp", ".gif",
    ".mov", ".mp4", ".m4v", ".avi", ".3gp", ".mts", ".mpg", ".mkv",
}


def run_mdls(p: Path):
    """One mdls call per file. Returns (lat, lon, content_creation_datetime|None)."""
    try:
        r = subprocess.run(
            ["mdls", "-name", "kMDItemLatitude", "-name", "kMDItemLongitude",
             "-name", "kMDItemContentCreationDate", str(p)],
            capture_output=True, text=True, timeout=15,
        )
        out = r.stdout
    except Exception:
        return None, None, None
    lat = lon = date = None
    m = re.search(r"kMDItemLatitude\s+=\s+([-\d.]+)", out)
    if m:
        try:
            lat = float(m.group(1))
        except ValueError:
            pass
    m = re.search(r"kMDItemLongitude\s+=\s+([-\d.]+)", out)
    if m:
        try:
            lon = float(m.group(1))
        except ValueError:
            pass
    m = re.search(r"kMDItemContentCreationDate\s+=\s+(.+)", out)
    if m and "(null)" not in m.group(1):
        ds = m.group(1).strip()
        for fmt in ("%Y-%m-%d %H:%M:%S %z", "%Y-%m-%d %H:%M:%S"):
            try:
                date = datetime.strptime(ds, fmt)
                break
            except ValueError:
                continue
    return lat, lon, date


def stat_oldest(p: Path):
    """Fallback when mdls has no date. Oldest of birth/mtime/ctime approximates taken date."""
    st = p.stat()
    cands = [st.st_mtime, st.st_ctime]
    birth = getattr(st, "st_birthtime", None)
    if birth:
        cands.append(birth)
    return datetime.fromtimestamp(min(cands))


def sanitize(s: str):
    s = (s or "").strip().replace("/", "_").replace("\\", "_")
    s = re.sub(r'[<>:"|?*\x00-\x1f]', "_", s).strip(" .")
    return s[:80] if s else ""


def reverse_geocode(lat, lon, cache):
    # Round to 3 decimals (~100m) so bursts in same town hit cache, not API.
    key = f"{round(lat, 3)},{round(lon, 3)}"
    if key in cache:
        return cache[key]
    q = urlencode({"latitude": lat, "longitude": lon, "localityLanguage": "en"})
    url = f"https://api.bigdatacloud.net/data/reverse-geocode-client?{q}"
    country = city = ""
    try:
        req = Request(url, headers={"User-Agent": "photo-organizer"})
        with urlopen(req, timeout=15) as r:
            j = json.loads(r.read().decode())
        country = sanitize(j.get("countryName") or "")
        city = sanitize(j.get("city") or j.get("locality") or j.get("principalSubdivision") or "")
        time.sleep(0.5)
    except Exception as e:
        print(f"  geocode fail {lat},{lon}: {e}")
    if not country:
        country = "UnknownCountry"
    if not city:
        city = "UnknownCity"
    cache[key] = (country, city)
    return country, city


def unique_dest(d: Path):
    if not d.exists():
        return d
    for i in range(1, 1000):
        nd = d.with_name(f"{d.stem}_{i}{d.suffix}")
        if not nd.exists():
            return nd
    return d


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--move", action="store_true",
                    help="move instead of copy (instant same drive, destructive)")
    ap.add_argument("--dry-run", action="store_true", help="log only, touch nothing")
    a = ap.parse_args()
    src, dst_root = Path(a.src), Path(a.dst)

    if not src.is_dir():
        raise SystemExit(f"SRC not found: {src}")
    # Prevent DST inside SRC (would recurse into own output).
    try:
        if dst_root.resolve().is_relative_to(src.resolve()):
            raise SystemExit("DST must NOT be inside SRC - pick a sibling folder like .../organized")
    except AttributeError:
        # Python <3.9 fallback
        if str(dst_root.resolve()).startswith(str(src.resolve())):
            raise SystemExit("DST must NOT be inside SRC")

    cache = {}
    if Path(CACHE_FILE).exists():
        try:
            cache = {k: tuple(v) for k, v in json.load(open(CACHE_FILE)).items()}
        except Exception:
            cache = {}

    files = [p for p in src.rglob("*") if p.is_file() and p.suffix.lower() in EXTS]
    print(f"found {len(files)} media files")

    log = open("organize_log.csv", "w", newline="")
    wr = csv.writer(log)
    wr.writerow(["src", "dst", "lat", "lon", "country", "city", "date", "mode"])

    try:
        for i, p in enumerate(files, 1):
            lat, lon, d = run_mdls(p)
            if d is None:
                try:
                    d = stat_oldest(p)
                except Exception:
                    d = datetime.now()
            if lat is None or lon is None:
                country, city = "NoGPS", "NoGPS"
            else:
                country, city = reverse_geocode(lat, lon, cache)

            date_s = d.strftime(DATE_FMT)
            dest = unique_dest(dst_root / country / city / date_s / p.name)
            mode = "dry-run" if a.dry_run else ("move" if a.move else "copy")
            print(f"[{i}/{len(files)}] {p.name} -> {country}/{city}/{date_s}/ ({lat},{lon}) [{mode}]")
            wr.writerow([str(p), str(dest), lat, lon, country, city, d.isoformat(), mode])
            log.flush()
            if not a.dry_run:
                dest.parent.mkdir(parents=True, exist_ok=True)
                if a.move:
                    shutil.move(str(p), str(dest))
                else:
                    shutil.copy2(str(p), str(dest))
    finally:
        json.dump({k: list(v) for k, v in cache.items()}, open(CACHE_FILE, "w"), indent=1)
        log.close()
    print("done. log=organize_log.csv cache=" + CACHE_FILE)


if __name__ == "__main__":
    main()
