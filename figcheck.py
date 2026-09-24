#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["trimesh", "numpy", "scipy", "networkx", "rtree", "shapely", "manifold3d"]
# ///
"""Разбор органической модели (фигурки) перед печатью на A1.

printcheck.py отвечает на вопрос «это вообще корректная сетка». Этот скрипт
отвечает на четыре других, которые у фигурок решают всё:

    1. на чём деталь стоит — площадь первого слоя и число пятен;
    2. на какой высоте резать плоское дно, чтобы стенки над срезом
       не оказались свесом;
    3. нужны ли поддержки «везде» или хватит «только от стола»;
    4. что даст уменьшение высоты слоя — карта ступенек по площади.

    uv run tools/figcheck.py модель.stl
    uv run tools/figcheck.py модель.stl --layers 0.20 0.12 0.08
    uv run tools/figcheck.py модель.stl --rx 15        # то же в наклонённой позе

Углы везде считаются ОТ ГОРИЗОНТАЛИ: 90° — грань смотрит прямо вниз
(худший свес), 0° — вертикальная стенка. У Bambu в `support_threshold_angle`
обратное соглашение («угол наклона»), пересчёт: порог = 90 − наш угол.
"""
import argparse, sys
import numpy as np
import trimesh
import hardware                 # ширина линии и слой — из hardware.json

NOZZLE_LINE = hardware.line_width()   # ширина линии внешнего периметра у профиля сопла


def contour_stats(m, z):
    s = m.section(plane_origin=[0, 0, z], plane_normal=[0, 0, 1])
    if s is None:
        return 0.0, 0, 0.0
    p, _ = s.to_2D()
    return p.area, len(p.polygons_full), sum(g.length for g in p.polygons_full)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("stl")
    ap.add_argument("--layers", nargs="*", type=float, default=[0.20, 0.12, 0.08])
    ap.add_argument("--cuts", nargs="*", type=float, default=[0.5, 1.0, 1.5, 2.0, 3.0])
    ap.add_argument("--rx", type=float, default=0.0,
                    help="наклонить вокруг оси X, градусы — перебор ориентации")
    ap.add_argument("--ry", type=float, default=0.0, help="то же вокруг оси Y")
    a = ap.parse_args()

    m = trimesh.load(a.stl, process=True)
    # Наклон применяется ДО постановки на ноль: все числа ниже — про ту
    # ориентацию, в которой деталь реально поедет на стол.
    for ang, axis in ((a.rx, [1, 0, 0]), (a.ry, [0, 1, 0])):
        if ang:
            m.apply_transform(trimesh.transformations.rotation_matrix(
                np.radians(ang), axis, m.centroid))
    m.apply_translation([0, 0, -m.bounds[0][2]])
    ext = m.extents
    print(f"{a.stl}: {len(m.faces)} граней, габарит "
          f"{ext[0]:.1f} x {ext[1]:.1f} x {ext[2]:.1f} мм, объём {m.volume/1000:.1f} см³")
    print(f"  замкнутость {m.is_watertight}, тел {m.body_count}, эйлер {m.euler_number}")

    # 1. на чём стоит
    print("\n--- ОПОРА НА СТОЛ")
    area, n, _ = contour_stats(m, 0.05)
    print(f"  первый слой: {area:.1f} мм² в {n} пятнах")
    if area < 100:
        print("  !! меньше 100 мм² — деталь стоит почти в точку, нужен плоский срез дна")

    # 2. где резать: угол стенки сразу над срезом
    print("\n--- ВЫБОР ВЫСОТЫ СРЕЗА ДНА")
    print(f"  {'рез':>6} {'опора':>9} {'пятен':>6} {'стенка над срезом':>19}")
    for c in a.cuts:
        A0, n0, per = contour_stats(m, c)
        A1, _, _ = contour_stats(m, c + 0.5)
        if per == 0:
            continue
        # расширение контура по радиусу на 1 мм высоты -> угол стенки от горизонтали
        dr = (A1 - A0) / 0.5 / per
        ang = np.degrees(np.arctan2(1.0, max(dr, 1e-6)))
        mark = "" if ang >= 55 else "   <- свес, резать выше"
        print(f"  {c:6.2f} {A0:8.0f} мм² {n0:5d} {ang:16.0f}°{mark}")

    # 3. поддержки: куда они упрутся
    print("\n--- СВЕСЫ И ПОДДЕРЖКИ")
    nrm, ar, cen = m.face_normals, m.area_faces, m.triangles_center
    ang = np.degrees(np.arcsin(np.clip(-nrm[:, 2], -1, 1)))
    tot = ar.sum()
    for t in (30, 45, 60, 70):
        s = ang > t
        print(f"  нависание круче {t}° от горизонтали: {ar[s].sum():7.0f} мм² "
              f"({100*ar[s].sum()/tot:4.1f}%)   порог Bambu для этого = {90-t}")
    sel = np.where((ang > 45) & (cen[:, 2] > 3))[0]
    if len(sel):
        org = cen[sel] + [0, 0, -0.05]
        loc, idx, _ = m.ray.intersects_location(org, np.tile([0, 0, -1.0], (len(sel), 1)),
                                                multiple_hits=False)
        hit = np.zeros(len(sel), bool)
        hit[idx] = True
        on_model = ar[sel][hit].sum()
        print(f"  из свесов выше z=3: на саму модель опираются {on_model:.0f} мм² "
              f"({100*on_model/ar[sel].sum():.0f}%), на стол {ar[sel][~hit].sum():.0f} мм²")
        if on_model / ar[sel].sum() > 0.15:
            print("  !! «Поддержка только от стола» оставит эти площади без поддержки")

    # 4. слоистость
    print("\n--- СТУПЕНЬКИ НА ПОВЕРХНОСТИ (ширина = высота слоя / tg наклона)")
    slope = np.degrees(np.arccos(np.clip(np.abs(nrm[:, 2]), 0, 1)))
    print(f"  {'слой':>6} {'<0.1 мм':>9} {'0.1-0.2':>9} {'0.2-0.4':>9} {'>0.4 мм':>9}"
          f" {'порог поддержки':>17}")
    for lh in a.layers:
        step = np.where(slope > 0.5, lh / np.tan(np.radians(np.clip(slope, 0.5, 90))), np.inf)
        row = []
        for loq, hiq in [(0, .1), (.1, .2), (.2, .4), (.4, 1e9)]:
            s = (step >= loq) & (step < hiq)
            row.append(100 * ar[s].sum() / tot)
        # порог, при котором соседние линии перекрываются хотя бы наполовину
        thr = np.degrees(np.arctan2(lh, NOZZLE_LINE / 2))
        print(f"  {lh:6.2f} " + " ".join(f"{x:8.1f}%" for x in row) + f" {thr:16.0f}°")
    print("  порог поддержки — это значение support_threshold_angle: при нём шаг стенки\n"
          "  за слой не больше половины ширины линии, край не загибается")


if __name__ == "__main__":
    sys.exit(main())
