#!/usr/bin/env python3
"""Flatten a Bambu Studio profile: resolve the `inherits` chain to the end.

The Bambu Studio CLI (--load-settings / --load-filaments) accepts only
"flat" JSON, while Resources/profiles/BBL/ profiles are built on inheritance.

Usage:
    python3 resolve_profile.py "Bambu Lab A1 0.4 nozzle" out.json
    python3 resolve_profile.py "0.20mm Standard @BBL A1" out.json
    python3 resolve_profile.py "Bambu PLA Basic @BBL A1" out.json
"""
import json
import sys
from pathlib import Path

ROOT = Path("/Applications/BambuStudio.app/Contents/Resources/profiles/BBL")
SUBDIRS = ("machine", "process", "filament")
# `from` and `type` are required by the CLI — do not drop them.
SKIP = {"inherits", "instantiation", "setting_id", "name", "include"}


def index() -> dict[str, Path]:
    """name -> path to the json, searched across every profile subfolder."""
    out = {}
    for sub in SUBDIRS:
        for p in (ROOT / sub).rglob("*.json"):
            try:
                nm = json.loads(p.read_text()).get("name")
            except Exception:
                continue
            if nm:
                out.setdefault(nm, p)
    return out


def flatten(name: str, idx: dict[str, Path], seen=None) -> dict:
    """Collect keys from the root of the chain to the leaf: child beats parent."""
    seen = seen or set()
    if name in seen:
        raise SystemExit(f"циклическое наследование на «{name}»")
    seen.add(name)
    if name not in idx:
        raise SystemExit(f"профиль не найден: «{name}»")
    data = json.loads(idx[name].read_text())
    parent = data.get("inherits")
    merged = flatten(parent, idx, seen) if parent else {}
    # The `include` key is a second assembly branch, independent of `inherits`.
    # On Bambu machines the real start G-code (calibration, warm-up, the first
    # line) lives not in the printer profile but in template files listed
    # under `include`. Without them inheritance bottoms out at
    # fdm_machine_common with a stub "M109 S205", printing the whole part at 205 C.
    for inc in data.get("include", []):
        merged.update({k: v for k, v in json.loads(idx[inc].read_text()).items()
                       if k not in SKIP})
    merged.update({k: v for k, v in data.items() if k not in SKIP})
    merged["name"] = name
    merged["from"] = "system"   # without this the CLI fails: "from unsupported"
    return merged


if __name__ == "__main__":
    if len(sys.argv) < 3:
        idx = index()
        print("Укажите имя профиля и файл вывода. Доступно, например:")
        for n in sorted(idx):
            if "A1" in n and "A1 mini" not in n:
                print("   ", n)
        sys.exit(1)
    idx = index()
    res = flatten(sys.argv[1], idx)
    Path(sys.argv[2]).write_text(json.dumps(res, indent=1, ensure_ascii=False))
    print(f"{sys.argv[1]} -> {sys.argv[2]}  ({len(res)} ключей)")
