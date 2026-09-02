#!/usr/bin/env python3
"""Cron wrapper for TripIt map sync.
Stdout is delivered verbatim by the scheduler. This job intentionally prints a
short status every successful run so the delivered cron message always includes
the live map URL.
"""
import subprocess, sys

SYNC = r"C:\Users\pkoon\tripit_map\tripit_map_sync.py"
PY = sys.executable
MAP_URL = "https://pkooney.github.io/iceland-trip-map/"
KML_URL = "https://pkooney.github.io/iceland-trip-map/Iceland_Trip_Sep2026_Map.kml"
SOURCE_URL = "https://www.tripit.com/p/ACAF6D20819AD7BFA3F72D57C62D4435"

try:
    r = subprocess.run([PY, SYNC], capture_output=True, text=True, timeout=900)
    out = (r.stdout or "") + (r.stderr or "")

    if r.returncode != 0:
        print(f"TripIt map sync FAILED (exit {r.returncode}):")
        print(f"Source TripIt URL: {SOURCE_URL}")
        print(f"Live map URL: {MAP_URL}")
        print(out[-800:])
        sys.exit(r.returncode)

    lines = [l for l in out.splitlines() if l.startswith(("  ", "Files"))]
    if "Pushed to GitHub Pages." in out:
        status = "TripIt map synced & published"
    elif "No changes since last sync." in out:
        status = "TripIt map sync checked — no changes"
    else:
        status = "TripIt map sync completed"

    print(f"{status}:")
    print(f"Live map: {MAP_URL}")
    print(f"KML overlay: {KML_URL}")
    print(f"TripIt source: {SOURCE_URL}")
    if lines:
        print("\n".join(lines[-6:]))
except Exception as e:
    print(f"TripIt map sync ERROR: {e}")
    print(f"Live map URL: {MAP_URL}")
    print(f"TripIt source: {SOURCE_URL}")
    sys.exit(1)
