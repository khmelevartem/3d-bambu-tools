#!/usr/bin/env python3
"""Разбор нарезанного G-кода по типам линий: сколько граммов и времени
на что ушло, и есть ли в печати поддержки.

Зачем отдельный скрипт. Вес и время, которые печатает slice.sh, — это итог
по всей печати, и по нему не видно главного: включены ли поддержки. Один
раз это уже стоило кривой детали (12.09.2026, переключатель жд: 32 г без
поддержек вместо 36 г с ними). Здесь считается то, что реально записано
в G-коде, а не то, что стоит в профиле.

    python3 tools/gcode_report.py out/plate_1.gcode

Работает с любым G-кодом Bambu Studio — и от slice.sh, и от нарезки
чужого проекта .3mf его собственным профилем.

Как считается вес. Bambu пишет экструзию в относительных координатах
(M83), поле E — длина проглоченного филамента в мм. Объём — длина на
площадь сечения прутка, дальше на плотность. Диаметр и плотность берутся
из шапки самого G-кода (`; filament_diameter`, `; filament_density`),
поэтому для Matte (1.32) и для Basic (1.26) выходят разные граммы.
Считаются только ходы с перемещением по X/Y: ретракт и его возврат
филамент не тратят.

Время — из result.json рядом с G-кодом (feature_type_times, секунды),
если он есть. Ключи там те же, что в `; FEATURE:`.
"""
import json
import pathlib
import re
import sys

SUPPORT_FEATURES = ("Support", "Support interface")


def parse(path):
    """-> (граммы по фичам, плотность, диаметр, итоговая строка времени)"""
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
                continue            # ретракт/возврат — движения нет
            if m := re.search(r"\sE(-?[\d.]+)", line):
                e = float(m.group(1))
                if e > 0:
                    mm[feat] = mm.get(feat, 0.0) + e
    area = 3.141592653589793 * (diameter / 2) ** 2      # мм²
    grams = {k: v * area * density / 1000 for k, v in mm.items()}
    return grams, density, diameter, eta


def seconds_by_feature(gcode):
    """feature_type_times из result.json рядом с G-кодом, если он есть."""
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
