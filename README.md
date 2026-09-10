# Photo / Video Organizer

Organizes a folder of photos/videos into:

```
organized/Country/City/YYYY-MM-DD/filename.jpg
```

* GPS via macOS `mdls`, plus `ffprobe` for videos (`mdls` misses mp4 GPS on ExFAT)
* Town + country via Nominatim/OpenStreetMap (no key), BigDataCloud fallback
* Date priority: EXIF/mdls → video container time → camera filename → filesystem
* No GPS files go to `NoGPS/NoGPS/YYYY-MM-DD/`

No pip dependencies. macOS + `python3` only.

## Usage

```bash
# dry-run first (touches nothing, writes log only)
python3 organize.py SRC DST --dry-run

# interactive batched copy (safe default)
python3 organize.py SRC DST

# non-interactive: fixed 200-file batches, or everything at once
python3 organize.py SRC DST --batch 200
python3 organize.py SRC DST --yes

# move instead of copy (instant on same drive, destructive - verify log first)
python3 organize.py SRC DST --move
```

The script loops: it scans, reports total/remaining, asks how many to process next, runs that batch with a progress bar, then asks again until done or you quit with `q`. Re-running resumes from `organize_done.txt`.

Rules:
* `DST` must NOT be inside `SRC` (script refuses).
* Original filenames kept; collisions become `name_1.jpg`, `name_2.jpg`.
* Copy uses `shutil.copy2` (preserves file data + mtime, so EXIF/GPS is retained).

## Files created (in current working directory)

* `organize_log.csv` - src,dst,lat,lon,country,city,date,mode,status for every file.
* `organize_done.txt` - one completed source path per line, appended immediately (crash-safe resume).
* `geocache.json` - rounded lat/lon (3 decimals, ~100m) -> country,city. Avoids repeat API calls.

## Notes

* Copy duplicates all bytes (slow, needs free space). Same-drive move is an instant rename.
* `mdls` can return `(null)` for GPS/date if Spotlight hasn't indexed the drive - script falls back to filesystem date and `NoGPS`.
* Nominatim allows 1 req/sec - script caches + throttles new lookups.

## Troubleshooting

* All `NoGPS` -> check one file manually: `mdls -name kMDItemLatitude -name kMDItemLongitude file.jpg`
* Wrong city -> delete the bad entry from `geocache.json` and re-run.
* Interrupted run -> just re-run, it skips entries in `organize_done.txt`.
