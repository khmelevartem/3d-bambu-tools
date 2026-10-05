#!/usr/bin/env python3
"""Cut painted relief out of an organic body as separate inlay parts.

For a figurine printed in colours and glued: hair, a beard, a moustache, a ring
in an ear - each painted zone becomes a part that slides out of its pocket along
one straight direction, and the body keeps that pocket with a clearance.
solid_cut.py cuts with planes and prisms where the border is a line; this tool
is for zones whose border wanders over a curved surface and whose cut floor has
to stay smooth.

    uv run --with numpy --with scipy --with trimesh --with manifold3d \\
        --with shapely --with mapbox_earcut --with scikit-image \\
        --with pymeshfix --with rtree python tools/relief_cut.py <command> ...

Input is the paint as `paint.py parse` writes it (`V`, `F`, `lab` in an npz);
`--labels file.npy` replaces `lab` with any per-face array, for zones that share
one filament but are separate parts (hair and beard). `--body` is the solid to
cut, when it is not the painted mesh itself - for instance a body that already
has other pockets. Its faces count as original surface where all three vertices
coincide with vertices of the painted mesh (or of `--surface`); the rest are
walls of earlier pockets and belong to the body.

Commands
--------
sheet   a zone lying ON the surface: a hair cap, a beard. The cut floor is one
        smooth sheet, a height field along the removal direction, the flattest
        surface between two bounds: the part keeps its paint at full thickness
        (`--edge`) and the body keeps its own (`--margin`). Both thin out to
        zero at the zone border under the angle `--theta`, so the sheet comes
        up to the surface exactly at the paint line. `--plane` pins the body's
        side of the sheet to a plane - a flat platform for printing the body
        upside down on it.

drape   a relief that hangs over the surface: a moustache, a fringe. Seen along
        the removal direction, every column is either attached (relief grows
        straight out of the skin) or overhanging (relief, then air, then skin).
        The plug is a prism on the ATTACHMENT line, not the silhouette, so the
        overhang stays on the part and takes no skin with it. The floor is flat
        and square to the removal direction. Attached relief outside the plug
        is cut along the skin continued harmonically under it.

ring    a ring or a tunnel: an ear gauge, a nose ring. The painted faces are
        measured - axis from the normals, bore from a cylinder fit, lip from the
        profile - and rebuilt as a body of revolution: a tube with a torus lip
        tangent to the bore. It is one piece inserted from the free end; the
        pocket sweeps the lip out along the axis. `--same` makes several rings
        one identical part.

check   parts that must not touch: pairwise intersections, a sweep of each part
        with a direction (`part.stl@x,y,z`) along it, the volume sum against a
        source, and where each painted zone ended up.

    relief_cut.py sheet --paint p.npz --zone 3 --axis 0,0.5,0.87 --plane 48.3 --out w/hair
    relief_cut.py drape --body head.stl --paint p.npz --labels parts.npy --zone 3 \\
                        --axis 0,-0.77,-0.64 --out w/moustache
    relief_cut.py ring  --paint p.npz --zone 1 --at 55,114,21 --at 94,110,21 --same --lip 3.2 --out w/gauge
    relief_cut.py check w/head_rest.stl w/hair_part.stl@0,0.5,0.87 --paint p.npz --zones 3

Choosing the removal direction is a separate question - see the 3mf-paint skill,
references/split-to-parts.md. Every command prints how much of the zone's paint
came out on the part and how much stayed on the body.
"""
import sys, os, json, argparse
import numpy as np

GAP_AIR = 0.08     # mm: air under an overhang thinner than this counts as attached
NEAR = 0.05        # mm: paint closer than this to a body belongs to it
RING_NEAR = 0.3    # mm: the same for a ring rebuilt as an ideal body of revolution


# ------------------------------------------------------------------ common

def die(msg):
    print(msg)
    sys.exit(2)


def vec(s):
    v = np.array([float(x) for x in s.split(',')])
    return v / np.linalg.norm(v)


def frame(a):
    ref = np.array([0.31, 0.2, 0.93])
    if abs(ref @ a) > 0.95:
        ref = np.array([1.0, 0, 0])
    e1 = np.cross(ref, a); e1 /= np.linalg.norm(e1)
    return e1, np.cross(a, e1)


def tri_area(P):
    return 0.5 * np.linalg.norm(np.cross(P[:, 1] - P[:, 0], P[:, 2] - P[:, 0]), axis=1)


def sample(P, ar, n, rng):
    fi = rng.choice(len(P), int(n), p=ar / ar.sum())
    r1, r2 = rng.random(len(fi)), rng.random(len(fi))
    m = r1 + r2 > 1; r1[m], r2[m] = 1 - r1[m], 1 - r2[m]
    return fi, P[fi, 0] + r1[:, None] * (P[fi, 1] - P[fi, 0]) + r2[:, None] * (P[fi, 2] - P[fi, 0])


def load_paint(a):
    z = np.load(a.paint, allow_pickle=True)
    V, F = np.asarray(z['V'], float), np.asarray(z['F'], np.int64)
    lab = np.load(a.labels) if getattr(a, 'labels', None) else np.asarray(z['lab'])
    if len(lab) != len(F):
        die(f"меток {len(lab)}, а граней {len(F)}: --labels не от этой сетки")
    return V, F, lab


def load_body(a, V, F):
    import trimesh
    if getattr(a, 'body', None):
        b = trimesh.load(a.body, force='mesh')
    else:
        b = trimesh.Trimesh(V, F)
    if not b.is_watertight:
        die("тело не замкнуто — сначала починить (mesh-repair), булевы операции на нём не сойдутся")
    return b


def original_faces(body, V):
    """Faces of the body that are the painted surface, not walls of old pockets."""
    from scipy.spatial import cKDTree
    d, _ = cKDTree(V).query(body.vertices, workers=-1)
    return (d[body.faces] < 1e-4).all(1)


def to_mf(m):
    import manifold3d as mf
    return mf.Manifold(mf.Mesh(vert_properties=np.asarray(m.vertices, np.float32),
                               tri_verts=np.asarray(m.faces, np.uint32)))


def mf_from(V, F):
    import manifold3d as mf
    m = mf.Manifold(mf.Mesh(vert_properties=V.astype(np.float32), tri_verts=F.astype(np.uint32)))
    if m.volume() <= 0 or m.status() != mf.Error.NoError:
        m = mf.Manifold(mf.Mesh(vert_properties=V.astype(np.float32),
                                tri_verts=F[:, ::-1].astype(np.uint32)))
    return m


def prism(poly, a, e1, e2, s0, s1):
    """Exact prism along a over a shapely (Multi)Polygon, from s0 to s1."""
    import trimesh, mapbox_earcut as earcut
    from shapely.geometry.polygon import orient
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from solid_cut import _prism
    out = None
    for g in getattr(poly, 'geoms', [poly]):
        T = _prism(g, a, e1, e2, s0, s1, orient, earcut, np)
        t = trimesh.Trimesh(**trimesh.triangles.to_kwargs(T))
        t.merge_vertices()
        m = mf_from(np.asarray(t.vertices), np.asarray(t.faces))
        out = m if out is None else out + m
    return out


def block(uu, vv, h, top, a, e1, e2, taubin=0, fixed=None, cap=None):
    """Solid between the height field h and the flat top, along a."""
    import trimesh
    ny, nx = h.shape
    idx = np.arange(ny * nx).reshape(ny, nx)
    base = uu.ravel()[:, None] * e1 + vv.ravel()[:, None] * e2
    V = np.r_[base + h.ravel()[:, None] * a, base + top * a]
    nb = ny * nx
    i0, i1, i2, i3 = idx[:-1, :-1].ravel(), idx[:-1, 1:].ravel(), idx[1:, 1:].ravel(), idx[1:, :-1].ravel()
    F = [np.c_[i0, i2, i1], np.c_[i0, i3, i2], np.c_[i0 + nb, i1 + nb, i2 + nb], np.c_[i0 + nb, i2 + nb, i3 + nb]]
    ring = np.r_[idx[0, :], idx[1:, -1], idx[-1, -2::-1], idx[-2:0:-1, 0]]
    r2 = np.roll(ring, -1)
    F += [np.c_[ring, r2, r2 + nb], np.c_[ring, r2 + nb, ring + nb]]
    F = np.vstack(F)
    if taubin:
        # the grid diagonals leave a saw on steep parts of the sheet: smooth the
        # bottom surface in 3D, with its rim and any pinned plateau held still
        tm = trimesh.Trimesh(V, F, process=False)
        V0 = V.copy()
        trimesh.smoothing.filter_taubin(tm, lamb=0.5, nu=0.53, iterations=taubin)
        V = np.array(tm.vertices); V[nb:] = V0[nb:]
        hold = np.zeros(nb, bool); hold[ring] = True
        if fixed is not None:
            hold |= fixed.ravel()
        V[:nb][hold] = V0[:nb][hold]
    if cap is not None:
        n, d = cap
        over = V[:nb] @ n - d
        V[:nb] -= np.maximum(over, 0)[:, None] * n
    return mf_from(V, F)


def pieces(m):
    ps = sorted(m.decompose(), key=lambda q: -q.volume())
    return ps[0], [q.volume() for q in ps[1:]]


def save(m, path):
    """Manifold -> watertight single-body STL (simplify, then pymeshfix)."""
    import trimesh, pymeshfix
    for tol in (0.003, 0.005, 0.01):
        mm = m.simplify(tol).to_mesh()
        v, f = pymeshfix.clean_from_arrays(np.ascontiguousarray(mm.vert_properties[:, :3], np.float64),
                                           np.ascontiguousarray(mm.tri_verts, np.int32),
                                           remove_smallest_components=False)
        t = trimesh.Trimesh(v, f)
        if t.is_watertight and t.body_count == 1:
            break
    t.export(path)
    return t


def contour_poly(mask, sig, U0, V0, px, largest=True):
    """Smooth vector outline of a raster mask (with holes when largest=False)."""
    from scipy import ndimage
    from skimage import measure
    from shapely.geometry import Polygon
    from shapely.geometry.polygon import orient
    cs = measure.find_contours(np.pad(ndimage.gaussian_filter(mask.astype(float), sig), 2), 0.5)
    if not cs:
        return None
    polys = []
    for c in sorted(cs, key=len, reverse=True):
        c = c - 2
        xy = np.c_[U0 + (c[:, 1] + .5) * px, V0 + (c[:, 0] + .5) * px]
        seg = np.linalg.norm(np.diff(xy, axis=0), axis=1); L = seg.sum()
        if L < 1.0:
            continue
        t = np.r_[0, np.cumsum(seg)]; tt = np.linspace(0, L, int(L / 0.05), endpoint=False)
        xy = np.c_[np.interp(tt, t, xy[:, 0]), np.interp(tt, t, xy[:, 1])]
        xy = np.c_[ndimage.gaussian_filter1d(xy[:, 0], 5, mode='wrap'),
                   ndimage.gaussian_filter1d(xy[:, 1], 5, mode='wrap')]
        p = Polygon(xy).buffer(0)
        if not p.is_empty:
            polys.append(p)
        if largest:
            break
    if not polys:
        return None
    g = polys[0]
    for p in polys[1:]:
        g = g.symmetric_difference(p)          # even-odd: inner outlines are holes
    g = g.simplify(0.005)
    if g.geom_type == 'Polygon':
        return orient(g, 1.0)
    return g


def account(V, F, lab, zones, bodies, rng, n=4000, faces=None, near=NEAR):
    """Paint of each zone: area near each body, and what fell into the gaps.
    `faces` narrows a zone to these faces (one ring out of all the black)."""
    import trimesh
    P = V[F]; ar = tri_area(P)
    for k in zones:
        idx = np.flatnonzero(lab == k) if faces is None else faces
        if not len(idx):
            print(f"  зона {k}: в покраске нет"); continue
        A = ar[idx].sum()
        _, X = sample(P[idx], ar[idx], n, rng)
        D = np.stack([trimesh.proximity.closest_point(b, X)[1] for b in bodies.values()], 1)
        best = D.argmin(1); near = D.min(1) < near
        s = ', '.join(f"{nm} {A * np.mean(near & (best == i)):.1f}" for i, nm in enumerate(bodies))
        what = f"зоны {k}" if faces is None else f"колец в пределах {near} мм"
        print(f"  краска {what} ({A:.1f} мм²): {s}, в зазоре {A * np.mean(~near):.1f} мм²")


def write_parts(part, rest, out, what):
    p, chips = pieces(part)
    r, scraps = pieces(rest)
    print(f"{what}: {p.volume():.1f} мм³" + (f", осколки {[round(c, 3) for c in chips[:5]]}" if chips else ""))
    print(f"тело: {r.volume():.1f} мм³" + (f", обрывки {[round(c, 3) for c in scraps[:5]]}" if scraps else ""))
    tp = save(p, out + '_part.stl'); tr = save(r, out + '_rest.stl')
    print(f"записано {out}_part.stl (замкнута {'да' if tp.is_watertight else 'НЕТ'}) "
          f"и {out}_rest.stl (замкнуто {'да' if tr.is_watertight else 'НЕТ'})")
    return tp, tr


# ------------------------------------------------------------------ sheet

def cmd_sheet(a):
    import trimesh
    from scipy import ndimage
    from scipy.spatial import cKDTree
    rng = np.random.default_rng(1)
    V, F, lab = load_paint(a); body = load_body(a, V, F)
    ax = vec(a.axis); e1, e2 = frame(ax)
    P = V[F]; ar = tri_area(P)
    zone = lab == a.zone
    if not zone.any():
        die(f"зоны {a.zone} в покраске нет")
    cz = P[zone].reshape(-1, 3); uvz = np.c_[cz @ e1, cz @ e2]
    lo, hi = uvz.min(0) - 3, uvz.max(0) + 3

    def inreg(X):
        q = np.c_[X @ e1, X @ e2]
        return np.all((q > lo) & (q < hi), 1)

    # paint samples for labelling, body samples for the bounds
    pf = np.flatnonzero(inreg(P.mean(1)))
    fw, Xw = sample(P[pf], ar[pf], ar[pf].sum() * a.density * 1.2, rng)
    Lw = lab[pf][fw] == a.zone; kw = cKDTree(Xw)
    orig = original_faces(body, trimesh.load(a.surface).vertices if a.surface else V)
    bf = np.flatnonzero(inreg(body.triangles_center))
    fi, X = sample(body.triangles[bf], body.area_faces[bf], body.area_faces[bf].sum() * a.density, rng)
    N = body.face_normals[bf][fi]; og = orig[bf][fi]
    _, ii = kw.query(X, workers=-1)
    isK = og & Lw[ii]
    Areg = body.area_faces[bf].sum()
    print(f"ось снятия {np.round(ax, 3)}; точек {len(X)}, на стенках старых ниш {(~og).sum()}")

    # smoothed border: share of the zone in a ball of radius rho, counting only
    # neighbours facing the same way (not across a thin gap)
    nsub = min(len(X), int(Areg * 48 / (np.pi * a.rho ** 2)))
    sub = rng.choice(len(X), nsub, replace=False); ks = cKDTree(X[sub])
    dd, jj = ks.query(X[sub], k=min(64, nsub), distance_upper_bound=a.rho, workers=-1)
    ok = np.isfinite(dd); jj = np.where(ok, jj, 0)
    same = ok & (np.einsum('ijk,ik->ij', N[sub][jj], N[sub]) > 0)
    fs = (isK[sub][jj] & same).sum(1) / np.maximum(same.sum(1), 1)
    _, jj = ks.query(X, k=4, workers=-1); f = fs[jj].mean(1)
    inK = (f > 0.5) & og
    bd = sub[np.abs(fs - 0.5) < 0.2]
    dist = cKDTree(X[bd]).query(X, workers=-1)[0] if len(bd) else np.full(len(X), np.inf)
    print(f"деталь после сглаживания границы: {inK.mean() * body.area_faces[bf].sum():.0f} мм² "
          f"(по краске {isK.mean() * body.area_faces[bf].sum():.0f})")

    # bounds per column
    px = a.px
    U0, V0 = lo; W, Hh = ((hi - lo) / px).astype(int) + 1
    gy, gx = np.mgrid[0:Hh, 0:W]; uu = U0 + (gx + .5) * px; vv = V0 + (gy + .5) * px

    def cell(p):
        i = np.clip(((p @ e1 - U0) / px).astype(int), 0, W - 1)
        j = np.clip(((p @ e2 - V0) / px).astype(int), 0, Hh - 1)
        return j * W + i
    Uc = np.full(W * Hh, np.inf); Lc = np.full(W * Hh, -np.inf)
    tan = np.tan(np.radians(a.theta))
    te = np.minimum(a.edge, dist * tan); me = np.minimum(a.margin, dist * tan)
    for frac in (0, 0.25, 0.5, 0.75, 1.0):
        q = X[inK] - (te[inK] * frac)[:, None] * N[inK]; np.minimum.at(Uc, cell(q), q @ ax)
        q = X[~inK] - (me[~inK] * frac)[:, None] * N[~inK]; np.maximum.at(Lc, cell(q), q @ ax)
    Uc = Uc.reshape(Hh, W); Lc = Lc.reshape(Hh, W)

    # bounds without sampling noise: U only goes down, L only up
    rr = max(1, int(a.env / px))

    def env(A, low):
        B = np.where(np.isfinite(A), A, 1e4 if low else -1e4)
        B = ndimage.grey_erosion(B, size=2 * rr + 1) if low else ndimage.grey_dilation(B, size=2 * rr + 1)
        G = ndimage.gaussian_filter(B, a.env / 2 / px)
        G = np.minimum(G, A) if low else np.maximum(G, A)
        return np.where(np.isfinite(A), G, A)
    Uc = env(Uc, True); Lc = env(Lc, False)
    conf = Lc > Uc
    print(f"конфликтных столбцов {conf.sum() * px * px:.2f} мм² проекции — там краска детали остаётся на теле")

    # flat platform: the body side of the sheet pinned to the plane x.n = d
    pn = vec(a.plane_normal)
    if pn @ ax < 0:
        pn = -pn
    if (a.plane is not None or a.plane_scan) and pn @ ax < 0.2:
        die("плоскость площадки почти параллельна оси снятия — прибить к ней лист нельзя")
    sgn = 1.0 if vec(a.plane_normal) @ ax >= 0 else -1.0

    def plane_s(d):
        d = d * sgn
        return (d - uu * (e1 @ pn) - vv * (e2 @ pn)) / (ax @ pn)

    def flat(d):
        hp = plane_s(d); okp = (hp >= Lc) & (hp <= Uc) & np.isfinite(Uc)
        lb, n = ndimage.label(okp)
        if n == 0:
            return hp, okp
        big = lb == np.bincount(lb.ravel())[1:].argmax() + 1
        return hp, ndimage.binary_fill_holes(big) & okp
    if a.plane_scan:
        for d in a.plane_scan.split(','):
            _, okp = flat(float(d))
            print(f"  плоскость {float(d):g}: площадка {okp.sum() * px * px / (ax @ pn):.0f} мм²")
        return 0
    PIN = None
    Lx = Lc.copy(); Ux = np.where(conf, Lc, Uc)
    if a.plane is not None:
        hp, okp = flat(a.plane)
        okp = ndimage.binary_erosion(okp, iterations=int(0.5 / px))
        PIN = (hp, okp)
        print(f"площадка на плоскости {a.plane:g}: {okp.sum() * px * px / (ax @ pn):.0f} мм² в проекции")
        Ux = np.minimum(Ux, hp); Lx = np.minimum(Lx, Ux)
        Lx = np.where(okp, hp, Lx); Ux = np.where(okp, hp, Ux)

    # membrane between the bounds, coarse to fine
    def pool(A, k, fn):
        h_, w_ = A.shape; h2, w2 = -(-h_ // k), -(-w_ // k)
        B = np.full((h2 * k, w2 * k), np.inf if fn is np.min else -np.inf); B[:h_, :w_] = A
        return fn(fn(B.reshape(h2, k, w2, k), axis=3), axis=1)
    h = None
    for k, it in ((16, 4000), (8, 3000), (4, 2000), (2, 1500), (1, 1500)):
        Lf = pool(Lx, k, np.max); Uf = pool(Ux, k, np.min)
        if h is None:
            h = np.where(np.isfinite(Uf), Uf, np.where(np.isfinite(Lf), Lf, np.nan))
            h = np.where(np.isnan(h), np.nanmean(h), h)
        else:
            h = ndimage.zoom(h, 2, order=1)[:Lf.shape[0], :Lf.shape[1]]
            if h.shape != Lf.shape:
                h = np.pad(h, ((0, Lf.shape[0] - h.shape[0]), (0, Lf.shape[1] - h.shape[1])), mode='edge')
        for _ in range(it):
            hp_ = np.pad(h, 1, mode='edge')
            h = 0.25 * (hp_[:-2, 1:-1] + hp_[2:, 1:-1] + hp_[1:-1, :-2] + hp_[1:-1, 2:])
            h = np.minimum(np.maximum(h, Lf), Uf)
    for _ in range(4):
        h = np.minimum(np.maximum(ndimage.gaussian_filter(h, a.smooth / px), Lx - a.tol), Ux + a.tol)
    if PIN is not None:
        h = np.where(PIN[1], PIN[0], h)            # the plateau exactly in the plane

    # footprint of the part along a -> prism walls
    fp = np.zeros(W * Hh, bool); fp[cell(X[inK])] = True; fp = fp.reshape(Hh, W)
    r = int(0.4 / px)
    fp = ndimage.binary_opening(ndimage.binary_fill_holes(ndimage.binary_closing(fp, iterations=r)), iterations=r)
    poly = contour_poly(fp, a.contour / px, U0, V0, px, largest=False)
    if poly is None:
        die("след детали пуст")
    print(f"контур стенок {poly.length:.1f} мм, площадь следа {poly.area:.1f} мм²")

    gyv, gxv = np.gradient(h, px)
    hp = h - a.gap * np.sqrt(1 + gxv ** 2 + gyv ** 2)
    S = X @ ax
    top = float(max(np.nanmax(h), S.max()) + 5)
    s0 = float(np.nanmin(hp) - 2)
    cap = capp = None
    if a.plane is not None:
        cap = (pn, a.plane * sgn); capp = (pn, a.plane * sgn - a.gap)
    pin = PIN[1] if PIN is not None else None
    C = block(uu, vv, h, top, ax, e1, e2, a.taubin, pin, cap) ^ prism(poly, ax, e1, e2, s0, top - 0.5)
    Cp = block(uu, vv, hp, top, ax, e1, e2, a.taubin, pin, capp) ^ \
        prism(poly.buffer(a.wall, join_style=1), ax, e1, e2, s0, top - 0.5)
    H = to_mf(body)
    tp, tr = write_parts((H ^ C) ^ Cp, H - Cp, a.out, "деталь")
    if a.plane is not None:
        nrm = tr.face_normals @ pn; d = tr.triangles_center @ pn - (a.plane * sgn - a.gap)
        flatA = tr.area_faces[(nrm > 0.9999) & (np.abs(d) < 0.01)].sum()
        print(f"площадка тела под печать: {flatA:.0f} мм²")
    account(V, F, lab, [a.zone], {'деталь': tp, 'тело': tr}, rng)
    json.dump({'axis': ax.tolist()}, open(a.out + '.json', 'w'))
    return 0


# ------------------------------------------------------------------ drape

def cmd_drape(a):
    import trimesh, shapely
    from scipy import ndimage
    from scipy.spatial import cKDTree
    from shapely.geometry import Polygon
    from shapely.geometry.polygon import orient
    from shapely.ops import unary_union
    rng = np.random.default_rng(0)
    V, F, lab = load_paint(a); body = load_body(a, V, F)
    ax = vec(a.axis); e1, e2 = frame(ax); px = a.px
    P = V[F]; ar = tri_area(P)
    zone = lab == a.zone
    if not zone.any():
        die(f"зоны {a.zone} в покраске нет")
    Z = P[zone].reshape(-1, 3); uvz = np.c_[Z @ e1, Z @ e2]; sz = Z @ ax
    U0, V0 = uvz.min(0) - 1.5; W, Hh = ((uvz.max(0) + 1.5 - [U0, V0]) / px).astype(int) + 1
    gy, gx = np.mgrid[0:Hh, 0:W]; uu = U0 + (gx + .5) * px; vv = V0 + (gy + .5) * px; n = W * Hh

    def inreg(X, m):
        q = np.c_[X @ e1, X @ e2]
        return np.all((q > [U0 - m, V0 - m]) & (q < [U0 + W * px + m, V0 + Hh * px + m]), 1)
    pf = np.flatnonzero(inreg(P.mean(1), 3))
    fw, Q = sample(P[pf], ar[pf], ar[pf].sum() * a.density, rng)
    QL = lab[pf][fw] == a.zone; kq = cKDTree(Q)
    orig = original_faces(body, trimesh.load(a.surface).vertices if a.surface else V)
    sel = np.flatnonzero(inreg(body.triangles_center, 1))
    Hc = body.submesh([sel], append=True); forig = orig[sel]

    # rays against the removal direction: relief, then air, then skin = overhang
    far = sz.max() + 30
    O = uu.ravel()[:, None] * e1 + vv.ravel()[:, None] * e2 + far * ax
    loc, ri, ti = Hc.ray.intersects_location(O, np.tile(-ax, (n, 1)), multiple_hits=True)
    s = loc @ ax; ent = (Hc.face_normals[ti] @ ax) > 0
    dq, iq = kq.query(loc, workers=-1); isM = forig[ti] & QL[iq] & (dq < 0.05)
    o = np.lexsort((-s, ri)); ri, s, ent, isM = ri[o], s[o], ent[o], isM[o]
    first = np.searchsorted(ri, np.arange(n)); cnt = np.bincount(ri, minlength=n)
    sskin = np.full(n, np.nan); mask = np.zeros(n, bool); over = np.zeros(n, bool)
    hq = np.full(n, np.nan); sfront = np.full(n, np.nan)
    for p in np.flatnonzero(cnt):
        j = first[p]; b = j + cnt[p]
        if not (ent[j] and isM[j]):
            if ent[j]:
                sskin[p] = s[j]
            continue
        mask[p] = True; sfront[p] = s[j]
        if j + 2 < b and not ent[j + 1] and isM[j + 1] and ent[j + 2] and s[j + 1] - s[j + 2] > GAP_AIR:
            over[p] = True; hq[p] = (s[j + 1] + s[j + 2]) / 2
    sskin, mask, over, hq, sfront = (x.reshape(Hh, W) for x in (sskin, mask, over, hq, sfront))
    if not mask.any():
        die("вдоль этой оси рельеф не виден спереди — ось снятия не та")
    base = mask & ~over
    print(f"ось снятия {np.round(ax, 3)}: рельеф в проекции {mask.sum() * px * px:.1f} мм², "
          f"прирастает к коже {base.sum() * px * px:.1f}, свисает над воздухом {over.sum() * px * px:.1f}")

    # plug on the attachment line
    r = int(0.2 / px)
    b2 = ndimage.binary_fill_holes(ndimage.binary_opening(ndimage.binary_closing(base, iterations=r), iterations=r))
    lb, nl = ndimage.label(b2)
    if nl == 0:
        die("рельеф нигде не прирастает к коже вдоль этой оси")
    b2 = lb == np.bincount(lb.ravel())[1:].argmax() + 1
    pin = contour_poly(b2, 4, U0, V0, px)
    sil = contour_poly(mask, 2, U0, V0, px)
    pout = contour_poly(ndimage.binary_fill_holes(ndimage.binary_closing(mask, iterations=r)), 4, U0, V0, px)
    a0 = pin.area
    pin = pin.intersection(sil.buffer(-a.clip))
    if pin.geom_type != 'Polygon':
        pin = max(pin.geoms, key=lambda g: g.area)
    if a.narrow > 0:
        # an opening removes thin necks; keep the ones that bridge two halves,
        # drop the dangling ones at the ends
        op = pin.buffer(-a.narrow, join_style=1).buffer(a.narrow, join_style=1)
        if not op.is_empty:
            cores = list(getattr(op, 'geoms', [op]))
            rem = pin.difference(op)
            keep, drop = [], 0.0
            for g in getattr(rem, 'geoms', [rem]):
                if sum(g.distance(c) < 1e-3 for c in cores) >= 2:
                    keep.append(g)
                else:
                    drop += g.area
            pin = unary_union([op] + keep).buffer(0.02).buffer(-0.02)
            print(f"узкие концы пробки ({a.narrow} мм) срезаны: {drop:.2f} мм²")
    if a.tip > 0:
        # each end of the plug stops `tip` short of the relief's own tip
        pts = np.c_[uu[mask], vv[mask]]; c = pts.mean(0)
        pa = np.linalg.eigh(np.cov((pts - c).T))[1][:, -1]
        cut0 = pin.area
        for sg in (-1, 1):
            q = pts[((pts - c) @ pa) * sg > 0]
            if not len(q):
                continue
            pe = q[np.argmax(np.linalg.norm(q - c, axis=1))]
            d = (pe - c) / np.linalg.norm(pe - c); pp = np.array([-d[1], d[0]]); B = 1e3
            k0 = pe - a.tip * d
            pin = pin.difference(Polygon([k0 + B * pp, k0 - B * pp, k0 + B * d - B * pp, k0 + B * d + B * pp]))
        pin = pin.buffer(0.3, join_style=1).buffer(-0.3, join_style=1).buffer(-0.2, join_style=1).buffer(0.2, join_style=1)
        print(f"концы пробки отступают от кончиков рельефа на {a.tip} мм: срезано {cut0 - pin.area:.2f} мм²")
    if pin.is_empty:
        die("пробка по линии прирастания вышла пустой — ослабить --narrow и --tip или взять другую ось")
    if pin.geom_type != 'Polygon':
        pin = max(pin.geoms, key=lambda g: g.area)
    pin = orient(pin.buffer(0.05).buffer(-0.05).simplify(0.005), 1.0)
    inside = shapely.contains_xy(pin, uu, vv)
    print(f"пробка по линии прирастания {a0:.1f} -> {pin.area:.1f} мм²; "
          f"прирастающего вне пробки {(base & ~inside).sum() * px * px:.1f} мм², "
          f"свисающего над пробкой {(over & inside).sum() * px * px:.1f} мм²")

    # skin under the relief: harmonic continuation from a ring of skin around it
    ring = ndimage.binary_dilation(mask, iterations=int(a.skin_ring / px)) & ~mask & np.isfinite(sskin)
    ring &= sskin > np.nanmin(sfront[mask]) - 2.0          # not the bottom of a deep cavity
    if not ring.any():
        die("вокруг рельефа нет кожи — достроить её под ним не из чего")
    known = ring.copy(); unk = ndimage.binary_dilation(mask, iterations=2) & ~known
    hbase = np.where(known, sskin, np.nanmean(sskin[known]))
    for _ in range(6000):
        hp_ = np.pad(hbase, 1, mode='edge')
        hbase = np.where(unk, 0.25 * (hp_[:-2, 1:-1] + hp_[2:, 1:-1] + hp_[1:-1, :-2] + hp_[1:-1, 2:]), hbase)
    th = sfront - hbase
    print(f"толщина рельефа над кожей: медиана {np.nanmedian(th[mask]):.2f}, 5% {np.nanpercentile(th[mask], 5):.2f} мм")

    s0 = float(sz.min() - a.edge); s1 = float(sz.max() + 0.6)
    att = mask & ~over & ~inside
    hh = np.where(over & ~inside, hq, np.where(att, hbase, s1 + 1.0))
    hhp = np.where(over & ~inside, hq, np.where(att, hbase - a.skin_under, s1 + 1.0))
    Po = prism(pout.buffer(0.3, join_style=1), ax, e1, e2, s0, s1)
    C = prism(pin, ax, e1, e2, s0, s1) + (block(uu, vv, hh, s1 + 1.5, ax, e1, e2) ^ Po)
    Cp = prism(pin.buffer(a.fit, join_style=1), ax, e1, e2, s0 - a.depth_fit, s1) + \
        (block(uu, vv, hhp, s1 + 1.5, ax, e1, e2) ^ Po)
    H = to_mf(body)
    tp, tr = write_parts(H ^ C, H - Cp, a.out, "рельеф")
    back = tp.area_faces[(tp.face_normals @ -ax) > 0.9999].sum()
    print(f"плоская спина ⟂ оси: {back:.1f} мм², дно кармана на {sz.min() - s0 + a.depth_fit:.2f} мм "
          f"ниже самой глубокой точки рельефа; зазор {a.fit} вбок, {a.depth_fit} по глубине")
    account(V, F, lab, [a.zone], {'деталь': tp, 'тело': tr}, rng)
    json.dump({'axis': ax.tolist(), 's0': s0}, open(a.out + '.json', 'w'))
    return 0


# ------------------------------------------------------------------ ring

def _ring_axis(X, N, A):
    """Axis of a surface of revolution: normal lines meet it; refined by a
    cylinder fit to the bore (faces looking at the axis, middle of the length).
    Every eigenvector is tried as a start, the best bore fit wins."""
    from scipy.optimize import least_squares
    w, vec_ = np.linalg.eigh((N * A[:, None]).T @ N)
    best = None
    for k in range(3):
        a0 = vec_[:, k]
        Q = np.eye(3) - np.outer(a0, a0); Np = N @ Q
        Np /= np.linalg.norm(Np, axis=1, keepdims=True) + 1e-12
        c0 = least_squares(lambda p: (np.cross((X - p) @ Q, Np) @ a0) * np.sqrt(A), X.mean(0)).x
        ri, cost = None, np.inf
        for _ in range(4):
            u = np.cross(a0, [0, 0, 1.]) if abs(a0[2]) < 0.9 else np.cross(a0, [1., 0, 0])
            u /= np.linalg.norm(u); v = np.cross(a0, u)
            rv = X - c0; h = rv @ a0; rad = rv - np.outer(h, a0)
            bore = ((N * rad).sum(1) < 0) & (np.abs(N @ a0) < 0.25)
            if bore.sum() < 6:
                break

            def f(p, a0=a0, c0=c0, u=u, v=v, bore=bore):
                aa = a0 + p[0] * u + p[1] * v; aa /= np.linalg.norm(aa); cc = c0 + p[2] * u + p[3] * v
                rv = X[bore] - cc
                return (np.linalg.norm(rv - np.outer(rv @ aa, aa), axis=1) - p[4]) * np.sqrt(A[bore])
            r0 = np.linalg.norm(rad[bore], axis=1)
            sol = least_squares(f, [0, 0, 0, 0, np.median(r0)])
            p = sol.x
            a0 = a0 + p[0] * u + p[1] * v; a0 /= np.linalg.norm(a0); c0 = c0 + p[2] * u + p[3] * v; ri = p[4]
            cost = np.sqrt(2 * sol.cost / A[bore].sum()) / max(ri, 1e-6)
        if ri is not None and ri > 0 and cost < (best[3] if best else np.inf):
            best = (a0, c0, ri, cost)
    if best is None:
        die("отверстия кольца не нашлось: среди граней нет стенки, смотрящей на ось")
    a0, c0, ri, cost = best
    c0 = c0 + a0 * ((X - c0) @ a0).mean()
    return a0, c0, ri, cost


def cmd_ring(a):
    import trimesh
    from shapely.geometry import Point, box
    from shapely.ops import unary_union
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    rng = np.random.default_rng(1)
    V, F, lab = load_paint(a); body = load_body(a, V, F)
    P = V[F]; c = P.mean(1); ar = tri_area(P)
    nrm = np.cross(P[:, 1] - P[:, 0], P[:, 2] - P[:, 0]); nrm /= 2 * ar[:, None] + 1e-12
    zone = np.flatnonzero(lab == a.zone)
    if not len(zone):
        die(f"зоны {a.zone} в покраске нет")
    groups = []
    if a.at:
        for s in a.at:
            p = np.array([float(x) for x in s.split(',')])
            g = zone[np.linalg.norm(c[zone] - p, axis=1) < a.within]
            if not len(g):
                die(f"у точки {s} граней зоны {a.zone} ближе {a.within} мм нет")
            groups.append(g)
    else:
        key = np.round(V, 4); _, inv = np.unique(key, axis=0, return_inverse=True); inv = inv.ravel()
        fv = inv[F[zone]]; k = len(zone)
        rows = np.repeat(np.arange(k), 3); M = coo_matrix((np.ones(3 * k), (rows, fv.ravel()))).tocsr()
        G = (M @ M.T).tocoo()
        nc, cl = connected_components(coo_matrix((np.ones(len(G.row)), (G.row, G.col)), shape=(k, k)))
        for i in range(nc):
            g = zone[cl == i]
            if ar[g].sum() >= a.min_area:
                groups.append(g)
        print(f"колец в зоне {a.zone}: {len(groups)} (кусков меньше {a.min_area} мм² не считаю)")
    pts, _ = trimesh.sample.sample_surface(body, max(200000, int(body.area * 40)), seed=1)

    rings = []
    for g in groups:
        # area-weighted samples: long bore triangles count by their area
        fi, X = sample(P[g], ar[g], max(4000, ar[g].sum() * 200), rng)
        Nn, A = nrm[g][fi], np.ones(len(fi))
        ax, c0, ri, cost = _ring_axis(X, Nn, A)
        rv = X - c0; h = rv @ ax; rad = rv - np.outer(h, ax); r = np.linalg.norm(rad, axis=1)
        outw = (Nn * rad).sum(1) > 0
        hf, hb = np.percentile(h, 99.7), np.percentile(h, 0.3)
        Rf = np.percentile(r[outw & (h > 0)], 99.5) if (outw & (h > 0)).any() else ri
        Rb = np.percentile(r[outw & (h < 0)], 99.5) if (outw & (h < 0)).any() else ri
        # which end is free: body material just beyond each end, inside the lip radius
        pr = pts - c0; ph = pr @ ax; prr = np.linalg.norm(pr - np.outer(ph, ax), axis=1)
        Rm = max(Rf, Rb) + 0.1
        busy_f = int(((ph > hf + 0.05) & (ph < hf + 2.5) & (prr < Rm)).sum())
        busy_b = int(((ph < hb - 0.05) & (ph > hb - 2.5) & (prr < Rm)).sum())
        if busy_b < busy_f or (busy_b == busy_f and Rb > Rf):
            ax, h, hf, hb, Rf, Rb, busy_f, busy_b = -ax, -h, -hb, -hf, Rb, Rf, busy_b, busy_f
        rings.append(dict(axis=ax, center=c0, ri=ri, hf=hf, hb=hb, Rf=Rf, Rb=Rb, fit=cost,
                          busy_f=busy_f, busy_b=busy_b))
    for i, R in enumerate(rings, 1):
        print(f"кольцо {i}: ось {np.round(R['axis'], 3)}, центр {np.round(R['center'], 2)}, "
              f"отверстие r {R['ri']:.3f} (разброс {R['fit'] * 100:.1f}%), "
              f"бортик снаружи r {R['Rf']:.2f}, с другого конца {R['Rb']:.2f}, длина {R['hf'] - R['hb']:.2f} мм")
        print(f"   вставляется со стороны бортика: там свободно ({R['busy_f']} точек тела), "
              + (f"с другого конца тесно ({R['busy_b']}) — второй бортик остаётся краской на теле"
                 if R['busy_b'] else "с другого конца тоже свободно"))
    if a.same and len(rings) > 1:
        ri = float(np.mean([R['ri'] for R in rings])); Rf = float(np.mean([R['Rf'] for R in rings]))
        L = float(np.mean([R['hf'] - R['hb'] for R in rings]))
        for R in rings:
            R.update(ri=ri, Rf=Rf, hb=R['hf'] - L)
        print(f"кольца одинаковые: отверстие r {ri:.3f}, длина {L:.2f} мм")
    for R in rings:
        if a.lip:
            R['Rf'] = a.lip
        if a.length:
            R['hb'] = R['hf'] - a.length

    def revolve(poly, ax, c0):
        poly = poly.buffer(0).simplify(0.002)
        if poly.geom_type != 'Polygon':
            poly = max(poly.geoms, key=lambda g: g.area)
        m = trimesh.creation.revolve(np.asarray(poly.exterior.coords), sections=a.sections)
        if m.volume < 0:
            m.invert()
        T = np.eye(4); T[:3, :3] = trimesh.geometry.align_vectors([0, 0, 1.], ax)[:3, :3]; T[:3, 3] = c0
        m.apply_transform(T)
        return m
    H = to_mf(body); made = {}; meta = []
    for i, R in enumerate(rings, 1):
        ri, hf, hb = R['ri'], R['hf'], R['hb']
        rho = (R['Rf'] - ri) / 2; Rc = ri + rho; hc = hf - rho; rw = ri + a.wall
        if rho <= 0.1:
            die(f"кольцо {i}: бортика нет (r {R['Rf']:.2f} при отверстии {ri:.2f})")
        lip = Point(Rc, hc).buffer(rho, resolution=64)
        piece = unary_union([lip, box(ri, hb, rw, hc)]).intersection(box(ri, -50, 50, 50))
        pocket = unary_union([lip.buffer(a.fit), box(0.01, hb - a.depth_fit, rw + a.fit, hc),
                              box(0.01, hc, Rc + rho + a.fit, hc + a.sweep)])
        m = revolve(piece, R['axis'], R['center']); m.export(f"{a.out}_{i}.stl")
        H = H - to_mf(revolve(pocket, R['axis'], R['center']))
        made[f'кольцо {i}'] = m
        print(f"кольцо {i}: деталь {m.volume:.2f} мм³ (замкнута {'да' if m.is_watertight else 'НЕТ'}), "
              f"бортик r {R['Rf']:.2f}, трубка до r {rw:.2f}, длина {hf - hb:.2f} мм -> {a.out}_{i}.stl")
        meta.append(dict(axis=R['axis'].tolist(), center=R['center'].tolist(), ri=ri, lip=R['Rf'],
                         length=hf - hb))
    r, scraps = pieces(H)
    tr = save(r, a.out + '_rest.stl')
    print(f"тело: {r.volume():.1f} мм³" + (f", обрывки {[round(x, 3) for x in scraps[:5]]}" if scraps else "")
          + f", замкнуто {'да' if tr.is_watertight else 'НЕТ'} -> {a.out}_rest.stl")
    made['тело'] = tr
    # a rebuilt ring is ideal, the painted one is not: count paint within RING_NEAR
    account(V, F, lab, [a.zone], made, rng, faces=np.concatenate(groups), near=RING_NEAR)
    json.dump(meta, open(a.out + '.json', 'w'))
    return 0


# ------------------------------------------------------------------ check

def cmd_check(a):
    import trimesh, itertools
    B, T, AX = {}, {}, {}
    for s in a.parts:
        path, _, d = s.partition('@')
        nm = os.path.splitext(os.path.basename(path))[0]
        T[nm] = trimesh.load(path, force='mesh'); B[nm] = to_mf(T[nm])
        if d:
            AX[nm] = vec(d)
        print(f"{nm}: {B[nm].volume():.1f} мм³, замкнута {'да' if T[nm].is_watertight else 'НЕТ'}")
    bad = [(x, y, (B[x] ^ B[y]).volume()) for x, y in itertools.combinations(B, 2)]
    bad = [t for t in bad if t[2] > 1e-3]
    print("пересечения: " + ("нет" if not bad else ', '.join(f"{x} × {y} {v:.4f} мм³" for x, y, v in bad)))
    if a.src:
        sv = trimesh.load(a.src, force='mesh').volume; tot = sum(b.volume() for b in B.values())
        print(f"сумма деталей {tot:.1f} мм³, исходник {sv:.1f}, на зазоры ушло {sv - tot:.1f} мм³")
    stuck = False
    for nm, d in AX.items():
        others = None
        for k, b in B.items():
            if k != nm:
                others = b if others is None else others + b
        worst = 0.0
        if others is not None:
            for t in np.arange(a.step, a.travel + 1e-9, a.step):
                worst = max(worst, (B[nm].translate((d * t).tolist()) ^ others).volume())
        print(f"{nm}: снятие по {np.round(d, 3)} на {a.travel:g} мм — худшее пересечение {worst:.4f} мм³"
              + (" — ЗАСТРЕВАЕТ" if worst > 1e-3 else ""))
        stuck = stuck or worst > 1e-3
    if a.paint:
        V, F, lab = load_paint(a)
        zones = [int(z) for z in a.zones.split(',')] if a.zones else sorted(set(lab.tolist()))
        account(V, F, lab, zones, T, np.random.default_rng(0))
    return 1 if bad or stuck else 0


# ------------------------------------------------------------------ main

def main():
    ap = argparse.ArgumentParser(prog='relief_cut.py', add_help=True)
    sp = ap.add_subparsers(dest='cmd')

    def paint_args(p):
        p.add_argument('--paint', required=True, help='npz с V, F, lab (paint.py parse)')
        p.add_argument('--labels', help='npy с меткой на грань вместо lab')
        p.add_argument('--zone', type=int, required=True)
        p.add_argument('--body', help='тело для резки, если это не сама покрашенная сетка')
        p.add_argument('--surface', help='сетка, вершины которой — нетронутая поверхность')
        p.add_argument('--out', help='префикс выходных файлов')

    p = sp.add_parser('sheet'); paint_args(p)
    p.add_argument('--axis', required=True)
    p.add_argument('--edge', type=float, default=1.6, help='толщина детали под краской')
    p.add_argument('--margin', type=float, default=1.2, help='толщина тела под своей краской')
    p.add_argument('--rho', type=float, default=0.6, help='радиус сглаживания границы зоны')
    p.add_argument('--theta', type=float, default=50, help='угол выхода листа к границе, градусы')
    p.add_argument('--px', type=float, default=0.1)
    p.add_argument('--density', type=float, default=250, help='точек на мм²')
    p.add_argument('--env', type=float, default=0.3)
    p.add_argument('--smooth', type=float, default=0.5)
    p.add_argument('--tol', type=float, default=0.03)
    p.add_argument('--gap', type=float, default=0.1, help='зазор по листу')
    p.add_argument('--wall', type=float, default=0.2, help='зазор по стенкам')
    p.add_argument('--contour', type=float, default=0.5, help='сглаживание контура стенок, мм')
    p.add_argument('--taubin', type=int, default=60)
    p.add_argument('--plane', type=float, help='площадка тела на плоскости x.n = d')
    p.add_argument('--plane-normal', default='0,0,1')
    p.add_argument('--plane-scan', help='перебрать d через запятую и выйти')

    p = sp.add_parser('drape'); paint_args(p)
    p.add_argument('--axis', required=True)
    p.add_argument('--px', type=float, default=0.05)
    p.add_argument('--density', type=float, default=1000)
    p.add_argument('--edge', type=float, default=1.2, help='дно ниже самой глубокой точки рельефа')
    p.add_argument('--fit', type=float, default=0.2)
    p.add_argument('--depth-fit', type=float, default=0.05)
    p.add_argument('--clip', type=float, default=-0.05, help='отступ пробки внутрь силуэта; минус — наружу')
    p.add_argument('--narrow', type=float, default=0.8, help='срезать концы пробки уже 2×N')
    p.add_argument('--tip', type=float, default=1.6, help='пробка короче кончиков рельефа на столько')
    p.add_argument('--skin-ring', type=float, default=0.8)
    p.add_argument('--skin-under', type=float, default=0.08)

    p = sp.add_parser('ring'); paint_args(p)
    p.add_argument('--at', action='append', help='x,y,z рядом с кольцом; можно несколько')
    p.add_argument('--within', type=float, default=6.0)
    p.add_argument('--min-area', type=float, default=5.0)
    p.add_argument('--same', action='store_true', help='сделать кольца одинаковыми')
    p.add_argument('--lip', type=float, help='наружный радиус бортика')
    p.add_argument('--length', type=float, help='длина трубки')
    p.add_argument('--wall', type=float, default=0.75)
    p.add_argument('--fit', type=float, default=0.1)
    p.add_argument('--depth-fit', type=float, default=0.05)
    p.add_argument('--sweep', type=float, default=1.5)
    p.add_argument('--sections', type=int, default=128)

    p = sp.add_parser('check')
    p.add_argument('parts', nargs='+', help='деталь.stl или деталь.stl@x,y,z — с осью снятия')
    p.add_argument('--src')
    p.add_argument('--paint'); p.add_argument('--labels'); p.add_argument('--zones')
    p.add_argument('--travel', type=float, default=25.0)
    p.add_argument('--step', type=float, default=0.1)

    if len(sys.argv) < 2 or sys.argv[1] not in ('sheet', 'drape', 'ring', 'check'):
        print(__doc__)
        return 1
    a = ap.parse_args()
    if a.cmd != 'check' and not a.out and not getattr(a, 'plane_scan', None):
        die("нужен --out: префикс выходных файлов")
    if getattr(a, 'out', None):
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    return {'sheet': cmd_sheet, 'drape': cmd_drape, 'ring': cmd_ring, 'check': cmd_check}[a.cmd](a) or 0


if __name__ == '__main__':
    sys.exit(main())
