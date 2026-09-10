# Photo / Video Organizer

Organizes `camera310826` (or any folder) into:

```
organized/Country/City/YYYY-MM-DD/filename.jpg
```

* GPS via macOS built-in `mdls` (`kMDItemLatitude` / `kMDItemLongitude`)
* Town + country via BigDataCloud reverse-geocode (no API key)
* Date via `mdls kMDItemContentCreationDate`, fallback to oldest of birth/mtime/ctime
* No GPS files go to `NoGPS/NoGPS/YYYY-MM-DD/`

No pip dependencies. macOS + `python3` only.

## Usage

```bash
# 1. dry-run on small folder first (touches nothing)
python3 organize.py "/Volumes/ExFAT Part/NothingBkup/test" "/Volumes/ExFAT Part/NothingBkup/organized" --dry-run

# 2. real copy (safe, slow, needs ~same free space)
python3 organize.py "/Volumes/ExFAT Part/NothingBkup/test" "/Volumes/ExFAT Part/NothingBkup/organized"

# 3. full run
python3 organize.py "/Volumes/ExFAT Part/NothingBkup/camera310826" "/Volumes/ExFAT Part/NothingBkup/organized" --dry-run
python3 organize.py "/Volumes/ExFAT Part/NothingBkup/camera310826" "/Volumes/ExFAT Part/NothingBkup/organized"

# 4. move instead of copy (instant on same drive, destructive - only after log looks right)
python3 organize.py "/Volumes/ExFAT Part/NothingBkup/camera310826" "/Volumes/ExFAT Part/NothingBkup/organized" --move
```

Rules:
* `DST` must NOT be inside `SRC` (script refuses - avoids recursing into own output).
* Same filename collision -> `name_1.jpg`, `name_2.jpg`, etc.
* Original filenames kept, `copy2` preserves timestamps.

## Files created

* `organize_log.csv` - src,dst,lat,lon,country,city,date,mode for every file. Keep for audit / undo.
* `geocache.json` - rounded lat/lon (3 decimals, ~100m) -> country,city. Re-runs don't re-hit API.

## Notes for 123G ExFAT drive

* `copy` reads+writes 123G (20min-2hr+). `move` same drive is instant rename.
* `mv` same volume keeps inode; ExFAT has no real inodes so don't rely on `ls -i` there.
* `mdls` can return `(null)` if Spotlight hasn't indexed the external drive - script then falls back to filesystem date and `NoGPS`.
* BigDataCloud free endpoint is rate-limited - script caches + sleeps 0.5s between new lookups.
* Internal Mac drive only has ~6G free - keep output on the ExFAT volume (430G free).

## Troubleshooting

* All `NoGPS` -> check one file manually: `mdls -name kMDItemLatitude -name kMDItemLongitude file.jpg`
* Wrong city -> delete bad entry from `geocache.json` and re-run.
* Interrupted run -> just re-run, `unique_dest` skips existing names, cache resumes.
