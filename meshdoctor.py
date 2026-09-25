#!/usr/bin/env python3
"""Diagnose a mesh: what exactly is broken, and whether it can be fixed
automatically.

Why this exists next to printcheck.py: that one merges vertices by rounding
coordinates and reports false holes on imported float32 meshes, as its own
header says. Here vertices are merged with a tolerance, through shifted hash
grids, and the result is compared against an exact merge. **A discrepancy
between the two is a seam that fell apart in the numbers, not a real hole.**

Defects are separated by class, because they are repaired differently:

  open edge (1 face)          a hole           -> hole filling closes it
  non-manifold edge (>=3)     a T-shaped seam  -> needs a rebuild; automation is dangerous
  inconsistent winding        a reversed face  -> recompute normals
  degenerate face             zero area        -> collapse
  duplicate face                               -> remove

Thresholds come from the installed nozzle, so the verdict answers whether a
defect can reach the plastic at all.

Formats: STL (binary and ASCII), 3MF (a Bambu project and bare core), OFF, OBJ.
For GLB/PLY, convert through Blender first (see meshfix.py --convert).
"""
import sys, os, struct, zipfile, re, math, json
import xml.etree.ElementTree as ET
import numpy as np
import hardware                       # nozzle diameter comes from hardware.json


# ---------- reading ----------

def read_stl(path):
    buf = open(path, 'rb').read()
    ascii_hdr = buf[:5].lower() == b'solid' and b'facet' in buf[:512]
    if not ascii_hdr:
        n = struct.unpack('<I', buf[80:84])[0]
        if len(buf) < 84 + n * 50:
            raise ValueError(f"бинарный STL оборван: заявлено {n} треугольников")
        raw = np.frombuffer(buf, dtype=np.uint8, count=n * 50, offset=84).reshape(n, 50)
        v = raw[:, 12:48].copy().view('<f4').reshape(n, 3, 3).astype(np.float64)
        return [("stl", v.reshape(-1, 3), np.arange(n * 3).reshape(n, 3), False)]
    txt = buf.decode('utf-8', 'replace')
    nums = np.array(re.findall(r'vertex\s+(\S+)\s+(\S+)\s+(\S+)', txt), dtype=np.float64)
    n = len(nums) // 3
    return [("stl", nums[:n * 3], np.arange(n * 3).reshape(n, 3), False)]


SCALE_WARN = {}          # label -> the <build> scale note, see _build_scales


def _uniform_scale(t):
    """Uniform scale from a 3MF matrix (9 rotation+scale numbers). None if non-uniform."""
    a = [float(x) for x in t.split()]
    if len(a) < 9:
        return None
    cols = [math.sqrt(a[0] ** 2 + a[1] ** 2 + a[2] ** 2),
            math.sqrt(a[3] ** 2 + a[4] ** 2 + a[5] ** 2),
            math.sqrt(a[6] ** 2 + a[7] ** 2 + a[8] ** 2)]
    if max(cols) - min(cols) > 1e-6 * max(cols):
        return None
    return cols[0]


def _build_scales(z, models):
    """{(file, object id): scale} from <build><item>, descending into <components>.

    A mesh inside a 3MF lies in its own units; the matrix in <build> converts it
    to millimetres. `BambuStudio --info` does NOT apply it and prints the raw
    bounding box. Neither do we, so that the two agree — but the scale is
    reported, because without it a verdict of "finer than the nozzle" understates
    the defect by exactly that factor.
    """
    comps, builds = {}, []
    for name in models:
        try:
            root = ET.fromstring(z.read(name))
        except Exception:
            continue
        ns = {'c': root.tag.split('}')[0].strip('{')} if '}' in root.tag else {}
        P = '{http://schemas.microsoft.com/3dmanufacturing/production/2015/06}path'
        def find(el, tag):
            return el.findall(f'c:{tag}', ns) if ns else el.findall(tag)
        for res in find(root, 'resources'):
            for obj in find(res, 'object'):
                for cs in find(obj, 'components'):
                    for c in list(cs):
                        k = _uniform_scale(c.get('transform') or '1 0 0 0 1 0 0 0 1')
                        tgt = (c.get(P) or '').lstrip('/') or name
                        comps.setdefault((name, obj.get('id')), []).append(
                            (tgt, c.get('objectid'), 1.0 if k is None else k))
        for b in find(root, 'build'):
            for it in find(b, 'item'):
                k = _uniform_scale(it.get('transform') or '1 0 0 0 1 0 0 0 1')
                tgt = (it.get(P) or '').lstrip('/') or name
                builds.append((tgt, it.get('objectid'), 1.0 if k is None else k))

    out, seen = {}, set()
    stack = list(builds)
    while stack:
        f, oid, k = stack.pop()
        if (f, oid) in seen:
            continue
        seen.add((f, oid))
        out[(f, oid)] = k
        for f2, oid2, k2 in comps.get((f, oid), []):
            stack.append((f2, oid2, k * k2))
    return out


def read_3mf(path):
    """Every <object> with a mesh separately. The indices are already in the file; no joining needed."""
    out = []
    with zipfile.ZipFile(path) as z:
        models = [n for n in z.namelist() if n.lower().endswith('.model')]
        scales = _build_scales(z, models)
        for name in sorted(models):
            root = ET.fromstring(z.read(name))
            ns = {'c': root.tag.split('}')[0].strip('{')} if '}' in root.tag else {}
            def find(el, tag):
                return el.findall(f'c:{tag}', ns) if ns else el.findall(tag)
            for res in find(root, 'resources'):
                for obj in find(res, 'object'):
                    for mesh in find(obj, 'mesh'):
                        vs, ts = find(mesh, 'vertices'), find(mesh, 'triangles')
                        if not vs or not ts:
                            continue
                        V = np.array([[float(v.get('x')), float(v.get('y')), float(v.get('z'))]
                                      for v in list(vs[0])], dtype=np.float64)
                        F = np.array([[int(t.get('v1')), int(t.get('v2')), int(t.get('v3'))]
                                      for t in list(ts[0])], dtype=np.int64)
                        if len(F):
                            label = f"{os.path.basename(name)}:object id={obj.get('id')}"
                            if obj.get('name'):
                                label += f" «{obj.get('name')}»"
                            k = scales.get((name, obj.get('id')))
                            if k is not None and abs(k - 1) > 1e-6:
                                SCALE_WARN[label] = (
                                    f"!! сетка в своих единицах: <build> масштабирует её в {k:.4g} раза. "
                                    f"Всё ниже — сырые единицы файла (так же считает BambuStudio --info); "
                                    f"настоящие миллиметры = ×{k:.4g}, и дефект «мельче сопла» тоже")
                            out.append((label, V, F, True))
    if not out:
        raise ValueError("в 3MF нет ни одной сетки (возможно, это .gcode.3mf — результат нарезки)")
    return out


def read_off(path):
    toks = []
    for line in open(path):
        line = line.split('#')[0].strip()
        if line:
            toks.append(line)
    assert toks[0].upper().startswith('OFF')
    head = toks[0][3:].split() or toks[1].split()
    start = 1 if toks[0][3:].split() else 2
    nv, nf = int(head[0]), int(head[1])
    V = np.array([list(map(float, toks[start + i].split()[:3])) for i in range(nv)])
    F = []
    for i in range(nf):
        p = list(map(int, toks[start + nv + i].split()[:1 + int(toks[start + nv + i].split()[0])]))
        for k in range(1, p[0] - 1):           # fan over an n-gon
            F.append([p[1], p[1 + k], p[2 + k]])
    return [("off", V, np.array(F, dtype=np.int64), True)]


def read_obj(path):
    V, F = [], []
    for line in open(path):
        if line.startswith('v '):
            V.append(list(map(float, line.split()[1:4])))
        elif line.startswith('f '):
            idx = [int(p.split('/')[0]) for p in line.split()[1:]]
            idx = [i - 1 if i > 0 else len(V) + i for i in idx]
            for k in range(1, len(idx) - 1):
                F.append([idx[0], idx[k], idx[k + 1]])
    return [("obj", np.array(V), np.array(F, dtype=np.int64), True)]


def read_any(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == '.stl':  return read_stl(path)
    if ext == '.3mf':  return read_3mf(path)
    if ext == '.off':  return read_off(path)
    if ext == '.obj':  return read_obj(path)
    raise ValueError(f"формат {ext} не читается напрямую: сперва meshfix.py --convert через Blender")


# ---------- vertex merging ----------

def weld(V, F, tol):
    """Weld vertices closer than tol. Eight shifted grids: two points within tol
        on every axis are guaranteed to land in one cell in at least one shift, so
        a seam does not fall apart on a rounding boundary."""
    n = len(V)
    parent = np.arange(n)

    def find(x):
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:
            parent[x], x = root, parent[x]
        return root

    for combo in range(8):
        shift = np.array([(combo >> k) & 1 for k in range(3)]) * (tol / 2)
        key = np.floor((V + shift) / tol).astype(np.int64)
        _, inv = np.unique(key, axis=0, return_inverse=True)
        order = np.argsort(inv, kind='stable')
        inv_s = inv[order]
        starts = np.flatnonzero(np.r_[True, inv_s[1:] != inv_s[:-1]])
        for s, e in zip(starts, np.r_[starts[1:], len(order)]):
            if e - s > 1:
                rep = find(order[s])
                for j in order[s + 1:e]:
                    rj = find(j)
                    if rj != rep:
                        parent[rj] = rep
    roots = np.array([find(i) for i in range(n)])
    uniq, remap = np.unique(roots, return_inverse=True)
    return V[uniq], remap[F]


# ---------- analysis ----------

def analyse(V, F, tol):
    r = {"faces_in": len(F), "verts_in": len(V)}

    # degenerate: repeated indices, or zero area
    a, b, c = V[F[:, 0]], V[F[:, 1]], V[F[:, 2]]
    cross = np.cross(b - a, c - a)
    area2 = np.linalg.norm(cross, axis=1)
    same_idx = (F[:, 0] == F[:, 1]) | (F[:, 1] == F[:, 2]) | (F[:, 0] == F[:, 2])
    degen = same_idx | (area2 <= 1e-14)
    r["degenerate"] = int(degen.sum())

    # A degenerate face must be COLLAPSED, not dropped. Dropping tears a hole:
    # its neighbours lose a shared edge, which is exactly the false diagnosis
    # printcheck.py is prone to. Blender on import and Bambu Studio on load
    # both collapse, which is why their meshes come out closed.

    if degen.any():
        nv = len(V)
        parent_v = np.arange(nv)

        def fv(x):
            root = x
            while parent_v[root] != root:
                root = parent_v[root]
            while parent_v[x] != root:
                parent_v[x], x = root, parent_v[x]
            return root
        for tri in F[degen]:
            a0 = fv(tri[0])
            for t in tri[1:]:
                rt = fv(t)
                if rt != a0:
                    parent_v[rt] = a0
        roots = np.array([fv(i) for i in range(nv)])
        keep_v, remap = np.unique(roots, return_inverse=True)
        V = V[keep_v]          # reindex vertices together with the faces
        F = remap[F]
        keep = ~((F[:, 0] == F[:, 1]) | (F[:, 1] == F[:, 2]) | (F[:, 0] == F[:, 2]))
        F = F[keep]
        a, b, c = V[F[:, 0]], V[F[:, 1]], V[F[:, 2]]
        cross = np.cross(b - a, c - a)
        area2 = np.linalg.norm(cross, axis=1)
    if len(F) == 0:
        r["empty"] = True
        return r

    # duplicate faces (up to rotation and direction)
    key = np.sort(F, axis=1)
    _, first, counts = np.unique(key, axis=0, return_index=True, return_counts=True)
    r["duplicate_faces"] = int((counts - 1).sum())

    # edges
    E = np.vstack([F[:, [0, 1]], F[:, [1, 2]], F[:, [2, 0]]])
    Es = np.sort(E, axis=1)
    uniq_e, inv_e, cnt_e = np.unique(Es, axis=0, return_inverse=True, return_counts=True)
    r["edges"] = len(uniq_e)
    r["open_edges"] = int((cnt_e == 1).sum())          # a hole
    r["nonmanifold_edges"] = int((cnt_e >= 3).sum())   # a T-shaped seam
    nm_e = uniq_e[cnt_e >= 3]
    r["nm_edge_max"] = (round(float(np.linalg.norm(V[nm_e[:, 0]] - V[nm_e[:, 1]], axis=1).max()), 4)
                        if len(nm_e) else 0.0)

    # Count winding ONLY over normal two-face edges. Measured over all edges,
    # every non-manifold edge lands here too and the number inflates to exactly
    # twice the non-manifold count, telling nothing of its own.

    order_e = np.argsort(inv_e, kind='stable')
    inv_es = inv_e[order_e]
    starts_e = np.flatnonzero(np.r_[True, inv_es[1:] != inv_es[:-1]])
    pair_mask = cnt_e[inv_es[starts_e]] == 2
    p0, p1 = order_e[starts_e[pair_mask]], order_e[starts_e[pair_mask] + 1]
    r["bad_winding_edges"] = int((E[p0] == E[p1]).all(axis=1).sum())

    # shells: face connectivity through edges
    nf = len(F)
    parent = np.arange(nf)

    def find(x):
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:
            parent[x], x = root, parent[x]
        return root


    # Two body counts. Through all edges, a non-manifold seam glues touching
    # bodies into one; through normal edges they are counted separately.
    # The discrepancy is the diagnosis: pieces stuck together by contact
    # rather than united by a boolean.

    face_of = np.tile(np.arange(nf), 3)
    for only_pairs in (False, True):
        parent = np.arange(nf)
        for s, e in zip(starts_e, np.r_[starts_e[1:], len(order_e)]):
            if only_pairs and (e - s) != 2:
                continue
            rep = find(face_of[order_e[s]])
            for j in order_e[s + 1:e]:
                rj = find(face_of[j])
                if rj != rep:
                    parent[rj] = rep
        lbl = np.array([find(i) for i in range(nf)])
        n = len(np.unique(lbl))
        r["bodies_touching" if not only_pairs else "bodies_separate"] = n
        if only_pairs and n > 1:
            # Per-body volume: this is what reveals the junk islands left
            # behind by voxel rebuilds and boolean operations. They must not
            # be printed, yet they do enter the bounding box.
            vol_f = np.einsum('ij,ij->i', a, cross) / 6.0
            uniq_l, inv_l = np.unique(lbl, return_inverse=True)
            vols = np.bincount(inv_l, weights=vol_f, minlength=len(uniq_l))
            cnts = np.bincount(inv_l, minlength=len(uniq_l))
            order_b = np.argsort(-np.abs(vols))
            # Show a dozen of the largest, but COUNT junk over all of them.
            # Counting over the displayed slice under-reports it by orders
            # of magnitude on a mesh with thousands of bodies.
            biggest = abs(vols[order_b[0]])
            r["junk_bodies"] = int((np.abs(vols) < biggest * 1e-3).sum())
            r["body_volumes"] = [(int(cnts[i]), round(float(vols[i]), 3)) for i in order_b[:12]]
    r["shells"] = r["bodies_touching"]

    # holes as loops of open edges
    bnd = uniq_e[cnt_e == 1]
    if len(bnd):
        vs = np.unique(bnd)
        idx = {v: i for i, v in enumerate(vs)}
        p2 = np.arange(len(vs))

        def f2(x):
            root = x
            while p2[root] != root:
                root = p2[root]
            while p2[x] != root:
                p2[x], x = root, p2[x]
            return root
        for u, w in bnd:
            ru, rw = f2(idx[u]), f2(idx[w])
            if ru != rw:
                p2[rw] = ru
        groups = {}
        for u, w in bnd:
            groups.setdefault(f2(idx[u]), []).append(np.linalg.norm(V[u] - V[w]))
        r["holes"] = len(groups)
        r["hole_perimeters"] = sorted((round(float(sum(g)), 3) for g in groups.values()), reverse=True)
    else:
        r["holes"] = 0
        r["hole_perimeters"] = []

    # volume and bounding box
    vol = np.einsum('ij,ij->i', a, cross).sum() / 6.0
    r["volume"] = float(vol)
    r["area"] = float(area2.sum() / 2)
    lo, hi = V[np.unique(F)].min(0), V[np.unique(F)].max(0)
    r["bbox"] = [round(x, 3) for x in (hi - lo)]
    r["faces"] = len(F)
    r["verts"] = len(np.unique(F))
    r["euler"] = chi = r["verts"] - r["edges"] + r["faces"]
    # Compute genus from the number of SHELLS: (2*shells - chi)/2. The formula
    # (2-chi)/2 holds for a single body only and returns negative values on
    # several. An odd chi means the surface is not simple and has no genus.
    bodies = r["bodies_separate"]
    if r["open_edges"] or r["nonmanifold_edges"]:
        r["genus"] = "не определён: сетка не замкнута"
    elif (2 * bodies - chi) % 2:
        r["genus"] = "не определён: хи нечётно, поверхность не простая"
    else:
        r["genus"] = str((2 * bodies - chi) // 2)
    r["watertight"] = (r["open_edges"] == 0 and r["nonmanifold_edges"] == 0)
    r["consistent"] = (r["bad_winding_edges"] == 0)
    return r


NOZZLE = hardware.nozzle()   # from hardware.json: nothing finer than the nozzle exists in print


def verdict(r):
    # Measure a defect against the nozzle instead of counting items. A hole
    # with a perimeter of a fraction of a millimetre is a circle the extruder
    # simply sweeps over. Sending a model to repair because of it risks the
    # paint and the topology for something that will not exist in plastic.
    tiny = r.get("hole_perimeters") and all(p < NOZZLE * math.pi for p in r["hole_perimeters"])

    if r.get("empty"):
        return "ПУСТО", "после выброса вырожденных граней не осталось геометрии"
    njunk = r.get("junk_bodies", 0)
    if njunk and r["watertight"] and r["consistent"]:
        return "АВТОМАТ", (f"сетка замкнута, но рядом с деталью висит {njunk} мусорных тел "
                           "— meshfix.py --dropjunk")
    if r["watertight"] and r["consistent"] and r["volume"] > 0 and not r["duplicate_faces"]:
        extra = f" (вырожденных граней {r['degenerate']} — выбрасываются при нарезке)" if r["degenerate"] else ""
        return "ЧИСТО", "слайсер съест как есть" + extra
    # Is everything broken smaller than the nozzle? Then it is mesh noise.
    nm_tiny = r.get("nm_edge_max", 0) < NOZZLE
    junk_only = r.get("junk_bodies", 0) == max(0, r.get("bodies_separate", 1) - 1)
    if (tiny or not r["holes"]) and nm_tiny and junk_only and not r["degenerate"] and not r["duplicate_faces"]:
        bits = []
        if r["holes"]:
            bits.append(f"{r['holes']} дыр периметром до {max(r['hole_perimeters'])} мм")
        if r["nonmanifold_edges"]:
            bits.append(f"{r['nonmanifold_edges']} non-manifold рёбер длиной до {r['nm_edge_max']} мм")
        if njunk:
            bits.append(f"{njunk} мусорных тел")
        if bits:
            return "МЕЛОЧЬ", ("; ".join(bits) + f" — всё мельче сопла {NOZZLE} мм. "
                              "Слайсер это заметает; ремонт рискует больше, чем чинит")
    if r["nonmanifold_edges"]:
        return "РУКАМИ", (f"{r['nonmanifold_edges']} non-manifold рёбер, самое длинное {r['nm_edge_max']} мм — "
                          f"поточечный ремонт их не берёт, лечит воксельная пересборка: "
                          f"meshfix.py --remesh {NOZZLE / 2:g}")
    if r["volume"] < 0:
        return "АВТОМАТ", "сетка вывернута наизнанку целиком — recalc_face_normals"
    bits = []
    if r["holes"]:            bits.append(f"{r['holes']} дыр")
    if r["bad_winding_edges"]: bits.append(f"{r['bad_winding_edges']} рёбер с рассогласованным обходом")
    if r["degenerate"]:       bits.append(f"{r['degenerate']} вырожденных граней")
    if r["duplicate_faces"]:  bits.append(f"{r['duplicate_faces']} дублей граней")
    return "АВТОМАТ", ", ".join(bits) or "мелочь"


def report(path, tol_rel=1e-6, as_json=False, quiet_clean=False):
    parts = read_any(path)
    results = []
    for label, V, F, indexed in parts:
        diag = float(np.linalg.norm(V.max(0) - V.min(0))) if len(V) else 1.0
        tol = max(diag * tol_rel, 1e-9)

        if indexed:
            # 3MF, OBJ, OFF: connectivity is the author's indices, and that is
            # the truth. Merging vertices here is forbidden - identical
            # coordinates on a UV seam get stitched into non-manifold edges
            # that do not exist in the file at all.

            r = analyse(V, F, tol)
            r["seam_noise"] = 0
            uniq_v = np.unique(V, axis=0)
            r["split_vertices"] = len(V) - len(uniq_v)
        else:
            # STL: no indices at all, every triangle carries its own copies of
            # the vertices. Merge bit-exactly first; if that already yields a
            # closed mesh there is no reason to merge by tolerance, which is
            # the most expensive step here.
            uniq_v, inv_v = np.unique(V, axis=0, return_inverse=True)
            r_exact = analyse(uniq_v, inv_v[F], tol)
            if r_exact.get("empty") or (r_exact["open_edges"] == 0
                                        and r_exact["nonmanifold_edges"] == 0
                                        and r_exact["bad_winding_edges"] == 0):
                r, r["seam_noise"] = r_exact, 0
            else:
                r = analyse(*weld(V, F, tol), tol=tol)
                r["seam_noise"] = r_exact["open_edges"] - r["open_edges"]
            r["split_vertices"] = 0
        r["label"], r["file"], r["indexed"] = label, path, indexed
        r["tol"] = tol
        r["verdict"], r["why"] = verdict(r)
        results.append(r)

    if as_json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return results

    for r in results:
        if quiet_clean and r["verdict"] == "ЧИСТО":
            continue
        head = f"{os.path.basename(path)}"
        if r["label"] not in ("stl", "obj", "off"):
            head += f"  [{r['label']}]"
        print(f"\n{head}")
        if r["label"] in SCALE_WARN:
            print("  " + SCALE_WARN[r["label"]])
        bodies = (f"тел {r['bodies_touching']}" if r['bodies_touching'] == r['bodies_separate']
                  else f"тел {r['bodies_touching']}, но по нормальным рёбрам {r['bodies_separate']} "
                       f"— куски слеплены касанием, а не объединены")
        print(f"  {r['faces_in']} треугольников, габарит {r['bbox'][0]}×{r['bbox'][1]}×{r['bbox'][2]} мм, {bodies}")
        bv = r.get("body_volumes")
        if bv and len(bv) > 1:
            big = bv[0][1]
            print("     тела (граней / мм³): " + ", ".join(f"{c}/{v:g}" for c, v in bv[:6])
                  + (f" ... ещё {r['bodies_separate']-len(bv[:6])}" if r['bodies_separate'] > 6 else ""))
            if r.get("junk_bodies"):
                print(f"     мусорных островков мельче тысячной доли главного тела: "
                      f"{r['junk_bodies']} из {r['bodies_separate']} — выбросить перед печатью")
        if r.get("empty"):
            print("  !! пусто"); continue
        mark = lambda ok: "OK " if ok else "!! "
        # small enough for the slicer to discard itself: raise no false alarm
        petty = "OK " if r["verdict"] == "ЧИСТО" else "!! "
        print(f"  {mark(r['open_edges']==0)}открытых рёбер (дырки): {r['open_edges']}"
              + (f" -> {r['holes']} петель, периметры {r['hole_perimeters'][:5]}" if r['holes'] else ""))
        print(f"  {mark(r['nonmanifold_edges']==0)}non-manifold рёбер (>=3 граней): {r['nonmanifold_edges']}")
        print(f"  {mark(r['bad_winding_edges']==0)}рёбер с рассогласованным обходом: {r['bad_winding_edges']}")
        print(f"  {petty if r['degenerate'] else 'OK '}вырожденных граней: {r['degenerate']}"
              + ("  (слайсер их игнорирует)" if r['degenerate'] and r['verdict'] == "ЧИСТО" else ""))
        print(f"  {petty if r['duplicate_faces'] else 'OK '}дублей граней: {r['duplicate_faces']}")
        print(f"  {mark(r['volume']>0)}объём {r['volume']:.1f} мм³ ({r['volume']/1000:.2f} см³)"
              + ("  (отрицательный: сетка вывернута)" if r['volume'] < 0 else ""))
        print(f"     эйлерова хар-ка {r['euler']}, тел {r['bodies_separate']}, "
              f"род (сквозных отверстий) {r['genus']}")
        if r["seam_noise"]:
            print(f"     склейка по допуску {r['tol']:.2e} мм убрала {r['seam_noise']} ложных открытых рёбер")
        if r.get("split_vertices"):
            print(f"     вершин-дублей по координатам: {r['split_vertices']} — это швы автора, "
                  "не дефект: связность берётся из индексов файла")
        print(f"  ВЕРДИКТ: {r['verdict']} — {r['why']}")
        if r["verdict"] in ("РУКАМИ", "АВТОМАТ"):
            worst = max([r.get("nm_edge_max", 0)] + (r.get("hole_perimeters") or [0]))
            if worst and worst < NOZZLE:
                print(f"     масштаб: самый крупный дефект {worst} мм при сопле {NOZZLE} мм. "
                      "Деталь, скорее всего, напечатается и так — чинить, если слайсер даст сбой")
    return results


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    flags = {a for a in sys.argv[1:] if a.startswith('--')}
    if not args:
        print(__doc__); sys.exit(1)
    bad = 0
    for p in args:
        try:
            rs = report(p, as_json='--json' in flags, quiet_clean='--only-broken' in flags)
            if any(r["verdict"] != "ЧИСТО" for r in rs):
                bad += 1
        except Exception as e:
            print(f"\n{os.path.basename(p)}\n  !! не прочитан: {e}")
            bad += 1
    sys.exit(1 if bad else 0)
