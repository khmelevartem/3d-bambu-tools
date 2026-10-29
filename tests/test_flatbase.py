"""flatbase: is what the part stands on a plane, and pressing it flat.

Checked on substance: a box with a 0.3 mm sine wave on its bottom is called
not a plane with the deviation measured back, a flat bottom is called a plane,
flatten brings the wave under 0.02 mm without changing the face count, a rim
vertex moves by the cosine falloff, and in a painted 3MF in a rotated pose the
paint and the faces come out unchanged while the wavy foot is flattened and the
flat one is left where it was.
"""
import re, zipfile
from harness import case, run, num, close, contains, work, TESTS, TOOLS

DEPS = ("numpy", "scipy", "trimesh", "shapely", "networkx", "rtree")
TOOL = TOOLS / "flatbase.py"

_FIX = {}


def fix(name):
    if not _FIX:
        d = work("flatbase_фикстуры")
        run([TESTS / "flatbase_fixtures.py", d], deps=("numpy", "trimesh"))
        _FIX["dir"] = d
    return _FIX["dir"] / name


def after(out):
    """The part of a flatten report that describes the result."""
    assert "после правки:" in out, out
    return out.split("после правки:", 1)[1]


@case("flatbase:волна 0.3 мм на дне — не плоскость")
def _():
    out = run([TOOL, "check", fix("wave.stl")], deps=DEPS)
    contains(out, "площадок опоры 1", "не плоскость: волна", "ИТОГ: не плоских площадок 1")
    close(num(out, r"площадка 1: ([\d.]+) мм²"), 600.0, 1.0, "площадь площадки")
    # an LS plane through three whole periods: p95 just under the amplitude
    close(num(out, r"p95 ([\d.]+) мм"), 0.33, 0.03, "p95 отклонения")
    # the first layer is cut at half a layer (0.10): only the troughs reach it
    assert num(out, r"проекции \((\d+)%\)") < 40, "след первого слоя у волны слишком велик"


@case("flatbase:плоское дно — плоскость")
def _():
    out = run([TOOL, "check", fix("flat.stl")], deps=DEPS)
    contains(out, "ВЕРДИКТ: плоскость", "ИТОГ: все площадки плоские")
    assert num(out, r"max ([\d.]+) мм") < 0.005, "плоское дно не плоское"
    close(num(out, r"проекции \((\d+)%\)"), 100, 1, "след первого слоя")


@case("flatbase:flatten прижимает волну, грани не меняются")
def _():
    d = work("flatbase_stl")
    out = run([TOOL, "flatten", fix("wave.stl"), "-o", d / "w.stl"], deps=DEPS)
    res = after(out)
    assert num(res, r"max ([\d.]+) мм") < 0.02, "после правки волна осталась"
    contains(res, "ВЕРДИКТ: плоскость")
    contains(out, "граней 11200 -> 11200", "вывернуто граней 0")
    # the bottom goes down to the bed: the troughs are filled, 0.3 mm over 600 mm²
    close(num(out, r"\(([+-][\d.]+)\), граней"), 180.0, 3.0, "прирост объёма")
    assert num(out, r"каёмка (\d+)") > 0, "каёмка не сдвинулась"
    # the second look comes from the file written, not from the report
    chk = run([TOOL, "check", d / "w.stl"], deps=DEPS)
    contains(chk, "граней 11200,", "ИТОГ: все площадки плоские")
    assert num(chk, r"max ([\d.]+) мм") < 0.02


@case("flatbase:3MF в повороте — покраска и грани целы, плоская нога не тронута")
def _():
    d = work("flatbase_3mf")
    src, dst = fix("feet.3mf"), d / "feet.3mf"
    chk = run([TOOL, "check", src], deps=DEPS)
    contains(chk, "площадок опоры 2", "ИТОГ: не плоских площадок 1")
    # the build item turns the object 90° about Z: the feet stand one behind the other
    contains(chk, "центр (118.0, 115.0)", "центр (118.0, 155.0)")
    out = run([TOOL, "flatten", src, "-o", dst], deps=DEPS)
    contains(out, "вывернуто граней 0", "переписано")
    chk = run([TOOL, "check", dst], deps=DEPS)
    contains(chk, "ИТОГ: все площадки плоские")
    for m in re.findall(r"max ([\d.]+) мм", chk):
        assert float(m) < 0.02, f"после правки отклонение {m}"

    def mesh(p):
        raw = zipfile.ZipFile(p).read("3D/Objects/object_1.model").decode()
        tri = re.findall(r"<triangle [^>]*/>", raw)
        V = re.findall(r'<vertex x="([^"]*)" y="([^"]*)" z="([^"]*)"', raw)
        return tri, V
    t0, v0 = mesh(src)
    t1, v1 = mesh(dst)
    assert t0 == t1, "грани или покраска изменились"
    assert len(v0) == len(v1)
    # the flat foot is the second shell: none of its vertices may move at all
    half = len(v0) // 2
    assert v0[half:] == v1[half:], "плоская нога сдвинулась"
    # x and y never change, only z, and only in the file's own coordinates
    assert all(a[:2] == b[:2] or (abs(float(a[0]) - float(b[0])) < 1e-6 and
               abs(float(a[1]) - float(b[1])) < 1e-6) for a, b in zip(v0, v1))
    moved = sum(a != b for a, b in zip(v0, v1))
    assert moved > 2000, f"сдвинуто всего {moved} вершин"
    # the rim: the wall row 0.5 mm above the bottom sits halfway into the 1 mm rim,
    # so the cosine falloff moves it by exactly half of the bottom vertex below it
    nx, ny = 61, 41
    ring = ([i * ny for i in range(nx)] + [(nx - 1) * ny + j for j in range(1, ny)]
            + [i * ny + ny - 1 for i in range(nx - 2, -1, -1)] + [j for j in range(ny - 2, 0, -1)])
    dz = lambda i: float(v1[i][2]) - float(v0[i][2])
    s = max(range(len(ring)), key=lambda k: abs(dz(ring[k])))
    close(dz(2 * nx * ny + s), 0.5 * dz(ring[s]), 0.002, "сдвиг каёмки на 0.5 мм над дном")
