#!/usr/bin/env python3
"""Break sliced G-code down by line type: how many grams and how much time
went where, and whether the print has supports.

Why a separate script. The weight and time slice.sh prints are totals for
the whole print, and they hide the thing that matters most: whether
supports are on. That has already cost one warped part (2026-09-12, a rail
switch: 32 g without supports instead of 36 g with them). What is counted
here is what the G-code actually contains, not what the profile says.

    python3 tools/gcode_report.py out/plate_1.gcode

Works with any Bambu Studio G-code — from slice.sh, and from slicing
someone else's .3mf project with its own profile.

How the weight is computed. Bambu writes extrusion in relative coordinates
(M83), so the E field is the length of filament swallowed, in mm. Volume is
that length times the cross-section of the strand, then times density.
Diameter and density are read from the G-code's own header
(`; filament_diameter`, `; filament_density`), so Matte (1.32) and Basic
(1.26) come out at different grams. Only moves that travel in X/Y count:
a retract and its return spend no filament.

Time comes from result.json next to the G-code (feature_type_times,
seconds), when it is there. Its keys are the same as in `; FEATURE:`.
"""
import json
import pathlib
import re
import sys

SUPPORT_FEATURES = ("Support", "Support interface")


def parse(path):
    """-> (grams per feature, density, diameter, total time string)"""
    feat = "Undefined"
    mm = {}
    density, diameter, eta = 1.26, 1.75, None
    with open(path, errors="ignore") as f:
        for line in f:
            if line.startswith(";"):
                if m := re.match(r";\s*FEATURE:\s*(.+)", line):
                    feat = m.group(1).strip()
                elif m := re.match(r";\s*filament_density:?\s*=?\s*([\d.]+)", line):
                    density = float(m.group(1))
                elif m := re.match(r";\s*filament_diameter:?\s*=?\s*([\d.]+)", line):
                    diameter = float(m.group(1))
                elif "total estimated time" in line:
                    eta = line.strip("; \n")
                continue
            if not line.startswith(("G1", "G2", "G3")):
                continue
            if "X" not in line and "Y" not in line:
                continue            # retract/return — no movement
            if m := re.search(r"\sE(-?[\d.]+)", line):
                e = float(m.group(1))
                if e > 0:
                    mm[feat] = mm.get(feat, 0.0) + e
    area = 3.141592653589793 * (diameter / 2) ** 2      # mm^2
    grams = {k: v * area * density / 1000 for k, v in mm.items()}
    return grams, density, diameter, eta


def seconds_by_feature(gcode):
    """feature_type_times from result.json next to the G-code, if present."""
    for p in (gcode.parent / "result.json",):
        if p.exists():
            r = json.loads(p.read_text())
            out = {}
            for plate in r.get("sliced_plates", []):
                for k, v in plate.get("feature_type_times", {}).items():
                    out[k] = out.get(k, 0.0) + v
            return out
    return {}


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    gcode = pathlib.Path(sys.argv[1])
    grams, density, diameter, eta = parse(gcode)
    secs = seconds_by_feature(gcode)

    total_g = sum(grams.values())
    rows = sorted(set(grams) | set(secs), key=lambda k: -grams.get(k, 0))
    print(f"{'тип линии':22} {'грамм':>7} {'доля':>6} {'время':>9}")
    print("-" * 48)
    for k in rows:
        g, s = grams.get(k, 0.0), secs.get(k)
        share = f"{g / total_g * 100:5.1f}%" if total_g else "     —"
        t = f"{int(s // 60)}:{int(s % 60):02d}" if s else "—"
        print(f"{k:22} {g:7.2f} {share:>6} {t:>9}")
    print("-" * 48)
    print(f"{'итого':22} {total_g:7.2f}   филамент {diameter} мм, {density} г/см³")
    if eta:
        print(eta)

    sup_g = sum(grams.get(k, 0.0) for k in SUPPORT_FEATURES)
    sup_s = sum(secs.get(k, 0.0) for k in SUPPORT_FEATURES)
    print()
    if sup_g or sup_s:
        print(f"ПОДДЕРЖКИ ЕСТЬ: {sup_g:.2f} г, {int(sup_s // 60)} мин "
              f"({sup_g / total_g * 100:.0f}% пластика)")
    else:
        print("ПОДДЕРЖЕК В ЭТОЙ НАРЕЗКЕ НЕТ — так и писать в ответе, "
              "а не умалчивать.")


if __name__ == "__main__":
    main()
