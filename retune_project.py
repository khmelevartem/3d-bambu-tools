#!/usr/bin/env python3
"""Port print settings into a project saved by the Bambu Studio GUI.

Why. A project assembled in code (`make_multicolor_3mf.py`) will not open
in the GUI: "Invalid configuration file" and then "The file does not contain
any geometry data". The working way around is to hand the file over with
`--no-project`, let the human open and save it, and send the settings into
THEIR file: its shell and `project_settings.config` were written by Bambu itself.

    python3 tools/retune_project.py human_file.3mf -o ready.3mf
    python3 tools/retune_project.py file.3mf -o ready.3mf \
        --from "models/other/reference.3mf"

What gets ported: key values from a reference project — one that Bambu
Studio saved with the right nozzle and process.

What is NOT touched, and this is the important part:

* `different_settings_to_system` — the list of edits relative to the system
  preset. **It must not be invented.** A hand-written list, even a plausible
  one, makes the GUI refuse to open the file; the same file with the author's
  list left intact opens.
* **the keys in that list themselves** — they are what the human changed by
  hand: supports switched on, the layer chosen, the prime tower switched off.
  The reference carries its own decisions for the same keys, and without this
  caveat the port silently wipes their work.
* `filament_colour` — the colours they picked for their own spools.
* print hosts (`host_type`, `printhost_*`) — those are about their account.

A consequence worth saying out loud. The GUI takes the system preset and
applies only the keys from `different_settings_to_system`. So edits that are
not in that list do reach a CLI slice, but the human will not see them in
the GUI — those they set by hand (and better, save as their own preset).
Numbers from `slice.sh` and `figopt.py` on such a file describe the CLI slice.
"""
import argparse
import collections
import json
import re
import sys
import zipfile

# Not ported: either the human's choice, or structure the GUI writes itself.
KEEP = {
    "different_settings_to_system",     # see the docstring — never invent it
    "filament_colour",                  # their spools
    "host_type", "printhost_authorization_type", "printhost_ssl_ignore_revoke",
    "filament_colour_type", "filament_multi_colour", "extruder_nozzle_stats_new",
}

CFG = "Metadata/project_settings.config"


def paint_signature(path):
    """Fingerprint of geometry and paint: triangle count and code counters."""
    z = zipfile.ZipFile(path)
    names = [n for n in z.namelist() if n.endswith(".model") and "Objects" in n]
    raw = z.read(names[0] if names else "3D/3dmodel.model").decode("utf-8", "replace")
    codes = collections.Counter(re.findall(r'paint_color="([0-9A-Fa-f]+)"', raw))
    whole = {k: v for k, v in codes.items() if k in ("4", "8", "0C", "1C", "2C", "3C")}
    split = sum(v for k, v in codes.items() if k not in whole)
    return raw.count("<triangle "), dict(sorted(whole.items())), split


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src", help="project saved by the Bambu Studio GUI")
    ap.add_argument("-o", "--out", required=True, help="where to write the result")
    ap.add_argument("--from", dest="ref", required=True,
                    help="settings reference, a project also saved by the GUI")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    zin = zipfile.ZipFile(a.src)
    if CFG not in zin.namelist():
        sys.exit(f"{a.src}: нет {CFG} — это файл без настроек, его сперва должен "
                 f"открыть и сохранить человек, иначе переносить не во что")
    mine = json.loads(zin.read(CFG).decode("utf-8"))
    zref = zipfile.ZipFile(a.ref)
    if CFG not in zref.namelist():
        sys.exit(f"{a.ref}: нет {CFG} — эталон тоже должен быть сохранён "
                 f"интерфейсом, иначе переносить нечего")
    ref = json.loads(zref.read(CFG).decode("utf-8"))

    # The keys the human edited by hand — the GUI listed them itself.
    hands = {k for part in mine.get("different_settings_to_system", [])
             for k in part.split(";") if k}
    keep = KEEP | hands

    changed = []
    for key, val in ref.items():
        if key in keep or key not in mine:
            continue
        if mine[key] != val:
            changed.append((key, mine[key], val))
            mine[key] = val

    body = json.dumps(mine, indent=4, ensure_ascii=False).encode("utf-8")
    zout = zipfile.ZipFile(a.out, "w", zipfile.ZIP_DEFLATED)
    for item in zin.infolist():
        data = zin.read(item.filename)
        if item.filename == CFG:
            data = body
        zout.writestr(item, data)
    zout.close()
    zin.close()

    before, after = paint_signature(a.src), paint_signature(a.out)
    if not a.quiet:
        print(f"эталон: {a.ref}")
        print(f"перенесено значений: {len(changed)}")
        for key, old, new in changed:
            if key in ("printer_settings_id", "print_settings_id", "nozzle_diameter",
                       "layer_height", "initial_layer_print_height", "wall_loops",
                       "sparse_infill_density", "support_style", "wall_generator",
                       "support_top_z_distance", "filament_settings_id", "enable_support"):
                print(f"  {key:30} {str(old)[:38]:38} -> {str(new)[:38]}")
        own = mine.get("different_settings_to_system") or [""]
        print(f"\nсписок правок оставлен авторский: {own[0] or '(пуст)'}")
        print("  интерфейс применит системный пресет плюс ЭТИ ключи; остальное "
              "человек ставит руками")
        if hands:
            print("  их значения не тронуты: "
                  + ", ".join(f"{k}={mine[k]}" for k in sorted(hands) if k in mine))
        print(f"\nгеометрия и покраска: {before[0]} треугольников, коды {before[1]}, "
              f"дроблёных кистью {before[2]}")
        print("перенесены без потерь:", "да" if before == after else "!! НЕТ, разошлись")
        print("записан", a.out)
    if before != after:
        sys.exit("покраска не совпала — проверь, не пересохранил ли человек файл "
                 "прямо во время сборки")


if __name__ == "__main__":
    main()
