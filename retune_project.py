#!/usr/bin/env python3
"""Перенести настройки печати в проект, сохранённый интерфейсом Bambu Studio.

Зачем. Проект, собранный кодом (`make_multicolor_3mf.py`), интерфейс не
открывает: «Invalid configuration file» и следом «The file does not contain
any geometry data». Рабочий обход — отдать файл с `--no-project`, дать
человеку открыть и сохранить его, а настройки досылать уже в ЕГО файл:
оболочку и `project_settings.config` там написала сама Bambu Studio.

    python3 tools/retune_project.py файл_человека.3mf -o готовый.3mf
    python3 tools/retune_project.py файл.3mf -o готовый.3mf \
        --from "models/adam guitar/adam guitar 2.3mf"

Что переносится: значения ключей из эталонного проекта — того, который
Bambu Studio сохранила с нужным соплом и процессом.

Что НЕ трогается, и это главное:

* `different_settings_to_system` — список правок относительно системного
  пресета. **Его нельзя сочинять.** 20.09.2026 проверено на
  `robbie albert hand lower`: со списком, написанным мной (11 ключей,
  включая `curr_bed_type`), интерфейс файл не открыл; с тем же файлом, где
  список остался авторский, — открыл. Больше отличий между попытками не было.
* **сами ключи из этого списка** — это то, что человек менял руками:
  включённые поддержки, выбранный слой, выключенная башня. Эталон несёт
  свои решения по тем же ключам, и без этой оговорки перенос гасит чужую
  работу: на `robbie albert` эталон выключал поддержки, которые человек
  включил.
* `filament_colour` — цвета, которые человек выбрал под свои катушки.
* хосты печати (`host_type`, `printhost_*`) — они про его аккаунт.

Следствие, о котором надо сказать вслух. Интерфейс берёт системный пресет и
накладывает только ключи из `different_settings_to_system`. Значит правки,
которых в этом списке нет, доедут до CLI-нарезки, но в интерфейсе человек их
не увидит — их он ставит руками (и лучше сохраняет своим пресетом). Числа,
посчитанные `slice.sh` и `figopt.py` по такому файлу, описывают CLI-нарезку.
"""
import argparse
import collections
import json
import re
import sys
import zipfile

DEFAULT_REF = "models/adam guitar/adam guitar 2.3mf"

# Не переносим: это выбор человека либо структура, которую пишет интерфейс.
KEEP = {
    "different_settings_to_system",     # см. докстроку — сочинять нельзя
    "filament_colour",                  # его катушки
    "host_type", "printhost_authorization_type", "printhost_ssl_ignore_revoke",
    "filament_colour_type", "filament_multi_colour", "extruder_nozzle_stats_new",
}

CFG = "Metadata/project_settings.config"


def paint_signature(path):
    """Отпечаток геометрии и покраски: число треугольников и счётчики кодов."""
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
    ap.add_argument("src", help="проект, сохранённый интерфейсом Bambu Studio")
    ap.add_argument("-o", "--out", required=True, help="куда писать результат")
    ap.add_argument("--from", dest="ref", default=DEFAULT_REF,
                    help=f"эталон настроек, тоже сохранённый интерфейсом (по умолчанию {DEFAULT_REF})")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    zin = zipfile.ZipFile(a.src)
    if CFG not in zin.namelist():
        sys.exit(f"{a.src}: нет {CFG} — это файл без настроек, его сперва должен "
                 f"открыть и сохранить человек, иначе переносить не во что")
    mine = json.loads(zin.read(CFG).decode("utf-8"))
    ref = json.loads(zipfile.ZipFile(a.ref).read(CFG).decode("utf-8"))

    # Ключи, которые человек правил руками, — их интерфейс и перечислил.
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
        print(f"\nсписок правок оставлен авторский: "
              f"{mine['different_settings_to_system'][0] or '(пуст)'}")
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
