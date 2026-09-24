#!/usr/bin/env python3
"""Поворотные узлы на цилиндрах между напечатанными деталями.

Деталь крутится относительно соседней, если у них общая ось и обе
поверхности вокруг неё — тела вращения. Дешевле всего это делается так:
ровный рез поперёк оси, слепое отверстие в каждой половине и отдельно
напечатанный штифт. Штифт не торчит из детали при печати, поэтому не
просит поддержек, а посадку можно подобрать одним размером отверстия:
свободно крутится — плюс 0.3 мм к диаметру, сидит намертво под клей —
плюс 0.15.

Задание — JSON:

    {
      "out": "work/joints",
      "parts": {"голова": "work/parts/filament3.stl"},
      "cuts":  [{"part":"рубашка","p":[-1.9,7,-0.4],"d":[0,1,0],
                 "keep":"рубашка","away":"рука"}],
      "holes": [{"part":"голова","p":[0.85,-4.35,3.5],"d":[0,0,1],
                 "dia":5.3,"from":0.2,"to":7.5}],
      "dowels":[{"dia":5.0,"len":14,"n":4}]
    }

`cuts` режет деталь плоскостью (p, d) надвое: `keep` — половина со стороны
−d, `away` — со стороны +d; обе закрываются плоской крышкой. `holes`
вырезает цилиндр вдоль оси на отрезке [from, to] от точки p — тем же
ключом делается и выборка под воротник, просто большего диаметра.
`dowels` пишет отдельный файл со штифтами, расставленными в ряд.

`joints` — то же самое, но одной строкой на стык и без ручной арифметики:

    {"joints":[{"parts":["рубашка","рука"],"p":[-1.9,7,-0.4],"d":[0,1,0],
                "size":4.0,"depth":[5.0,5.0],"shape":"rect","fit":[0.15,0.3]}]}

Раскрывается в две слепые ниши навстречу друг другу и один штифт. Правила
допусков зашиты внутрь и повторяют разобранную 21.09.2026 фигурку
Spider-Man с MakerWorld: ниша шире штифта на `fit` по диаметру (0.15 —
сидит под клей, 0.3 — входит от руки), а штифт **на миллиметр короче**
суммы глубин, иначе он упрётся в дно раньше, чем сойдутся сами детали,
и на стыке останется щель. `shape: rect` даёт квадратное сечение: круглый
штифт оставляет деталям поворот вокруг своей оси, квадратный — нет.

    uv run --quiet python tools/pivot_joint.py work/joints.json

Работает через Blender в фоне (как meshfix.py), булево — точным решателем.
"""
import json, os, subprocess, sys

BLENDER = "/Applications/Blender.app/Contents/MacOS/Blender"


# ======================= часть, работающая внутри Blender =======================

def run_in_blender(argv):
    import bpy, bmesh
    from mathutils import Vector, Matrix

    spec = json.loads(argv[0])
    out = spec["out"]
    os.makedirs(out, exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)

    def stats(o):
        bm = bmesh.new(); bm.from_mesh(o.data)
        v = bm.calc_volume(signed=True)
        opens = sum(1 for e in bm.edges if len(e.link_faces) == 1)
        n = len(bm.faces); bm.free()
        return n, v / 1000.0, opens

    objs = {}
    for name, path in spec["parts"].items():
        bpy.ops.wm.stl_import(filepath=path)
        o = bpy.context.view_layer.objects.active
        o.name = name
        objs[name] = o
        n, v, op = stats(o)
        print(f'загружено «{name}»: граней {n}, объём {v:.2f} см3, открытых рёбер {op}')

    def heal(o, rounds=6):
        """Убрать non-manifold рёбра: они ломают точное булево.

        У деталей от paint_split.py такие рёбра сидят там, где сходятся
        крышки трёх зон — глубоко внутри детали. Грани при таком ребре
        выбрасываются, дырка зашивается. Правка локальная, снаружи не видна,
        но без неё Blender на булевом выдаёт вывернутый наизнанку объём."""
        bm = bmesh.new(); bm.from_mesh(o.data)
        n0 = sum(1 for e in bm.edges if len(e.link_faces) > 2)
        for _ in range(rounds):
            bad = [e for e in bm.edges if len(e.link_faces) > 2]
            if not bad:
                break
            faces = {f for e in bad for f in e.link_faces}
            bmesh.ops.delete(bm, geom=list(faces), context='FACES')
            holes = [e for e in bm.edges if len(e.link_faces) == 1]
            if holes:
                bmesh.ops.holes_fill(bm, edges=holes, sides=0)
            bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 3])
        bmesh.ops.delete(bm, geom=[v for v in bm.verts if not v.link_faces],
                         context='VERTS')
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
        n1 = sum(1 for e in bm.edges if len(e.link_faces) > 2)
        op = sum(1 for e in bm.edges if len(e.link_faces) == 1)
        bm.to_mesh(o.data); bm.free(); o.data.update()
        if n0 or n1:
            print(f'  «{o.name}»: non-manifold рёбер {n0} → {n1}, открытых {op}')

    def orient(d):
        """поворот, переводящий +Z в направление d"""
        return Vector(d).normalized().to_track_quat('Z', 'Y').to_euler()

    def cylinder(p, d, t0, t1, dia, verts=128):
        d = Vector(d).normalized()
        mid = Vector(p) + d * ((t0 + t1) / 2)
        bpy.ops.mesh.primitive_cylinder_add(vertices=verts, radius=dia / 2,
                                            depth=(t1 - t0), location=mid,
                                            rotation=orient(d))
        return bpy.context.view_layer.objects.active

    def box(p, d, t0, t1, side, verts=None):
        """Призма квадратного сечения вдоль оси d — ниша или штифт от поворота."""
        d = Vector(d).normalized()
        mid = Vector(p) + d * ((t0 + t1) / 2)
        bpy.ops.mesh.primitive_cube_add(size=1.0, location=mid, rotation=orient(d))
        o = bpy.context.view_layer.objects.active
        o.scale = (side, side, t1 - t0)
        bpy.ops.object.transform_apply(scale=True)
        return o

    def solid(p, d, t0, t1, size, shape):
        return (box if shape == 'rect' else cylinder)(p, d, t0, t1, size)

    def tube(p, d, t0, t1, dia, dia_in):
        """кольцевой резец: выборка только в поясе от dia_in до dia"""
        o = cylinder(p, d, t0, t1, dia)
        i = cylinder(p, d, t0 - 1, t1 + 1, dia_in)
        boolean(o, i)
        return o

    def boolean(target, cutter, op='DIFFERENCE', solver='EXACT'):
        m = target.modifiers.new('bool', 'BOOLEAN')
        m.operation = op; m.object = cutter; m.solver = solver
        bpy.context.view_layer.objects.active = target
        bpy.ops.object.modifier_apply(modifier=m.name)
        bpy.data.objects.remove(cutter, do_unlink=True)

    def bisect(o, p, d, keep_positive):
        """отрезать половину плоскостью и закрыть срез плоской крышкой"""
        bm = bmesh.new(); bm.from_mesh(o.data)
        geom = bm.verts[:] + bm.edges[:] + bm.faces[:]
        res = bmesh.ops.bisect_plane(bm, geom=geom, dist=1e-6,
                                     plane_co=Vector(p), plane_no=Vector(d),
                                     clear_inner=keep_positive,
                                     clear_outer=not keep_positive)
        edges = [e for e in res['geom_cut'] if isinstance(e, bmesh.types.BMEdge)]
        bmesh.ops.holes_fill(bm, edges=edges, sides=0)
        bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 3])
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
        bm.to_mesh(o.data); bm.free()
        o.data.update()

    for c in spec.get("cuts", []):
        src = objs[c["part"]]
        other = src.copy(); other.data = src.data.copy()
        bpy.context.collection.objects.link(other)
        bisect(src,  c["p"], c["d"], keep_positive=False)
        bisect(other, c["p"], c["d"], keep_positive=True)
        for nm, o in ((c.get("keep"), src), (c.get("away"), other)):
            if nm is None:
                bpy.data.objects.remove(o, do_unlink=True)
            else:
                o.name = nm; objs[nm] = o
        objs.pop(c["part"], None) if c["part"] not in (c.get("keep"), c.get("away")) else None
        for nm in [x for x in (c.get("keep"), c.get("away")) if x]:
            n, v, op = stats(objs[nm])
            print(f'рез «{c["part"]}» → «{nm}»: граней {n}, объём {v:.2f} см3, '
                  f'открытых рёбер {op}')

    print('чистка сеток перед булевым:')
    for o in list(objs.values()):
        heal(o)

    def material(t, cy):
        """Сколько материала детали попадает в резец, см3.

        Считается отдельным пересечением, а не разностью объёмов: резец,
        который детали не касается, Blender не выбрасывает, а вклеивает
        вывернутым наизнанку, и разность тогда врёт."""
        probe = t.copy(); probe.data = t.data.copy()
        bpy.context.collection.objects.link(probe)
        c2 = cy.copy(); c2.data = cy.data.copy()
        bpy.context.collection.objects.link(c2)
        boolean(probe, c2, op='INTERSECT')
        v = stats(probe)[1]
        bpy.data.objects.remove(probe, do_unlink=True)
        return v

    made_joints = []
    # Стык одной строкой: две встречные слепые ниши и штифт под них.
    # Проверяются обе ниши сразу и до сверления: деталь, вырезанная по цвету,
    # у самого шва бывает тонким клином, и ниша уходит мимо неё. Сделать
    # половину стыка нельзя — штифту тогда некуда встать, поэтому стык
    # либо целиком, либо никак.
    for j in spec.get("joints", []):
        sz, (da, db) = j["size"], j["depth"]
        fa, fb = j.get("fit", [0.15, 0.3])
        sh = j.get("shape", "rect")
        names = [j["parts"][0]] + [x for x in j.get("alts", [])
                                   if x != j["parts"][0]]
        names = [x for x in names if x in objs]

        def probe(part, sgn, dep, fit):
            d = [sgn * x for x in j["d"]]
            cy = solid(j["p"], d, -1.0, dep, sz + fit, sh)
            full = ((sz + fit) ** 2 if sh == 'rect'
                    else 3.14159 * (sz + fit) ** 2 / 4) * dep / 1000
            vi = material(objs[part], cy)
            bpy.data.objects.remove(cy, do_unlink=True)
            return vi / full if full else 0.0

        # Кто с какой стороны и куда смотрит ось — четыре варианта, и
        # решает их булево, а не догадка выше по конвейеру. Догадка по
        # средней стороне куска ошибается там, где у шва три соседа:
        # на талии Робби она уверенно показала на брюки, а ниша уходила
        # в воздух.
        best, a, b, sgn = None, None, None, 1
        for pa in names:
            for pb in names:
                if pa == pb:
                    continue
                for g in (1, -1):
                    q = min(probe(pa, g, da, fa), probe(pb, -g, db, fb))
                    if best is None or q > best:
                        best, a, b, sgn = q, pa, pb, g
        if best is None or best < 0.75:
            print(f'стык {j["parts"][0]} — {j.get("alts")}: ОТМЕНЁН, ни один '
                  f'из вариантов не попал в тело (лучший {100*(best or 0):.0f} %)')
            continue
        d = [sgn * x for x in j["d"]]
        hs = [dict(part=a, p=j["p"], d=d, size=sz + fa, shape=sh, deep=da,
                   way=1, **{"from": -1.0, "to": da}),
              dict(part=b, p=j["p"], d=d, size=sz + fb, shape=sh, deep=db,
                   way=-1, **{"from": -db, "to": 1.0})]
        ok = True
        for h in hs:
            cy = solid(h["p"], h["d"], h["from"], h["to"], h["size"], sh)
            full = (h["size"] ** 2 if sh == 'rect'
                    else 3.14159 * h["size"] ** 2 / 4) * h["deep"] / 1000
            vi = material(objs[h["part"]], cy)
            bpy.data.objects.remove(cy, do_unlink=True)
            if vi < 0.75 * full:
                print(f'стык «{a}» — «{b}»: ОТМЕНЁН, в «{h["part"]}» ниша '
                      f'попадает в тело лишь на {100 * vi / full:.0f} % — '
                      f'деталь у шва тоньше ниши либо ось смотрит не туда')
                ok = False
                break
        if not ok:
            continue
        made_joints.append((a, b, j, hs))
        spec.setdefault("holes", []).extend(hs)
        spec.setdefault("dowels", []).append(
            dict(size=sz, len=round(da + db - 1.0, 2), n=j.get("n", 2), shape=sh))
        print(f'стык «{a}» — «{b}»: ниши {sz + fa:.2f} и {sz + fb:.2f} на '
              f'{da:.1f} и {db:.1f} мм, штифт {sz:.1f}×{da + db - 1:.1f}')

    for h in spec.get("holes", []):
        t = objs[h["part"]]
        sz = h.get("size", h.get("dia"))
        sh = h.get("shape", 'round')
        if h.get("dia_in"):
            cy = tube(h["p"], h["d"], h["from"], h["to"], h["dia"], h["dia_in"])
            full = 0.0
        else:
            cy = solid(h["p"], h["d"], h["from"], h["to"], sz, sh)
            # Объём резца ниже поверхности — то, с чем сравнивать выбранное.
            # Считается только когда глубина ниши названа явно (`deep`):
            # у отверстия, написанного руками, где кончается тело, неизвестно.
            deep = h.get("deep", 0.0)
            full = (sz * sz if sh == 'rect' else 3.14159 * sz * sz / 4) * deep / 1000
        v0 = stats(t)[1]
        tag = (f'{"□" if sh == "rect" else "Ø"}{sz} в «{h["part"]}» '
               f'на [{h["from"]}, {h["to"]}]')
        vi = material(t, cy)
        if vi <= 0.001:
            bpy.data.objects.remove(cy, do_unlink=True)
            print(f'отверстие {tag}: ПРОПУЩЕНО — в резце нет материала')
            continue
        backup = t.data.copy()
        boolean(t, cy, solver=h.get('solver', 'EXACT'))
        n, v, op = stats(t)
        if v < 0 or abs((v0 - v) - vi) > 0.02 * max(vi, 0.01) + 1e-6:
            t.data = backup
            print(f'отверстие {tag}: ОТМЕНЕНО — булево дало {v:.2f} см3, '
                  f'а в резце было {1000*vi:.0f} мм3 материала')
            continue
        # Ниша, у которой выбрано заметно меньше своего объёма, стенку
        # пробила: часть резца прошла мимо тела. Снаружи это дырка, и
        # увидеть её иначе нечем — сетка после булевого всё равно замкнута.
        warn = ''
        if full > 0 and vi < 0.92 * full:
            warn = (f'  ← ВНИМАНИЕ: резец ушёл из тела на '
                    f'{100*(1-vi/full):.0f} %, ниша пробила стенку')
        было = (f'{1000*vi:.0f} из {1000*full:.0f} полных' if full > 0
                else f'{1000*vi:.0f}')
        print(f'отверстие {tag}: убрано {1000*(v0-v):.0f} мм3 '
              f'(в резце было {было}), осталось {v:.2f} см3, '
              f'открытых рёбер {op}{warn}')

    # Ниша готова — теперь доказать, что она именно ниша, а не сквозная
    # дыра и не подкоп под наружную поверхность. Оба ответа даёт то же
    # булево пересечение, что и при выборе: сколько материала детали
    # попадает в пробный объём.
    if made_joints:
        print('проверка готовых ниш:')
    for a, b, j, hs in made_joints:
        for h in hs:
            t = objs[h["part"]]
            sz, dp, sh = h["size"], h["deep"], h["shape"]
            d = [h["way"] * x for x in h["d"]]
            # 1. дно: слой толщиной wall сразу за нишей обязан быть полным
            wall = 1.2
            flo = solid(h["p"], d, dp + 0.15, dp + 0.15 + wall, sz, sh)
            vf = material(t, flo)
            bpy.data.objects.remove(flo, do_unlink=True)
            need_f = (sz * sz if sh == 'rect' else 3.14159 * sz * sz / 4) * wall / 1000
            # 2. стенка: короб шире ниши на wall с каждой стороны; ниша в нём
            #    пустая, значит материала обязано быть ровно на разницу объёмов
            big = solid(h["p"], d, 0.15, dp - 0.15, sz + 2 * wall, sh)
            vw = material(t, big)
            bpy.data.objects.remove(big, do_unlink=True)
            k = 1.0 if sh == 'rect' else 3.14159 / 4
            need_w = k * ((sz + 2 * wall) ** 2 - sz ** 2) * (dp - 0.3) / 1000
            print(f'  «{h["part"]}» {sz}×{dp}: дно {100*vf/need_f:5.0f} %, '
                  f'стенка {100*vw/need_w:5.0f} % '
                  f'({"ок" if vf > 0.9 * need_f and vw > 0.9 * need_w else "ПЛОХО"})')

    for name, o in objs.items():
        for x in bpy.context.scene.objects:
            x.select_set(x is o)
        bpy.context.view_layer.objects.active = o
        p = os.path.join(out, f'{name}.stl')
        bpy.ops.wm.stl_export(filepath=p, export_selected_objects=True, global_scale=1.0)
        n, v, op = stats(o)
        print(f'записано {p}: граней {n}, объём {v:.2f} см3, открытых рёбер {op}')

    dw = spec.get("dowels", [])
    if dw:
        made = []
        x = 0.0
        for g in dw:
            sz = g.get("size", g.get("dia"))
            sh = g.get("shape", 'round')
            for i in range(g["n"]):
                c = solid((x, 0, g["len"] / 2), (0, 0, 1), -g["len"] / 2,
                          g["len"] / 2, sz, sh)
                made.append(c); x += sz + 4
        for o in bpy.context.scene.objects:
            o.select_set(o in made)
        bpy.context.view_layer.objects.active = made[0]
        p = os.path.join(out, 'штифты.stl')
        bpy.ops.wm.stl_export(filepath=p, export_selected_objects=True, global_scale=1.0)
        print(f'записано {p}: штифтов {len(made)} ' + ', '.join(
            f'{"□" if g.get("shape") == "rect" else "Ø"}'
            f'{g.get("size", g.get("dia"))}×{g["len"]} — {g["n"]} шт' for g in dw))


# ============================ запуск снаружи Blender ============================

def main():
    if len(sys.argv) < 2:
        print(__doc__); return 1
    spec = json.load(open(sys.argv[1], encoding='utf-8'))
    if not os.path.exists(BLENDER):
        print(f'Blender не найден: {BLENDER}'); return 1
    r = subprocess.run([BLENDER, '--factory-startup', '--background',
                        '--python', os.path.abspath(__file__), '--',
                        json.dumps(spec, ensure_ascii=False)],
                       capture_output=True, text=True)
    keep = [l for l in r.stdout.splitlines()
            if not l.startswith(('Blender', 'Read prefs', 'found bundled',
                                 'Info:', 'Warning:'))]
    print('\n'.join(l for l in keep if l.strip()))
    if r.returncode:
        print(r.stderr[-4000:])
    return r.returncode


if __name__ == '__main__':
    if 'bpy' in sys.modules or '--' in sys.argv:
        run_in_blender(sys.argv[sys.argv.index('--') + 1:])
    else:
        sys.exit(main())
