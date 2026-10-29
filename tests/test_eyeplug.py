"""eyeplug: an eye as a separate part fitted to its socket.

Checked on substance: the socket is measured back to the radius it was cut
with, the eye comes out with the clearance measured ray by ray against the
head, its volume matches the plug-and-cap formula, inserts do not overlap the
white, a donor eye is rescaled to another socket, and a painted eye is found
by its filament.
"""
import json, math
from harness import case, run, num, close, contains, work, TESTS, TOOLS

DEPS = ("numpy", "scipy", "trimesh", "manifold3d")
TOOL = TOOLS / "eyeplug.py"
SEED = "--at=13.5,4,2.7"           # у дна ниши фикстуры, ось ~ (0.94, 0.28, 0.19)

_FIX = {}


def fix(name):
    if not _FIX:
        d = work("eyeplug_фикстуры")
        run([TESTS / "eyeplug_fixtures.py", d], deps=("numpy", "trimesh", "manifold3d"))
        _FIX["dir"] = d
    return _FIX["dir"] / name


def fitted(name, d):
    out = run([TOOL, "fit", fix(name), SEED, "-o", d / "fit.json"], deps=DEPS)
    return out, d / "fit.json"


@case("eyeplug:fit находит радиус, ось, плоское дно и косое устье ниши")
def _():
    d = work("eyeplug_fit")
    out, f = fitted("head40.stl", d)
    close(num(out, r"ниша R ([\d.]+)"), 4.0, 0.02, "радиус ниши")
    contains(out, "ось [0.9407 0.2822 0.1881]", "наклон к оси 0.0°")
    assert num(out, r"разброс ([\d.]+) мм") < 0.01, "дно фикстуры плоское"
    # ниша смещена от центра шара на 2 мм: устье — косое седло
    lo = num(out, r"глубина устья ([\d.]+)\.\.")
    hi = num(out, r"глубина устья [\d.]+\.\.([\d.]+)")
    close(lo, 0.72, 0.05, "мелкий край устья")
    close(hi, 1.86, 0.05, "глубокий край устья")
    e = json.load(open(f))["eyes"][0]
    close(e["R"], 4.0, 0.02, "R в json")


@case("eyeplug:build — зазор 0.15 к стенке головы, деталь замкнута")
def _():
    d = work("eyeplug_build")
    _, f = fitted("head40.stl", d)
    out = run([TOOL, "build", "--fit", f, "-o", d / "b"], deps=DEPS)
    contains(out, "зазор глаза в нише 0.15 на сторону (печать на ребре)", "замкнуто да")
    close(num(out, r"R пробки ([\d.]+)"), 3.85, 0.005, "радиус глаза")
    close(num(out, r"зазор по стенке к голове: мин ([\d.]+)"), 0.15, 0.01, "наименьший зазор")
    close(num(out, r"зазор по стенке к голове: мин [\d.]+, медиана ([\d.]+)"), 0.15, 0.01, "зазор")
    close(num(out, r"макс ([\d.]+) мм \("), 0.15, 0.01, "наибольший зазор")
    v = num(out, r"white\s+объём\s+([\d.]+)")
    assert 90 < v < 98, f"объём глаза {v}"
    # на ребре: ось вдоль X, по X — высота глаза, по Y и Z — диаметр
    close(num(out, r"габарит ([\d.]+) ×"), num(out, r"высота глаза ([\d.]+)"), 0.01, "поза на ребре")


@case("eyeplug:build по размерам — объём как у цилиндра с шапкой")
def _():
    d = work("eyeplug_руками")
    out = run([TOOL, "build", "--niche-r", "4.0", "--side", "1.15", "--dome-r", "5.8",
               "--print", "flat", "-o", d], deps=DEPS)
    contains(out, "зазор глаза в нише 0.05 на сторону (печать плашмя)")
    r, R = 3.95, 5.8
    hc = R - math.sqrt(R * R - r * r)
    want = math.pi * r * r * (1.15 - 0.02) + math.pi * hc * hc * (3 * R - hc) / 3
    close(num(out, r"white\s+объём\s+([\d.]+)"), want, 0.2, "объём")
    close(num(out, r"высотой ([\d.]+)"), hc, 0.002, "высота купола")


@case("eyeplug:зрачок и блик — вкладки без пересечений, толщина у края 1.0")
def _():
    d = work("eyeplug_зрачок")
    _, f = fitted("head40.stl", d)
    out = run([TOOL, "build", "--fit", f, "--iris", "2.4,2.6", "--pupil", "1.2,1.5",
               "--glint", "pin", "--glint-d", "1.6", "-o", d / "b"], deps=DEPS)
    contains(out, "худшее пересечение 0.0000 мм³")
    for nm in ("white", "iris", "pupil", "glint"):
        contains(out, f"eye1_{nm}.stl")
    assert out.count("замкнуто да") == 4, "не все детали замкнуты"
    close(num(out, r"толщина у края ([\d.]+)\.\."), 1.0, 0.01, "толщина зрачка у края")
    close(num(out, r"glint\s+объём\s+[\d.]+ мм³, габарит ([\d.]+)"), 1.6, 0.02, "диаметр штырька")
    t = num(out, r"pupil.*\n.*в центре ([\d.]+)")
    assert t > 1.0, "зрачок в центре толще, чем у края"


@case("eyeplug:donor — готовый глаз под нишу другого радиуса")
def _():
    d = work("eyeplug_донор")
    _, f40 = fitted("head40.stl", d)
    out = run([TOOL, "build", "--fit", f40, "--no-check", "-o", d / "b"], deps=DEPS)
    v0 = num(out, r"white\s+объём\s+([\d.]+)")
    f35 = d / "fit35.json"
    run([TOOL, "fit", fix("head35.stl"), SEED, "-o", f35], deps=DEPS)
    out = run([TOOL, "donor", d / "b" / "eye1_white.stl", "--fit", f35, "-o", d / "d"], deps=DEPS)
    close(num(out, r"донор \S+: R ([\d.]+)"), 3.85, 0.005, "радиус донора")
    close(num(out, r"радиус глаза ([\d.]+)"), 3.35, 0.005, "радиус после подгонки")
    k = num(out, r"масштаб ([\d.]+)")
    close(k, 3.35 / 3.85, 0.001, "масштаб")
    close(num(out, r"объём ([\d.]+) мм³"), v0 * k ** 3, 0.3, "объём масштабом")
    close(num(out, r"зазор по стенке к голове: мин ([\d.]+)"), 0.15, 0.01, "зазор донора")
    contains(out, "замкнуто да")
    out = run([TOOL, "donor", d / "b" / "eye1_white.stl", "--fit", f35, "--rim", "follow",
               "-o", d / "d2"], deps=DEPS)
    assert num(out, r"остаток седла ([\d.]+)") < 0.02, "край донора не лёг на устье"


@case("eyeplug:нарисованный глаз находится по филаменту, резак шире на зазор")
def _():
    d = work("eyeplug_краска")
    out = run([TOOL, "fit", fix("painted.3mf"), "--code", "2", "-o", d / "fit.json"], deps=DEPS)
    close(num(out, r"сфера R ([\d.]+)"), 12.0, 0.02, "радиус головы под краской")
    close(num(out, r"контур ([\d.]+) ×"), 2 * 12 * math.sin(math.radians(18)), 0.06, "контур")
    w = num(out, r"контур ([\d.]+) ×")
    out = run([TOOL, "socket", "--fit", d / "fit.json", "-o", d / "s"], deps=DEPS)
    close(num(out, r"резак ниши ([\d.]+) ×"), w + 0.30, 0.005, "резак = контур + 2 × 0.15")
    out = run([TOOL, "build", "--fit", d / "fit.json", "-o", d / "b"], deps=DEPS)
    close(num(out, r"купол R ([\d.]+)"), 12.0, 0.02, "купол повторяет голову")
    contains(out, "замкнуто да")
