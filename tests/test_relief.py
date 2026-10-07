"""relief_cut: painted relief cut out as inlay parts.

Checked on substance: the ring is measured back to the numbers it was built
with, the parts close, do not touch the body and slide out along their axis
without catching, the platform under the sheet is flat, the moustache has a
flat back, and the zone's paint comes out on the part, not on the body.
"""
from harness import case, run, num, close, contains, work, TESTS, TOOLS

DEPS = ("numpy", "scipy", "trimesh", "manifold3d", "shapely", "mapbox_earcut",
        "scikit-image", "pymeshfix", "rtree")
TOOL = TOOLS / "relief_cut.py"

_FIX = {}


def fix(name):
    """cap / relief / gauge from relief_fixtures.py, built once per run."""
    if not _FIX:
        d = work("relief_фикстуры")
        run([TESTS / "relief_fixtures.py", d], deps=("numpy", "trimesh", "manifold3d"))
        _FIX["dir"] = d
    return _FIX["dir"] / name


GAUGE_AXIS = "0.2704,-0.3167,0.9092"     # the fixture's z axis after its rotation
DRAPE_AXIS = "0,-0.94,-0.34"


@case("relief_cut:кольцо обмеряется и восстанавливается телом вращения")
def _():
    d = work("relief_кольцо")
    out = run([TOOL, "ring", "--paint", fix("gauge.npz"), "--zone", "1", "--out", d / "g"], deps=DEPS)
    contains(out, "колец в зоне 1: 1", "ось [ 0.27  -0.317  0.909]",
              "вставляется со стороны бортика: там свободно (0 точек тела)")
    close(num(out, r"отверстие r ([\d.]+)"), 1.5, 0.02, "радиус отверстия")
    close(num(out, r"бортик снаружи r ([\d.]+)"), 3.1, 0.05, "наружный радиус бортика")
    close(num(out, r"длина ([\d.]+) мм -> "), 5.4, 0.1, "длина тоннеля")
    contains(out, "(замкнута да)", "замкнуто да")
    # почти вся чёрная краска на детали: бортик касательный к отверстию
    assert num(out, r"кольцо 1 ([\d.]+), тело") > 85, "краска кольца не на детали"
    chk = run([TOOL, "check", d / "g_rest.stl", f"{d / 'g_1.stl'}@{GAUGE_AXIS}",
               "--travel", "8", "--step", "0.2"], deps=DEPS)
    contains(chk, "пересечения: нет", "худшее пересечение 0.0000 мм³")


@case("relief_cut:размеры кольца задаются руками")
def _():
    d = work("relief_кольцо_руками")
    out = run([TOOL, "ring", "--paint", fix("gauge.npz"), "--zone", "1",
               "--at", "5,-3,10", "--lip", "3.2", "--length", "5", "--out", d / "g"], deps=DEPS)
    contains(out, "бортик r 3.20", "длина 5.00 мм")


@case("relief_cut:гладкий лист снимает шапку и оставляет площадку")
def _():
    d = work("relief_лист")
    out = run([TOOL, "sheet", "--paint", fix("cap.npz"), "--zone", "3", "--axis", "0,0,1",
               "--plane", "12", "--out", d / "c"], deps=DEPS)
    contains(out, "(замкнута да)", "(замкнуто да)")
    assert num(out, r"площадка тела под печать: ([\d.]+)") > 60, "площадка мала"
    assert num(out, r"деталь ([\d.]+), тело") > 1020, "краска шапки осталась на теле"
    assert num(out, r"тело ([\d.]+), в зазоре") < 10, "краска шапки осталась на теле"
    chk = run([TOOL, "check", d / "c_rest.stl", f"{d / 'c_part.stl'}@0,0,1",
               "--src", fix("cap.stl"), "--travel", "10", "--step", "0.5"], deps=DEPS)
    contains(chk, "пересечения: нет", "худшее пересечение 0.0000 мм³")
    assert num(chk, r"на зазоры ушло ([\d.]+)") < 150, "зазор съел слишком много"


@case("relief_cut:перебор плоскости площадки без резки")
def _():
    out = run([TOOL, "sheet", "--paint", fix("cap.npz"), "--zone", "3", "--axis", "0,0,1",
               "--plane-scan", "11,13"], deps=DEPS)
    a11 = num(out, r"плоскость 11: площадка (\d+)")
    a13 = num(out, r"плоскость 13: площадка (\d+)")
    assert a11 > a13 > 0, f"ниже по шару площадка должна быть шире: {a11} против {a13}"


@case("relief_cut:усы — пробка по линии прирастания и плоская спина")
def _():
    d = work("relief_усы")
    out = run([TOOL, "drape", "--paint", fix("relief.npz"), "--zone", "3", "--axis", DRAPE_AXIS,
               "--out", d / "m"], deps=DEPS)
    assert num(out, r"свисает над воздухом ([\d.]+)") > 1, "свисающих краёв не нашлось"
    assert num(out, r"от кончиков рельефа на 1.6 мм: срезано ([\d.]+)") > 0.5, "кончики не укорочены"
    back = num(out, r"плоская спина ⟂ оси: ([\d.]+)")
    plug = num(out, r"пробка по линии прирастания [\d.]+ -> ([\d.]+)")
    close(back, plug, 1.0, "спина — это дно пробки")
    contains(out, "(замкнута да)", "(замкнуто да)", "зазор 0.2 вбок, 0.05 по глубине")
    assert num(out, r"деталь ([\d.]+), тело") > 134, "краска усов осталась на теле"
    chk = run([TOOL, "check", d / "m_rest.stl", f"{d / 'm_part.stl'}@{DRAPE_AXIS}"], deps=DEPS)
    contains(chk, "пересечения: нет", "худшее пересечение 0.0000 мм³")


@case("relief_cut:проверка ловит наложение и застревание")
def _():
    d = work("relief_наложение")
    run([TOOL, "ring", "--paint", fix("gauge.npz"), "--zone", "1", "--out", d / "g"], deps=DEPS)
    # деталь против исходной пластины: лезет в неё и застревает при снятии
    out = run([TOOL, "check", fix("gauge.stl"), f"{d / 'g_1.stl'}@{GAUGE_AXIS}",
               "--travel", "4", "--step", "0.5"], deps=DEPS, expect=1)
    contains(out, "gauge × g_1", "ЗАСТРЕВАЕТ")


@case("relief_cut:без подкоманды печатает справку")
def _():
    out = run([TOOL], deps=DEPS, expect=1)
    contains(out, "Cut painted relief out of an organic body")


@case("relief_cut:защемление по ребру сохраняется замкнутым и без лишнего объёма")
def _():
    # два кубика касаются по ребру: булева операция держит там вершины-близнецы,
    # при записи они склеиваются в рёбра с четырьмя гранями; ремонт дыр
    # в таком месте достраивает материал, которого у детали не было
    d = work("relief_защемление")
    probe = d / "probe.py"
    probe.write_text(
        "import sys, trimesh, manifold3d as mf\n"
        f"sys.path.insert(0, {str(TOOLS)!r})\n"
        "import relief_cut as rc\n"
        "a = mf.Manifold.cube([2, 2, 2]); b = mf.Manifold.cube([2, 2, 2]).translate([2, 2, 0])\n"
        "m = a + b\n"
        f"t = rc.save(m, {str(d / 'pinch.stl')!r})\n"
        f"u = trimesh.load({str(d / 'pinch.stl')!r})\n"
        "print(f'объём {u.volume:.4f} из {m.volume():.4f}, замкнута {u.is_watertight}')\n",
        encoding="utf-8")
    out = run([probe], deps=DEPS)
    close(num(out, r"объём ([\d.]+) из"), 16.0, 0.01, "объём после записи")
    contains(out, "замкнута True")
