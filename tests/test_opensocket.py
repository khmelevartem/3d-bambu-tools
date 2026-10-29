"""opensocket.py: a socket cut into an open body without booleans.

The fixture is a sphere torn the way a generator tears it - a hole at the
equator and a seam of unconnected twin vertices running through the top. A
socket cut there must have the size asked for, must not add a single open
edge, must leave the hole and the rest of the seam exactly as they were, and
must keep every face away from the cut at its own index with its own paint.
"""
import math, re, struct, zipfile

from harness import case, run, num, close, contains, work, TESTS, TOOLS

TOOL = TOOLS / "opensocket.py"
DEPS = ("numpy", "scipy", "shapely", "triangle")
_FIX = {}


def fix(name):
    if not _FIX:
        d = work("opensocket_фикстуры")
        run([TESTS / "opensocket_fixtures.py", d], deps=("numpy", "trimesh"))
        _FIX["dir"] = d
    return _FIX["dir"] / name


def stl(path):
    """Triangles of a binary STL as tuples of three vertex tuples."""
    b = path.read_bytes()
    n = struct.unpack("<I", b[80:84])[0]
    out = []
    for i in range(n):
        f = struct.unpack("<9f", b[84 + 50 * i + 12: 84 + 50 * i + 48])
        out.append((f[0:3], f[3:6], f[6:9]))
    return out


def open_edges(tris, where=lambda m: True):
    cnt = {}
    for t in tris:
        for a, b in ((t[0], t[1]), (t[1], t[2]), (t[2], t[0])):
            k = (a, b) if a < b else (b, a)
            cnt[k] = cnt.get(k, 0) + 1
    return sum(1 for (a, b), c in cnt.items()
               if c == 1 and where([(a[i] + b[i]) / 2 for i in range(3)]))


def cavity(tris, rmax):
    """Vertices below the sphere's surface near the top: the socket's wall and floor."""
    pts = {v for t in tris for v in t}
    return [v for v in pts if math.hypot(v[0], v[1]) < rmax and 20 < v[2] < 29.5]


def rim_z(r):
    return 15 + math.sqrt(225 - r * r)


def cap_volume(r, floor):
    """Volume between the sphere r 15 at (0,0,15) and a plane z = floor, over a disc r."""
    return math.pi * r * r * (15 - floor) + 2 * math.pi / 3 * (15 ** 3 - (225 - r * r) ** 1.5)


@case("opensocket:гнездо по окружности в рваной сфере: размер, открытые рёбра, старые грани")
def _():
    d = work("opensocket_круг")
    src, dst = fix("open_ball.stl"), d / "sock.stl"
    out = run([TOOL, "cut", src, "--circle", "0,0,30", "--radius", "3", "--axis", "0,0,1",
               "--depth", "2", "--gap", "0.05", "--gap-depth", "0.05", "--out", dst], deps=DEPS)
    close(num(out, r"глубина гнезда от нижней точки кромки до дна ([\d.]+)"), 2.05, 0.02, "глубина")
    contains(out, "на стенке 100%", "лишних петель 0", "несогласованной ориентацией до 0, после 0")
    a, b = stl(src), stl(dst)
    cav = cavity(b, 3.6)
    floor = min(v[2] for v in cav)
    close(floor, rim_z(3.05) - 2.05, 0.02, "высота дна")
    close(max(math.hypot(v[0], v[1]) for v in cav), 3.05, 0.02, "радиус гнезда")
    flat = [v[2] for v in cav if math.hypot(v[0], v[1]) < 3.0]
    assert max(flat) - min(flat) < 1e-4, f"дно не плоское: размах {max(flat) - min(flat)}"
    close(num(out, r"объём ниши ([\d.]+)"), cap_volume(3.05, floor), 0.3, "объём ниши")
    # open edges: none added; away from the socket not one is touched
    e0, e1 = open_edges(a), open_edges(b)
    assert e1 <= e0, f"открытых рёбер стало больше: {e0} -> {e1}"
    away = lambda m: math.hypot(m[0], m[1]) > 6 or m[2] < 26
    assert open_edges(a, away) == open_edges(b, away) > 0, "открытые рёбра вдали от гнезда изменились"
    close(num(out, r"открытых рёбер до (\d+)"), e0, 0, "открытых рёбер до (отчёт)")
    close(num(out, r"открытых рёбер до \d+, после (\d+)"), e1, 0, "открытых рёбер после (отчёт)")
    # faces away from the cut stay at their index, vertex for vertex
    moved = [i for i in range(len(a)) if b[i] != a[i]
             and math.hypot(*[sum(v[k] for v in a[i]) / 3 for k in (0, 1)]) > 5.5]
    assert not moved, f"грани вдали от гнезда сдвинулись: {moved[:5]}"
    same = sum(1 for i in range(len(a)) if b[i] == a[i])
    assert same > len(a) - 700, f"на своём месте {same} из {len(a)}"


@case("opensocket:наклонное дно держит полную глубину в самой мелкой точке")
def _():
    d = work("opensocket_наклон")
    dst = d / "sock.stl"
    out = run([TOOL, "cut", fix("open_ball.stl"), "--circle", "0,0,30", "--radius", "2",
               "--axis", "0,0,1", "--depth", "1.5", "--floor-normal", "0.2,0,1",
               "--gap", "0.1", "--gap-depth", "0", "--out", dst], deps=DEPS)
    contains(out, "наклон 11.3° к оси")
    close(num(out, r"глубина гнезда от нижней точки кромки до дна ([\d.]+)"), 1.5, 0.02, "глубина")
    fl = [v for v in cavity(stl(dst), 2.05) if math.hypot(v[0], v[1]) < 2.05]
    k = [v[2] + 0.2 * v[0] for v in fl if math.hypot(v[0], v[1]) < 2.0]
    assert max(k) - min(k) < 1e-4, f"дно не плоскость z = h0 - 0.2x: размах {max(k) - min(k)}"


@case("opensocket:гнездо по силуэту вставки, дно под её нижней точкой")
def _():
    d = work("opensocket_вставка")
    dst = d / "sock.stl"
    out = run([TOOL, "cut", fix("open_ball.stl"), "--insert", fix("plug.stl"), "--axis", "0,0,1",
               "--gap", "0.1", "--gap-depth", "0.05", "--out", dst], deps=DEPS)
    contains(out, "силуэт вставки plug.stl")
    cav = cavity(stl(dst), 2.6)
    close(min(v[2] for v in cav), 27 - 0.05, 0.005, "дно под вставкой")
    close(max(math.hypot(v[0], v[1]) for v in cav), 2.1, 0.02, "радиус гнезда")


TRI = re.compile(r'<triangle v1="(\d+)" v2="(\d+)" v3="(\d+)"([^/]*)/>')
VX = re.compile(r'<vertex x="([^"]*)" y="([^"]*)" z="([^"]*)"')


def faces_3mf(path):
    """[(triangle as vertex tuples, paint attribute string)] of the only object."""
    z = zipfile.ZipFile(path)
    raw = z.read("3D/Objects/object_1.model").decode()
    V = [tuple(round(float(x), 5) for x in m.groups()) for m in VX.finditer(raw)]
    return [((V[int(m.group(1))], V[int(m.group(2))], V[int(m.group(3))]),
             re.search(r'paint_color="([^"]*)"', m.group(4)).group(1)
             if 'paint_color' in m.group(4) else '') for m in TRI.finditer(raw)]


@case("opensocket:гнездо по пятну покраски в 3MF, покраска старых граней на месте",
      tools=("opensocket.py", "make_multicolor_3mf.py"))
def _():
    d = work("opensocket_покраска")
    src = d / "ball.3mf"
    run([TOOLS / "make_multicolor_3mf.py", fix("open_ball.stl"), fix("open_ball_zones.npy"),
         "-o", src, "--no-project", "--no-center", "--one-based"], deps=("trimesh", "numpy"))
    dst = d / "sock.3mf"
    out = run([TOOL, "cut", src, "--paint", "2", "--depth", "1.5", "--gap", "0.05",
               "--paint-new", "3", "--out", dst], deps=DEPS)
    contains(out, "пятен 1;", "на стенке 100%")      # the seam does not split the patch in two
    close(num(out, r"глубина гнезда от нижней точки кромки до дна ([\d.]+)"), 1.55, 0.02, "глубина")
    e0, e1 = num(out, r"открытых рёбер до (\d+)"), num(out, r"открытых рёбер до \d+, после (\d+)")
    assert e1 <= e0, f"открытых рёбер стало больше: {e0} -> {e1}"
    a, b = faces_3mf(src), faces_3mf(dst)
    code = lambda fs, c: sum(1 for _, p in fs if p == c)
    assert code(a, "8") > 50 and code(b, "8") == 0, "пятно филамента 2 не вынуто целиком"
    new = num(out, r"стенка (\d+)", int) + num(out, r"дно (\d+)", int)
    close(code(b, "0C"), code(a, "0C") + new, 0, "граней филамента 3: старое пятно + новые грани")
    diff = [i for i in range(len(a)) if a[i][0] == b[i][0] and a[i][1] != b[i][1]]
    assert not diff, f"у граней на своём месте сменилась покраска: {diff[:5]}"
    same = sum(1 for i in range(len(a)) if a[i][0] == b[i][0])
    assert same > len(a) - 500, f"на своём месте {same} из {len(a)}"
