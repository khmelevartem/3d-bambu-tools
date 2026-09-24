#!/usr/bin/env python3
"""Ремонт сетки через Blender в фоне. Запускается обычным python3 — сам себя
перезапускает внутри Blender, руками ничего включать не надо.

    python3 tools/meshfix.py сломанная.stl                 # безопасный набор
    python3 tools/meshfix.py вход.stl -o чинёная.stl --holes --normals
    python3 tools/meshfix.py вход.3mf --extract            # достать сетку из 3MF
    python3 tools/meshfix.py вход.stl --dry                # только показать, что сделал бы

Blender зовётся с --factory-startup: он не читает пользовательские настройки
и не пишет их обратно, поэтому запускать можно при открытом Blender — аддоны
и userpref не пострадают. По той же причине аддон 3D Print Toolbox тут
недоступен и не нужен: всё делается штатным bmesh, он встроен.

Шаги выключены по умолчанию поодиночке; без флагов выполняется безопасный
набор: вырожденные грани, дубли граней, потерянные вершины, пересчёт нормалей.
Дырки и сварка вершин меняют геометрию, поэтому просятся явно.
"""
import sys, os, subprocess, json

BLENDER = "/Applications/Blender.app/Contents/MacOS/Blender"

STEPS = {
    "--weld":      "склеить вершины ближе допуска (по умолчанию 1e-4 мм)",
    "--degenerate":"выбросить грани нулевой площади",
    "--dedup":     "выбросить грани-дубли",
    "--loose":     "выбросить вершины и рёбра без граней",
    "--normals":   "пересчитать нормали наружу",
    "--holes":     "закрыть дырки (петли открытых рёбер)",
    "--triangulate":"разбить n-угольники на треугольники",
    "--remesh S":  "пересобрать поверхность вокселями со стороной S мм — "
                   "единственное, что лечит non-manifold без ручной работы",
    "--dropjunk":  "выбросить отдельные тела мельче тысячной доли главного",
    "--smooth N":  "N проходов сглаживания вершин — снимает ступеньки вокселя; "
                   "без него воксельная сетка выглядит мохнатой",
}


# ======================= часть, работающая внутри Blender =======================

def run_in_blender(argv):
    import bpy, bmesh
    from mathutils import Vector

    args = json.loads(argv[0])
    src, dst = args["src"], args["dst"]

    bpy.ops.wm.read_factory_settings(use_empty=True)
    ext = os.path.splitext(src)[1].lower()
    if   ext == ".stl": bpy.ops.wm.stl_import(filepath=src)
    elif ext == ".obj": bpy.ops.wm.obj_import(filepath=src)
    elif ext == ".ply": bpy.ops.wm.ply_import(filepath=src)
    elif ext in (".glb", ".gltf"): bpy.ops.import_scene.gltf(filepath=src)
    else: raise SystemExit(f"формат {ext} Blender тут не читает; для 3MF сперва --extract")

    objs = [o for o in bpy.context.scene.objects if o.type == 'MESH']
    if not objs:
        raise SystemExit("в файле нет сеток")
    # всё в один объект: ремонт идёт по цельной сетке
    ctx = bpy.context.copy()
    for o in objs: o.select_set(True)
    bpy.context.view_layer.objects.active = objs[0]
    if len(objs) > 1:
        bpy.ops.object.join()
    obj = bpy.context.view_layer.objects.active
    # применить трансформации: иначе экспорт уедет в масштабе
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)

    def survey(bm):
        opens = sum(1 for e in bm.edges if len(e.link_faces) == 1)
        nonm  = sum(1 for e in bm.edges if len(e.link_faces) >= 3)
        loose = sum(1 for e in bm.edges if len(e.link_faces) == 0)
        lo = Vector((min(v.co.x for v in bm.verts), min(v.co.y for v in bm.verts), min(v.co.z for v in bm.verts)))
        hi = Vector((max(v.co.x for v in bm.verts), max(v.co.y for v in bm.verts), max(v.co.z for v in bm.verts)))
        return {"verts": len(bm.verts), "edges": len(bm.edges), "faces": len(bm.faces),
                "open": opens, "nonmanifold": nonm, "loose_edges": loose,
                "volume": bm.calc_volume(signed=True),
                "bbox": [round(x, 4) for x in (hi - lo)]}

    bm = bmesh.new(); bm.from_mesh(obj.data)
    before = survey(bm)
    log = []

    if args["weld"] is not None:
        n0 = len(bm.verts)
        bmesh.ops.remove_doubles(bm, verts=bm.verts[:], dist=args["weld"])
        log.append(f"сварка вершин по {args['weld']} мм: {n0} -> {len(bm.verts)}")

    if args["degenerate"]:
        n0 = len(bm.faces)
        bmesh.ops.dissolve_degenerate(bm, dist=1e-9, edges=bm.edges[:])
        # dissolve_degenerate не берёт грани нулевой площади с разными вершинами
        zero = [f for f in bm.faces if f.calc_area() <= 1e-12]
        if zero:
            bmesh.ops.delete(bm, geom=zero, context='FACES')
        log.append(f"вырожденные грани: {n0} -> {len(bm.faces)}")

    if args["dedup"]:
        seen, dup = set(), []
        for f in bm.faces:
            k = tuple(sorted(v.index for v in f.verts))
            (dup.append(f) if k in seen else seen.add(k))
        if dup:
            bmesh.ops.delete(bm, geom=dup, context='FACES')
        log.append(f"грани-дубли: удалено {len(dup)}")

    if args["loose"]:
        junk = [v for v in bm.verts if not v.link_faces] + [e for e in bm.edges if not e.link_faces]
        if junk:
            bmesh.ops.delete(bm, geom=junk, context='VERTS')
        log.append(f"потерянная геометрия: удалено {len(junk)}")

    if args["holes"]:
        bnd = [e for e in bm.edges if e.is_boundary]
        if bnd:
            bmesh.ops.holes_fill(bm, edges=bnd, sides=args["hole_sides"])
        still = sum(1 for e in bm.edges if e.is_boundary)
        log.append(f"дырки: открытых рёбер {len(bnd)} -> {still}"
                   + ("" if not still else "  (остаток — петли сложнее, чем закрывает holes_fill)"))

    if args["triangulate"]:
        ngons = [f for f in bm.faces if len(f.verts) > 3]
        if ngons:
            bmesh.ops.triangulate(bm, faces=ngons)
        log.append(f"n-угольников разбито: {len(ngons)}")

    if args["normals"]:
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
        log.append("нормали пересчитаны наружу")

    if args["remesh"]:
        # Воксельная пересборка: поверхность строится заново по объёму, и
        # результат замкнут и манифолден по построению. Ею лечатся Т-образные
        # швы, самопересечения и слипшиеся куски — всё, что поточечный ремонт
        # не берёт. Плата — потеря деталей мельче вокселя и другая топология.
        bm.to_mesh(obj.data); obj.data.update(); bm.free()
        obj.data.remesh_voxel_size = args["remesh"]
        obj.data.remesh_voxel_adaptivity = 0.0
        bpy.ops.object.voxel_remesh()
        bm = bmesh.new(); bm.from_mesh(obj.data)
        bmesh.ops.triangulate(bm, faces=bm.faces[:])
        log.append(f"воксельная пересборка со стороной {args['remesh']} мм: "
                   f"граней стало {len(bm.faces)}")

    if args["dropjunk"]:
        # Мусорные островки: после воксельной пересборки и булевых операций
        # остаются отдельные тела в несколько граней и нулевого объёма.
        # В слайсере они дают крошки рядом с деталью.
        import itertools
        seen, groups = set(), []
        for f in bm.faces:
            if f.index in seen:
                continue
            stack, grp = [f], []
            seen.add(f.index)
            while stack:
                cur = stack.pop(); grp.append(cur)
                for e in cur.edges:
                    for nb in e.link_faces:
                        if nb.index not in seen:
                            seen.add(nb.index); stack.append(nb)
            groups.append(grp)
        if len(groups) > 1:
            vols = [sum(f.calc_area() for f in g) for g in groups]
            biggest = max(vols)
            junk = [f for g, v in zip(groups, vols) if v < biggest * 1e-3 for f in g]
            if junk:
                bmesh.ops.delete(bm, geom=junk, context='FACES')
                bmesh.ops.delete(bm, geom=[v for v in bm.verts if not v.link_faces], context='VERTS')
            log.append(f"тел было {len(groups)}, выброшено мусорных {len(groups)-sum(1 for v in vols if v >= biggest*1e-3)}")
        else:
            log.append("тело одно, выбрасывать нечего")

    if args["smooth"]:
        # Воксельная пересборка оставляет ступеньку в половину вокселя: на 0.15 мм
        # это 0.05-0.08 мм ряби, в печати её нет, а в слайсере поверхность выглядит
        # мохнатой. Лапласово сглаживание снимает рябь; следить надо за усадкой —
        # оно тянет выпуклости внутрь, поэтому проходов нужно мало.
        for _ in range(args["smooth"]):
            bmesh.ops.smooth_vert(bm, verts=bm.verts[:], factor=0.5,
                                  use_axis_x=True, use_axis_y=True, use_axis_z=True)
        log.append(f"сглаживание вершин: {args['smooth']} проходов")

    after = survey(bm)
    nm_pts = [tuple(round(c, 3) for c in ((e.verts[0].co + e.verts[1].co) / 2))
              for e in bm.edges if len(e.link_faces) >= 3][:20]

    if not args["dry"]:
        bm.to_mesh(obj.data); obj.data.update()
        # validate() ловит то, что bmesh пропускает: битые индексы, дубли петель
        args_changed = obj.data.validate(verbose=False)
        log.append(f"mesh.validate(): {'нашёл и поправил битую геометрию' if args_changed else 'претензий нет'}")
        e2 = os.path.splitext(dst)[1].lower()
        if   e2 == ".stl": bpy.ops.wm.stl_export(filepath=dst, export_selected_objects=False, global_scale=1.0)
        elif e2 == ".obj": bpy.ops.wm.obj_export(filepath=dst, export_materials=False)
        elif e2 == ".ply": bpy.ops.wm.ply_export(filepath=dst)
        else: raise SystemExit(f"на выход {e2} не умею: бери .stl, .obj или .ply")
    bm.free()

    print("@@MESHFIX@@" + json.dumps({"before": before, "after": after, "log": log,
                                      "nonmanifold_points": nm_pts}, ensure_ascii=False))


# ============================ обычный запуск ============================

def main():
    argv = sys.argv[1:]
    if not argv or "-h" in argv or "--help" in argv:
        print(__doc__)
        print("Шаги:")
        for k, v in STEPS.items():
            print(f"  {k:15} {v}")
        return 0

    files = [a for a in argv if not a.startswith("-")]
    if "-o" in argv:
        out = argv[argv.index("-o") + 1]
        files = [f for f in files if f != out]
    else:
        out = None
    if not files:
        print("не указан входной файл"); return 1
    src = files[0]

    if src.lower().endswith(".3mf") and "--put" in argv:
        k = argv.index("--put")
        return put_3mf(src, argv[k + 1], argv[k + 2], out)
    if src.lower().endswith(".3mf") or "--extract" in argv:
        return extract_3mf(src)

    explicit = [k for k in STEPS if k in argv]
    args = {
        "src": os.path.abspath(src),
        "dst": os.path.abspath(out or os.path.splitext(src)[0] + "_fixed.stl"),
        "weld": float(argv[argv.index("--weld") + 1]) if "--weld" in argv
                and len(argv) > argv.index("--weld") + 1
                and not argv[argv.index("--weld") + 1].startswith("-") else (1e-4 if "--weld" in argv else None),
        "degenerate": "--degenerate" in argv or not explicit,
        "dedup":      "--dedup" in argv or not explicit,
        "loose":      "--loose" in argv or not explicit,
        "normals":    "--normals" in argv or not explicit,
        "holes":      "--holes" in argv,
        "hole_sides": 0,
        "triangulate": "--triangulate" in argv,
        "remesh": float(argv[argv.index("--remesh") + 1]) if "--remesh" in argv else None,
        "dropjunk": "--dropjunk" in argv,
        "smooth": int(argv[argv.index("--smooth") + 1]) if "--smooth" in argv else 0,
        "dry": "--dry" in argv,
    }

    if not os.path.exists(BLENDER):
        print(f"Blender не найден: {BLENDER}"); return 1
    proc = subprocess.run([BLENDER, "--factory-startup", "--background",
                           "--python", os.path.abspath(__file__), "--", json.dumps(args)],
                          capture_output=True, text=True)
    line = next((l for l in proc.stdout.splitlines() if l.startswith("@@MESHFIX@@")), None)
    if not line:
        print(proc.stdout[-3000:]); print(proc.stderr[-3000:]); return 1
    r = json.loads(line[len("@@MESHFIX@@"):])
    b, a = r["before"], r["after"]

    print(f"\n{os.path.basename(src)}")
    print(f"  было : {b['faces']} граней, {b['verts']} вершин, открытых рёбер {b['open']}, "
          f"non-manifold {b['nonmanifold']}, объём {b['volume']:.1f} мм³, габарит {b['bbox']}")
    for l in r["log"]:
        print(f"    · {l}")
    print(f"  стало: {a['faces']} граней, {a['verts']} вершин, открытых рёбер {a['open']}, "
          f"non-manifold {a['nonmanifold']}, объём {a['volume']:.1f} мм³, габарит {a['bbox']}")

    # Габарит: тревожить только на величину, видную в печати. Слой 0.2 мм,
    # сопло 0.4 — сдвиг в 17 микрон после воксельной пересборки не значит ничего,
    # а вот пойманный масштаб 1:1000 (метры вместо миллиметров) значит всё.
    drift = max(abs(x - y) for x, y in zip(b["bbox"], a["bbox"]))
    if drift > 0.05:
        print(f"  !! ГАБАРИТ УЕХАЛ на {drift:.3f} мм: {b['bbox']} -> {a['bbox']} — проверить масштаб перед печатью")
    elif drift > 0:
        print(f"  ~ габарит сдвинулся на {drift*1000:.0f} мкм — четверти слоя не набирается, печати не видно")
    dv = abs(a["volume"] - b["volume"]) / max(abs(b["volume"]), 1e-9) * 100
    if dv > 1:
        why = []
        if args["normals"]:
            why.append("вывернутые грани считались со знаком минус, и до ремонта объём был неверный")
        if args["holes"]:
            why.append("закрытие дырок добавляет тело")
        if args["weld"] is not None:
            why.append("сварка вершин стягивает поверхность")
        print(f"  ~ объём изменился на {dv:.1f}%"
              + (f": {'; '.join(why)}" if why else " — ремонт тронул форму, а не только топологию"))
        print("     Сверить с BambuStudio --info: совпадение объёма доказывает, что стало верно.")
    if a["nonmanifold"]:
        print(f"  !! non-manifold рёбер осталось {a['nonmanifold']}: автомат их не лечит.")
        print(f"     Середины рёбер (мм), смотреть тут: {r['nonmanifold_points'][:8]}")
    if args["dry"]:
        print("  (--dry: файл не записан)")
    else:
        print(f"  записано: {args['dst']}")
    return 0


def extract_3mf(path):
    """Достать сетки из 3MF в отдельные STL — ремонтировать и смотреть.
    Обратно в проект они НЕ кладутся: см. skills/mesh-repair."""
    import zipfile, xml.etree.ElementTree as ET, struct
    base = os.path.splitext(path)[0]
    made, painted = [], 0
    with zipfile.ZipFile(path) as z:
        for name in sorted(n for n in z.namelist() if n.lower().endswith(".model")):
            blob = z.read(name)
            painted += blob.count(b"paint_color")
            root = ET.fromstring(blob)
            nsu = root.tag.split('}')[0].strip('{') if '}' in root.tag else None
            q = (lambda t: f"{{{nsu}}}{t}") if nsu else (lambda t: t)
            for obj in root.iter(q("object")):
                mesh = obj.find(q("mesh"))
                if mesh is None: continue
                vs = [(float(v.get("x")), float(v.get("y")), float(v.get("z")))
                      for v in mesh.find(q("vertices"))]
                ts = [(int(t.get("v1")), int(t.get("v2")), int(t.get("v3")))
                      for t in mesh.find(q("triangles"))]
                if not ts: continue
                out = f"{base}__obj{obj.get('id')}.stl"
                with open(out, "wb") as fh:
                    fh.write(b"\0" * 80 + struct.pack("<I", len(ts)))
                    for i, j, k in ts:
                        fh.write(struct.pack("<3f", 0, 0, 0))
                        for idx in (i, j, k):
                            fh.write(struct.pack("<3f", *vs[idx]))
                        fh.write(b"\0\0")
                made.append((out, len(ts)))
    for out, n in made:
        print(f"  {os.path.basename(out)}: {n} треугольников")
    if painted:
        print(f"\n  !! в проекте {painted} покрашенных треугольников.")
        print("     Ремонт меняет число и порядок граней — покраска к ним привязана и пропадёт.")
        print("     Чинить только с последующей перекраской (скилл 3mf-paint) либо не чинить.")
    return 0


def put_3mf(project, obj_id, stl, out):
    """Вернуть починенную сетку в авторскую оболочку 3MF.

    Меняются только <vertices> и <triangles> нужного объекта плюс счётчики
    граней. Пластины, вторые детали, превью, профиль печати автора остаются
    как были — это тот же приём, что в 3d-modeling/references/foreign-3mf.md.
    Покраска по треугольникам при этом теряется: она привязана к их номерам.
    """
    import zipfile, shutil, struct, xml.etree.ElementTree as ET, re as _re
    out = out or os.path.splitext(project)[0] + "_fixed.3mf"

    buf = open(stl, "rb").read()
    n = struct.unpack("<I", buf[80:84])[0]
    raw = memoryview(buf)[84:]
    verts, tris, index = [], [], {}
    for i in range(n):
        base = i * 50 + 12
        tri = []
        for j in range(3):
            xyz = struct.unpack("<3f", raw[base + j * 12: base + j * 12 + 12])
            k = index.get(xyz)
            if k is None:
                k = index[xyz] = len(verts); verts.append(xyz)
            tri.append(k)
        tris.append(tri)
    print(f"  сетка на вход: {n} треугольников, {len(verts)} вершин после склейки")

    def fmt(v):
        return f'<vertex x="{v[0]:.6f}" y="{v[1]:.6f}" z="{v[2]:.6f}"/>'
    new_v = "".join(fmt(v) for v in verts)
    new_t = "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in tris)

    replaced = False
    with zipfile.ZipFile(project) as zin, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename.lower().endswith(".model") and b"<mesh>" in data:
                txt = data.decode("utf-8")
                pat = _re.compile(r'(<object[^>]*id="' + _re.escape(obj_id) + r'"[^>]*>.*?<mesh>)'
                                  r'\s*<vertices>.*?</vertices>\s*<triangles>.*?</triangles>\s*(</mesh>)',
                                  _re.S)
                new_txt, cnt = pat.subn(
                    lambda m: m.group(1) + f"<vertices>{new_v}</vertices>"
                                           f"<triangles>{new_t}</triangles>" + m.group(2), txt)
                if cnt:
                    replaced = True
                    if b"paint_color" in data:
                        print("  !! в этом объекте была покраска по треугольникам — она потеряна")
                    data = new_txt.encode("utf-8")
            elif item.filename.endswith("model_settings.config"):
                txt = data.decode("utf-8")
                txt = _re.sub(r'(<mesh_stat[^>]*face_count=")\d+(")',
                              lambda m: m.group(1) + str(len(tris)) + m.group(2), txt)
                data = txt.encode("utf-8")
            zout.writestr(item, data)
    if not replaced:
        print(f"  !! объект id={obj_id} с сеткой не найден — файл записан без подмены")
        return 1
    print(f"  записано: {out}")
    print("  Проверить: BambuStudio --info и meshdoctor.py по новому файлу,")
    print("  и обязательно открыть в интерфейсе — CLI и интерфейс читают проект разными ветками.")
    return 0


if __name__ == "__main__":
    if "bpy" in sys.modules or os.environ.get("BLENDER_MESHFIX"):
        run_in_blender(sys.argv[sys.argv.index("--") + 1:])
    else:
        try:
            import bpy  # noqa: F401
            run_in_blender(sys.argv[sys.argv.index("--") + 1:])
        except ImportError:
            sys.exit(main())
