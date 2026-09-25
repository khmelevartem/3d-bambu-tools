#!/usr/bin/env bash
# Estimate a print without opening Bambu Studio: plastic weight, time, G-code,
# plus a .3mf project that opens in the GUI.
#
# About the 3MF the CLI writes. Exactly one thing stands in the way of opening
# it: Bambu Studio writes compatible_printers into
# Metadata/model_settings.config with unescaped quotes inside an XML attribute:
#     <metadata key="compatible_printers" value=""Bambu Lab A1 0.4 nozzle""/>
# The parser then fails on a malformed token and the GUI reports that the file
# contains no geometry data. The Production extension and p:path are not at
# fault; they read fine. This script repairs the quotes itself. BUT: a file
# that parses after the repair is not proven to display in the GUI - the
# open-check in the 3d-modeling skill passes no project file at all, including
# ones Bambu Studio wrote. Treat "it opens" as unverified.
#
# Usage:
#     ./slice.sh /path/model.stl              - the stock profile
#     ./slice.sh --supports /path/model.stl   - the same, with supports
#
# WITHOUT the flag there are no supports: the stock process has
# enable_support = 0, and the weight and time then describe a print without
# them. What actually came out is printed by gcode_report.py at the end, from
# the G-code itself rather than from the profile.
#
# The nozzle and the preset names come from hardware.json (through
# tools/hardware.py) and are not duplicated here. One-off override:
# A1_NOZZLE=0.2 ./slice.sh part.stl
#
# Everything this script writes goes into work/out inside the project, not next
# to the model: a model folder holds only what gets printed.

set -euo pipefail

# The macOS bundle by default; BS=/path/to/binary picks another one.
BS="${BS:-/Applications/BambuStudio.app/Contents/MacOS/BambuStudio}"
HERE="$(cd "$(dirname "$0")" && pwd)"
PROF="$HERE/profiles"
SUPPORTS=0
if [ "${1:-}" = "--supports" ]; then SUPPORTS=1; shift; fi
[ $# -eq 1 ] || { sed -n '19,31p' "$0"; exit 2; }
[ -x "$BS" ] || { echo "Bambu Studio не найден: $BS" >&2
                  echo "путь задаётся переменной BS перед командой" >&2; exit 2; }

MODEL="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"
OUT="$(dirname "$HERE")/work/out"

# The machine comes from hardware.json. The flattened-profile cache is tagged by
# nozzle, or the script would silently slice with the previous nozzle's profile.
# The nozzle alone is not enough: change the printer, the process or the filament
# in hardware.json and the tag does not move, so the cache is also rebuilt when
# the preset name inside it stops matching the one being asked for. Without that
# the numbers stay plausible while belonging to the previous preset.
NZ="$(python3 "$HERE/hardware.py" nozzle_key)"
MACHINE_ID="$(python3 "$HERE/hardware.py" machine)"
PROCESS_ID="$(python3 "$HERE/hardware.py" process)"
FILAMENT_ID="$(python3 "$HERE/hardware.py" filament)"
echo "сопло $NZ: $MACHINE_ID / $PROCESS_ID / $FILAMENT_ID"

mkdir -p "$PROF" "$OUT"
cached() {   # cached <file> <preset name>: is this the flattened profile of it?
    [ -f "$1" ] && [ "$(python3 -c 'import json,sys
print(json.load(open(sys.argv[1])).get("name", ""))' "$1" 2>/dev/null)" = "$2" ]
}
cached "$PROF/machine-$NZ.json"  "$MACHINE_ID"  || python3 "$HERE/resolve_profile.py" "$MACHINE_ID"  "$PROF/machine-$NZ.json"
cached "$PROF/process-$NZ.json"  "$PROCESS_ID"  || python3 "$HERE/resolve_profile.py" "$PROCESS_ID"  "$PROF/process-$NZ.json"
cached "$PROF/filament-$NZ.json" "$FILAMENT_ID" || python3 "$HERE/resolve_profile.py" "$FILAMENT_ID" "$PROF/filament-$NZ.json"

# Process: the stock one, or its copy with supports. Both send curr_bed_type:
# without it the CLI selects a cold plate type and the bed heats far below what
# PLA on a textured PEI plate needs (verified by the M190 line in the G-code).
PROCESS="$PROF/process-$NZ.json"
[ "$SUPPORTS" = 1 ] && PROCESS="$PROF/process_support-$NZ.json"
python3 - "$PROF/process-$NZ.json" "$PROCESS" "$SUPPORTS" "$(python3 "$HERE/hardware.py" plate)" <<'PY'
import json, sys, pathlib
base, dst, supports, plate = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]), sys.argv[3] == "1", sys.argv[4]
d = json.loads(base.read_text())
d["curr_bed_type"] = plate
if supports:
    # exactly what the support profile changed relative to the stock one
    d["name"] = d["name"] + " + поддержки"
    d["enable_support"] = "1"
    d["support_on_build_plate_only"] = "1"
    d["wall_loops"] = "3"
pathlib.Path(dst).write_text(json.dumps(d, ensure_ascii=False, indent=4))
PY

# --outputdir is mandatory and absolute; the name in --export-3mf carries no path.
# Profiles must be flattened: the CLI does not complain about "inherits", it
# silently ignores inheritance and substitutes its own defaults - slicing
# "succeeds" with the wrong settings. That is what resolve_profile.py is for.
"$BS" --outputdir "$OUT" \
      --load-settings "$PROF/machine-$NZ.json;$PROCESS" \
      --load-filaments "$PROF/filament-$NZ.json" \
      --orient 1 --arrange 1 --slice 0 \
      --export-3mf "$(basename "${MODEL%.*}").gcode.3mf" "$MODEL" > "$OUT/slice.log" 2>&1

python3 - "$OUT" "$MODEL" <<'PY'
import json, re, shutil, sys, pathlib, zipfile
out, model = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
r = json.loads((out / "result.json").read_text())

def walk(d, key):
    if isinstance(d, dict):
        if key in d: yield d[key]
        for v in d.values(): yield from walk(v, key)
    elif isinstance(d, list):
        for v in d: yield from walk(v, key)

grams = sum(walk(r, "total_used_g"))
# Time comes from total_predication - the same estimate Bambu writes into the
# G-code as the total estimated time. Summing feature_type_times overstates it
# by roughly 15 %.
secs = sum(walk(r, "total_predication"))
print(f"пластик:  {grams:.1f} г")
print(f"время:    {int(secs // 3600)} ч {int(secs % 3600 // 60)} мин")
print(f"G-код:    {out / 'plate_1.gcode'}")

p3mf = out / (model.stem + ".gcode.3mf")
if p3mf.exists():
    with zipfile.ZipFile(p3mf) as z:
        if "Metadata/plate_1.png" in z.namelist():
            (out / "plate_preview.png").write_bytes(z.read("Metadata/plate_1.png"))
            print(f"превью:   {out / 'plate_preview.png'}")

    # repair the unescaped quotes and rebuild the archive
    tmp = out / "_repack"
    if tmp.exists(): shutil.rmtree(tmp)
    with zipfile.ZipFile(p3mf) as z: z.extractall(tmp)
    cfg = tmp / "Metadata" / "model_settings.config"
    if cfg.exists():
        s = cfg.read_text()
        fixed = re.sub(r'<metadata key="([^"]*)" value="(.*?)"/>',
                       lambda m: '<metadata key="%s" value="%s"/>'
                                 % (m.group(1), m.group(2).replace('"', "&quot;")), s)
        if fixed != s:
            cfg.write_text(fixed)
            with zipfile.ZipFile(p3mf, "w", zipfile.ZIP_DEFLATED) as z:
                for f in sorted(tmp.rglob("*")):
                    if f.is_file(): z.write(f, f.relative_to(tmp))
    shutil.rmtree(tmp)
    print(f"проект:   {p3mf}  (открывается в Bambu Studio)")

print(f"\nисходная модель: {model}")
PY

# The breakdown by line type and the explicit answer about supports - read that,
# not only the total grams: the total looks the same with and without supports.
echo
python3 "$HERE/gcode_report.py" "$OUT/plate_1.gcode"
