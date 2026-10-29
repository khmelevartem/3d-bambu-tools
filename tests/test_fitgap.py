"""fitgap: clearance of a socket-and-insert pair by the print pose of its walls.

Checked on substance: a pin in a blind hole at 0.20 per side with vertical
walls is measured at 0.20 and asked for 0.05; apply brings it to 0.05 without
adding or renumbering a face, leaves the bottom where it was, and writes the
same shift back into a painted 3MF without touching a triangle; a dome printed
on its edge is asked for 0.15; a pin made too fat is reported as interference
with its depth.
"""
import re, struct, zipfile

from harness import case, run, num, close, contains, work, TESTS, TOOLS

DEPS = ("numpy", "scipy", "trimesh", "rtree")
TOOL = TOOLS / "fitgap.py"
_FIX = {}


def fix(name):
    """peg / peg_socket / dome / dome_socket from fitgap_fixtures.py, built once per run."""
    if not _FIX:
        d = work("fitgap_фикстуры")
        run([TESTS / "fitgap_fixtures.py", d], deps=("numpy", "trimesh", "manifold3d"))
        _FIX["dir"] = d
    return _FIX["dir"] / name


def zone(out, name):
    """(min, p50, max) of the gap in a zone row of the report."""
    m = re.search(name + r"\s+[\d.]+\s+(?:\d+°/\s*\d+°)?\s+(-?[\d.]+)\s+-?[\d.]+\s+(-?[\d.]+)"
                  r"\s+-?[\d.]+\s+(-?[\d.]+)", out)
    assert m, f"в отчёте нет строки «{name}»\n{out[-1500:]}"
    return tuple(float(x) for x in m.groups())


def stl_faces(path):
    return struct.unpack("<I", path.read_bytes()[80:84])[0]


@case("fitgap:штифт с вертикальными стенками — факт 0.20, просит 0.05")
def _():
    out = run([TOOL, "measure", fix("peg.stl"), fix("peg_socket.stl")], deps=DEPS)
    lo, p50, hi = zone(out, "стенки вертикальные")
    close(p50, 0.20, 0.005, "зазор стенки p50")
    close(lo, 0.20, 0.01, "зазор стенки min")
    close(hi, 0.20, 0.01, "зазор стенки max")
    close(zone(out, r"дно \(вдоль оси\)")[1], 0.20, 0.005, "зазор дна")
    contains(out, "ось вставки [ 0.  0. -1.]", "натяг: нет", "зазор пары 0.05 на сторону")
    assert "наклонные" not in out.split("ИТОГ")[1], "у прямого штифта нашлись наклонные стенки"


@case("fitgap:apply доводит стенку до 0.05, грани и дно не трогает")
def _():
    d = work("fitgap_apply")
    out = run([TOOL, "apply", fix("peg.stl"), fix("peg_socket.stl"), "--out", d / "fit.stl"],
              deps=DEPS)
    contains(out, "граней 5952 -> 5952, нумерация та же")
    assert stl_faces(d / "fit.stl") == stl_faces(fix("peg.stl")), "число граней поменялось"
    chk = run([TOOL, "measure", d / "fit.stl", fix("peg_socket.stl")], deps=DEPS)
    lo, p50, hi = zone(chk, "стенки вертикальные")
    close(p50, 0.05, 0.01, "зазор стенки после apply")
    assert 0.04 <= lo and hi <= 0.06, f"стенка после apply {lo}..{hi}"
    close(zone(chk, r"дно \(вдоль оси\)")[1], 0.20, 0.01, "дно после apply")
    contains(chk, "натяг: нет")
    # радиус 3.0 + 0.15: объём штифта вырос на кольцо по глубине ниши
    vol = re.search(r"объём ([\d.]+) -> ([\d.]+) мм³", out)
    assert float(vol.group(2)) > float(vol.group(1)) + 15, "штифт не стал толще"


@case("fitgap:дно ведётся вдоль оси до своей цели")
def _():
    d = work("fitgap_дно")
    run([TOOL, "apply", fix("peg.stl"), fix("peg_socket.stl"), "--bottom", "0.1",
         "--out", d / "fit.stl"], deps=DEPS)
    chk = run([TOOL, "measure", d / "fit.stl", fix("peg_socket.stl")], deps=DEPS)
    close(zone(chk, r"дно \(вдоль оси\)")[1], 0.10, 0.01, "дно после apply --bottom 0.1")
    close(zone(chk, "стенки вертикальные")[1], 0.05, 0.01, "стенка при правке дна")


@case("fitgap:купол на ребре просит 0.15")
def _():
    out = run([TOOL, "measure", fix("dome.stl"), fix("dome_socket.stl"), "--up-insert", "1,0,0"],
              deps=DEPS)
    contains(out, "печать вверх [1. 0. 0.] (задано)", "зазор пары 0.15 на сторону")
    close(zone(out, "стенки наклонные")[1], 0.20, 0.005, "зазор купола")
    share = num(out, r"наклонные (\d+) %")
    assert share > 40, f"наклонных стенок у купола на ребре только {share} %"


@case("fitgap:толстый штифт — натяг с глубиной")
def _():
    d = work("fitgap_натяг")
    run([TOOL, "apply", fix("peg.stl"), fix("peg_socket.stl"), "--vertical=-0.05",
         "--out", d / "fat.stl"], deps=DEPS)
    out = run([TOOL, "measure", d / "fat.stl", fix("peg_socket.stl")], deps=DEPS)
    close(num(out, r"НАТЯГ: \d+ граней, площадь [\d.]+ мм², глубина до ([\d.]+)"), 0.05, 0.01,
          "глубина натяга")


@case("fitgap:--put возвращает сдвиг в 3MF, треугольники и покраска дословно")
def _():
    d = work("fitgap_3mf")
    src = d / "peg.3mf"
    run([TOOLS / "make_multicolor_3mf.py", fix("peg.stl"), fix("peg_zones.npy"), "-o", src,
         "--no-project"], deps=("trimesh", "numpy"))
    ref = f"{src}:peg.stl"
    out = run([TOOL, "apply", fix("peg.stl"), fix("peg_socket.stl"), "--print-insert", ref,
               "--put", ref, "--out", d / "fit.3mf"], deps=DEPS)
    contains(out, f"печать вверх [0. 0. 1.] (из {ref})", "переписано")
    ent = "3D/Objects/object_1.model"
    a = zipfile.ZipFile(src).read(ent).decode()
    b = zipfile.ZipFile(d / "fit.3mf").read(ent).decode()
    assert a[a.index("<triangles>"):] == b[b.index("<triangles>"):], "треугольники изменились"
    V = [tuple(map(float, m)) for m in
         re.findall(r'<vertex x="([^"]*)" y="([^"]*)" z="([^"]*)"', b)]
    cx = sum(v[0] for v in V) / len(V)
    cy = sum(v[1] for v in V) / len(V)
    z0 = min(v[2] for v in V)
    r = sorted(((v[0] - cx) ** 2 + (v[1] - cy) ** 2) ** 0.5 for v in V if 0.1 < v[2] - z0 < 6)
    close(r[len(r) // 2], 3.15, 0.002, "радиус стенки в 3MF")
