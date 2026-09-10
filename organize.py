#!/usr/bin/env python3
"""Organize photos/videos into Country/City/YYYY-MM/ using mdls + Nominatim.

Usage:
  python3 organize.py SRC DST [--move] [--dry-run] [--batch N] [--yes]

  SRC = e.g. "/Volumes/ExFAT Part/NothingBkup/camera310826"
  DST = e.g. "/Volumes/ExFAT Part/NothingBkup/organized" (must NOT be inside SRC)

Default is copy (safe, slow, needs extra space).
--move is instant on same drive (rename only) but destructive.
--dry-run touches nothing, only prints + writes CSV.

Loop: script scans, says "4000 files, 3500 remaining", asks how many to do
now, processes that batch, then shows remaining and asks again. Repeats
until all done or you quit (q). Every completed file is appended to
organize_done.txt immediately, so Ctrl-C / crash -> re-run continues.
--batch N loops with fixed size N (no prompt). --yes does everything at once.
"""
import argparse
import csv
import json
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.parse import urlencode
from urllib.error import HTTPError

DATE_FMT = "%Y-%m"  # YYYY-MM monthly buckets
CACHE_FILE = "geocache.json"
DONE_FILE = "organize_done.txt"
LOG_FILE = "organize_log.csv"

IMAGE_EXTS = {
    ".jpg", ".jpeg", ".heic", ".heif", ".png", ".dng", ".cr2", ".nef",
    ".arw", ".rw2", ".tif", ".tiff", ".webp", ".bmp", ".gif",
}
VIDEO_EXTS = {
    ".mov", ".mp4", ".m4v", ".avi", ".3gp", ".mts", ".mpg", ".mkv",
}
EXTS = IMAGE_EXTS | VIDEO_EXTS


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


def _nominatim_lookup(lat, lon):
    """Single Nominatim attempt. Returns (country, city), may be ("",""). Raises on HTTP/network error."""
    q = urlencode({"format": "json", "lat": lat, "lon": lon, "zoom": 10,
                   "accept-language": "en"})
    url = f"https://nominatim.openstreetmap.org/reverse?{q}"
    req = Request(url, headers={"User-Agent": "photo-organizer/1.0"})
    with urlopen(req, timeout=20) as r:
        j = json.loads(r.read().decode())
    addr = j.get("address", {}) or {}
    country = sanitize(addr.get("country") or "")
    city = sanitize(
        addr.get("city") or addr.get("town") or addr.get("village")
        or addr.get("municipality") or addr.get("hamlet")
        or addr.get("water") or addr.get("ocean")
        or addr.get("county") or addr.get("state") or ""
    )
    time.sleep(1.1)  # Nominatim policy: max 1 req/sec
    return country, city


def _bigdatacloud_lookup(lat, lon):
    """Single BigDataCloud client-endpoint attempt. Fallback only (bans server-side IPs)."""
    q = urlencode({"latitude": lat, "longitude": lon, "localityLanguage": "en"})
    url = f"https://api.bigdatacloud.net/data/reverse-geocode-client?{q}"
    req = Request(url, headers={"User-Agent": "photo-organizer/1.0"})
    with urlopen(req, timeout=20) as r:
        j = json.loads(r.read().decode())
    country = sanitize(j.get("countryName") or "")
    city = sanitize(j.get("city") or j.get("locality") or j.get("principalSubdivision") or "")
    time.sleep(0.5)
    return country, city


def reverse_geocode(lat, lon, cache):
    # Provider chain: Nominatim primary (server-side OK), BigDataCloud fallback.
    # Round to 3 decimals (~100m) so bursts in same town hit cache, not API.
    key = f"{round(lat, 3)},{round(lon, 3)}"
    if key in cache:
        return cache[key]
    country = city = ""
    chain = (("nominatim", _nominatim_lookup, 3),
             ("bigdatacloud", _bigdatacloud_lookup, 2))
    for name, fn, tries in chain:
        if country and city:
            break
        for attempt in range(tries):
            try:
                c1, c2 = fn(lat, lon)
            except HTTPError as e:
                if e.code in (402, 403, 429):
                    print(f"\n  {name} blocked (HTTP {e.code}), trying next provider")
                    break
                wait = 2 ** (attempt + 1)
                print(f"\n  {name} fail {lat},{lon} (try {attempt + 1}/{tries}): {e} - retry in {wait}s")
                time.sleep(wait)
                continue
            except Exception as e:
                wait = 2 ** (attempt + 1)
                print(f"\n  {name} fail {lat},{lon} (try {attempt + 1}/{tries}): {e} - retry in {wait}s")
                time.sleep(wait)
                continue
            if not country:
                country = c1
            if not city:
                city = c2
            break
    if not country or not city:
        print(f"\n  geocode partial {lat},{lon} -> {country or '?'}/{city or '?'}")
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


def load_done():
    p = Path(DONE_FILE)
    if not p.exists():
        return set()
    return set(line.strip() for line in p.read_text().splitlines() if line.strip())


def ask_batch(remaining: int):
    """Returns batch size, or 0 to quit."""
    print(f"\n>>> {remaining} files remaining.")
    while True:
        try:
            ans = input("How many to process in next batch? [Enter=all, number, q=quit]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nQuit requested.")
            return 0
        if ans in ("", "all", "a"):
            return remaining
        if ans in ("q", "quit", "0"):
            return 0
        try:
            n = int(ans)
            if 1 <= n <= remaining:
                return n
            print(f"Enter 1..{remaining} or Enter for all.")
        except ValueError:
            print("Enter a number, Enter for all, or q to quit.")


def progress_bar(done_n: int, total_n: int, start_t: float, label: str = ""):
    pct = done_n / total_n if total_n else 1.0
    width = 30
    filled = int(width * pct)
    bar = "█" * filled + "─" * (width - filled)
    el = time.time() - start_t
    rate = done_n / el if el > 0 else 0
    eta = (total_n - done_n) / rate if rate > 0 else 0
    em, es = divmod(int(el), 60)
    tm, ts = divmod(int(eta), 60)
    short = label[-45:] if len(label) > 45 else label
    sys.stdout.write(
        f"\r[{bar}] {done_n}/{total_n} {pct:5.1%} | {rate:5.1f}/s "
        f"| el {em:02d}:{es:02d} eta {tm:02d}:{ts:02d} | {short}   "
    )
    sys.stdout.flush()


def scan_media(src: Path, allowed=None):
    # Skip AppleDouble companions (._*.jpg on ExFAT) and any hidden dot-files.
    if allowed is None:
        allowed = EXTS
    return sorted(
        p for p in src.rglob("*")
        if p.is_file() and p.suffix.lower() in allowed and not p.name.startswith(".")
    )


def process_batch(todo, dst_root: Path, cache, done: set, wr, done_f, save_cache, dry_run: bool, move: bool):
    """Process one batch. Returns (processed, skipped, failed). Updates done set + files."""
    start_t = time.time()
    processed = skipped = failed = 0
    interrupted = False
    for i, p in enumerate(todo, 1):
        progress_bar(i - 1, len(todo), start_t, p.name)
        if not p.exists():
            wr.writerow([str(p), "", "", "", "", "", "", "move", "already-gone"])
            done.add(str(p.resolve()))
            done_f.write(str(p.resolve()) + "\n")
            skipped += 1
            continue
        try:
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
            dest = dst_root / country / city / date_s / p.name
            if not move and not dry_run and dest.exists():
                try:
                    if dest.stat().st_size == p.stat().st_size:
                        wr.writerow([str(p), str(dest), lat, lon, country, city, d.isoformat(), "copy", "already-exists"])
                        key = str(p.resolve())
                        done.add(key)
                        done_f.write(key + "\n")
                        skipped += 1
                        continue
                except OSError:
                    pass
                dest = unique_dest(dest)
            elif dest.exists():
                dest = unique_dest(dest)

            mode = "dry-run" if dry_run else ("move" if move else "copy")
            if not dry_run:
                dest.parent.mkdir(parents=True, exist_ok=True)
                if move:
                    shutil.move(str(p), str(dest))
                else:
                    shutil.copy2(str(p), str(dest))
                key = str(p.resolve()) if p.exists() else str(p)
                # for move, p no longer exists - record original string + dest-resolved fallback
                try:
                    key = str(p.resolve())
                except OSError:
                    pass
                done.add(str(p))
                done.add(key)
                done_f.write(str(p) + "\n")
                done_f.flush()
            wr.writerow([str(p), str(dest), lat, lon, country, city, d.isoformat(), mode, "ok"])
            processed += 1
            if len(cache) % 20 == 0:
                save_cache()
        except KeyboardInterrupt:
            print(f"\nInterrupted inside batch at {i}/{len(todo)}. Progress saved.")
            interrupted = True
            break
        except Exception as e:
            wr.writerow([str(p), "", "", "", "", "", "", "error", str(e)[:200]])
            print(f"\n  FAIL {p.name}: {e}")
            failed += 1
        try:
            progress_bar(i, len(todo), start_t, f"{p.name} -> {country}/{city}/{date_s}/")
        except NameError:
            pass
    print()  # newline after bar
    return processed, skipped, failed, interrupted


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--move", action="store_true",
                    help="move instead of copy (instant same drive, destructive)")
    ap.add_argument("--dry-run", action="store_true", help="log only, touch nothing")
    ap.add_argument("--batch", type=int, default=None, help="fixed batch size, loop without asking")
    ap.add_argument("--yes", action="store_true", help="process all remaining without asking")
    ap.add_argument("--no-resume", action="store_true", help="ignore organize_done.txt, start over")
    ap.add_argument("--images-only", action="store_true", help="skip video files")
    ap.add_argument("--videos-only", action="store_true", help="only video files")
    a = ap.parse_args()
    if a.images_only and a.videos_only:
        raise SystemExit("Pick only one of --images-only / --videos-only")
    allowed = VIDEO_EXTS if a.videos_only else (IMAGE_EXTS if a.images_only else EXTS)
    src, dst_root = Path(a.src), Path(a.dst)

    if not src.is_dir():
        raise SystemExit(f"SRC not found: {src}")
    try:
        if dst_root.resolve().is_relative_to(src.resolve()):
            raise SystemExit("DST must NOT be inside SRC - pick a sibling folder like .../organized")
    except AttributeError:
        if str(dst_root.resolve()).startswith(str(src.resolve())):
            raise SystemExit("DST must NOT be inside SRC")

    cache = {}
    if Path(CACHE_FILE).exists():
        try:
            cache = {k: tuple(v) for k, v in json.load(open(CACHE_FILE)).items()}
        except Exception:
            cache = {}

    def save_cache():
        json.dump({k: list(v) for k, v in cache.items()}, open(CACHE_FILE, "w"), indent=1)

    done = set() if a.no_resume else load_done()

    log_exists = Path(LOG_FILE).exists() and Path(LOG_FILE).stat().st_size > 0 and not a.no_resume
    log = open(LOG_FILE, "a" if log_exists else "w", newline="")
    wr = csv.writer(log)
    if not log_exists:
        wr.writerow(["src", "dst", "lat", "lon", "country", "city", "date", "mode", "status"])
    done_f = open(DONE_FILE, "a")

    grand_ok = grand_skip = grand_fail = 0
    batch_no = 0
    try:
        while True:
            files = scan_media(src, allowed)
            remaining = [p for p in files if str(p.resolve()) not in done and str(p) not in done]
            print(f"\n=== found {len(files)} {'image' if allowed is IMAGE_EXTS else ('video' if allowed is VIDEO_EXTS else 'media')} files, {len(files) - len(remaining)} already done, {len(remaining)} remaining ===")
            if not remaining:
                print("All done!")
                break

            # batch size decision
            if a.yes:
                batch_n = len(remaining)
            elif a.batch is not None:
                batch_n = max(0, min(a.batch, len(remaining)))
                if batch_n == 0:
                    break
                print(f"Auto batch: {batch_n} (fixed --batch {a.batch}), {len(remaining) - batch_n} will remain after.")
            else:
                batch_n = ask_batch(len(remaining))
                if batch_n == 0:
                    print(f"Stopping with {len(remaining)} still remaining. Re-run to continue.")
                    break

            todo = remaining[:batch_n]
            batch_no += 1
            print(f"--- Batch {batch_no}: processing {len(todo)} file(s) [{'move' if a.move else 'copy'}{' dry-run' if a.dry_run else ''}] ---")
            ok, sk, fl, interrupted = process_batch(
                todo, dst_root, cache, done, wr, done_f, save_cache,
                dry_run=a.dry_run, move=a.move)
            log.flush()
            done_f.flush()
            save_cache()
            grand_ok += ok
            grand_skip += sk
            grand_fail += fl
            left = len(remaining) - ok - sk
            print(f"Batch {batch_no} done: ok={ok} skipped={sk} failed={fl}. Still remaining ~{max(left, 0)}.")
            if interrupted:
                print("Interrupted. Re-run to continue from where it stopped.")
                break
            if a.yes:
                break  # single shot
            # else loop: rescan, show remaining, ask again
    finally:
        save_cache()
        log.close()
        done_f.close()
    print(f"\nTOTAL: ok={grand_ok} skipped={grand_skip} failed={grand_fail}")
    print(f"log={LOG_FILE} done={DONE_FILE} cache={CACHE_FILE}")


if __name__ == "__main__":
    main()
