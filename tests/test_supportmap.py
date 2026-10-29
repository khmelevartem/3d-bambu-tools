"""supportmap: касание поддержек по факту G-кода.

Фикстура собирается здесь же, без слайсера: проект из четырёх плит и
синтетический G-код, в котором поддержка упирается в известные площадки.
Площадь каждой известна заранее, и кейс сверяет её числом, вместе с тем,
куда она попала — в видимое или в скрытое и по какой причине:

* «Slab»  — площадка 10×10 под открытым дном: видимо 100 мм²; деталь собрана
  через component с поворотом в item, так что проверяется и поза на столе;
* «Core»  — площадка 6×6 под объектом, скрытым целиком: 36 мм²;
* «Disk»  — круг R 3, выложенный дугами G2, под гранями филамента 2: 28.3 мм²;
* «Cup»   — площадка 2×2 под потолком глухой ниши 2×2×4: 4 мм², «ниша».

Пятый объект выключен (printable="0"): его нет в G-коде, и совмещение
по габаритам не должно его учитывать.

Рядом стоит ствол, сужающийся до радиуса 0.6 на высоте 2.2–3.0 мм: шейка
Ø ≈ 1.6 мм (с шириной линии).
"""
import math
import zipfile

from harness import case, run, num, close, contains, work, FIX, TOOLS

DEPS = ("numpy", "scipy", "trimesh", "embreex")
W = 0.42                       # ширина линии поддержки
LH = 0.2


# ------------------------------------------------------------ сетки

def box(lo, hi):
    V = [(lo[0], lo[1], lo[2]), (hi[0], lo[1], lo[2]), (hi[0], hi[1], lo[2]), (lo[0], hi[1], lo[2]),
         (lo[0], lo[1], hi[2]), (hi[0], lo[1], hi[2]), (hi[0], hi[1], hi[2]), (lo[0], hi[1], hi[2])]
    F = [(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4),
         (1, 2, 6), (1, 6, 5), (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7)]
    return V, F


def cup():
    """Плита 20×20×5 с глухой нишей 2×2 глубиной 4 снизу. Замкнутость не нужна."""
    V, F = [], []

    def quad(a, b, c, d):
        n = len(V)
        V.extend([a, b, c, d])
        F.extend([(n, n + 1, n + 2), (n, n + 2, n + 3)])

    def down(x0, x1, y0, y1, z):              # смотрит вниз
        quad((x0, y0, z), (x0, y1, z), (x1, y1, z), (x1, y0, z))

    X0, X1, h0, h1 = 130, 150, 139, 141
    down(X0, X1, X0, h0, 5); down(X0, X1, h1, X1, 5)
    down(X0, h0, h0, h1, 5); down(h1, X1, h0, h1, 5)
    down(h0, h1, h0, h1, 9)                   # потолок ниши
    quad((X0, X0, 10), (X1, X0, 10), (X1, X1, 10), (X0, X1, 10))
    for (xa, ya), (xb, yb) in (((h0, h0), (h1, h0)), ((h1, h0), (h1, h1)),
                               ((h1, h1), (h0, h1)), ((h0, h1), (h0, h0))):
        quad((xa, ya, 5), (xa, ya, 9), (xb, yb, 9), (xb, yb, 5))
    for (xa, ya), (xb, yb) in (((X0, X0), (X1, X0)), ((X1, X0), (X1, X1)),
                               ((X1, X1), (X0, X1)), ((X0, X1), (X0, X0))):
        quad((xa, ya, 5), (xb, yb, 5), (xb, yb, 10), (xa, ya, 10))
    return V, F


def mesh_xml(V, F, paint=None):
    s = ["<mesh><vertices>"]
    s += [f'<vertex x="{x}" y="{y}" z="{z}"/>' for x, y, z in V]
    s.append("</vertices><triangles>")
    for i, (a, b, c) in enumerate(F):
        p = f' paint_color="{paint[i]}"' if paint and paint.get(i) else ""
        s.append(f'<triangle v1="{a}" v2="{b}" v3="{c}"{p}/>')
    s.append("</triangles></mesh>")
    return "".join(s)


def project(path):
    head = ('<?xml version="1.0" encoding="UTF-8"?>\n<model unit="millimeter" '
            'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" '
            'xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06">')
    # Slab: сетка вокруг нуля, component поднимает на 1, item поворачивает на 90° и ставит
    slab = head + '<resources><object id="1" type="model">' + \
        mesh_xml(*box((-10, -10, -1), (10, 10, 1))) + "</object></resources><build/></model>"
    disk_V, disk_F = box((100, 130, 5), (120, 150, 7))
    top = head + "<resources>"
    top += ('<object id="2" type="model"><components><component p:path="/3D/Objects/object_1.model" '
            'objectid="1" transform="1 0 0 0 1 0 0 0 1 0 0 1"/></components></object>')
    top += '<object id="3" type="model">' + mesh_xml(*box((130, 100, 5), (140, 110, 7))) + "</object>"
    top += '<object id="4" type="model">' + mesh_xml(disk_V, disk_F, {0: "8", 1: "8"}) + "</object>"
    top += '<object id="5" type="model">' + mesh_xml(*cup()) + "</object>"
    # выключенный в интерфейсе объект: в G-коде его нет, в совмещение он не входит
    top += '<object id="6" type="model">' + mesh_xml(*box((200, 200, 0), (230, 230, 9))) + "</object>"
    top += ('</resources><build><item objectid="2" transform="0 1 0 -1 0 0 0 0 1 110 110 5"/>'
            '<item objectid="3"/><item objectid="4"/><item objectid="5"/><item objectid="6" printable="0"/></build></model>')
    cfg = '<?xml version="1.0" encoding="UTF-8"?>\n<config>\n'
    for oid, nm in ((2, "Slab"), (3, "Core"), (4, "Disk"), (5, "Cup")):
        cfg += (f'  <object id="{oid}">\n    <metadata key="name" value="{nm}"/>\n'
                f'    <metadata key="extruder" value="1"/>\n')
        if oid == 2:
            cfg += '    <part id="1" subtype="normal_part">\n      <metadata key="name" value="Slab"/>\n    </part>\n'
        cfg += "  </object>\n"
    cfg += '  <plate>\n    <metadata key="plater_id" value="1"/>\n'
    for oid in (2, 3, 4, 5):
        cfg += (f'    <model_instance>\n      <metadata key="object_id" value="{oid}"/>\n'
                f'      <metadata key="instance_id" value="0"/>\n    </model_instance>\n')
    cfg += "  </plate>\n</config>\n"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("3D/3dmodel.model", top)
        z.writestr("3D/Objects/object_1.model", slab)
        z.writestr("Metadata/model_settings.config", cfg)


# ------------------------------------------------------------ G-код

class GC:
    def __init__(self):
        self.l = ["; HEADER_BLOCK_START", f"; layer_height = {LH}",
                  f"; initial_layer_print_height = {LH}", "; support_top_z_distance = 0.2",
                  "; support_bottom_z_distance = 0.2", "; support_interface_spacing = 0.5",
                  "; support_type = normal(auto)", "G90", "M83"]
        self.x = self.y = 0.0

    def feat(self, f):
        self.l += [f"; FEATURE: {f}", f"; LINE_WIDTH: {W}"]

    def go(self, x, y):
        self.l.append(f"G1 X{x:.3f} Y{y:.3f} F30000"); self.x, self.y = x, y

    def ex(self, x, y):
        self.l.append(f"G1 X{x:.3f} Y{y:.3f} E0.05"); self.x, self.y = x, y

    def lines(self, x0, x1, y0, y1, pitch):
        """Сплошная заливка прямоугольника линиями вдоль X: края полосы на границе."""
        y, k = y0 + W / 2, 0
        while y <= y1 - W / 2 + 1e-6:
            a, b = (x0 + W / 2, x1 - W / 2)[::1 if k % 2 == 0 else -1]
            self.go(a, y); self.ex(b, y)
            y += pitch; k += 1
        if y - pitch < y1 - W / 2 - 1e-6:          # последняя полоса — по краю
            self.go(x0 + W / 2, y1 - W / 2); self.ex(x1 - W / 2, y1 - W / 2)

    def rect(self, x0, x1, y0, y1):
        x0, x1, y0, y1 = x0 + W / 2, x1 - W / 2, y0 + W / 2, y1 - W / 2
        self.go(x0, y0)
        for p in ((x1, y0), (x1, y1), (x0, y1), (x0, y0)):
            self.ex(*p)

    def circle(self, cx, cy, r):
        """Полный круг одной командой G2: конец совпадает с началом."""
        self.go(cx + r, cy)
        self.l.append(f"G2 X{cx + r:.3f} Y{cy:.3f} I{-r:.3f} J0 E0.3")


def gcode(path):
    g = GC()
    pads = [((102, 112, 102, 112), 5.0), ((131, 137, 101, 107), 5.0),
            ((139, 141, 139, 141), 9.0)]
    zmax = 9.0
    k = 1
    while LH * k <= zmax + 1e-6:
        z = round(LH * k, 3)
        g.l += ["; CHANGE_LAYER", f"; Z_HEIGHT: {z}", f"G1 Z{z}"]
        for (x0, x1, y0, y1), under in pads:
            if z > under - 0.2 + 1e-6:
                continue
            g.feat("Support interface" if z >= under - 0.4 - 1e-6 else "Support")
            if z >= under - 0.4 - 1e-6:
                g.lines(x0, x1, y0, y1, 0.5 + W)
            else:
                g.rect(x0, x1, y0, y1)
                g.lines(x0, x1, y0, y1, 2.0)
        if z <= 4.8 + 1e-6:                     # круг под Disk: дуги
            g.feat("Support interface" if z >= 4.6 - 1e-6 else "Support")
            rs = [3 - W / 2 - W * i for i in range(7)] if z >= 4.6 - 1e-6 else [3 - W / 2]
            for r in rs:
                g.circle(110, 140, r)
        if z <= 6.0 + 1e-6:                     # ствол с шейкой
            g.feat("Support")
            g.circle(160, 160, 0.6 if 2.2 - 1e-6 <= z <= 3.0 + 1e-6 else 2.0)
        if 5.2 - 1e-6 <= z <= 5.4 + 1e-6:       # внешние стенки — для совмещения с проектом
            g.feat("Outer wall")
            for b in ((100, 120, 100, 120), (130, 140, 100, 110), (100, 120, 130, 150),
                      (130, 150, 130, 150)):
                g.rect(*b)
        k += 1
    path.write_text("\n".join(g.l) + "\n")


def fixture():
    d = work("supportmap_fixture")
    project(d / "p.3mf")
    gcode(d / "plate_1.gcode")
    return d


def obj_line(out, name):
    for line in out.splitlines():
        if line.startswith(f"объект «{name}»"):
            return line
    raise AssertionError(f"нет строки объекта {name}\n{out[-2000:]}")


# ------------------------------------------------------------ кейсы

@case("supportmap:площадь касания и видимое/скрытое на фикстуре", tools=("supportmap.py",))
def _():
    d = fixture()
    out = run([TOOLS / "supportmap.py", "map", d / "p.3mf", d / "plate_1.gcode",
               "--hide-object", "Core", "--hide-fil", "2"], deps=DEPS)
    contains(out, "совмещение с проектом: расхождение габаритов 0.00 мм")
    s = obj_line(out, "Slab")
    close(num(s, r"касание ([\d.]+) мм²"), 100.0, 10.0, "Slab, касание")
    close(num(s, r"видимо ([\d.]+) мм²"), 100.0, 10.0, "Slab, видимо")
    c = obj_line(out, "Core")
    close(num(c, r"касание ([\d.]+) мм²"), 36.0, 3.6, "Core, касание")
    close(num(c, r"видимо ([\d.]+) мм²"), 0.0, 0.01, "Core, видимо")
    close(num(c, r"объект ([\d.]+)"), 36.0, 3.6, "Core, скрыто как объект")
    k = obj_line(out, "Disk")
    close(num(k, r"касание ([\d.]+) мм²"), math.pi * 9, 2.8, "Disk, касание по дугам")
    close(num(k, r"покраска ([\d.]+)"), math.pi * 9, 2.8, "Disk, скрыто покраской")
    u = obj_line(out, "Cup")
    close(num(u, r"касание ([\d.]+) мм²"), 4.0, 0.6, "Cup, касание в нише")
    close(num(u, r"ниша ([\d.]+)"), 4.0, 0.6, "Cup, скрыто нишей")
    close(num(out, r"ИТОГО: касание [\d.]+ мм², видимо ([\d.]+)"), 100.0, 10.0, "видимо всего")
    # первое место списка — видимая площадка Slab, в её координатах на столе
    row = [l for l in out.splitlines() if l.strip().startswith("Slab")][0].split()
    close(float(row[3]), 107.0, 0.5, "центр места касания, x")
    close(float(row[4]), 107.0, 0.5, "центр места касания, y")
    close(float(row[5]), 5.0, 0.1, "место касания, z")
    # шейка ствола
    close(num(out, r"Ø\s+([\d.]+) мм на высоте"), 1.62, 0.2, "шейка ствола, Ø")
    z = num(out, r"Ø\s+[\d.]+ мм на высоте\s+([\d.]+)")
    assert 2.2 - 1e-6 <= z <= 3.0 + 1e-6, f"шейка не там: z {z}"


@case("supportmap:--joints auto относит плоское дно к стыку", tools=("supportmap.py",))
def _():
    d = fixture()
    out = run([TOOLS / "supportmap.py", "map", d / "p.3mf", d / "plate_1.gcode",
               "--hide-object", "Core", "--joints", "auto", "--plane-min", "50"], deps=DEPS)
    s = obj_line(out, "Slab")
    close(num(s, r"стык ([\d.]+)"), 100.0, 10.0, "Slab, скрыто стыком")
    close(num(s, r"видимо ([\d.]+) мм²"), 0.0, 0.01, "Slab, видимо")
    # дно Disk тоже плоскость 400 мм², без --hide-fil оно стык, а не покраска
    k = obj_line(out, "Disk")
    close(num(k, r"стык ([\d.]+)"), math.pi * 9, 2.8, "Disk, стык")
    # потолок ниши 4 мм² меньше --plane-min: остаётся нишей
    close(num(obj_line(out, "Cup"), r"ниша ([\d.]+)"), 4.0, 0.6, "Cup, ниша")


@case("supportmap:neck без проекта", tools=("supportmap.py",))
def _():
    d = fixture()
    out = run([TOOLS / "supportmap.py", "neck", d / "plate_1.gcode"], deps=DEPS)
    close(num(out, r"Ø\s+([\d.]+) мм"), 1.62, 0.2, "шейка, Ø")


@case("supportmap:нарезка проекта и перебор поз", needs=("bambu",), tools=("supportmap.py",))
def _():
    d = work("supportmap_bambu")
    p = d / "fig.3mf"
    run([TOOLS / "make_multicolor_3mf.py", FIX / "figurine.stl", FIX / "figurine_zones.npy",
         "-o", p, "--project"], deps=("trimesh", "numpy"))
    out = run([TOOLS / "supportmap.py", "map", p, "--supports", "--out", d / "map"],
              deps=DEPS, timeout=1800)
    contains(out, "нарезано:", "совмещение с проектом")
    off = num(out, r"расхождение габаритов ([\d.]+) мм")
    assert off < 0.3, f"G-код и проект разошлись на {off} мм"
    tot = num(out, r"ИТОГО: касание ([\d.]+) мм²")
    assert tot > 5, f"касание под рукой фигурки не найдено: {tot} мм²"
    out = run([TOOLS / "supportmap.py", "sweep", p, "--object", "figurine.stl", "--supports",
               "--poses", "0,0;90,0", "--out", d / "sweep"], deps=DEPS, timeout=3600)
    contains(out, "рейтинг", "проект лучшей позы")
    assert out.count("поза ") >= 2, out
