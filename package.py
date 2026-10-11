"""Build the submission ZIP (output/, source/, optional demo/) and refuse to include secrets.

    python package.py --out ../output --zip ../submission.zip [--demo ../demo/demo.mp4]
"""
from __future__ import annotations

import argparse
import re
import sys
import zipfile
from pathlib import Path

SRC = Path(__file__).resolve().parent
SKIP_DIRS = {".cache", "__pycache__", ".venv", "venv"}
SKIP_FILES = {".env"}
KEY_PATTERN = re.compile(r"AIza[0-9A-Za-z_\-]{30,}|AQ\.[0-9A-Za-z_\-]{30,}|sk-[A-Za-z0-9]{20,}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="../output")
    ap.add_argument("--zip", default="../submission.zip")
    ap.add_argument("--demo")
    a = ap.parse_args()
    out = Path(a.out)
    for f in ("level1.csv", "level2.csv"):
        if not (out / f).exists():
            print(f"missing {out / f}")
            return 1

    with zipfile.ZipFile(a.zip, "w", zipfile.ZIP_DEFLATED) as z:
        for f in ("level1.csv", "level2.csv"):
            z.write(out / f, f"output/{f}")
        for p in sorted(SRC.rglob("*")):
            rel = p.relative_to(SRC)
            if p.is_dir() or any(part in SKIP_DIRS for part in rel.parts) or p.name in SKIP_FILES:
                continue
            if p.suffix in {".py", ".md", ".txt", ".example", ".toml", ".cfg"} or p.name == ".env.example":
                text = p.read_text(encoding="utf-8", errors="ignore")
                if KEY_PATTERN.search(text):
                    print(f"REFUSING: possible API key in {rel}")
                    return 1
            z.write(p, f"source/{rel.as_posix()}")
        if a.demo:
            d = Path(a.demo)
            z.write(d, f"demo/demo{d.suffix.lower()}")

    size = Path(a.zip).stat().st_size / 1e6
    with zipfile.ZipFile(a.zip) as z:
        names = z.namelist()
    print("\n".join(names))
    print(f"\n{a.zip}: {len(names)} files, {size:.2f} MB {'(OK)' if size <= 15 else '(TOO LARGE: limit is 15 MB)'}")
    return 0 if size <= 15 else 1


if __name__ == "__main__":
    sys.exit(main())
