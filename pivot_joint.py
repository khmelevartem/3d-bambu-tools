#!/usr/bin/env python3
"""Pivot joints on cylinders between printed parts.

A part turns against its neighbour when they share an axis and both surfaces
around it are solids of revolution. The cheapest way to build that: a straight
cut across the axis, a blind hole in each half, and a separately printed pin.
A pin does not stick out of the part while printing, so it needs no supports,
and the fit is set by the hole diameter alone - free to turn, or seated for
glue.

The job is JSON:

    {
      "out": "work/joints",
      "parts": {"head": "work/parts/filament3.stl"},
      "cuts":  [{"part":"shirt","p":[-1.9,7,-0.4],"d":[0,1,0],
                 "keep":"shirt","away":"arm"}],
      "holes": [{"part":"head","p":[0.85,-4.35,3.5],"d":[0,0,1],
                 "dia":5.3,"from":0.2,"to":7.5}],
      "dowels":[{"dia":5.0,"len":14,"n":4}]
    }

`cuts` splits a part with the plane (p, d): `keep` is the half on the -d side,
`away` the one on +d, and both get a flat cap. `holes` cuts a cylinder along an
axis over the span [from, to] from point p. `dowels` writes a separate file of
pins laid out in a row.

`joints` is the same thing in one line per joint, without hand arithmetic:

    {"joints":[{"parts":["shirt","arm"],"p":[-1.9,7,-0.4],"d":[0,1,0],
                "size":4.0,"depth":[5.0,5.0],"shape":"rect","fit":[0.15,0.3]}]}

It expands into two blind sockets facing each other and one pin. The tolerance
rules are built in: the socket is wider than the pin by `fit` per side -
tighter in the half that sits under glue, looser in the half that goes on by
hand - and **the pin is a millimetre shorter than the sum of the depths**, or
it bottoms out before the parts meet and leaves a gap at the visible joint.
`shape: rect` gives a square section: **a round pin leaves the parts free to
rotate about it, a square one does not.**

    uv run --quiet python tools/pivot_joint.py work/joints.json

Runs through Blender headless, like meshfix.py, with the exact boolean solver.
"""
import json, os, subprocess, sys

# The macOS bundle by default; BLENDER=/path/to/blender picks another one.
BLENDER = os.environ.get("BLENDER",
                         "/Applications/Blender.app/Contents/MacOS/Blender")


# ======================= the part that runs inside Blender ======================

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
        """Remove non-manifold edges: they break exact booleans.

                On parts from paint_split.py such edges sit where the caps of three
                zones meet, deep inside the part. The faces at such an edge are dropped
                and the hole is stitched. The fix is local and invisible from outside,
                but without it Blender's boolean returns an inside-out volume."""
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
        """The rotation taking +Z to direction d."""
        return Vector(d).normalized().to_track_quat('Z', 'Y').to_euler()

    def cylinder(p, d, t0, t1, dia, verts=128):
        d = Vector(d).normalized()
        mid = Vector(p) + d * ((t0 + t1) / 2)
        bpy.ops.mesh.primitive_cylinder_add(vertices=verts, radius=dia / 2,
                                            depth=(t1 - t0), location=mid,
                                            rotation=orient(d))
        return bpy.context.view_layer.objects.active

    def box(p, d, t0, t1, side, verts=None):
        """A square-section prism along axis d — a socket or a pin, depending on sign."""
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
        """An annular cutter: material is taken only in the band from dia_in to dia."""
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
        """Cut half away with a plane and close the cut with a flat cap."""
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
        """How much of the part's material falls inside the cutter, cm3.

                Computed as a separate intersection, not as a difference of volumes: a
                cutter that does not touch the part is not discarded by Blender but
                glued in inside-out, and the difference then lies."""
        probe = t.copy(); probe.data = t.data.copy()
        bpy.context.collection.objects.link(probe)
        c2 = cy.copy(); c2.data = cy.data.copy()
        bpy.context.collection.objects.link(c2)
        boolean(probe, c2, op='INTERSECT')
        v = stats(probe)[1]
        bpy.data.objects.remove(probe, do_unlink=True)
        return v

    made_joints = []
    # A joint in one line: two opposing blind sockets and a pin to match.
    # Both sockets are checked at once and before drilling: a part cut out by
    # colour can be a thin wedge right at the seam, and the socket then goes
    # past it. Half a joint is useless - the pin has nowhere to stand - so the
    # joint is made whole or not at all.
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

        # Who is on which side and where the axis points: four variants, decided
        # by the boolean rather than by a guess earlier in the pipeline. A guess
        # from a piece's mean side is wrong wherever a seam has three
        # neighbours, and the socket then drills into thin air.

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
            # Cutter volume below the surface - the reference for what was
            # actually removed. Computed only when the socket depth is stated
            # explicitly: for a hand-written hole, where the body ends is unknown.
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
        # A socket that removed noticeably less than its own volume has broken
        # through the wall: part of the cutter passed outside the body. From
        # outside that is a hole, and nothing else reveals it - the mesh stays
        warn = ''
        if full > 0 and vi < 0.92 * full:
            warn = (f'  ← ВНИМАНИЕ: резец ушёл из тела на '
                    f'{100*(1-vi/full):.0f} %, ниша пробила стенку')
        было = (f'{1000*vi:.0f} из {1000*full:.0f} полных' if full > 0
                else f'{1000*vi:.0f}')
        print(f'отверстие {tag}: убрано {1000*(v0-v):.0f} мм3 '
              f'(в резце было {было}), осталось {v:.2f} см3, '
              f'открытых рёбер {op}{warn}')

    # The socket exists - now prove it is a socket and not a through hole or an
    # undercut beneath the outer surface. Both answers come from the same
    # boolean intersection used to choose it: how much of the part's material
    # falls inside a probe volume.
    if made_joints:
        print('проверка готовых ниш:')
    for a, b, j, hs in made_joints:
        for h in hs:
            t = objs[h["part"]]
            sz, dp, sh = h["size"], h["deep"], h["shape"]
            d = [h["way"] * x for x in h["d"]]
            # 1. floor: a layer of thickness wall right behind the socket must be full
            wall = 1.2
            flo = solid(h["p"], d, dp + 0.15, dp + 0.15 + wall, sz, sh)
            vf = material(t, flo)
            bpy.data.objects.remove(flo, do_unlink=True)
            need_f = (sz * sz if sh == 'rect' else 3.14159 * sz * sz / 4) * wall / 1000
            # 2. wall: a box wider than the socket by wall on each side; the socket
            #    inside it is empty, so the material must equal the volume difference
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


# ============================ invocation outside Blender ============================

def main():
    if {'-h', '--help'} & set(sys.argv[1:]):
        print(__doc__); return 0
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
