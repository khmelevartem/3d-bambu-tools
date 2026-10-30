"""gcoderender: what the nozzle lays down in a layer, against the target part.

Checked on substance with a hand-written G-code: the offset between G-code and
part coordinates is recovered from the footprints, the bar and the full-circle
G2 disc are covered, the dot the G-code leaves out is reported as dropped at its
place and size, the prime tower is not counted, and without a target the layer
is still drawn with its area.
"""
from harness import case, run, num, close, contains, work, TESTS, TOOLS

DEPS = ("numpy", "scipy", "networkx", "opencv-python-headless", "trimesh")
TOOL = TOOLS / "gcoderender.py"
_FIX = {}


def fix(name):
    if not _FIX:
        d = work("gcoderender_фикстуры")
        run([TESTS / "gcoderender_fixtures.py", d], deps=("numpy", "trimesh"))
        _FIX["dir"] = d
    return _FIX["dir"] / name


@case("gcoderender:пропавшая точка найдена, дуги и сдвиг на месте")
def _():
    out = run([TOOL, fix("plate.gcode"), "--z", "2.6", "--target", f"2={fix('target.stl')}",
               "--out", work("gcoderender_выход")], deps=DEPS)
    close(num(out, r"сдвиг G-код − детали: ([+-][\d.]+),"), 100.0, 0.02, "сдвиг по X")
    close(num(out, r"сдвиг G-код − детали: [+-][\d.]+, ([+-][\d.]+)"), 50.0, 0.02, "сдвиг по Y")
    # bar 37.8 + disc 12.57 + dot 0.50 mm²
    close(num(out, r"цель +([\d.]+) мм²"), 50.87, 0.6, "площадь цели")
    cov = num(out, r"покрыто +([\d.]+) %")
    assert 95.0 <= cov <= 99.2, f"покрытие {cov} % — дот один, остальное залито"
    contains(out, "элементов 3, пропало (покрыто меньше 50%): 1")
    close(num(out, r"x +([\d.]+) +y +[\d.]+ +размер"), 14.0, 0.05, "x пропавшей точки")
    close(num(out, r"размер ([\d.]+)×"), 0.80, 0.06, "размер пропавшей точки")
    # the disc is drawn from G2 circles: without arcs nothing would cover it
    assert num(out, r"вылезло за контур +([\d.]+) мм²") < 1.5, "заливка не вылезает за контур"


@case("gcoderender:без цели — слой с площадью, башня не в счёт")
def _():
    out = run([TOOL, fix("plate.gcode"), "--out", work("gcoderender_слой")], deps=DEPS)
    contains(out, "рисую z = 2.6", "филамент 2: напечатано")
    close(num(out, r"филамент 2: напечатано +([\d.]+) мм²"), 50.37, 1.0, "площадь слоя")
    out = run([TOOL, fix("plate.gcode"), "--z", "0.2", "--out", work("gcoderender_низ")], deps=DEPS)
    # base: two lines 26 + 8 mm at 0.42; the 20 mm prime tower line is not counted
    close(num(out, r"филамент 1: напечатано +([\d.]+) мм²"), 14.4, 0.5, "площадь основания без башни")
