#!/usr/bin/env python3
"""Ball joints in a foreign model: find, measure, restore, verify assembly.

Figurines that arrive already cut into parts are almost always assembled on
ball-and-socket joints. The author set that fit and it is already tuned - ball
and socket mouth are matched to an interference of a few hundredths. **Do not
redesign it.** Measure it, and restore a socket only when it is clogged with
debris from a cut or from a mesh rebuild. A socket that will not accept its
ball is far more often full of flat scraps than too narrow.

    UV="uv run --quiet --with numpy --with scipy --with trimesh --with rtree python"
    $UV tools/balljoint.py find    project.3mf
    $UV tools/balljoint.py profile project.3mf --object 6 --socket=-15.40,-0.06,2.62 -o work/prof.json
    $UV tools/balljoint.py cutter  work/prof.json -o work/cutter.stl
    $UV tools/balljoint.py fit     project.3mf --torso 6

argparse accepts negative coordinates only through `=`.

## 1. Find spheres by voting on normals, not by rings in sections

For a face with centre `p` and unit normal `n`, the centre of a sphere of
radius `r` lies at `p + r*n` for a concave sphere (a socket) or `p - r*n` for a
convex one (a ball). Sweep `r`, vote into a lattice weighted by face area, and
keep the bins holding a sizeable share of `4*pi*r^2`; then refine centre and
radius by least squares.

This finds **all** the joints at once, to micron radii. Identical radii across
several joints mean the author placed one primitive repeatedly, which is itself
proof that the fit was designed as a single thing.

**Searching for rings in internal sections produces false sockets** whose
channels run into solid material while the real sockets stay clogged.

## 2. Measure a socket with rays from its axis, not by "free diameter"

Cast rays outwards in a circle from points on the socket axis; the first hit is
the wall. That yields `r(depth, angle)`. A small spread over angle means the
socket is a solid of revolution and its profile can be reproduced exactly.

A "free radius around the axis" metric lies here: it reports a healthy diameter
where nothing passes at all, because the channel was cut past the socket.

**Trap: a ray cast below the bottom of the sphere flies past it and hits the
far wall of the part**, reporting a huge radius. A cutter built from such raw
readings comes out as a disc and gouges a slab out of the part. The bottom of a
socket is therefore always built from an analytic sphere fitted to the depth
range where the readings are still trustworthy.

## 3. Restore with a solid of revolution built from the measured profile

The cutter is rings along the measured curve, an apex at the bottom, continued
outwards as a cylinder so the channel reaches the surface. Subtract it from a
watertight part with a boolean.

**Verify ray by ray against the original**: for every (depth, angle) pair
compare the radius before and after.

## 4. Verify assembly by intersecting volumes, not by eye

Torso occupancy is computed once into a voxel lattice with a distance map
inside. A part is placed in a pose and its surface sampled, which shows
directly how many millimetres it intrudes.

**A joint has more than one pose.** A ball in a socket leaves the part free to
swing: three degrees of freedom with one ball, one with two balls. So the
question is not whether an intersection exists but whether **a pose without one
exists**; sweep the freedom.

**Assign sockets globally, not greedily.** Resolve two-ball parts first - the
distance between their balls names the socket pair almost uniquely - then
distribute single-ball parts over the remainder with the Hungarian algorithm on
a matrix of best-pose intersection. Greedy assignment in object order puts a
leg into a wrist socket and everything after it slides.

**Try both ways round for a two-ball part as well**: swapping which ball is the
shoulder puts the limb on backwards, which shows up as a large intersection
that vanishes with the correct pairing.

Points within the socket radius of its centre do not count - the ball is
supposed to be inside the body there.
"""
import argparse, json, re, sys, zipfile
import numpy as np

VERT = re.compile(r'<vertex x="([^"]*)" y="([^"]*)" z="([^"]*)"\s*/>')
TRI = re.compile(r'<triangle v1="(\d+)" v2="(\d+)" v3="(\d+)"')


# ---------------------------------------------------------------- reading
def entries(z):
    """Соответствие id объекта -> запись .model, по 3D/_rels."""
    rels = z.read('3D/_rels/3dmodel.model.rels').decode()
    tg = [m.group(1).lstrip('/') for m in re.finditer(r'Target="([^"]+)"', rels)]
    root = z.read('3D/3dmodel.model').decode()
    ids = re.findall(r'<item objectid="(\d+)"', root)
    return {i: t for i, t in zip(ids, tg)}


def load(path, oid=None):
    """(V, F) одного объекта 3MF/STL. Для 3MF без oid — первый объект."""
    if path.lower().endswith('.stl'):
        import trimesh
        m = trimesh.load(path, process=False)
        m.merge_vertices()
        return np.asarray(m.vertices), np.asarray(m.faces)
    z = zipfile.ZipFile(path)
    ent = entries(z)
    key = str(oid) if oid is not None else sorted(ent, key=lambda k: int(k))[0]
    raw = z.read(ent[key]).decode()
    return (np.array(VERT.findall(raw), dtype=np.float64),
            np.array(TRI.findall(raw), dtype=np.int64))


def faces(V, F):
    """Центры, единичные нормали и площади невырожденных граней."""
    P = V[F]
    n = np.cross(P[:, 1] - P[:, 0], P[:, 2] - P[:, 0])
    a = np.linalg.norm(n, axis=1)
    m = a > 1e-12
    return P[m].mean(1), n[m] / a[m, None], a[m] / 2


# ---------------------------------------------------------------- find
def hough(c, n, ar, sign, rmin, rmax, step, cover, binsz=0.30):
    """Голосование за центры сфер: c + sign·r·n. sign=+1 гнездо, −1 шар."""
    found = []
    for r in np.arange(rmin, rmax, step):
        q = np.round((c + sign * r * n) / binsz).astype(np.int64)
        key = (q[:, 0] << 42) ^ (q[:, 1] << 21) ^ q[:, 2]
        u, inv = np.unique(key, return_inverse=True)
        w = np.bincount(inv, weights=ar)
        for i in np.where(w > cover * 4 * np.pi * r * r)[0]:
            ctr = (c + sign * r * n)[inv == i].mean(0)
            for k, f in enumerate(found):
                if np.linalg.norm(ctr - f[1]) < 3.0:
                    if w[i] > f[2]:
                        found[k] = (r, ctr, w[i])
                    break
            else:
                found.append((r, ctr, w[i]))
    return found


def refine(c, n, ar, ctr, r, sign):
    """МНК-уточнение центра и радиуса по граням самой сферы + ось выхода."""
    cc = np.asarray(ctr, float)
    sel = None
    for _ in range(10):
        d = np.linalg.norm(c - cc, axis=1)
        u = (c - cc) / np.maximum(d, 1e-9)[:, None]
        sel = (d > r - 0.35) & (d < r + 0.35) & ((u * n).sum(1) * sign < -0.88)
        if sel.sum() < 30:
            return None
        p, w = c[sel], ar[sel]
        M = np.column_stack([2 * p, np.ones(len(p))])
        sol, *_ = np.linalg.lstsq(M * w[:, None], (p ** 2).sum(1) * w, rcond=None)
        cc = sol[:3]
        r = float(np.sqrt(sol[3] + (cc ** 2).sum()))
    d = np.linalg.norm(c[sel] - cc, axis=1)
    rms = float(np.sqrt(np.average((d - r) ** 2, weights=ar[sel])))
    ax = (n[sel] * ar[sel, None]).sum(0) * sign
    ax = ax / np.linalg.norm(ax)
    return dict(c=[round(float(x), 4) for x in cc], r=round(r, 4),
                rms_um=round(rms * 1000, 1),
                cover=round(float(ar[sel].sum() / (4 * np.pi * r * r)), 3),
                axis=[round(float(x), 5) for x in ax])


def cmd_find(a):
    z = zipfile.ZipFile(a.src) if not a.src.lower().endswith('.stl') else None
    ids = sorted(entries(z), key=lambda k: int(k)) if z else [None]
    out = {}
    for oid in ids:
        V, F = load(a.src, oid)
        c, n, ar = faces(V, F)
        res = {'гнёзда': [], 'шары': []}
        for sign, key in ((+1, 'гнёзда'), (-1, 'шары')):
            for r, ctr, w in hough(c, n, ar, sign, a.rmin, a.rmax, 0.05, a.cover):
                d = refine(c, n, ar, ctr, r, sign)
                if d and d['rms_um'] < a.rms:
                    res[key].append(d)
        out[str(oid)] = res
        print(f"объект {oid}: граней {len(F):,}")
        for key in ('гнёзда', 'шары'):
            for d in sorted(res[key], key=lambda x: -x['cover']):
                print(f"   {key[:-1]:6s} Ø{2*d['r']:.3f}  центр ({d['c'][0]:8.3f},{d['c'][1]:8.3f},"
                      f"{d['c'][2]:8.3f})  невязка {d['rms_um']:5.1f} мкм  покрытие {d['cover']*100:3.0f}%"
                      f"  ось ({d['axis'][0]:6.3f},{d['axis'][1]:6.3f},{d['axis'][2]:6.3f})")
    if a.out:
        json.dump(out, open(a.out, 'w'), ensure_ascii=False, indent=1)
        print('\n->', a.out)


# ---------------------------------------------------------------- profile
def ray_profile(V, F, c, d, ts, na=144, reach=10.0):
    """r(глубина, угол): лучи с оси наружу, первое попадание."""
    import trimesh
    c = np.asarray(c, float)
    d = np.asarray(d, float)
    d = d / np.linalg.norm(d)
    keep = np.where(np.linalg.norm(V[F].mean(1) - c, axis=1) < reach)[0]
    sub = trimesh.Trimesh(V, F[keep], process=False)
    a = np.cross(d, [0, 0, 1.0])
    if np.linalg.norm(a) < 0.3:
        a = np.cross(d, [1.0, 0, 0])
    a /= np.linalg.norm(a)
    b = np.cross(d, a)
    th = np.linspace(0, 2 * np.pi, na, endpoint=False)
    dirs = np.outer(np.cos(th), a) + np.outer(np.sin(th), b)
    prof = np.full((len(ts), na), np.nan)
    for i, t in enumerate(ts):
        org = np.tile(c + t * d, (na, 1))
        loc, idx, _ = sub.ray.intersects_location(org, dirs, multiple_hits=False)
        if len(idx):
            prof[i, idx] = np.linalg.norm(loc - org[idx], axis=1)
    return prof, (a, b)


def sphere_bottom(ts, med, lo, hi):
    """Радиус сферы дна по достоверному участку: r² + t² = R²."""
    m = (ts >= lo) & (ts <= hi) & np.isfinite(med)
    return float(np.median(np.sqrt(med[m] ** 2 + ts[m] ** 2)))


def cmd_profile(a):
    V, F = load(a.src, a.object)
    c = np.array([float(x) for x in a.socket.split(',')])
    if a.axis:
        d = np.array([float(x) for x in a.axis.split(',')])
    else:
        cc, nn, ar = faces(V, F)
        got = refine(cc, nn, ar, c, a.rmax * 0.95, +1)
        if got is None:
            sys.exit('не нашёл сферу гнезда возле указанной точки')
        c, d = np.array(got['c']), np.array(got['axis'])
        print(f"гнездо уточнено: центр {np.round(c,3)}  Ø{2*got['r']:.3f}  ось {np.round(d,3)}")
    ts = np.round(np.arange(a.tmin, a.tmax + 1e-9, 0.05), 3)
    prof, _ = ray_profile(V, F, c, d, ts, a.angles)
    med = np.nanmedian(prof, axis=1)
    iqr = np.nanpercentile(prof, 75, axis=1) - np.nanpercentile(prof, 25, axis=1)
    R = sphere_bottom(ts, med, a.fit_lo, a.fit_hi)
    print(f"дно: сфера R={R:.3f} (подгонка по t от {a.fit_lo} до {a.fit_hi})")
    print("глубина  r медиана  межквартиль  промахов")
    for i, t in enumerate(ts):
        if abs(round(t * 20)) % 4:
            continue
        print(f"  {t:+5.2f}     {med[i]:5.2f}       {iqr[i]:5.2f}      "
              f"{int(np.isnan(prof[i]).sum()):3d}/{a.angles}")
    json.dump(dict(seat=[float(x) for x in c], axis=[float(x) for x in d],
                   R=R, ts=[float(x) for x in ts],
                   r=[None if not np.isfinite(x) else float(x) for x in med],
                   iqr=[None if not np.isfinite(x) else float(x) for x in iqr]),
              open(a.out, 'w'), ensure_ascii=False)
    print('->', a.out)


# ---------------------------------------------------------------- cutter
def cmd_cutter(a):
    import trimesh
    p = json.load(open(a.prof))
    ts = np.array(p['ts'])
    rs = np.array([np.nan if x is None else x for x in p['r']])
    R = p['R']
    up = (ts >= a.tswitch) & (ts <= a.tmax) & np.isfinite(rs)
    tl = np.arange(-R, a.tswitch, 0.05)
    rl = np.sqrt(np.maximum(R * R - tl * tl, 0.0))
    T = np.concatenate([tl, ts[up], [a.out_len]])
    Rr = np.concatenate([rl, rs[up], [rs[up][-1]]])
    c = np.array(p['seat']) + np.array([float(x) for x in a.offset.split(',')])
    d = np.array(p['axis'])
    d = d / np.linalg.norm(d)
    ax = np.cross(d, [0, 0, 1.0])
    if np.linalg.norm(ax) < 0.3:
        ax = np.cross(d, [1.0, 0, 0])
    ax /= np.linalg.norm(ax)
    b = np.cross(d, ax)
    th = np.linspace(0, 2 * np.pi, a.segments, endpoint=False) + a.phase * np.pi / 180
    ring = np.outer(np.cos(th), ax) + np.outer(np.sin(th), b)
    V = [c + T[0] * d]
    for t, r in zip(T[1:], Rr[1:]):
        V.extend(c + t * d + r * ring)
    V.append(c + T[-1] * d)
    V = np.array(V)
    na, nr = a.segments, len(T) - 1
    Fc = [[0, 1 + j, 1 + (j + 1) % na] for j in range(na)]
    for i in range(nr - 1):
        o1, o2 = 1 + i * na, 1 + (i + 1) * na
        for j in range(na):
            k = (j + 1) % na
            Fc.append([o1 + j, o2 + j, o2 + k])
            Fc.append([o1 + j, o2 + k, o1 + k])
    top, o = len(V) - 1, 1 + (nr - 1) * na
    Fc += [[top, o + (j + 1) % na, o + j] for j in range(na)]
    M = trimesh.Trimesh(V, np.array(Fc), process=False)
    M.fix_normals()
    M.export(a.out)
    print(f"резак: граней {len(M.faces)}, объём {M.volume:.2f} мм³, "
          f"герметичен {M.is_watertight}, дно t={-R:.2f}, макс r {Rr.max():.2f}")
    print('->', a.out)


# ---------------------------------------------------------------- fit
def occupancy(V, F, ctr, half, vox):
    """Занятость по числу оборотов: сдвоенные поверхности не ломают счёт."""
    T = V[F]
    bl, bh = T[:, :, :2].min(1), T[:, :, :2].max(1)
    sel = ((bh[:, 0] >= ctr[0] - half) & (bl[:, 0] <= ctr[0] + half) &
           (bh[:, 1] >= ctr[1] - half) & (bl[:, 1] <= ctr[1] + half))
    T, bl, bh = T[sel], bl[sel], bh[sel]
    xs = np.arange(ctr[0] - half, ctr[0] + half + 1e-9, vox)
    ys = np.arange(ctr[1] - half, ctr[1] + half + 1e-9, vox)
    zs = np.arange(ctr[2] - half, ctr[2] + half + 1e-9, vox)
    occ = np.zeros((len(xs), len(ys), len(zs)), bool)
    for j, py in enumerate(ys):
        row = (bl[:, 1] <= py) & (bh[:, 1] >= py)
        Tr = T[row]
        if not len(Tr):
            continue
        A, B, C = Tr[:, 0], Tr[:, 1], Tr[:, 2]
        nz = np.cross(B - A, C - A)[:, 2]
        den = (B[:, 1] - C[:, 1]) * (A[:, 0] - C[:, 0]) + (C[:, 0] - B[:, 0]) * (A[:, 1] - C[:, 1])
        rl, rh = bl[row][:, 0], bh[row][:, 0]
        for i, px in enumerate(xs):
            k = (rl <= px) & (rh >= px)
            if not k.any():
                continue
            aa, bb, cc, dd, nn = A[k], B[k], C[k], den[k], nz[k]
            with np.errstate(invalid='ignore', divide='ignore'):
                l1 = ((bb[:, 1] - cc[:, 1]) * (px - cc[:, 0]) + (cc[:, 0] - bb[:, 0]) * (py - cc[:, 1])) / dd
                l2 = ((cc[:, 1] - aa[:, 1]) * (px - cc[:, 0]) + (aa[:, 0] - cc[:, 0]) * (py - cc[:, 1])) / dd
            l3 = 1 - l1 - l2
            h = (l1 >= 0) & (l2 >= 0) & (l3 >= 0) & np.isfinite(l1) & (np.abs(nn) > 1e-12)
            zc = l1[h] * aa[h, 2] + l2[h] * bb[h, 2] + l3[h] * cc[h, 2]
            s = np.sign(nn[h])
            o = np.argsort(zc)
            zc, s = zc[o], s[o]
            if not len(zc):
                continue
            w = np.cumsum(s)
            kk = np.searchsorted(zc, zs)
            occ[i, j] = np.where(kk > 0, w[np.clip(kk - 1, 0, len(w) - 1)], 0) < 0
    return occ, np.array([xs[0], ys[0], zs[0]])


def rot(axis, ang):
    a = np.asarray(axis, float)
    a = a / np.linalg.norm(a)
    K = np.array([[0, -a[2], a[1]], [a[2], 0, -a[0]], [-a[1], a[0], 0]])
    return np.eye(3) + np.sin(ang) * K + (1 - np.cos(ang)) * K @ K


def align(u, v):
    u = np.asarray(u, float)
    v = np.asarray(v, float)
    u, v = u / np.linalg.norm(u), v / np.linalg.norm(v)
    w = np.cross(u, v)
    s = np.linalg.norm(w)
    return np.eye(3) if s < 1e-9 else rot(w, np.arctan2(s, u @ v))


def surf_points(V, F, n, seed=0):
    P = V[F]
    A = np.linalg.norm(np.cross(P[:, 1] - P[:, 0], P[:, 2] - P[:, 0]), axis=1) / 2
    g = np.random.default_rng(seed)
    k = g.choice(len(F), size=n, p=A / A.sum())
    r1 = np.sqrt(g.random(n))[:, None]
    r2 = g.random(n)[:, None]
    a, b, c = P[k, 0], P[k, 1], P[k, 2]
    return (1 - r1) * a + r1 * (1 - r2) * b + r1 * r2 * c, float(A.sum()) / n


def neck_axis(V, F, c, lo=2.95, hi=3.60):
    """Куда уходит шейка: направление материала сразу за шаром."""
    ctr, _, ar = faces(V, F)
    d = np.linalg.norm(ctr - c, axis=1)
    sel = (d > lo) & (d < hi)
    ax = ((ctr[sel] - c) * ar[sel, None]).sum(0)
    return ax / np.linalg.norm(ax)


def cmd_fit(a):
    from scipy import ndimage
    z = zipfile.ZipFile(a.src)
    ids = sorted(entries(z), key=lambda k: int(k))
    tV, tF = load(a.src, a.torso)
    c0 = (tV.min(0) + tV.max(0)) / 2
    half = float(np.abs(tV - c0).max()) + 1.2
    print(f"занятость торса, воксель {a.vox} мм…")
    occ, o0 = occupancy(tV, tF, c0, half, a.vox)
    shp = np.array(occ.shape)
    depth = ndimage.distance_transform_edt(occ, sampling=a.vox)
    print(f"  объём по вокселям {occ.sum()*a.vox**3:.0f} мм³")

    tc, tn, tar = faces(tV, tF)
    sockets = []
    for r, ctr, w in hough(tc, tn, tar, +1, a.rmin, a.rmax, 0.05, a.cover):
        d = refine(tc, tn, tar, ctr, r, +1)
        if d and d['rms_um'] < a.rms:
            sockets.append(d)
    print(f"  гнёзд найдено: {len(sockets)}")

    def probe(pts, area, seats):
        I = np.round((pts - o0) / a.vox).astype(int)
        ok = np.all((I >= 0) & (I < shp), axis=1)
        dep = np.zeros(len(pts))
        dep[ok] = depth[I[ok, 0], I[ok, 1], I[ok, 2]]
        near = np.zeros(len(pts), bool)
        for s in seats:
            near |= np.linalg.norm(pts - np.asarray(s), axis=1) < a.joint_r
        d = dep[~near]
        return (np.array([(d > 0.25).sum(), (d > 0.5).sum(), (d > 1.0).sum()]) * area,
                float(d.max()) if len(d) else 0.0)

    report = []
    asm = [(tV, tF)] if a.assembly else []
    # Two-ball parts are resolved first: the distance between the balls names
    # the socket pair almost uniquely. The remaining single-ball parts are then
    # distributed over the remaining sockets GLOBALLY, with the Hungarian
    # algorithm on a best-pose intersection matrix: greedy assignment confuses
    # a leg with a wrist when their neck axes are similar.
    from scipy.optimize import linear_sum_assignment
    parts = []
    for oid in ids:
        if str(oid) == str(a.torso):
            continue
        V, F = load(a.src, oid)
        c, n, ar = faces(V, F)
        balls = []
        for r, ctr, w in hough(c, n, ar, -1, a.rmin, a.rmax, 0.05, a.cover):
            d = refine(c, n, ar, ctr, r, -1)
            if d and d['rms_um'] < a.rms:
                d['neck'] = [float(x) for x in neck_axis(V, F, np.array(d['c']))]
                balls.append(d)
        if not balls:
            print(f"объект {oid}: шаров не найдено, пропускаю")
            continue
        parts.append(dict(oid=oid, V=V, F=F, balls=balls,
                          coarse=surf_points(V, F, a.coarse),
                          fine=surf_points(V, F, a.fine, seed=1)))

    def probe(pts, area, seats):
        I = np.round((pts - o0) / a.vox).astype(int)
        ok = np.all((I >= 0) & (I < shp), axis=1)
        dep = np.zeros(len(pts))
        dep[ok] = depth[I[ok, 0], I[ok, 1], I[ok, 2]]
        near = np.zeros(len(pts), bool)
        for s in seats:
            near |= np.linalg.norm(pts - np.asarray(s), axis=1) < a.joint_r
        d = dep[~near]
        return (np.array([(d > 0.25).sum(), (d > 0.5).sum(), (d > 1.0).sum()]) * area,
                float(d.max()) if len(d) else 0.0)

    def spinframe(spin):
        perp = np.cross(spin, [0, 0, 1.0])
        if np.linalg.norm(perp) < 0.3:
            perp = np.cross(spin, [1.0, 0, 0])
        return perp / np.linalg.norm(perp)

    def search_two(pt, si, sj, pts, area):
        p = np.array([sockets[si]['c'], sockets[sj]['c']])
        q = np.array([pt['balls'][0]['c'], pt['balls'][1]['c']])
        w = (p[1] - p[0]) / np.linalg.norm(p[1] - p[0])
        R0 = align((q[1] - q[0]) / np.linalg.norm(q[1] - q[0]), w)
        best = None
        for an in np.arange(0, 2 * np.pi, np.radians(a.spin_step)):
            R = rot(w, an) @ R0
            T = p.mean(0) - R @ q.mean(0)
            sc, mx = probe(pts @ R.T + T, area, [p[0], p[1]])
            if best is None or tuple(sc) < tuple(best[0]):
                best = (sc, mx, R, T, (0.0, 0.0, np.degrees(an)), [p[0], p[1]])
        return best

    def search_one(pt, si, pts, area):
        sc0 = np.array(sockets[si]['c'])
        spin = np.array(sockets[si]['axis'])
        R0 = align(pt['balls'][0]['neck'], spin)
        perp = spinframe(spin)
        best = None
        for tl in np.radians(np.arange(0, a.tilt + 1, a.tilt_step)):
            for ph in (np.arange(0, 2 * np.pi, np.radians(a.az_step)) if tl > 0 else [0.0]):
                Rt = rot(rot(spin, ph) @ perp, tl)
                for an in np.arange(0, 2 * np.pi, np.radians(a.spin_step)):
                    R = Rt @ rot(spin, an) @ R0
                    T = sc0 - R @ np.array(pt['balls'][0]['c'])
                    sc, mx = probe(pts @ R.T + T, area, [sc0])
                    if best is None or tuple(sc) < tuple(best[0]):
                        best = (sc, mx, R, T,
                                (np.degrees(tl), np.degrees(ph), np.degrees(an)), [sc0])
        return best

    free = list(range(len(sockets)))
    chosen = {}
    for pt in sorted(parts, key=lambda x: -len(x['balls'])):
        if len(pt['balls']) < 2:
            continue
        db = np.linalg.norm(np.array(pt['balls'][0]['c']) - np.array(pt['balls'][1]['c']))
        cands = [(abs(np.linalg.norm(np.array(sockets[i]['c']) - np.array(sockets[j]['c'])) - db), i, j)
                 for i in free for j in free if i != j]
        cands = [c for c in cands if c[0] < a.tol]
        cands.sort()
        best = None
        for _, i, j in cands[:4]:
            r = search_two(pt, i, j, *pt['coarse'])
            if best is None or tuple(r[0]) < tuple(best[0][0]):
                best = (r, i, j)
        if best is None:
            print(f"объект {pt['oid']}: пары гнёзд по расстоянию {db:.2f} мм не нашлось")
            continue
        chosen[pt['oid']] = (best[1], best[2])
        free = [k for k in free if k not in (best[1], best[2])]

    singles = [pt for pt in parts if len(pt['balls']) < 2]
    if singles and free:
        cost = np.full((len(singles), len(free)), 1e9)
        cache = {}
        for r, pt in enumerate(singles):
            for k, si in enumerate(free):
                res = search_one(pt, si, *pt['coarse'])
                cache[(r, k)] = res
                cost[r, k] = res[0][0] + 10 * res[0][2]
        ri, ci = linear_sum_assignment(cost)
        for r, k in zip(ri, ci):
            chosen[singles[r]['oid']] = (free[k],)

    for pt in parts:
        key = chosen.get(pt['oid'])
        if key is None:
            continue
        if len(key) == 2:
            res = search_two(pt, key[0], key[1], *pt['fine'])
        else:
            res = search_one(pt, key[0], *pt['fine'])
        area, mx, R, T, par, seats = res
        report.append((pt['oid'], len(pt['balls']), area, mx, par,
                       [s.tolist() if hasattr(s, 'tolist') else list(s) for s in seats]))
        if a.assembly:
            asm.append((pt['V'] @ R.T + T, pt['F']))
        seat_txt = " + ".join(f"({s[0]:.1f},{s[1]:.1f},{s[2]:.1f})" for s in seats)
        print(f"объект {pt['oid']:>3}: шаров {len(pt['balls'])}, Ø{2*pt['balls'][0]['r']:.3f}; "
              f"гнездо {seat_txt}; поза (наклон {par[0]:.0f}°, азимут {par[1]:.0f}°, "
              f"поворот {par[2]:.0f}°) -> пересечение с торсом: "
              f">0.25 мм {area[0]:7.2f} мм², >0.5 мм {area[1]:6.2f}, >1.0 мм {area[2]:6.2f}; "
              f"макс {mx:.2f} мм")

    if a.assembly:
        import trimesh
        off = 0
        VV, FF = [], []
        for V, F in asm:
            VV.append(V)
            FF.append(F + off)
            off += len(V)
        trimesh.Trimesh(np.vstack(VV), np.vstack(FF), process=False).export(a.assembly)
        print('->', a.assembly)
    if a.out:
        json.dump([[r[0], r[1], list(map(float, r[2])), r[3], list(r[4]), r[5]] for r in report],
                  open(a.out, 'w'), ensure_ascii=False, indent=1)
        print('->', a.out)


# ---------------------------------------------------------------- CLI
def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)

    f = sub.add_parser('find', help='найти шары и гнёзда во всех объектах')
    f.add_argument('src')
    f.add_argument('--rmin', type=float, default=1.2)
    f.add_argument('--rmax', type=float, default=4.0)
    f.add_argument('--cover', type=float, default=0.20, help='доля 4πr², с которой корзина считается сферой')
    f.add_argument('--rms', type=float, default=150.0, help='предел невязки МНК, мкм')
    f.add_argument('-o', '--out')
    f.set_defaults(func=cmd_find)

    p = sub.add_parser('profile', help='профиль стенки гнезда лучами с оси')
    p.add_argument('src')
    p.add_argument('--object', help='id объекта в 3MF')
    p.add_argument('--socket', required=True, help='x,y,z — примерный центр гнезда')
    p.add_argument('--axis', help='x,y,z — ось выхода; по умолчанию считается сама')
    p.add_argument('--angles', type=int, default=144)
    p.add_argument('--tmin', type=float, default=-2.9)
    p.add_argument('--tmax', type=float, default=4.0)
    p.add_argument('--fit-lo', type=float, default=-2.6, help='низ участка подгонки сферы дна')
    p.add_argument('--fit-hi', type=float, default=-1.5)
    p.add_argument('--rmax', type=float, default=3.2)
    p.add_argument('-o', '--out', required=True)
    p.set_defaults(func=cmd_profile)

    c = sub.add_parser('cutter', help='резак-тело вращения по измеренному профилю')
    c.add_argument('prof')
    c.add_argument('--tswitch', type=float, default=-1.8, help='выше — замер, ниже — сфера дна')
    c.add_argument('--tmax', type=float, default=3.1, help='докуда доверять замеру')
    c.add_argument('--out-len', type=float, default=13.0, help='насколько вывести канал наружу')
    c.add_argument('--segments', type=int, default=192)
    c.add_argument('--phase', type=float, default=0.0, help='сдвиг колец по углу, градусы')
    c.add_argument('--offset', default='0,0,0', help='сдвиг кадра детали относительно замера')
    c.add_argument('-o', '--out', required=True)
    c.set_defaults(func=cmd_cutter)

    t = sub.add_parser('fit', help='собрать фигуру и померить пересечения объёмов')
    t.add_argument('src')
    t.add_argument('--torso', required=True, help='id объекта с гнёздами')
    t.add_argument('--vox', type=float, default=0.20)
    t.add_argument('--rmin', type=float, default=2.0)
    t.add_argument('--rmax', type=float, default=3.4)
    t.add_argument('--cover', type=float, default=0.20)
    t.add_argument('--rms', type=float, default=150.0)
    t.add_argument('--tol', type=float, default=0.30, help='допуск на расстояние между шарами, мм')
    t.add_argument('--tilt', type=float, default=30.0, help='предел качания шарнира, градусы')
    t.add_argument('--joint-r', type=float, default=4.2, help='радиус зоны шарнира, она не в счёт')
    t.add_argument('--tilt-step', type=float, default=5.0)
    t.add_argument('--az-step', type=float, default=30.0)
    t.add_argument('--spin-step', type=float, default=15.0)
    t.add_argument('--assembly', help='куда записать собранную фигуру (STL)')
    t.add_argument('--coarse', type=int, default=30000)
    t.add_argument('--fine', type=int, default=400000)
    t.add_argument('-o', '--out')
    t.set_defaults(func=cmd_fit)

    a = ap.parse_args()
    a.func(a)


if __name__ == '__main__':
    main()
