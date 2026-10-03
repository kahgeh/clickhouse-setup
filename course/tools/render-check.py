#!/usr/bin/env python3
"""Render a course page in light, dark and narrow to PNGs for visual checking (headless Chrome via shot.mjs).

  render-check.py modules/01-problem-and-outcome.html [--out /tmp/dir] [--only light|dark|narrow]

Writes temporary copies next to the page with data-theme set (so relative assets resolve), screenshots them
full-page, then deletes the copies. Console errors are reported by tools/smoke-test.mjs, not here.
"""
import argparse
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
COURSE = os.path.dirname(HERE)

ap = argparse.ArgumentParser()
ap.add_argument("page")
ap.add_argument("--out", default="/tmp/course-render")
ap.add_argument("--only")
args = ap.parse_args()
page = os.path.abspath(os.path.join(COURSE, args.page))
os.makedirs(args.out, exist_ok=True)
base = os.path.splitext(os.path.basename(page))[0]
html = open(page).read()
jobs = [("light", "light", 1000), ("dark", "dark", 1000), ("narrow", "light", 500)]
for name, theme, width in jobs:
    if args.only and args.only != name:
        continue
    tmp = os.path.join(os.path.dirname(page), f"_render_{name}.html")
    open(tmp, "w").write(re.sub(r"<html lang=\"en\">", f'<html lang="en" data-theme="{theme}">', html, count=1))
    try:
        out = os.path.join(args.out, f"{base}-{name}.png")
        r = subprocess.run(["node", os.path.join(HERE, "shot.mjs"), f"file://{tmp}", out, "--width", str(width),
                            "--height", "900", "--wait", "1500", "--full"], capture_output=True, text=True, timeout=120)
        print(r.stdout.strip() or r.stderr.strip())
    finally:
        os.remove(tmp)
