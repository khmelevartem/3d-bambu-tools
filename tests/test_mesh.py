"""Инструменты работы с сеткой: диагноз, ремонт, пересборка, правка 3MF.

Опорные числа берутся из фикстур tests/fixtures.py: чистая сфера 14015.5 мм³,
битая сфера 13945.7 мм³ с 2 дырками и мусорным островком, тор рода 1.

Рваный 3MF и 3MF с T-стыком собираются здесь же из покрашенного проекта:
готовых таких фикстур нет, а без них weldmesh и fixtjoints нечего чинить.
"""
import re, zipfile

from harness import case, run, num, close, contains, work, FIX, TOOLS

NP = ("numpy",)
NPSP = ("numpy", "scipy")
SOLID = ("numpy", "scipy", "scikit-image")
PAINT = ("numpy", "scipy", "PyMaxflow")
TRI = ("trimesh", "numpy")


# ------------------------------------------------------------------ вспомогательное

# Зоны покраски: цвет через грань — так любой перекос покраски виден по счёту.
ZONES_PY = '''
import sys
import numpy as np, trimesh
m = trimesh.load(sys.argv[1], process=False)
n = len(m.faces)
np.save(sys.argv[2], np.where(np.arange(n) % 2 == 0, 1, 2).astype(np.int32))
print(f"граней {n}")
'''

# Сфера той же формы, но втрое плотнее: приёмник для переноса покраски.
DENSE_PY = '''
import sys
import numpy as np, trimesh
m = trimesh.creation.icosphere(subdivisions=4, radius=15.0)
m.apply_translation([0, 0, 15.0])
m.export(sys.argv[1])
np.save(sys.argv[2], np.ones(len(m.faces), np.int32))
print(f"граней {len(m.faces)}, объём {m.volume:.1f}")
'''

# npz для writeverts: V со сдвигом по Z и флаг moved целиком False или True.
EDIT_PY = '''
import sys
import numpy as np
d = np.load(sys.argv[1], allow_pickle=True)
V = d["V"].astype(np.float64).copy()
moved = np.zeros(len(V), bool) if sys.argv[3] == "none" else np.ones(len(V), bool)
V[:, 2] += float(sys.argv[4])
np.savez(sys.argv[2], V=V, moved=moved, entry=str(d["entry"]))
print(f"вершин {len(V)}, moved {int(moved.sum())}")
'''


def script(d, name, code):
    p = d / name
    p.write_text(code)
    return p


def entry(path):
    """Имя элемента архива, в котором лежит сетка."""
    z = zipfile.ZipFile(path)
    names = [n for n in z.namelist() if n.endswith(".model") and "Objects/" in n]
    return (names or [n for n in z.namelist() if n.endswith(".model")])[0]


def model_xml(path):
    return zipfile.ZipFile(path).read(entry(path)).decode("utf-8")


def paint_counts(path):
    """Сколько граней каждого кода покраски — чем проверяется, что цвет цел."""
    xml = model_xml(path)
    out = {}
    for code in re.findall(r'paint_color="([^"]*)"', xml):
        out[code] = out.get(code, 0) + 1
    out["всего"] = len(re.findall(r"<triangle ", xml))
    return out


def painted(d, stl, name="c.3mf"):
    """Покрашенный 3MF из STL: то, с чем работают weldmesh, fixtjoints, paint.py."""
    zn = d / "zones.npy"
    run([script(d, "zones.py", ZONES_PY), stl, zn], deps=TRI)
    out = d / name
    run([TOOLS / "make_multicolor_3mf.py", stl, zn, "-o", out, "--no-project"], deps=TRI)
    return out


# Пересборка 3MF стандартной библиотекой: рвём швы или ставим лишнюю вершину
# на ребро. Покраска и порядок граней сохраняются.
def rebuild(src, dst, mode):
    VERT = re.compile(r'<vertex x="([^"]*)" y="([^"]*)" z="([^"]*)"\s*/>')
    TRIRE = re.compile(r'<triangle v1="(\d+)" v2="(\d+)" v3="(\d+)"'
                       r'((?:\s+[\w:]+="[^"]*")*)\s*/>')
    zi = zipfile.ZipFile(src)
    ent = entry(src)
    xml = zi.read(ent).decode("utf-8")
    V = [tuple(map(float, m)) for m in VERT.findall(xml)]
    T = [(int(a), int(b), int(c), att) for a, b, c, att in TRIRE.findall(xml)]
    if mode == "tear":                      # каждой грани свои вершины — шов GLB
        nv, nt = [], []
        for a, b, c, att in T:
            i = len(nv)
            nv += [V[a], V[b], V[c]]
            nt.append((i, i + 1, i + 2, att))
        V, T = nv, nt
    elif mode == "tjoint":                  # середина ребра в одной грани из двух
        a, b, c, att = T[0]
        V = V + [tuple((V[b][k] + V[c][k]) / 2 for k in range(3))]
        mid = len(V) - 1
        T = [(a, b, mid, att), (a, mid, c, att)] + T[1:]
    vs = "".join(f'<vertex x="{x:.6f}" y="{y:.6f}" z="{z:.6f}"/>' for x, y, z in V)
    ts = "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"{att}/>' for a, b, c, att in T)
    xml = re.sub(r"<vertices>.*</vertices>", "<vertices>" + vs + "</vertices>", xml, flags=re.S)
    xml = re.sub(r"<triangles>.*</triangles>", "<triangles>" + ts + "</triangles>", xml, flags=re.S)
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zo:
        for it in zi.infolist():
            zo.writestr(it, xml.encode("utf-8") if it.filename == ent else zi.read(it.filename))
    return dst


def parse_paint(d, threemf, name):
    out = d / name
    run([TOOLS / "paint.py", "parse", threemf, out], deps=PAINT)
    return out


# ------------------------------------------------------------------ meshdoctor

@case("meshdoctor:чистая сфера — вердикт ЧИСТО")
def _():
    out = run([TOOLS / "meshdoctor.py", FIX / "ball.stl"], deps=NPSP)
    contains(out, "ВЕРДИКТ: ЧИСТО",
             "OK открытых рёбер (дырки): 0",
             "OK non-manifold рёбер (>=3 граней): 0",
             "род (сквозных отверстий) 0")
    close(num(out, r"объём ([\d.]+) мм³"), 14015.5, 0.1, "объём сферы")
    close(num(out, r"(\d+) треугольников", int), 1280, 0, "граней")


@case("meshdoctor:на битой сетке находит дырки, вывернутую грань и островок")
def _():
    out = run([TOOLS / "meshdoctor.py", FIX / "broken.stl"], deps=NPSP, expect=1)
    contains(out, "ВЕРДИКТ: АВТОМАТ", "мусорных островков",
             "выбросить перед печатью", "сетка не замкнута")
    close(num(out, r"открытых рёбер \(дырки\): (\d+)", int), 7, 0, "открытых рёбер")
    close(num(out, r"-> (\d+) петель", int), 2, 0, "петель (дырок)")
    close(num(out, r"рассогласованным обходом: (\d+)", int), 3, 0, "вывернутых рёбер")
    close(num(out, r"тел (\d+)\n", int), 2, 0, "тел")
    close(num(out, r"объём ([\d.]+) мм³"), 13945.7, 0.2, "объём битой сферы")


@case("meshdoctor:тор — род 1, но сетка чистая")
def _():
    out = run([TOOLS / "meshdoctor.py", FIX / "torus.stl"], deps=NPSP)
    contains(out, "ВЕРДИКТ: ЧИСТО", "эйлерова хар-ка 0")
    close(num(out, r"род \(сквозных отверстий\) (\d+)", int), 1, 0, "род тора")
    close(num(out, r"объём ([\d.]+) мм³"), 3736.1, 0.2, "объём тора")


@case("meshdoctor:рваные швы 3MF — дубли вершин не считает дефектом")
def _():
    d = work("meshdoctor-torn")
    torn = rebuild(painted(d, FIX / "ball.stl"), d / "torn.3mf", "tear")
    out = run([TOOLS / "meshdoctor.py", torn], deps=NPSP, expect=1)
    contains(out, "это швы автора, не дефект", "ВЕРДИКТ: АВТОМАТ")
    close(num(out, r"вершин-дублей по координатам: (\d+)", int), 3198, 0, "дублей вершин")
    close(num(out, r"тел (\d+)\n", int), 1280, 0, "тел у рваной сетки")
    close(num(out, r"объём ([\d.]+) мм³"), 14015.5, 0.1, "объём не меняется от разрыва")


# ------------------------------------------------------------------ printcheck

@case("printcheck:тор — род 1, сквозное отверстие")
def _():
    out = run([TOOLS / "printcheck.py", FIX / "torus.stl"])
    contains(out, "OK герметичность", "OK согласованность обхода",
             "эйлерова хар-ка V-E+F = 0")
    close(num(out, r"род \(сквозных отверстий\) = (\d+)", int), 1, 0, "род тора")
    close(num(out, r"объём: ([\d.]+) мм³"), 3736.09, 0.05, "объём тора")


@case("printcheck:две сферы — два отдельных тела")
def _():
    out = run([TOOLS / "printcheck.py", FIX / "two_bodies.stl"])
    contains(out, "OK герметичность", "род (сквозных отверстий) = 0")
    close(num(out, r"отдельных тел: (\d+)", int), 2, 0, "тел")
    close(num(out, r"эйлерова хар-ка V-E\+F = (\d+)", int), 4, 0, "эйлерова хар-ка двух сфер")
    close(num(out, r"объём: ([\d.]+) мм³"), 4144.17, 0.05, "объём двух сфер")


@case("printcheck:битая сетка негерметична, род не определён")
def _():
    out = run([TOOLS / "printcheck.py", FIX / "broken.stl"])
    contains(out, "!! герметичность", "!! согласованность обхода",
             "род (сквозных отверстий) = не определён")
    close(num(out, r"рёбер с count!=2: (\d+)", int), 7, 0, "негерметичных рёбер")
    close(num(out, r"плохих направленных рёбер: (\d+)", int), 3, 0, "вывернутых рёбер")


@case("printcheck:фигурка — свесы, габарит и число слоёв")
def _():
    out = run([TOOLS / "printcheck.py", FIX / "figurine.stl"])
    contains(out, "OK герметичность", "!! свесы круче 45.0°", "(стол 256x256x256)")
    close(num(out, r"свесы круче 45.0°: ([\d.]+)% площади"), 8.4, 0.3, "доля свесов")
    close(num(out, r"максимум ([\d.]+)° от горизонтали"), 84.3, 0.5, "самый крутой свес")
    close(num(out, r"габарит: [\d.]+ x [\d.]+ x ([\d.]+) мм"), 69.0, 0.01, "высота фигурки")
    close(num(out, r"слоёв по 0.2 мм: (\d+)", int), 345, 0, "слоёв по 0.2 мм")


# ------------------------------------------------------------------ meshfix (Blender)

@case("meshfix:--dry только считает и файла не пишет", needs=("blender",))
def _():
    d = work("meshfix-dry")
    dst = d / "nope.stl"
    out = run([TOOLS / "meshfix.py", FIX / "broken.stl", "-o", dst, "--dry"])
    contains(out, "(--dry: файл не записан)", "нормали пересчитаны наружу")
    close(num(out, r"было : (\d+) граней", int), 1297, 0, "граней на входе")
    close(num(out, r"открытых рёбер (\d+)", int), 7, 0, "открытых рёбер на входе")
    assert not dst.exists(), "--dry всё-таки записал файл"


@case("meshfix:закрывает дырки и выбрасывает мусорный островок", needs=("blender",))
def _():
    d = work("meshfix-holes")
    dst = d / "fixed.stl"
    out = run([TOOLS / "meshfix.py", FIX / "broken.stl", "-o", dst,
               "--holes", "--normals", "--dropjunk"])
    contains(out, "дырки: открытых рёбер 7 -> 0", "тел было 2, выброшено мусорных 1",
             "mesh.validate(): претензий нет")
    assert dst.exists(), "файл не записан"
    chk = run([TOOLS / "meshdoctor.py", dst], deps=NPSP)
    contains(chk, "ВЕРДИКТ: ЧИСТО", "OK открытых рёбер (дырки): 0")
    close(num(chk, r"объём ([\d.]+) мм³"), 14015.5, 1.0, "объём после ремонта")
    close(num(chk, r"тел (\d+)\n", int), 1, 0, "тел после выброса мусора")


@case("meshfix:--extract предупреждает, что покраска пропадёт", needs=("blender",))
def _():
    d = work("meshfix-extract")
    out = run([TOOLS / "meshfix.py", painted(d, FIX / "ball.stl"), "--extract"])
    contains(out, "покрашенных треугольников", "покраска к ним привязана и пропадёт",
             "1280 треугольников")
    assert list(d.glob("*.stl")), "извлечённый STL не появился"


# ------------------------------------------------------------------ meshsolid

@case("meshsolid:пересобирает рваную сферу в замкнутое тело")
def _():
    d = work("meshsolid-rebuild")
    dst = d / "solid.stl"
    out = run([TOOLS / "meshsolid.py", FIX / "broken.stl", dst, "--voxel", "0.6"],
              deps=SOLID)
    contains(out, "лучи вдоль X", "лучи вдоль Y", "лучи вдоль Z", "записано")
    close(num(out, r"объём до ([\d.]+)"), 13945.7, 0.2, "объём на входе")
    close(num(out, r"объём (\d+\.\d+) \([-+][\d.]+ %\)"), 14000.0, 150.0, "объём тела")
    chk = run([TOOLS / "meshdoctor.py", dst], deps=NPSP)
    contains(chk, "ВЕРДИКТ: ЧИСТО")
    close(num(chk, r"тел (\d+)\n", int), 1, 0, "тел после пересборки")


@case("meshsolid:объём почти не зависит от вокселя — это тело, а не корка")
def _():
    d = work("meshsolid-voxel")
    vols = []
    for vx in ("0.6", "1.2"):
        out = run([TOOLS / "meshsolid.py", FIX / "broken.stl", d / f"s{vx}.stl",
                   "--voxel", vx], deps=SOLID)
        vols.append(num(out, r"объём (\d+\.\d+) \([-+][\d.]+ %\)"))
    close(vols[0], vols[1], 0.03 * vols[0], "объём при вдвое крупном вокселе")
    for v in vols:
        close(v, 14000.0, 300.0, "объём далёк от объёма корки")


# ------------------------------------------------------------------ weldmesh

@case("weldmesh:сваривает рваные швы, покраску не теряет")
def _():
    d = work("weldmesh-tear")
    src = painted(d, FIX / "ball.stl")
    torn = rebuild(src, d / "torn.3mf", "tear")
    dst = d / "welded.3mf"
    out = run([TOOLS / "weldmesh.py", torn, dst], deps=NPSP)
    close(num(out, r"вход: вершин (\d+)", int), 3840, 0, "вершин у рваной сетки")
    close(num(out, r"схлопнулось (\d+)", int), 3198, 0, "схлопнутых вершин")
    close(num(out, r"открытых рёбер после сварки: (\d+)", int), 0, 0, "открытых рёбер")
    assert paint_counts(dst) == paint_counts(src), "сварка сдвинула покраску"
    chk = run([TOOLS / "meshdoctor.py", dst], deps=NPSP)
    contains(chk, "ВЕРДИКТ: ЧИСТО")
    close(num(chk, r"тел (\d+)\n", int), 1, 0, "тел после сварки")


@case("weldmesh:затыкает оставшиеся дырки заплатками")
def _():
    d = work("weldmesh-fill")
    src = painted(d, FIX / "broken.stl")
    dst = d / "welded.3mf"
    out = run([TOOLS / "weldmesh.py", src, dst], deps=NPSP)
    close(num(out, r"открытых рёбер после сварки: (\d+)", int), 7, 0, "открытых рёбер")
    close(num(out, r"заплаток: (\d+) граней", int), 7, 0, "граней заплаток")
    close(num(out, r"заплаток: \d+ граней на (\d+) дырках", int), 2, 0, "залатанных дырок")
    close(num(out, r"граней (\d+)\s*$", int), 1304, 0, "граней на выходе")
    # островок мусора из broken.stl на месте, поэтому вердикт МЕЛОЧЬ и код 1
    chk = run([TOOLS / "meshdoctor.py", dst], deps=NPSP, expect=1)
    contains(chk, "OK открытых рёбер (дырки): 0", "ВЕРДИКТ: МЕЛОЧЬ")


@case("weldmesh:--no-fill только сваривает, граней не добавляет")
def _():
    d = work("weldmesh-nofill")
    src = painted(d, FIX / "broken.stl")
    dst = d / "welded.3mf"
    out = run([TOOLS / "weldmesh.py", src, dst, "--no-fill"], deps=NPSP)
    assert "заплаток" not in out, "с --no-fill появились заплатки"
    close(num(out, r"открытых рёбер после сварки: (\d+)", int), 7, 0, "дырки остались")
    close(num(out, r"граней (\d+)\s*$", int), 1297, 0, "граней не прибавилось")


# ------------------------------------------------------------------ fixtjoints

@case("fixtjoints:--dry находит T-стык и файла не пишет")
def _():
    d = work("fixtjoints-dry")
    tj = rebuild(painted(d, FIX / "ball.stl"), d / "tj.3mf", "tjoint")
    was = sorted(p.name for p in d.iterdir())
    out = run([TOOLS / "fixtjoints.py", tj, "--dry"], deps=NPSP)
    close(num(out, r"открытых рёбер: (\d+)", int), 3, 0, "открытых рёбер от T-стыка")
    close(num(out, r"граней с T-стыком: (\d+)", int), 1, 0, "граней с T-стыком")
    close(num(out, r"вставленных вершин: (\d+)", int), 1, 0, "чужих вершин на рёбрах")
    close(num(out, r"открытых рёбер осталось: (\d+)", int), 0, 0, "рёбер после веера")
    assert sorted(p.name for p in d.iterdir()) == was, "--dry записал файл"


@case("fixtjoints:расшивает T-стык веером, покраска цела")
def _():
    d = work("fixtjoints-fan")
    src = painted(d, FIX / "ball.stl")
    tj = rebuild(src, d / "tj.3mf", "tjoint")
    dst = d / "fixed.3mf"
    out = run([TOOLS / "fixtjoints.py", tj, dst], deps=NPSP)
    close(num(out, r"выход: вершин (\d+)", int), 644, 0, "вершин на выходе")
    close(num(out, r"граней (\d+)\s*$", int), 1284, 0, "граней на выходе")
    before, after = paint_counts(tj), paint_counts(dst)
    assert after["всего"] == 1284, f"граней в файле {after['всего']}"
    assert sum(v for k, v in after.items() if k != "всего") == after["всего"], \
        "часть граней осталась без кода покраски"
    # грань с T-стыком уходит веером в четыре, и все четыре берут цвет родителя:
    # ровно один код прибавляет 3 грани, остальные не меняются
    diff = {k: after.get(k, 0) - before.get(k, 0)
            for k in set(before) | set(after) if k != "всего"}
    grown = {k: v for k, v in diff.items() if v}
    assert list(grown.values()) == [3], f"цвет разъехался: {diff}"
    chk = run([TOOLS / "meshdoctor.py", dst], deps=NPSP)
    contains(chk, "ВЕРДИКТ: ЧИСТО", "OK открытых рёбер (дырки): 0")
    close(num(chk, r"объём ([\d.]+) мм³"), 14015.5, 0.1, "форма от веера не изменилась")


# ------------------------------------------------------------------ writeverts

@case("writeverts:с moved всё False XML выходит побайтно тем же")
def _():
    d = work("writeverts-none")
    src = painted(d, FIX / "ball.stl")
    npz = parse_paint(d, src, "p.npz")
    edit = d / "edit.npz"
    run([script(d, "edit.py", EDIT_PY), npz, edit, "none", "5"], deps=NP)
    dst = d / "out.3mf"
    out = run([TOOLS / "writeverts.py", src, edit, dst], deps=NP)
    contains(out, "вершин всего 642, переписано 0, дословно 642")
    a = zipfile.ZipFile(src).read(entry(src))
    b = zipfile.ZipFile(dst).read(entry(dst))
    assert a == b, f"XML разошёлся: было {len(a)} байт, стало {len(b)}"


@case("writeverts:с moved всё True координаты сдвинуты, покраска цела")
def _():
    d = work("writeverts-all")
    src = painted(d, FIX / "ball.stl")
    npz = parse_paint(d, src, "p.npz")
    edit = d / "edit.npz"
    run([script(d, "edit.py", EDIT_PY), npz, edit, "all", "5"], deps=NP)
    dst = d / "out.3mf"
    out = run([TOOLS / "writeverts.py", src, edit, dst], deps=NP)
    contains(out, "вершин всего 642, переписано 642, дословно 0")
    zs = [float(z) for z in re.findall(r'<vertex x="[^"]*" y="[^"]*" z="([^"]*)"',
                                       model_xml(dst))]
    close(min(zs), 5.0, 1e-4, "низ после сдвига")
    close(max(zs), 35.0, 1e-4, "верх после сдвига")
    assert paint_counts(dst) == paint_counts(src), "сдвиг вершин сбил покраску"


# ------------------------------------------------------------------ paint_transfer

@case("paint_transfer:на ту же сетку цвет ложится один в один")
def _():
    d = work("paint_transfer-id")
    npz = parse_paint(d, painted(d, FIX / "ball.stl"), "p.npz")
    out = run([TOOLS / "paint_transfer.py", npz, npz, d / "t.npz"], deps=NPSP)
    contains(out, "источник: 1280 одноцветных граней", "приёмник: 1280 граней")
    close(num(out, r"максимум ([\d.]+) мм"), 0.0, 1e-4, "максимум расстояния")
    close(num(out, r"\n  2\s+[\d.]+\s+[\d.]+\s+(-?[\d.]+)%"), 0.0, 0.05, "разница по филаменту 2")
    close(num(out, r"\n  3\s+[\d.]+\s+[\d.]+\s+(-?[\d.]+)%"), 0.0, 0.05, "разница по филаменту 3")
    assert (d / "t.npz").exists(), "npz не записан"


@case("paint_transfer:на плотную сетку той же формы площади сходятся")
def _():
    d = work("paint_transfer-dense")
    old = parse_paint(d, painted(d, FIX / "ball.stl"), "old.npz")
    dense, zn = d / "dense.stl", d / "dense_zones.npy"
    run([script(d, "dense.py", DENSE_PY), dense, zn], deps=TRI)
    run([TOOLS / "make_multicolor_3mf.py", dense, zn, "-o", d / "n.3mf", "--no-project"],
        deps=TRI)
    new = parse_paint(d, d / "n.3mf", "new.npz")
    out = run([TOOLS / "paint_transfer.py", old, new, d / "t.npz"], deps=NPSP)
    contains(out, "источник: 1280 одноцветных граней", "приёмник: 5120 граней")
    assert "цвет угадан, а не перенесён" not in out, "приёмник признан далёким от источника"
    close(num(out, r"максимум ([\d.]+) мм"), 0.05, 0.05, "максимум расстояния до старой сетки")
    close(num(out, r"\n  2\s+[\d.]+\s+[\d.]+\s+(-?[\d.]+)%"), 0.0, 1.5, "разница по филаменту 2")
    close(num(out, r"\n  3\s+[\d.]+\s+[\d.]+\s+(-?[\d.]+)%"), 0.0, 1.5, "разница по филаменту 3")


@case("meshfix:--extract кладёт STL туда, куда указано -o", needs=("blender",),
      tools=("meshfix.py",))
def _():
    d = work("meshfix_extract_out")
    src = painted(d, FIX / "ball.stl")      # покрашенный 3MF рядом с ним же
    out = d / "куда"
    out.mkdir()
    run([TOOLS / "meshfix.py", src, "--extract", "-o", out])
    made = sorted(out.glob("*.stl"))
    assert made, f"в {out} ничего не появилось: {sorted(out.iterdir())}"
    # и без -o по-прежнему рядом с исходником
    run([TOOLS / "meshfix.py", src, "--extract"])
    assert sorted(src.parent.glob("*__obj*.stl")), "без -o STL рядом с исходником не появился"
