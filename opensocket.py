#!/usr/bin/env python3
"""Cut a socket into an OPEN body - a ragged generator mesh, no booleans.

A mesh straight out of an image-to-3D generator carries thousands of open
edges. Booleans refuse it, and a global repair (voxel remesh, shrink-wrap)
moves the surface by millimetres and wipes out exactly the small features a
socket is cut for. This tool does surgery on the surface instead: the skin
is cut along the socket outline, the faces inside are taken out, and a new
wall and floor are stitched into the cut edge. Nothing else in the mesh is
touched - its own open edges stay as they were.

    UV="uv run --quiet --with numpy --with scipy --with shapely --with triangle python"
    $UV tools/opensocket.py cut body.stl --circle 0,0,30 --radius 3 --axis 0,0,1 \
        --depth 2 --out body_socket.stl
    $UV tools/opensocket.py cut model.3mf --object Body --paint 4 --depth 1.5 \
        --paint-new 4 --out model_socket.3mf
    $UV tools/opensocket.py cut body.stl --insert plug.stl --axis 0.1,-0.9,0.4 \
        --gap 0.15 --out body_socket.stl

The outline is the INSERT's outline; the socket is that outline grown by
`--gap` on every side:

* `--circle x,y,z --radius r` - a circle around the axis through that point;
* `--paint N` (3MF only) - the faces painted with filament N, projected along
  the axis; several patches of the same colour are told apart with `--near`;
  `--smooth k` rounds the ragged triangle border with radius k;
* `--insert part.stl` - the insert's silhouette along the axis. Without
  `--depth` the floor goes `--gap-depth` below the insert's lowest point.

`--axis` is the extraction direction, pointing OUT of the body; for `--paint`
it defaults to the mean normal of the painted patch. `--depth` is how deep the
insert goes in below the LOWEST point of the rim; the floor is another
`--gap-depth` deeper. `--floor-normal` tilts the floor: the plane is placed so
that the shallowest point of the wall still has the full depth.

How it works
------------
1. Duplicate vertices within 1e-5 mm near the socket are welded: generator
   seams would otherwise tear the cut edge into pieces.
2. Every face crossing the prism "outline x axis" is split along it, and the
   inside pieces between the floor and `--above` over the highest rim point
   are removed. The surface above that window (a nose over an eye) is kept.
3. The cut edge is collected into one loop; where an open crack of the mesh
   crosses it, the pieces are joined across the crack.
4. The wall is triangulated in the unrolled prism side (position along the
   outline x height), so a rim that goes up and down is no problem, and its
   top row IS the cut edge: no new open edge appears along the seam.
5. The floor is triangulated inside the outline at the floor plane.

Faces keep their index: an untouched face stays at its position, and new
faces go into the freed positions first, the rest at the end. So the
per-face `paint_color` of a 3MF is carried over; a split face keeps its
colour when it was painted whole (a brush-subdivided face loses its
subdivision). New faces get filament `--paint-new`, by default the object's.

Coordinates are those of the mesh as stored: for a 3MF, the object's own
mesh file, before the placement on the plate.

Traps
-----
* **The mesh must be finer than the outline's curvature.** A face whose three
  corners are outside but whose edge crosses the outline twice is not split;
  the rim then follows the chord. Thin a 3-mm socket on a 2-mm mesh out of it.
* The report prints faces facing away from the axis inside the cut: if there
  are any, the floor went through the body's other side.
* Clearance: 0.05 per side for walls along the print Z on a fine layer,
  0.15 for a wall across the layers or a plug printed on its edge.
"""
import argparse, os, re, sys, zipfile
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

WELD = 1e-5        # mm: vertices closer than this near the socket are one vertex
BRIDGE = 1e-4      # mm: ends of the cut edge this close across a crack are welded
STEP = 0.04        # mm: spacing of new vertices along the outline
BAND = 0.5         # mm: the wall thickness is measured below this band under the rim
RAY = 10.0         # mm: a wall thicker than this counts as "more than"


def die(msg):
    print(msg)
    sys.exit(2)


def vec(s):
    v = np.array([float(x) for x in s.split(',')], float)
    if len(v) != 3:
        die(f"нужно три числа через запятую: {s!r}")
    return v


def unit(v):
    return v / np.linalg.norm(v)


def frame(a):
    """Right-handed [u, v, a]: u x v = a, so CCW in (u, v) is CCW seen from +a."""
    ref = np.array([0.31, 0.2, 0.93])
    if abs(ref @ a) > 0.95:
        ref = np.array([1.0, 0, 0])
    u = unit(np.cross(ref, a))
    return np.c_[u, np.cross(a, u), a]


# ------------------------------------------------------------------ input / output

def load_stl(path):
    data = open(path, 'rb').read()
    if data[:5].lower() == b'solid' and b'facet' in data[:2000]:
        P = np.array(re.findall(rb'vertex\s+(\S+)\s+(\S+)\s+(\S+)', data), float)
    else:
        n = int.from_bytes(data[80:84], 'little')
        rec = np.dtype([('n', '<f4', 3), ('v', '<f4', (3, 3)), ('a', '<u2')])
        P = np.frombuffer(data, rec, n, 84)['v'].reshape(-1, 3).astype(float)
    V, inv = np.unique(P, axis=0, return_inverse=True)
    return V, inv.reshape(-1, 3).astype(np.int64)


def save_stl(path, V, F):
    P = V[F].astype(np.float32)
    n = np.cross(P[:, 1] - P[:, 0], P[:, 2] - P[:, 0])
    n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-30)
    rec = np.zeros(len(F), np.dtype([('n', '<f4', 3), ('v', '<f4', (3, 3)), ('a', '<u2')]))
    rec['n'], rec['v'] = n, P
    with open(path, 'wb') as f:
        f.write(b'opensocket'.ljust(80, b' '))
        f.write(np.uint32(len(F)).tobytes())
        f.write(rec.tobytes())


def load_3mf(path, want):
    import paint
    z = zipfile.ZipFile(path)
    name, ext, ent = paint.pick_object(z, want)
    raw = z.read(ent).decode('utf-8')
    V = np.array([m.groups() for m in paint.VERT.finditer(raw)], float)
    tris = list(paint.TRI.finditer(raw))
    F = np.array([[int(m.group(1)), int(m.group(2)), int(m.group(3))] for m in tris], np.int64)
    return V, F, dict(name=name, extruder=ext, attrs=[m.group(4) for m in tris],
                      codes=[paint.code_of(m) for m in tris])


def save_3mf(src, dst, name, V, F, attrs):
    import uuid
    from partedit import Project, NS
    p = Project(src)
    inner = p.replace_mesh(name, V, F)          # face counts in the config
    out = [NS, f' <resources>\n  <object id="{inner}" p:UUID="{uuid.uuid4()}" type="model">\n'
           '   <mesh>\n    <vertices>\n']
    out += [f'     <vertex x="{x:.6f}" y="{y:.6f}" z="{z:.6f}"/>\n' for x, y, z in V]
    out.append('    </vertices>\n    <triangles>\n')
    out += [f'     <triangle v1="{a}" v2="{b}" v3="{c}"{t}/>\n' for (a, b, c), t in zip(F, attrs)]
    out.append('    </triangles>\n   </mesh>\n  </object>\n </resources>\n <build/>\n</model>\n')
    p.files[p.entry_of(p.object_id(name))] = ''.join(out).encode('utf-8')
    p.save(dst)


# ------------------------------------------------------------------ mesh helpers

def edge_stats(F, mask=None):
    """(open edges, edges of more than two faces); mask limits it to some faces."""
    if mask is not None:
        F = F[mask]
    e = np.sort(np.r_[F[:, [0, 1]], F[:, [1, 2]], F[:, [2, 0]]], axis=1)
    _, cnt = np.unique(e[:, 0] * (1 << 32) + e[:, 1], return_counts=True)
    return int((cnt == 1).sum()), int((cnt > 2).sum())


def flipped(F):
    """Directed edges met twice: two faces next to each other facing opposite ways."""
    e = np.r_[F[:, [0, 1]], F[:, [1, 2]], F[:, [2, 0]]]
    _, cnt = np.unique(e[:, 0] * (1 << 32) + e[:, 1], return_counts=True)
    return int((cnt > 1).sum())


def signed_volume(V, F, o):
    P = V[F] - o
    return float(np.einsum('ij,ij->i', P[:, 0], np.cross(P[:, 1], P[:, 2])).sum() / 6)


def weld_near(V, F, zone):
    """Weld vertex twins among `zone` vertices. Returns new F and degenerate faces."""
    from scipy.spatial import cKDTree
    zi = np.where(zone)[0]
    mp = np.arange(len(V))
    if len(zi) > 1:
        pairs = cKDTree(V[zi]).query_pairs(WELD, output_type='ndarray')
        for i, j in pairs:
            a, b = zi[i], zi[j]
            while mp[a] != a: a = mp[a]
            while mp[b] != b: b = mp[b]
            if a != b: mp[max(a, b)] = min(a, b)
        for k in zi:
            r = k
            while mp[r] != r: r = mp[r]
            mp[k] = r
        n = len(pairs)
    else:
        n = 0
    F = mp[F]
    deg = (F[:, 0] == F[:, 1]) | (F[:, 1] == F[:, 2]) | (F[:, 0] == F[:, 2])
    return F, deg, n


def rays(O, D, P, tmax):
    """Distance along each ray to the first triangle of P (Moller-Trumbore), inf if none."""
    best = np.full(len(O), np.inf)
    e1, e2 = P[:, 1] - P[:, 0], P[:, 2] - P[:, 0]
    chunk = max(1, int(4e6 // max(len(P), 1)))
    for s in range(0, len(O), chunk):
        o, d = O[s:s + chunk, None], D[s:s + chunk, None]
        pv = np.cross(d, e2[None])
        det = np.einsum('rfk,fk->rf', pv, e1)
        ok = np.abs(det) > 1e-12
        inv = np.where(ok, 1 / np.where(ok, det, 1), 0)
        tv = o - P[None, :, 0]
        uu = np.einsum('rfk,rfk->rf', tv, pv) * inv
        qv = np.cross(tv, e1[None])
        vv = np.einsum('rfk,rk->rf', qv, d[:, 0]) * inv
        tt = np.einsum('rfk,fk->rf', qv, e2) * inv
        hit = ok & (uu >= 0) & (vv >= 0) & (uu + vv <= 1) & (tt > 1e-6) & (tt < tmax)
        best[s:s + chunk] = np.where(hit, tt, np.inf).min(1)
    return best


# ------------------------------------------------------------------ outline

def ring_poly(P, step):
    """The exterior densified to `step`; its own corners are kept, none is cut."""
    import shapely
    from shapely.geometry import Polygon
    return Polygon(shapely.segmentize(P.exterior, step).coords)


def biggest(g):
    from shapely.geometry import MultiPolygon, GeometryCollection
    if isinstance(g, (MultiPolygon, GeometryCollection)):
        g = max((x for x in g.geoms if x.area > 0), key=lambda p: p.area)
    return g


def silhouette(L2, Fs):
    """Union of the projected triangles: the shape seen along the axis."""
    import shapely
    T = L2[Fs]
    d1, d2 = T[:, 1] - T[:, 0], T[:, 2] - T[:, 0]
    ar = np.abs(d1[:, 0] * d2[:, 1] - d1[:, 1] * d2[:, 0])
    T = T[ar > 1e-12]
    polys = shapely.polygons(np.concatenate([T, T[:, :1]], axis=1))
    # closing by 0.02 mm: cracks and seams of the mesh do not split the shape
    u = shapely.union_all(shapely.buffer(polys, 0.02, quad_segs=2))
    return biggest(u.buffer(-0.02, quad_segs=2))


def outline(a, V, F, meta):
    """(c, T, outline polygon in (u, v), floor reference or None, description)."""
    from shapely.geometry import Polygon
    if a.circle:
        if not a.axis or not a.radius:
            die("--circle требует --axis и --radius")
        c = vec(a.circle); T = frame(unit(vec(a.axis)))
        n = max(128, int(np.ceil(2 * np.pi * a.radius / 0.01)))
        t = np.arange(n) * 2 * np.pi / n
        return c, T, Polygon(np.c_[a.radius * np.cos(t), a.radius * np.sin(t)]), None, \
            f"окружность r {a.radius:.3f}"
    if a.insert:
        if not a.axis:
            die("--insert требует --axis")
        IV, IF = load_stl(a.insert)
        T = frame(unit(vec(a.axis))); c = IV.mean(0)
        L = (IV - c) @ T
        P = silhouette(L[:, :2], IF)
        return c, T, P, float(L[:, 2].min()), f"силуэт вставки {os.path.basename(a.insert)}"
    if a.paint is not None:
        if meta is None:
            die("--paint читает покраску, нужен 3MF")
        import paint
        from scipy.sparse import coo_matrix
        from scipy.sparse.csgraph import connected_components
        code = paint.FIL2CODE.get(a.paint)
        sel = np.where(np.array([cd == code for cd in meta['codes']]))[0]
        if not len(sel):
            die(f"граней, покрашенных целиком филаментом {a.paint}, нет")
        Fs = F[sel]
        from scipy.spatial import cKDTree
        vs = np.unique(Fs)
        pr = cKDTree(V[vs]).query_pairs(WELD, output_type='ndarray')
        m = coo_matrix((np.ones(3 * len(sel)), (np.repeat(np.arange(len(sel)), 3), Fs.ravel())),
                       shape=(len(sel), len(V))).tocsr()
        tw = coo_matrix((np.ones(len(pr)), (vs[pr[:, 0]], vs[pr[:, 1]])), shape=(len(V), len(V)))
        tw = (tw + tw.T + coo_matrix((np.ones(len(V)), (np.arange(len(V)), np.arange(len(V)))))).tocsr()
        m = m @ tw      # seam twins count as one vertex
        ncomp, lab = connected_components(m @ m.T, directed=False)
        P3 = V[Fs]; cen = P3.mean(1)
        nrm = np.cross(P3[:, 1] - P3[:, 0], P3[:, 2] - P3[:, 0])
        ar = np.linalg.norm(nrm, axis=1)
        if a.near:
            q = vec(a.near)
            k = lab[np.argmin(np.linalg.norm(cen - q, axis=1))]
        else:
            k = np.argmax(np.bincount(lab, weights=ar))
        pick = lab == k
        print(f"филамент {a.paint}: граней {len(sel)}, пятен {ncomp}; беру пятно из {pick.sum()} граней")
        ax = unit(vec(a.axis)) if a.axis else unit(nrm[pick].sum(0))
        c = (cen[pick] * ar[pick, None]).sum(0) / ar[pick].sum()
        T = frame(ax)
        P = silhouette(((V - c) @ T)[:, :2], Fs[pick])
        if a.smooth:
            P = biggest(P.buffer(a.smooth, join_style=1).buffer(-2 * a.smooth, join_style=1)
                        .buffer(a.smooth, join_style=1))
        return c, T, Polygon(P.exterior.coords), None, f"покраска филаментом {a.paint}"
    die("нужен контур: --circle, --paint или --insert")


# ------------------------------------------------------------------ the cut

def unroll(ext, X2):
    """Position along the outline of points lying on it."""
    import shapely
    return shapely.line_locate_point(ext, shapely.points(X2))


def on_ring(ext, t):
    import shapely
    return shapely.get_coordinates(shapely.line_interpolate_point(ext, t))


def cut_socket(V, F, S, c, T, a, floor_ref):
    import shapely
    from shapely.geometry import Polygon
    import triangle as tr
    wld = lambda L: c + L @ T.T
    ext = S.exterior; Lx = ext.length
    shapely.prepare(S)
    L = (V - c) @ T
    big = S.buffer(2.0); shapely.prepare(big)
    zone = shapely.contains_xy(big, L[:, 0], L[:, 1])
    ins = np.zeros(len(V), bool)
    ins[zone] = shapely.contains_xy(S, L[zone, 0], L[zone, 1])

    # edges crossing the prism side and the points where they cross it
    def crossings(Fc):
        E = np.r_[Fc[:, [0, 1]], Fc[:, [1, 2]], Fc[:, [2, 0]]]
        E = E[ins[E[:, 0]] != ins[E[:, 1]]]
        E = np.unique(np.sort(E, axis=1), axis=0)
        A = np.where(ins[E[:, 0]], E[:, 0], E[:, 1]); B = np.where(ins[E[:, 0]], E[:, 1], E[:, 0])
        lo, hi = np.zeros(len(E)), np.ones(len(E))
        pa, pb = L[A, :2], L[B, :2]
        for _ in range(42):
            m = (lo + hi) / 2
            q = pa + m[:, None] * (pb - pa)
            k = shapely.contains_xy(S, q[:, 0], q[:, 1])
            lo, hi = np.where(k, m, lo), np.where(k, hi, m)
        t = (lo + hi) / 2
        return E, V[A] + t[:, None] * (V[B] - V[A])

    s = ins[F]
    mixed = s.any(1) & ~s.all(1)
    E0, X0 = crossings(F[mixed])
    if not len(E0):
        die("контур не пересекает сетку: проверить центр и ось")
    L0 = (X0 - c) @ T
    # the rim: the crossings form chains, one per surface layer the prism goes through;
    # the outer layer is the chain seen from outside along the axis, nothing above it
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    from scipy.spatial import cKDTree
    t0 = unroll(ext, L0[:, :2])
    k0 = E0[:, 0] * (1 << 32) + E0[:, 1]; o0 = np.argsort(k0)
    Fm = F[mixed]
    Em = np.sort(np.stack([Fm[:, [0, 1]], Fm[:, [1, 2]], Fm[:, [2, 0]]], 1), axis=2)
    cr = ins[Em[..., 0]] != ins[Em[..., 1]]
    km = Em[..., 0] * (1 << 32) + Em[..., 1]
    idx = o0[np.clip(np.searchsorted(k0[o0], km), 0, len(k0) - 1)]
    pairs = [np.where(r)[0] for r in cr]
    ab = np.array([(idx[i, p[0]], idx[i, p[1]]) for i, p in enumerate(pairs) if len(p) == 2]).reshape(-1, 2)
    tw = cKDTree(X0).query_pairs(WELD, output_type='ndarray').reshape(-1, 2)
    ab = np.vstack([ab, tw])
    g = coo_matrix((np.ones(len(ab)), (ab[:, 0], ab[:, 1])), shape=(len(X0), len(X0)))
    _, comp = connected_components(g, directed=False)
    zf = zone[F].any(1)
    up = rays(X0 + T[:, 2] * 1e-4, np.tile(T[:, 2], (len(X0), 1)), V[F[zf]], 1e6)
    seen = np.bincount(comp, weights=~np.isfinite(up)) / np.bincount(comp)
    front = seen[comp] >= 0.5
    if not front.any():
        die("над всеми точками контура есть поверхность тела: ось смотрит внутрь?")
    nb = max(16, int(Lx / 0.25))
    b = np.minimum((t0 / Lx * nb).astype(int), nb - 1)
    top = np.full(nb, -np.inf); np.maximum.at(top, b[front], L0[front, 2])
    rim = L0[front]
    nf = unit(T.T @ unit(vec(a.floor_normal))) if a.floor_normal else np.array([0, 0, 1.0])
    if nf[2] < 0.2:
        die("--floor-normal почти поперёк оси: дно встаёт стенкой")
    g = -nf[:2] / nf[2]       # floor slope
    if a.depth is not None:
        h0 = (rim[:, 2] - rim[:, :2] @ g).min() - a.depth - a.gap_depth
    elif floor_ref is not None:
        h0 = floor_ref - a.gap_depth
    else:
        die("нужна --depth")
    floor_h = lambda X2: h0 + X2 @ g
    hmax = rim[:, 2].max() + a.above
    # generator seams: twins near the socket are welded, within its height window only
    Sv = shapely.get_coordinates(ext)
    wz = zone & (L[:, 2] > floor_h(Sv).min() - 1.0) & (L[:, 2] < hmax + 1.0)
    F, deg, nweld = weld_near(V, F, wz)
    live = ~deg
    s = ins[F]
    mixed = live & s.any(1) & ~s.all(1)
    gaps = int((~np.isfinite(top)).sum())
    print(f"кромка по пробному срезу: h {rim[:, 2].min():.3f}…{rim[:, 2].max():.3f} "
          f"(ось наружу, 0 = центр), бинов вдоль контура без пересечения {gaps} из {nb}")

    # split the faces crossing the prism inside the window
    hv = L[:, 2]
    fl_lo = floor_h(L[:, :2])
    lo_ok = (hv > fl_lo - 0.5)[F].any(1); hi_ok = (hv < hmax)[F].any(1)
    split = np.where(mixed & lo_ok & hi_ok)[0]
    E, X = crossings(F[split])
    key = E[:, 0] * (1 << 32) + E[:, 1]
    order = np.argsort(key); key = key[order]; nid = len(V) + order.argsort()
    V2 = np.vstack([V, X])

    def cut(i, j):
        k = min(i, j) * (1 << 32) + max(i, j)
        return nid[np.searchsorted(key, k)]

    keep_slot, keep_F, keep_par, rem_F = [], [], [], []   # kept faces: slot (-1 none), source face
    inside_new = []
    is_split = np.zeros(len(F), bool); is_split[split] = True
    for f in split:
        tri = F[f]; sv = ins[tri]
        odd = int(np.where(sv)[0][0]) if sv.sum() == 1 else int(np.where(~sv)[0][0])
        A, B, C = tri[odd], tri[(odd + 1) % 3], tri[(odd + 2) % 3]
        ab, ac = cut(A, B), cut(A, C)
        one = [(A, ab, ac)]; two = [(ab, B, C), (ab, C, ac)]
        inn, out = (one, two) if sv.sum() == 1 else (two, one)
        keep_F += out; keep_slot += [f] + [-1] * (len(out) - 1); keep_par += [f] * len(out)
        inside_new += [(p, f) for p in inn]
    L2 = (V2 - c) @ T

    def in_window(Fx):
        C = L2[Fx].mean(1)
        return (C[:, 2] > floor_h(C[:, :2])) & (C[:, 2] < hmax)

    whole = live & ~is_split & ins[F].all(1)
    wi = np.where(whole)[0]
    remw = wi[in_window(F[wi])] if len(wi) else wi
    removed_old = np.zeros(len(F), bool); removed_old[remw] = True
    IN = np.array([p for p, _ in inside_new], np.int64).reshape(-1, 3)
    inw = in_window(IN) if len(IN) else np.zeros(0, bool)
    for (p, f), r in zip(inside_new, inw):
        if r: rem_F.append(p)
        else: keep_F.append(p); keep_slot.append(-1); keep_par.append(f)
    rem_F = np.array(rem_F, np.int64).reshape(-1, 3)
    if len(remw): rem_F = np.vstack([F[remw], rem_F])
    untouched = np.where(live & ~is_split & ~removed_old)[0]
    Fk = np.vstack([F[untouched], np.array(keep_F, np.int64).reshape(-1, 3)])
    slot = np.r_[untouched, np.array(keep_slot, np.int64)]
    par = np.r_[untouched, np.array(keep_par, np.int64)]
    if not len(rem_F):
        die("внутри контура в окне глубины нет ни одной грани")
    # faces left inside the prism above the floor (would block the insert)
    Sin = S.buffer(-1e-3)
    Ck = L2[Fk].mean(1)
    allin = shapely.contains_xy(Sin, L2[Fk][..., 0].ravel(), L2[Fk][..., 1].ravel()).reshape(-1, 3).all(1)
    stay = allin & (Ck[:, 2] > floor_h(Ck[:, :2])) & (Ck[:, 2] < hmax)
    Pr = V2[rem_F]; nr = np.cross(Pr[:, 1] - Pr[:, 0], Pr[:, 2] - Pr[:, 0]) @ T[:, 2]
    back = int((nr < -1e-12).sum())

    # the cut edge: edges of kept faces whose reverse belongs to a removed face
    def dedges(FF): return np.r_[FF[:, [0, 1]], FF[:, [1, 2]], FF[:, [2, 0]]]

    def chains_of(Fk):
        er = dedges(rem_F); rk = set((er[:, 0] * (1 << 32) + er[:, 1]).tolist())
        ek = dedges(Fk)
        bd = ek[np.isin(ek[:, 1] * (1 << 32) + ek[:, 0], list(rk))]
        nxt, branch = {}, 0
        for i, j in bd.tolist():
            if i in nxt: branch += 1; continue
            nxt[i] = j
        prev = {j: i for i, j in nxt.items()}
        chains, seen = [], set()
        for s0 in [i for i in nxt if i not in prev] + list(nxt):
            if s0 in seen: continue
            lp = [s0]; seen.add(s0); x = nxt.get(s0)
            while x is not None and x != s0 and x not in seen:
                lp.append(x); seen.add(x); x = nxt.get(x)
            chains.append((lp, x == s0))
        return chains, branch

    chains, branch = chains_of(Fk)
    opened = [ch for ch, cl in chains if not cl]
    welded = 0
    if len(opened) > 1:     # chain ends meeting across a crack of the mesh: one vertex
        from scipy.spatial import cKDTree
        ends = np.array([V2[ch[-1]] for ch in opened]); starts = np.array([V2[ch[0]] for ch in opened])
        dd, jj = cKDTree(starts).query(ends)
        remap = {}
        for i, (dist, j) in enumerate(zip(dd, jj)):
            if dist < BRIDGE and i != j: remap[opened[j][0]] = opened[i][-1]
        if remap:
            mp = np.arange(len(V2)); mp[list(remap)] = list(remap.values())
            Fk = mp[Fk]; rem_F = mp[rem_F]
            welded = len(remap)
            chains, branch = chains_of(Fk)
    loops = [ch for ch, cl in chains if cl]
    opened = [ch for ch, cl in chains if not cl]
    bridges = []
    if opened:
        cyc = [opened.pop(0)]
        while opened:
            e = V2[cyc[-1][-1]]
            k = int(np.argmin([np.linalg.norm(V2[o[0]] - e) for o in opened]))
            bridges.append(float(np.linalg.norm(V2[opened[k][0]] - e))); cyc.append(opened.pop(k))
        bridges.append(float(np.linalg.norm(V2[cyc[0][0]] - V2[cyc[-1][-1]])))
        loops.append(sum(cyc, []))
    Lp = np.array(max(loops, key=len))
    extra = [np.array(l) for l in loops if len(l) != len(Lp) or not np.array_equal(l, Lp)]
    lp = L2[Lp]
    off = np.abs(shapely.distance(shapely.points(lp[:, :2]), ext))
    print(f"срез: сварено двойников {nweld}, вырожденных граней от сварки {int(deg.sum())}; "
          f"разрезано граней {len(split)}, удалено {len(rem_F)}")
    print(f"петля среза: вершин {len(Lp)}, на стенке {np.mean(off < 1e-4) * 100:.0f}% "
          f"(дальше всего {off.max():.4f} мм), лишних петель {len(extra)}, ветвлений {branch}"
          + (f", концов через щели сварено {welded}" if welded else "")
          + (f", перемычек через щели {len(bridges)}: самая длинная {max(bridges):.4f} мм" if bridges else ""))

    # wall: unrolled prism side, x along the outline, h along the axis
    i0 = int(np.argmin(lp[:, 2]))
    Lp = np.roll(Lp, -i0); lp = np.roll(lp, -i0, axis=0)
    tt = unroll(ext, lp[:, :2])
    tu = np.unwrap(tt / Lx * 2 * np.pi) / (2 * np.pi) * Lx
    sgn = 1.0 if tu[-1] >= tu[0] else -1.0
    x_ = sgn * (tu - tu[0]); tb = tt[0]
    span = x_.max() - x_.min()
    if not 0.9 * Lx < span < 1.1 * Lx + 1:
        die(f"петля среза не обходит контур: {span:.2f} из {Lx:.2f} мм — сетка рвётся у кромки")
    pos = lambda x: (tb + sgn * np.asarray(x)) % Lx
    n = len(Lp); hs = lp[0, 2]
    f_seam = float(floor_h(on_ring(ext, pos(0.0)))[0])
    seam = np.linspace(hs, f_seam, max(2, int(np.ceil((hs - f_seam) / 0.15)) + 1))[1:-1]
    nfl = int(np.ceil(Lx / 0.15)); fx = np.linspace(Lx, 0, nfl + 1)
    fl2 = on_ring(ext, pos(fx))
    pv = np.vstack([np.c_[x_, lp[:, 2]], [[Lx, hs]], np.c_[np.full(len(seam), Lx), seam],
                    np.c_[fx, floor_h(fl2)], np.c_[np.zeros(len(seam)), seam[::-1]]])
    m_ = len(pv)
    if not Polygon(pv).is_valid:
        from shapely.validation import explain_validity
        die(f"развёртка стенки самопересекается ({explain_validity(Polygon(pv))}): "
            f"дно выше кромки или петля складывается — меньше --depth или --above")
    TR = tr.triangulate(dict(vertices=pv, segments=np.c_[np.arange(m_), (np.arange(m_) + 1) % m_]), 'pYq20a0.02')
    tv, ttri = TR['vertices'], TR['triangles']
    if not np.allclose(tv[:m_], pv):
        die("триангуляция стенки сдвинула вершины границы")
    gid = np.full(len(tv), -1); gid[:n] = Lp; gid[n] = Lp[0]
    new3 = []; n0 = len(V2)

    def add(P3):
        new3.append(P3); return n0 + len(new3) - 1

    def to3(x, h):
        p = on_ring(ext, pos(x))[0]
        return wld(np.r_[p, h][None])[0]
    ks = n + 1; ns = len(seam)
    seamR = list(range(ks, ks + ns)); fl_ids = list(range(ks + ns, ks + ns + nfl + 1))
    seamL = list(range(ks + ns + nfl + 1, m_))
    for i, j in zip(seamR, seamL[::-1]):
        gid[i] = gid[j] = add(to3(0.0, pv[i, 1]))
    for k in fl_ids[:-1]: gid[k] = add(to3(pv[k, 0], pv[k, 1]))
    gid[fl_ids[-1]] = gid[fl_ids[0]]
    for k in range(m_, len(tv)): gid[k] = add(to3(tv[k, 0], tv[k, 1]))
    wall = gid[ttri]
    wall = wall[(wall[:, 0] != wall[:, 1]) & (wall[:, 1] != wall[:, 2]) & (wall[:, 0] != wall[:, 2])]
    es = set(map(tuple, dedges(wall).tolist()))
    if (int(Lp[0]), int(Lp[1])) in es: wall = wall[:, ::-1]
    V3 = np.vstack([V2, np.array(new3)])
    ring = np.array([gid[k] for k in fl_ids[:-1]])[::-1]
    ring2 = ((V3[ring] - c) @ T)[:, :2]
    nr_ = len(ring2)
    TF = tr.triangulate(dict(vertices=ring2, segments=np.c_[np.arange(nr_), (np.arange(nr_) + 1) % nr_]), 'pYq20')
    if not np.allclose(TF['vertices'][:nr_], ring2):
        die("триангуляция дна сдвинула вершины контура")
    inner = TF['vertices'][nr_:]
    fid = np.r_[ring, len(V3) + np.arange(len(inner))]
    V3 = np.vstack([V3, wld(np.c_[inner, floor_h(inner)])])
    flo = fid[TF['triangles']]
    esw = set(map(tuple, dedges(wall).tolist()))
    if any(e in esw for e in map(tuple, dedges(flo).tolist())): flo = flo[:, ::-1]

    # islands: small loops where the prism side meets the surface in a separate spot
    patches, left = [], 0
    for isl in extra:
        li = L2[isl]
        on = np.abs(shapely.distance(shapely.points(li[:, :2]), ext)) < 1e-4
        ti = np.unwrap(unroll(ext, li[:, :2]) / Lx * 2 * np.pi) / (2 * np.pi) * Lx
        P2 = np.c_[ti, li[:, 2]]
        if not on.all() or Polygon(P2).area < 1e-9 or not Polygon(P2).is_valid:
            left += 1; continue
        TI = tr.triangulate(dict(vertices=P2, segments=np.c_[np.arange(len(P2)), (np.arange(len(P2)) + 1) % len(P2)]), 'pYY')
        if len(TI['vertices']) != len(P2):
            left += 1; continue
        pf = isl[TI['triangles']]
        if (int(isl[0]), int(isl[1])) in set(map(tuple, dedges(pf).tolist())): pf = pf[:, ::-1]
        patches.append(pf)
    newF = np.vstack([wall, flo] + patches)
    info = dict(Fk=Fk, slot=slot, par=par, newF=newF, loop=Lp, floor_h=floor_h, h0=h0, g=g,
                stay=int(stay.sum()), back=back, left=left, patches=len(patches),
                wall=len(wall), floor=len(flo), split=split, rem=len(rem_F), sgn=sgn, ext=ext)
    return V3, info


# ------------------------------------------------------------------ command

def cmd_cut(a):
    from shapely.geometry.polygon import orient
    is3 = a.body.lower().endswith('.3mf')
    if is3:
        V, F, meta = load_3mf(a.body, a.object)
    else:
        V, F = load_stl(a.body); meta = None
    if a.out.lower().endswith('.3mf') != is3:
        die("выход того же рода, что вход: STL -> STL, 3MF -> 3MF")
    nF = len(F)
    c, T, P, floor_ref, what = outline(a, V, F, meta)
    S = orient(ring_poly(P.buffer(a.gap, join_style=1), STEP), 1)
    print(f"тело {os.path.basename(a.body)}: вершин {len(V)}, граней {nF}; контур — {what}, "
          f"ось [{', '.join(f'{x:.4f}' for x in T[:, 2])}], центр [{', '.join(f'{x:.3f}' for x in c)}]")
    print(f"контур вставки: площадь {P.area:.3f} мм², периметр {P.exterior.length:.3f} мм; "
          f"гнездо +{a.gap:.3f} на сторону: площадь {S.area:.3f} мм²")
    oe0 = edge_stats(F); fl0 = flipped(F)
    V3, I = cut_socket(V, F, S, c, T, a, floor_ref)
    Fk, slot, newF = I['Fk'], I['slot'], I['newF']

    # faces into positions: untouched keep theirs, new ones fill the freed ones
    par = I['par']                                   # source face of every kept face
    own = slot >= 0
    used = np.zeros(nF, bool); used[slot[own]] = True
    free = np.where(~used)[0]
    pending = np.vstack([Fk[~own], newF])
    pend_par = np.r_[par[~own], np.full(len(newF), -1)]
    n_fill = min(len(free), len(pending))
    out = np.zeros((nF + max(0, len(pending) - len(free)), 3), np.int64)
    origin = np.full(len(out), -1)                   # source face, -1 for a new face
    same = np.zeros(len(out), bool)                  # an old face at its own position
    out[slot[own]] = Fk[own]; origin[slot[own]] = par[own]; same[slot[own]] = True
    out[free[:n_fill]] = pending[:n_fill]; origin[free[:n_fill]] = pend_par[:n_fill]
    out[nF:] = pending[n_fill:]; origin[nF:] = pend_par[n_fill:]
    gone = free[n_fill:]
    if len(gone):
        keepm = np.ones(len(out), bool); keepm[gone] = False
        out, origin, same = out[keepm], origin[keepm], same[keepm]
        print(f"!! новых граней меньше, чем освободилось мест: {len(gone)} граней сдвинуты к началу")
    lost = 0
    if meta is not None:
        attrs, codes = meta['attrs'], meta['codes']
        import paint
        newattr = f' paint_color="{paint.FIL2CODE[a.paint_new]}"' if a.paint_new else ''
        cut_set = set(I['split'].tolist())
        fa = []
        for k in range(len(out)):
            o = origin[k]
            if o < 0:
                fa.append(newattr)
            elif o in cut_set and len(codes[o]) > 2:    # brush subdivision does not survive a cut
                fa.append(''); lost += 1
            else:
                fa.append(attrs[o])
    # drop vertices nobody uses (face order is not touched)
    usedv = np.zeros(len(V3), bool); usedv[out.ravel()] = True
    mp = np.cumsum(usedv) - 1
    Vo, Fo = V3[usedv], mp[out]

    # report
    oe1 = edge_stats(Fo)
    keepsame = int((same & (origin == np.arange(len(origin)))).sum())
    vol = signed_volume(V, F, c) - signed_volume(Vo, Fo, c)
    lpL = (V3[I['loop']] - c) @ T
    eng = lpL[:, 2] - I['floor_h'](lpL[:, :2])
    print(f"кромка гнезда: h {lpL[:, 2].min():.3f}…{lpL[:, 2].max():.3f}; дно h {I['h0']:.3f} в центре"
          + (f", наклон {np.degrees(np.arctan(np.linalg.norm(I['g']))):.1f}° к оси" if np.linalg.norm(I['g']) > 1e-9 else ", плоское"))
    print(f"глубина гнезда от нижней точки кромки до дна {eng.min():.3f} мм, от верхней {eng.max():.3f} мм")
    print(f"новых граней: стенка {I['wall']}, дно {I['floor']}, заплаток {I['patches']}; "
          f"в старые места {n_fill}, в конец {max(0, len(pending) - len(free))}")
    print(f"старых граней на своём месте {keepsame} из {nF}")
    print(f"объём ниши {vol:.3f} мм³")
    print(f"открытых рёбер до {oe0[0]}, после {oe1[0]}; рёбер больше чем на две грани до {oe0[1]}, после {oe1[1]}; "
          f"рёбер между гранями с несогласованной ориентацией до {fl0}, после {flipped(Fo)}")
    if oe1[0] > oe0[0]:
        print("!! открытых рёбер стало больше: шов не сошёлся")
    if I['stay']:
        print(f"!! внутри гнезда выше дна осталось граней {I['stay']}: мешают вставке (поднять --above?)")
    if I['back']:
        print(f"!! в вырез попало граней, смотрящих против оси: {I['back']} — дно пробило тело насквозь")
    if I['left']:
        print(f"!! лишних петель не закрыто: {I['left']} (их открытые рёбра остались)")
    wall_report(a, V3, I, Vo, Fo, origin, c, T)
    if meta is not None:
        if lost:
            print(f"граней, разрезанных по кисти (покраска с подразбиением), стали цветом объекта: {lost}")
        save_3mf(a.body, a.out, meta['name'], Vo, Fo, fa)
    else:
        save_stl(a.out, Vo, Fo)
    print(f"записано: {a.out}")


def wall_report(a, V3, I, Vo, Fo, origin, c, T):
    """Thickness of material behind the floor and behind the wall, by rays."""
    import shapely
    ext = I['ext']
    old = Fo[origin >= 0]
    P = Vo[old]; C = (P.mean(1) - c) @ T
    sc = ext.bounds
    near = (C[:, 0] > sc[0] - RAY) & (C[:, 0] < sc[2] + RAY) & (C[:, 1] > sc[1] - RAY) & (C[:, 1] < sc[3] + RAY)
    P = P[near]
    lpL = (V3[I['loop']] - c) @ T
    Lx = ext.length
    # floor: rays along -axis from a grid inside the outline
    from shapely.geometry import Polygon
    S = Polygon(ext.coords)
    xs = np.linspace(sc[0], sc[2], 25); ys = np.linspace(sc[1], sc[3], 25)
    G = np.array([(x, y) for x in xs for y in ys])
    G = G[shapely.contains_xy(S.buffer(-0.05), G[:, 0], G[:, 1])]
    Of = c + np.c_[G, I['floor_h'](G)] @ T.T
    df = rays(Of, np.tile(-T[:, 2], (len(Of), 1)), P, RAY)
    # wall: rays outward across the axis, below the rim band
    t = np.arange(0, Lx, max(0.25, Lx / 160))
    p = shapely.get_coordinates(shapely.line_interpolate_point(ext, t))
    q = shapely.get_coordinates(shapely.line_interpolate_point(ext, (t + 0.01) % Lx))
    tg = q - p; tg /= np.linalg.norm(tg, axis=1, keepdims=True)
    nrm = np.c_[tg[:, 1], -tg[:, 0]]                  # outward for a CCW outline
    tl = shapely.line_locate_point(ext, shapely.points(lpL[:, :2]))
    o_ = np.argsort(tl)
    rimh = np.interp(t, tl[o_], lpL[o_, 2], period=Lx)
    Ow, Dw, Hw = [], [], []
    for k in range(len(t)):
        f0 = float(I['floor_h'](p[k][None])[0])
        for h in np.arange(f0 + 0.1, rimh[k] - BAND, 0.25):
            Ow.append(c + np.r_[p[k], h] @ T.T); Dw.append(T[:, :2] @ nrm[k]); Hw.append(rimh[k] - h)
    dw = rays(np.array(Ow), np.array(Dw), P, RAY) if Ow else np.zeros(0)
    fmt = lambda d: f"{d.min():.2f} мм" if len(d) and np.isfinite(d.min()) else f"больше {RAY:.0f} мм"
    print(f"толщина под дном: min {fmt(df)} (лучей {len(df)}, без попадания {int(np.isinf(df).sum())})")
    if len(dw):
        k = int(np.argmin(dw))
        print(f"толщина за стенкой глубже {BAND} мм под кромкой: min {fmt(dw)}"
              + (f" на {Hw[k]:.2f} мм под кромкой" if np.isfinite(dw[k]) else "")
              + f" (лучей {len(dw)}, без попадания {int(np.isinf(dw).sum())})")
    else:
        print(f"толщина за стенкой: стенка не глубже {BAND} мм, не мерялась")


def main():
    ap = argparse.ArgumentParser(description="Гнездо в незамкнутом теле: хирургия поверхности без булевых.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("cut", help="вырезать гнездо")
    c.add_argument("body", help="тело: .stl или .3mf")
    c.add_argument("--out", required=True, help="куда писать: .stl для STL, .3mf для 3MF")
    c.add_argument("--object", help="объект 3MF по имени или номеру")
    c.add_argument("--circle", help="x,y,z центра окружности")
    c.add_argument("--radius", type=float, help="радиус вставки, мм")
    c.add_argument("--paint", type=int, help="контур — пятно граней этого филамента (3MF)")
    c.add_argument("--near", help="x,y,z: какое из пятен покраски брать")
    c.add_argument("--smooth", type=float, default=0.0, help="скругление контура покраски, мм")
    c.add_argument("--insert", help="STL вставки: контур — её силуэт вдоль оси")
    c.add_argument("--axis", help="ось вынимания, наружу из тела")
    c.add_argument("--depth", type=float, help="насколько вставка уходит ниже нижней точки кромки, мм")
    c.add_argument("--floor-normal", dest="floor_normal", help="нормаль наклонного дна; по умолчанию ось")
    c.add_argument("--gap", type=float, default=0.05, help="зазор на сторону, мм (0.05 / 0.10 / 0.15)")
    c.add_argument("--gap-depth", dest="gap_depth", type=float, default=0.05, help="зазор под дном, мм")
    c.add_argument("--above", type=float, default=2.0,
                   help="поверхность внутри контура убирается до стольких мм над верхом кромки")
    c.add_argument("--paint-new", dest="paint_new", type=int, help="филамент новых граней (3MF)")
    a = ap.parse_args()
    if a.cmd == "cut":
        cmd_cut(a)


if __name__ == "__main__":
    main()
