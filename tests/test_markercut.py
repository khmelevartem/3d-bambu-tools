"""markercut: a seam drawn with a marker filament becomes a cut.

Checked on substance against the fixture's true seam, the circle n.x = D on a
sphere R 15: every point of the smoothed curve within half the strip width of
it, its length and the part's area those of the cap; an open strip is refused
with the gap located; `clean` leaves no marker in any face and no marker
filament in the project, and gives each face back the colour of its side.
"""
import json, math, re, sys, zipfile
from harness import case, run, num, close, contains, work, TESTS, TOOLS

sys.path.insert(0, str(TOOLS))
import patch3mf                                                    # noqa: E402

DEPS = ("numpy", "scipy")
TOOL = TOOLS / "markercut.py"
R, D, W, GAP_DEG = 15.0, 4.0, 2.0, 20.0
_n = (0.3, 0.2, 0.93)
N = tuple(x / math.sqrt(sum(y * y for y in _n)) for x in _n)
R0 = math.sqrt(R * R - D * D)                  # radius of the true seam circle
CAP = 2 * math.pi * R * (R - D)                # area of the cap n.x > D

_FIX = {}


def fix(name):
    if not _FIX:
        d = work("markercut_фикстуры")
        run([TESTS / "markercut_fixtures.py", d], deps=("numpy", "trimesh"))
        _FIX["dir"] = d
    return _FIX["dir"] / name


def dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def off_circle(p):
    """Distance from p to the circle n.x = D, radius R0."""
    h = dot(p, N) - D
    q = [x - dot(p, N) * n for x, n in zip(p, N)]
    return math.hypot(math.sqrt(dot(q, q)) - R0, h)


def seam_points(js):
    s = json.load(open(js))
    assert len(s["parts"]) == 1, f"деталей {len(s['parts'])}, ждали одну"
    loops = s["parts"][0]["loops"]
    assert len(loops) == 1 and loops[0]["closed"], "шов не один или не замкнут"
    return s, loops[0]


@case("markercut:полоса-кольцо — шов по середине полосы, в пределах W/2 от окружности")
def _():
    d = work("markercut_полоса")
    out = run([TOOL, "seam", fix("band.3mf"), "--marker", "3", "--out", d / "c"], deps=DEPS)
    contains(out, "замкнут да")
    s, lp = seam_points(d / "c_seam.json")
    err = [off_circle(p) for p in lp["points"]]
    assert max(err) <= W / 2, f"шов ушёл от окружности на {max(err):.3f} > {W / 2}"
    assert sum(err) / len(err) < 0.15, f"средний уход {sum(err) / len(err):.3f} мм"
    close(lp["length"], 2 * math.pi * R0, 1.5, "длина шва")
    close(s["parts"][0]["area"], CAP, 25.0, "площадь детали — шапка n·x > D")
    close(s["band_width"], W, 0.3, "ширина полосы")
    close(num(out, r"тело \(зона 1\): ([\d.]+)"), 4 * math.pi * R * R - CAP, 30.0,
          "площадь тела")


@case("markercut:--part выбирает сторону полосы по точке")
def _():
    d = work("markercut_part")
    # точка на большой стороне: деталью становится она
    p = ",".join(f"{-x * R:.3f}" for x in N)
    run([TOOL, "seam", fix("band.3mf"), "--marker", "3", f"--part={p}", "--out", d / "c"],
        deps=DEPS)
    s = json.load(open(d / "c_seam.json"))
    close(s["parts"][0]["area"], 4 * math.pi * R * R - CAP, 30.0, "деталь — большая сторона")


@case("markercut:незамкнутая полоса — отказ и координаты разрыва")
def _():
    d = work("markercut_разрыв")
    out = run([TOOL, "seam", fix("gap.3mf"), "--marker", "3", "--out", d / "c"],
              deps=DEPS, expect=1)
    contains(out, "полоса НЕ замкнута")
    width = num(out, r"РАЗРЫВ ([\d.]+) мм")
    close(width, 2 * R0 * math.sin(math.radians(GAP_DEG) / 2), 1.0, "ширина разрыва")
    m = re.search(r"середина \(([-\d.]+), ([-\d.]+), ([-\d.]+)\)", out)
    mid = [float(x) for x in m.groups()]
    # середина разрыва — на окружности шва, азимут GAP_DEG/2
    e1 = (-N[1], N[0], 0.0)              # z × n, as in the fixture
    l1 = math.sqrt(dot(e1, e1)); e1 = tuple(x / l1 for x in e1)
    e2 = (N[1] * e1[2] - N[2] * e1[1], N[2] * e1[0] - N[0] * e1[2], N[0] * e1[1] - N[1] * e1[0])
    a = math.radians(GAP_DEG / 2)
    want = [D * n + R0 * (math.cos(a) * u + math.sin(a) * v) for n, u, v in zip(N, e1, e2)]
    miss = math.dist(mid, want)
    assert miss < 1.5, f"середина разрыва в {miss:.2f} мм от истинной"
    assert not (d / "c_zones.npy").exists(), "при разрыве зоны записывать нельзя"


@case("markercut:заливка — шов по границе залитой детали")
def _():
    d = work("markercut_заливка")
    out = run([TOOL, "seam", fix("fill.3mf"), "--marker", "3", "--mode", "fill",
               "--out", d / "c"], deps=DEPS)
    s, lp = seam_points(d / "c_seam.json")
    err = [off_circle(p) for p in lp["points"]]
    assert max(err) <= 0.6, f"шов ушёл от границы заливки на {max(err):.3f}"
    close(s["parts"][0]["area"], CAP, 25.0, "площадь залитой детали")
    contains(out, "деталь 1 (зона 2)")


@case("markercut:clean — маркера не осталось, филамент выкинут, цвет по своей стороне")
def _():
    d = work("markercut_clean")
    out = run([TOOL, "clean", fix("band.3mf"), d / "c.3mf", "--marker", "3"], deps=DEPS)
    contains(out, "с маркером осталось 0", "филамент 3 выкинут: было 3, стало 2")
    z = zipfile.ZipFile(d / "c.3mf")
    cfg = json.loads(z.read("Metadata/project_settings.config"))
    assert len(cfg["filament_colour"]) == 2, cfg["filament_colour"]
    raw = z.read("3D/Objects/object_1.model").decode()
    V = [tuple(map(float, m)) for m in
         re.findall(r'<vertex x="([^"]*)" y="([^"]*)" z="([^"]*)"', raw)]
    bad = wrong = band = 0
    for m in re.finditer(r'<triangle v1="(\d+)" v2="(\d+)" v3="(\d+)"([^/]*)/>', raw):
        pc = re.search(r'paint_color="([^"]*)"', m.group(4))
        code = pc.group(1) if pc else ""
        st = set(patch3mf.leaves(patch3mf.paint_tree(code))) if code else {0}
        bad += any(s >= 3 for s in st)
        c = [sum(V[int(m.group(i))][k] for i in (1, 2, 3)) / 3 for k in range(3)]
        if abs(dot(c, N) - D) < W / 2 + 0.35:
            band += 1
            if abs(c[0]) > 1.5:          # у шва цветов x = 0 соседи спорят — не в счёт
                wrong += st != ({2} if c[0] > 0 else {0})
    assert band > 1500, f"граней полосы {band}"
    assert bad == 0, f"граней с филаментом 3 и выше: {bad}"
    assert wrong == 0, f"граней полосы с чужим цветом: {wrong}"


@case("markercut:без подкоманды печатает справку")
def _():
    out = run([TOOL], deps=DEPS, expect=1)
    contains(out, "Turn a seam drawn with a marker filament")
