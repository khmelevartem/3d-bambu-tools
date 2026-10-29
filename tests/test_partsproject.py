"""partsproject.py: a 3MF project and a folder of STL parts kept in agreement.

The fixture is built here with the standard library: a project of an L-shaped
arm turned on the plate and carrying its own layer height, a painted L-shaped
brow and two identical pins; and a parts folder where the arm and the brow are
longer than in the project, stored in a different (assembly) pose, plus a part
the project does not have yet. What is checked by number: the pose string of
every build item survives sync byte for byte, the object setting survives, the
new mesh lies on the old one where the shapes agree (vertex by vertex), paint
survives an edit that kept the topology, and export writes the plate pose.
"""
import json, math, re, struct, zipfile

from harness import case, run, num, close, contains, work, TOOLS

DEPS = ("numpy", "scipy")


# --------------------------------------------------------------- геометрия
def l_prism(a, b, t, h, c=(0, 0, 0)):
    """L in plan: long arm a along x, short arm b along y, thickness t, height h."""
    P = [(0, 0), (a, 0), (a, t), (t, t), (t, b), (0, b)]
    V = [(x + c[0], y + c[1], c[2]) for x, y in P] + [(x + c[0], y + c[1], h + c[2]) for x, y in P]
    F = []
    for i, j, k in ((0, 1, 2), (0, 2, 3), (0, 3, 4), (0, 4, 5)):
        F += [(i, k, j), (i + 6, j + 6, k + 6)]
    for i in range(6):
        j = (i + 1) % 6
        F += [(i, j, j + 6), (i, j + 6, i + 6)]
    return V, F


def box(sx, sy, sz):
    V = [(x, y, z) for z in (0, sz) for y in (0, sy) for x in (0, sx)]
    V = [V[0], V[1], V[3], V[2], V[4], V[5], V[7], V[6]]
    F = [(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4),
         (1, 2, 6), (1, 6, 5), (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7)]
    return V, F


def rot(axis, deg):
    x, y, z = axis
    n = math.sqrt(x * x + y * y + z * z); x, y, z = x / n, y / n, z / n
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg)); C = 1 - c
    return [[c + x * x * C, x * y * C - z * s, x * z * C + y * s],
            [y * x * C + z * s, c + y * y * C, y * z * C - x * s],
            [z * x * C - y * s, z * y * C + x * s, c + z * z * C]]


def mul(A, B):
    return [[sum(A[i][k] * B[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def move(V, R, T):
    return [tuple(sum(R[i][k] * v[k] for k in range(3)) + T[i] for i in range(3)) for v in V]


def tstr(R, T):
    """3MF transform: row-vector convention, so the rotation goes in transposed."""
    return " ".join(f"{R[j][i]:.9g}" for i in range(3) for j in range(3)) + \
        " " + " ".join(f"{x:.9g}" for x in T)


def write_stl(path, V, F):
    out = [b"\0" * 80, struct.pack("<I", len(F))]
    for f in F:
        out.append(struct.pack("<12fH", 0, 0, 0, *[c for i in f for c in V[i]], 0))
    path.write_bytes(b"".join(out))


def read_stl(path):
    raw = path.read_bytes()
    n = struct.unpack("<I", raw[80:84])[0]
    pts = []
    for i in range(n):
        f = struct.unpack("<12f", raw[84 + 50 * i: 84 + 50 * i + 48])
        pts += [f[3:6], f[6:9], f[9:12]]
    return pts


def bbox(P):
    lo = [min(p[i] for p in P) for i in range(3)]
    hi = [max(p[i] for p in P) for i in range(3)]
    return lo, hi


# --------------------------------------------------------------- проект
ARM_C = (-10.0, -6.0, -3.0)                      # сетка руки в координатах объекта
ARM_R = mul(rot((0, 0, 1), 30), rot((1, 0, 0), 90))
ARM_T = (100.0, 90.0, 6.0)
BROW_R = rot((0, 0, 1), -45)
Q = rot((1, 2, 3), 50)                           # поза сборки, в которой лежат STL
QT = (5.0, -7.0, 12.0)


def objects():
    arm = l_prism(20, 12, 4, 6, ARM_C)
    brow = l_prism(14, 8, 3, 2)
    pin = box(3, 3, 8)
    return [
        dict(name="Arm", mesh=arm, R=ARM_R, T=ARM_T, ext=1, meta={"layer_height": "0.12"}),
        dict(name="Brow", mesh=brow, R=BROW_R, T=(60.0, 60.0, 0.0), ext=2, paint=True),
        dict(name="Pin 1", mesh=pin, R=rot((0, 0, 1), 0), T=(30.0, 30.0, 0.0), ext=2),
        dict(name="Pin 2", mesh=pin, R=rot((0, 0, 1), 0), T=(40.0, 30.0, 0.0), ext=2),
    ]


def build(path):
    NS = ('<?xml version="1.0" encoding="UTF-8"?>\n<model unit="millimeter" xml:lang="en-US" '
          'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" '
          'xmlns:BambuStudio="http://schemas.bambulab.com/package/2021" '
          'xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06" '
          'requiredextensions="p">\n')
    files, res, items, cfg, inst = {}, [], [], [], []
    for k, o in enumerate(objects(), 1):
        inner, outer = 2 * k - 1, 2 * k
        V, F = o["mesh"]
        tri = []
        for i, f in enumerate(F):
            pc = f' paint_color="{"8" if i % 2 else "4"}"' if o.get("paint") else ""
            tri.append(f'     <triangle v1="{f[0]}" v2="{f[1]}" v3="{f[2]}"{pc}/>\n')
        files[f"3D/Objects/object_{k}.model"] = (
            NS + f' <resources>\n  <object id="{inner}" p:UUID="0000{k}" type="model">\n'
            '   <mesh>\n    <vertices>\n'
            + "".join(f'     <vertex x="{x}" y="{y}" z="{z}"/>\n' for x, y, z in V)
            + '    </vertices>\n    <triangles>\n' + "".join(tri)
            + '    </triangles>\n   </mesh>\n  </object>\n </resources>\n <build/>\n</model>\n')
        res.append(f'  <object id="{outer}" p:UUID="1000{k}" type="model">\n   <components>\n'
                   f'    <component p:path="/3D/Objects/object_{k}.model" objectid="{inner}" '
                   f'p:UUID="2000{k}" transform="1 0 0 0 1 0 0 0 1 0 0 0"/>\n   </components>\n  </object>\n')
        items.append(f'  <item objectid="{outer}" p:UUID="3000{k}" '
                     f'transform="{tstr(o["R"], o["T"])}" printable="1"/>\n')
        meta = "".join(f'    <metadata key="{a}" value="{b}"/>\n' for a, b in o.get("meta", {}).items())
        cfg.append(f'  <object id="{outer}">\n    <metadata key="name" value="{o["name"]}"/>\n'
                   f'    <metadata key="extruder" value="{o["ext"]}"/>\n{meta}'
                   f'    <metadata face_count="{len(F)}"/>\n'
                   f'    <part id="{inner}" subtype="normal_part">\n'
                   f'      <metadata key="name" value="{o["name"]}"/>\n'
                   f'      <metadata key="matrix" value="1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1"/>\n'
                   f'      <mesh_stat face_count="{len(F)}" edges_fixed="0"/>\n    </part>\n  </object>\n')
        inst.append(f'    <model_instance>\n      <metadata key="object_id" value="{outer}"/>\n'
                    f'      <metadata key="instance_id" value="0"/>\n    </model_instance>\n')
    files["3D/3dmodel.model"] = (NS + ' <metadata name="BambuStudio:3mfVersion">1</metadata>\n'
                                 ' <resources>\n' + "".join(res) + ' </resources>\n <build>\n'
                                 + "".join(items) + ' </build>\n</model>\n')
    files["Metadata/model_settings.config"] = (
        '<?xml version="1.0" encoding="UTF-8"?>\n<config>\n' + "".join(cfg)
        + '  <plate>\n    <metadata key="plater_id" value="1"/>\n' + "".join(inst)
        + '  </plate>\n  <assemble>\n  </assemble>\n</config>\n')
    files["Metadata/project_settings.config"] = json.dumps(
        {"layer_height": "0.2", "enable_support": "0", "support_type": "normal(auto)",
         "filament_colour": ["#FFFFFF", "#000000", "#D02020"]})
    with zipfile.ZipFile(path, "w") as z:
        for n, data in files.items():
            z.writestr(n, data)
    return path


def parts(d):
    """New arm (long arm 24 instead of 20), new brow (short arm 10 instead of 8) —
    both in the assembly pose Q, and a tail the project does not have."""
    d.mkdir(exist_ok=True)
    V, F = l_prism(24, 12, 4, 6, ARM_C)
    write_stl(d / "Fig arm.stl", move(V, Q, QT), F)
    V, F = l_prism(14, 10, 3, 2)
    write_stl(d / "Fig brow.stl", move(V, Q, (0.0, 3.0, 1.0)), F)
    V, F = box(4, 5, 6)
    write_stl(d / "Fig tail.stl", V, F)
    return d


def entry(z, name):
    return z.read(name).decode()


def item_transforms(p3mf):
    with zipfile.ZipFile(p3mf) as z:
        return dict(re.findall(r'<item objectid="(\d+)"[^>]*transform="([^"]+)"', entry(z, "3D/3dmodel.model")))


def obj_block(p3mf, name):
    with zipfile.ZipFile(p3mf) as z:
        cfg = entry(z, "Metadata/model_settings.config")
    for blk in re.findall(r'<object id="\d+">.*?</object>', cfg, re.S):
        if f'key="name" value="{name}"' in blk.split("<part")[0]:
            return blk
    raise AssertionError(f"нет объекта {name}")


def verts(p3mf, k):
    with zipfile.ZipFile(p3mf) as z:
        x = entry(z, f"3D/Objects/object_{k}.model")
    V = [tuple(map(float, m)) for m in re.findall(r'<vertex x="([^"]*)" y="([^"]*)" z="([^"]*)"', x)]
    return V, len(re.findall(r'paint_color=', x))


# --------------------------------------------------------------- кейсы
@case("partsproject:diff находит изменённые детали, недостающие и пару по форме")
def _():
    d = work("partsproject_diff")
    prj = build(d / "p.3mf")
    out = run([TOOLS / "partsproject.py", "diff", prj, parts(d / "parts")], deps=DEPS)
    # площадь L руки: 20·4 + 8·4 = 112 → 24·4 + 8·4 = 128, высота 6
    contains(out, "≠  Arm", "объём 672.0 → 768.0 мм³ (+14.3%)", "≠  Brow",
             "объём 114.0 → 126.0 мм³", "нет в папке: Pin 1, Pin 2", "нет в проекте: Fig tail.stl")
    # та же рука под чужим именем и в другой позе — пара находится по форме
    same = d / "same"; same.mkdir()
    V, F = l_prism(20, 12, 4, 6, ARM_C)
    write_stl(same / "whatever.stl", move(V, Q, QT), F)
    out = run([TOOLS / "partsproject.py", "diff", prj, same, "--write-map", d / "map.json"], deps=DEPS)
    contains(out, "=  Arm", "whatever.stl", "[форма]", "совпало 1, различается 0")
    assert json.loads((d / "map.json").read_text(encoding="utf-8")) == {"Arm": "whatever.stl"}


@case("partsproject:sync меняет сетку, не трогая позу, настройки и покраску")
def _():
    d = work("partsproject_sync")
    prj = build(d / "p.3mf")
    before = item_transforms(prj)
    out = run([TOOLS / "partsproject.py", "sync", prj, parts(d / "parts"), "-o", d / "new.3mf",
               "--add", "--filament", "3"], deps=DEPS)
    contains(out, "ЗАМЕНЁН Arm", "ЗАМЕНЁН Brow", "покраска сохранена", "ДОБАВЛЕН tail",
             "заменено 2, добавлено 1")
    after = item_transforms(d / "new.3mf")
    for oid, tr in before.items():
        assert after[oid] == tr, f"поза объекта {oid}: {tr} → {after[oid]}"
    contains(obj_block(d / "new.3mf", "Arm"), 'key="layer_height" value="0.12"', 'face_count="20"')
    contains(obj_block(d / "new.3mf", "tail"), 'key="extruder" value="3"')
    # новая рука легла на старую: её вершины — длинная L в той же системе объекта
    want, _ = l_prism(24, 12, 4, 6, ARM_C)
    got, _ = verts(d / "new.3mf", 1)
    err = max(min(math.dist(g, w) for w in want) for g in got)
    close(err, 0.0, 0.02, "вершины руки от ожидаемых, мм")
    want, _ = l_prism(14, 10, 3, 2)
    got, npaint = verts(d / "new.3mf", 2)
    close(max(min(math.dist(g, w) for w in want) for g in got), 0.0, 0.02, "вершины брови, мм")
    close(npaint, 20, 0, "граней брови с покраской")
    out = run([TOOLS / "partsproject.py", "diff", d / "new.3mf", d / "parts"], deps=DEPS)
    contains(out, "=  Arm", "=  Brow", "=  tail", "совпало 3, различается 0")


@case("partsproject:set пишет и снимает настройку объекта, чужой ключ не пускает")
def _():
    d = work("partsproject_set")
    prj = build(d / "p.3mf")
    out = run([TOOLS / "partsproject.py", "set", prj, "--object", "Brow", "--object", "Arm",
               "layer_height=0.08", "enable_support=1", "-o", d / "s.3mf"], deps=DEPS)
    contains(out, "Arm: layer_height 0.12 → 0.08", "Brow: enable_support — → 1")
    b = obj_block(d / "s.3mf", "Brow")
    contains(b, 'key="layer_height" value="0.08"', 'key="enable_support" value="1"')
    assert b.count('key="layer_height"') == 1
    assert obj_block(d / "s.3mf", "Arm").count('key="layer_height"') == 1
    run([TOOLS / "partsproject.py", "set", d / "s.3mf", "--object", "Arm", "layer_height=",
         "-o", d / "u.3mf"], deps=DEPS)
    assert "layer_height" not in obj_block(d / "u.3mf", "Arm")
    out = run([TOOLS / "partsproject.py", "set", prj, "--object", "Arm", "layer_heigth=0.1",
               "-o", d / "x.3mf"], deps=DEPS, expect=1)
    contains(out, "ключа 'layer_heigth' нет")


@case("partsproject:export пишет позу печати и склеивает одинаковые детали")
def _():
    d = work("partsproject_export")
    prj = build(d / "p.3mf")
    out = run([TOOLS / "partsproject.py", "export", prj, "-o", d / "out", "--world"], deps=DEPS)
    contains(out, "white_Arm.stl", "black_Brow.stl", "black_Pin_1_x2.stl", "файлов 3")
    V, _ = l_prism(20, 12, 4, 6, ARM_C)
    lo, hi = bbox(move(V, ARM_R, ARM_T))
    glo, ghi = bbox(read_stl(d / "out" / "white_Arm.stl"))
    for i in range(3):
        close(glo[i], lo[i], 1e-3, f"низ руки по оси {i}")
        close(ghi[i], hi[i], 1e-3, f"верх руки по оси {i}")
    out = run([TOOLS / "partsproject.py", "export", prj, "-o", d / "c"], deps=DEPS)
    glo, ghi = bbox(read_stl(d / "c" / "white_Arm.stl"))
    close(glo[2], 0.0, 1e-4, "низ руки на z=0")
    close(ghi[0] - glo[0], hi[0] - lo[0], 1e-3, "габарит руки по x")
    close(num(out, r"white_Arm\.stl.*?габарит [\d.]+ × [\d.]+ × ([\d.]+)"), hi[2] - lo[2], 0.01, "высота")
