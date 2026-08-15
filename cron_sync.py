#!/usr/bin/env python3
"""Cron wrapper for TripIt map sync. Quiet unless something changed.
Stdout is delivered verbatim by the scheduler; empty stdout = silent."""
import subprocess, sys

SYNC = r"C:\Users\pkoon\tripit_map\tripit_map_sync.py"
PY = sys.executable

try:
    r = subprocess.run([PY, SYNC], capture_output=True, text=True, timeout=900)
    out = (r.stdout or "") + (r.stderr or "")
    if "Pushed to GitHub Pages." in out:
        # extract the key lines for the notification
        lines = [l for l in out.splitlines() if l.startswith(("  ", "Files"))]
        print("TripIt map synced & published:")
        print("\n".join(lines[-6:]))
    elif r.returncode != 0:
        print(f"TripIt map sync FAILED (exit {r.returncode}):")
        print(out[-800:])
    # else: no changes -> stay silent
except Exception as e:
    print(f"TripIt map sync ERROR: {e}")
