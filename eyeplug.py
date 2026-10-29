#!/usr/bin/env python3
"""The eye of a figurine as a separate printed part, fitted to its socket.

A generator's mesh is no use for an eye: its rim sits in a ragged fold, the
socket carries reversed faces and the pupil is not an oval. What prints and
seats well is a clean solid built from a few measured numbers: a plug whose
side follows the socket wall and whose dome edge follows the socket mouth,
optionally with an iris, a pupil and a glint as nested inserts.

    UV="uv run --quiet --with numpy --with scipy --with trimesh --with manifold3d python"
    $UV tools/eyeplug.py fit    head.3mf --object Head --at=16,12.2,-1.7 -o fit.json
    $UV tools/eyeplug.py fit    head.3mf --object Head --code 2 -o fit.json
    $UV tools/eyeplug.py build  --fit fit.json --pupil 1.6,2.1 --glint white -o out/
    $UV tools/eyeplug.py build  --niche-r 4.0 --side 1.15 --dome-r 5.8 -o out/
    $UV tools/eyeplug.py donor  good_eye.stl --fit fit.json -o out/
    $UV tools/eyeplug.py socket --fit fit.json -o out/

argparse accepts negative coordinates only through `=`. Coordinates are those
of the object's own mesh in the 3MF (or of the STL), not the plate.

## fit: measure the socket, or the painted eye

`--at` is a point near a socket (on its floor or in its mouth). The socket is
taken as a prism along an axis: its wall faces have normals across the axis
and face it, so the axis is the direction least present in their normals (the
smallest eigenvector of the normal scatter), refined in turns with a circle
fitted to the wall. The floor is a plane fitted to the faces looking out of the
socket inside the wall, **checked by its spread, not assumed flat**. The mouth
is measured by rays cast down the axis just outside the wall: it is a height
profile around the circle, smoothed to its first harmonics so that bumps of
the sculpt do not end up on the dome.

`--code` is a filament of the paint (1-based, as Bambu Studio numbers them)
when there is no socket and the eye is only painted on: every connected patch
of that colour is one eye. A sphere is fitted to it, and an ellipse to its
border seen down the patch's mean normal. The socket for it is then cut with
`socket`.

## build: the part, in its print pose

The eye is a plug - its side the socket outline less the clearance, its
bottom the socket floor plane - under a dome: a spherical cap (`--dome-r`, or
`--dome-h` above the edge) whose edge follows the mouth (`--rim follow`), sunk
by `--sink` so no lip stands proud of the face. An elliptical socket gives an
ellipsoidal dome over an elliptical plug.

Inserts are elliptical prisms cut out of the dome, top flush with it, bottom a
flat plane `--edge-t` below the dome at the insert's own border: `--iris a,b`,
`--pupil a,b`, each with `--*-at u,v` and `--*-ang` in the eye's frame (u
horizontal, v up, see `--up`). Every outer part gets a pocket for every inner
one. The glint: `hole` is a through hole in the pupil, `white` leaves a column
of the white standing through the pupil, `pin` is a separate pin of
`--glint-d`.

**Clearance comes from how the two walls print.** Walls running along Z in
thin layers fit at 0.05 per side; a wall across the layers - a dome, a plug
lying on its edge - is stepped by the layers and needs 0.15. The eye is printed
on its edge by default (axis horizontal, so the dome is drawn by the perimeters
and not stepped), which puts its side across the layers: 0.15 in the socket.
Inserts print flat, walls along Z; their pockets take 0.15 in a white printed
on edge and 0.05 in one printed flat. `--gap` and `--insert-gap` override.

## donor: an eye that already printed well, rescaled

The donor's axis is the normal of its largest flat face (the back) refined by
its side wall; it is scaled uniformly by (socket radius - clearance) / donor
radius, which keeps the dome's shape, and seated on the socket floor.
`--rim follow` additionally stretches the side so its top edge follows the
mouth, turning the donor about its axis to match the mouth's saddle first.

## socket: a cutter when the head has no socket, or a crooked one

A prism along the eye axis from the floor (or `--depth` below the painted
edge) to well above the surface, ready to subtract from the head.

Every command prints numbers to check: radii, depth, flatness, the measured
wall gap ray by ray when the head is at hand, volumes and closedness.
"""
import argparse, json, os, pathlib, sys
import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

GAP_ACROSS = 0.15        # стенка поперёк слоёв: купол, пробка на ребре
GAP_ALONG = 0.05         # обе стенки вдоль Z, тонкий слой
SINK = 0.02              # край купола ниже устья: без порожка над лицом
FLOOR_GAP = 0.02         # дно встык
NA = 180                 # точек по окружности


def die(msg):
    sys.exit(f"ОШИБКА: {msg}")


def vec(s):
    return np.array([float(x) for x in s.split(',')])


# ---------------------------------------------------------------- reading
def load(src, obj=None):
    """(V, F, lab) of an STL, or of one object of a 3MF with its paint."""
    if src.lower().endswith('.stl'):
        import trimesh
        m = trimesh.load(src, process=False, force='mesh')
        m.merge_vertices()
        return np.asarray(m.vertices, float), np.asarray(m.faces, np.int64), None
    import zipfile, paint
    z = zipfile.ZipFile(src)
    name, ext, ent = paint.pick_object(z, obj)
    raw = z.read(ent).decode('utf-8')
    V = np.array(paint.VERT.findall(raw), dtype=np.float64)
    F, lab = [], []
    for m in paint.TRI.finditer(raw):
        F.append((int(m.group(1)), int(m.group(2)), int(m.group(3))))
        c = paint.code_of(m)
        lab.append(ext if c == '' else paint.CODE2FIL.get(c, paint.SPLIT))
    return V, np.array(F, np.int64), np.array(lab, np.int32)


def faces(V, F):
    P = V[F]
    n = np.cross(P[:, 1] - P[:, 0], P[:, 2] - P[:, 0])
    a = np.linalg.norm(n, axis=1)
    a[a == 0] = 1e-30
    return P.mean(1), n / a[:, None], a / 2


def frame(ax, up=(0, 0, 1.0)):
    """u horizontal, v towards `up`, ax out of the socket; u x v = ax."""
    ax = ax / np.linalg.norm(ax)
    up = np.asarray(up, float)
    if abs(up @ ax) > 0.95:
        up = np.array([0, 1.0, 0]) if abs(ax[1]) < 0.95 else np.array([1.0, 0, 0])
    v = up - (up @ ax) * ax
    v /= np.linalg.norm(v)
    return np.cross(v, ax), v, ax


def ray_hits(V, F, o, d):
    """Distances t > 0 of every hit of the ray o + t*d (Moller-Trumbore)."""
    A, B, C = V[F[:, 0]], V[F[:, 1]], V[F[:, 2]]
    e1, e2 = B - A, C - A
    p = np.cross(d, e2)
    det = np.einsum('ij,ij->i', e1, p)
    ok = np.abs(det) > 1e-12
    inv = np.zeros_like(det)
    inv[ok] = 1 / det[ok]
    s = o - A
    u = np.einsum('ij,ij->i', s, p) * inv
    q = np.cross(s, e1)
    v = (q @ d) * inv
    t = np.einsum('ij,ij->i', e2, q) * inv
    m = ok & (u >= 0) & (v >= 0) & (u + v <= 1) & (t > 1e-9)
    return np.sort(t[m])


def circle_fit(X, w):
    A = np.c_[2 * X, np.ones(len(X))]
    sol = np.linalg.lstsq(A * w[:, None], (X ** 2).sum(1) * w, rcond=None)[0]
    return sol[:2], float(np.sqrt(sol[2] + sol[:2] @ sol[:2]))


def ellipse_fit(X):
    """Semi-axes (major, minor), centre and angle of the major axis: a conic by SVD."""
    m = X.mean(0)
    x, y = (X - m).T
    D = np.c_[x * x, x * y, y * y, x, y, np.ones_like(x)]
    A, B, C, Dd, Ee, Ff = np.linalg.svd(D, full_matrices=False)[2][-1]
    M = np.array([[A, B / 2], [B / 2, C]])
    c = np.linalg.solve(2 * M, -np.array([Dd, Ee]))
    k = -(Ff + np.array([Dd, Ee]) @ c / 2)
    w, U = np.linalg.eigh(M / k)
    if (w <= 0).any():
        return None
    ax = 1 / np.sqrt(w)
    i = int(np.argmax(ax))
    return float(ax[i]), float(ax[1 - i]), c + m, float(np.arctan2(U[1, i], U[0, i]))


def smooth_periodic(y, keep=5):
    Z = np.fft.rfft(y)
    Z[keep:] = 0
    return np.fft.irfft(Z, len(y))


def periodic(th_s, val, th):
    o = np.argsort(th_s)
    xs, ys = th_s[o], val[o]
    xs = np.r_[xs - 2 * np.pi, xs, xs + 2 * np.pi]
    ys = np.r_[ys, ys, ys]
    return np.interp(np.mod(th + np.pi, 2 * np.pi) - np.pi, xs, ys)


# ---------------------------------------------------------------- fit
def fit_niche(V, F, seed, reach, up):
    C, N, A = faces(V, F)
    near = np.linalg.norm(C - seed, axis=1) < reach
    if near.sum() < 50:
        die(f"возле {seed} в радиусе {reach} мм граней {near.sum()} — точка мимо сетки")
    ax = (N[near] * A[near, None]).sum(0)
    ax /= np.linalg.norm(ax)
    c, R = seed.copy(), None
    for it in range(12):
        d = C - c
        h = d @ ax
        rad = d - np.outer(h, ax)
        r = np.linalg.norm(rad, axis=1)
        rh = rad / np.maximum(r, 1e-9)[:, None]
        wall = near & (np.abs(N @ ax) < 0.35) & ((N * rh).sum(1) < -0.6) & (np.abs(h) < reach)
        if R is not None:
            wall &= np.abs(r - R) < max(0.6, 0.15 * R)
        if wall.sum() < 20:
            die("у затравки нет стенки ниши: граней поперёк оси, смотрящих внутрь, почти нет")
        M = (N[wall] * A[wall, None]).T @ N[wall]
        new = np.linalg.eigh(M)[1][:, 0]
        ax = new if new @ ax > 0 else -new
        u, v, ax = frame(ax, up)
        X = np.c_[(C[wall] - c) @ u, (C[wall] - c) @ v]
        cc, R = circle_fit(X, A[wall])
        c = c + cc[0] * u + cc[1] * v
    # окончательно — по вершинам стенки: центр грани лежит на хорде, внутри круга
    Pw = V[np.unique(F[wall])] - c
    Pw = Pw[np.abs(Pw @ ax) < reach]
    X = np.c_[Pw @ u, Pw @ v]
    cc, R = circle_fit(X, np.ones(len(X)))
    c = c + cc[0] * u + cc[1] * v
    X = X - cc
    rms = float(np.sqrt(np.mean((np.linalg.norm(X, axis=1) - R) ** 2)))
    el = ellipse_fit(X)
    elliptic = el is not None and el[0] - el[1] > 0.05
    if elliptic:                                       # эллиптическая ниша: центр — эллипса
        c = c + el[2][0] * u + el[2][1] * v
        el = (el[0], el[1], np.zeros(2), el[3])
    d = C - c
    h = d @ ax
    r = np.linalg.norm(d - np.outer(h, ax), axis=1)
    # дно: плоскость по граням внутри стенки, смотрящим наружу
    fl = near & (r < R - 0.25) & (N @ ax > 0.8)
    if fl.sum() < 5:
        die("дна у ниши не нашёл: внутри стенки нет граней, смотрящих наружу")
    xf, yf = (C[fl] - c) @ u, (C[fl] - c) @ v
    h_low = np.percentile(h[fl], 5)
    fl_idx = np.where(fl)[0][h[fl] < h_low + 1.5]       # нижний слой, без чужих полок
    xf, yf, hf = (C[fl_idx] - c) @ u, (C[fl_idx] - c) @ v, h[fl_idx]
    pl = np.linalg.lstsq(np.c_[np.ones(len(xf)), xf, yf] * A[fl_idx, None], hf * A[fl_idx],
                         rcond=None)[0]
    res = hf - (pl[0] + pl[1] * xf + pl[2] * yf)
    spread = float(np.percentile(res, 95) - np.percentile(res, 5))
    c = c + pl[0] * ax
    pl = [0.0, float(pl[1]), float(pl[2])]
    # устье: лучи вниз по оси сразу за стенкой
    sel = np.where(np.linalg.norm(C - c, axis=1) < R + reach)[0]
    th = np.linspace(-np.pi, np.pi, NA, endpoint=False)
    rim = np.full(NA, np.nan)
    top = reach + 5
    for i, t in enumerate(th):
        if elliptic:                                   # за стенкой эллипса, тот же угол параметра
            q = outline(el[0] + 0.05, el[1] + 0.05, el[3], NA)[i]
            o = c + q[0] * u + q[1] * v + top * ax
        else:
            o = c + (R + 0.05) * (np.cos(t) * u + np.sin(t) * v) + top * ax
        hit = ray_hits(V, F[sel], o, -ax)
        if len(hit):
            rim[i] = top - hit[0]
    bad = np.isnan(rim)
    if bad.all():
        die("устье не нашлось: лучи вдоль оси не встретили поверхность у стенки")
    if bad.any():
        rim[bad] = np.interp(th[bad], th[~bad], rim[~bad], period=2 * np.pi)
    rim_s = smooth_periodic(rim)
    return dict(kind='niche', c=c.tolist(), axis=ax.tolist(), u=u.tolist(), v=v.tolist(),
                R=R, rms=rms, ell=None if el is None else [el[0], el[1], el[3]],
                ell_c=None if el is None else [float(x) for x in el[2]],
                floor=pl, floor_spread=spread, n_wall=int(wall.sum()),
                rim_th=th.tolist(), rim=rim_s.tolist(), rim_param='ellipse' if elliptic else 'polar', rim_raw_dev=float(np.abs(rim_s - rim).max()),
                misses=int(bad.sum()))


def fit_zones(V, F, lab, code, min_area, up):
    import scipy.sparse as sp
    from scipy.sparse.csgraph import connected_components
    if lab is None:
        die("--code только для 3MF с покраской")
    # сварка по координатам: у сетки генератора вершины на швах развёртки двоятся
    key = np.round(V / 1e-5).astype(np.int64)
    _, inv = np.unique(key, axis=0, return_inverse=True)
    Fw = inv.ravel()[F]
    sel = np.where(lab == code)[0]
    if not len(sel):
        die(f"граней с филаментом {code} нет; есть {sorted(set(lab.tolist()))}")
    C, N, A = faces(V, F)
    n = len(sel)
    rows = np.repeat(np.arange(n), 3)
    G = sp.coo_matrix((np.ones(3 * n), (rows, Fw[sel].ravel())), shape=(n, inv.max() + 1)).tocsr()
    k, cc = connected_components(G @ G.T, directed=False)
    out = []
    for ci in range(k):
        fi = sel[cc == ci]
        if A[fi].sum() < min_area:
            continue
        P, w = C[fi], A[fi]
        M = np.c_[2 * P, np.ones(len(P))]
        sol = np.linalg.lstsq(M * w[:, None], (P ** 2).sum(1) * w, rcond=None)[0]
        ctr = sol[:3]
        Rs = float(np.sqrt(sol[3] + ctr @ ctr))
        rms = float(np.sqrt(np.average((np.linalg.norm(P - ctr, axis=1) - Rs) ** 2, weights=w)))
        ax = (N[fi] * w[:, None]).sum(0)
        ax /= np.linalg.norm(ax)
        # граница зоны: рёбра, которые в зоне встречаются один раз
        E = np.sort(np.concatenate([Fw[fi][:, [0, 1]], Fw[fi][:, [1, 2]], Fw[fi][:, [2, 0]]]), 1)
        ku, cnt = np.unique(E[:, 0] * (1 << 32) + E[:, 1], return_counts=True)
        bv = np.unique(np.c_[ku[cnt == 1] >> 32, ku[cnt == 1] & 0xffffffff].ravel())
        Vw = np.zeros((inv.max() + 1, 3))
        Vw[inv.ravel()] = V
        B = Vw[bv]
        u, v, ax = frame(ax, up)
        hb = float(np.median((B - ctr) @ ax))
        base = ctr + hb * ax
        X = np.c_[(B - base) @ u, (B - base) @ v]
        el = ellipse_fit(X)
        if el is None:
            die(f"зона {ci}: граница не эллипс")
        base = base + el[2][0] * u + el[2][1] * v
        apex = float(Rs - hb) if (ctr - base) @ ax < 0 else float(Rs + hb)
        out.append(dict(kind='zone', c=base.tolist(), axis=ax.tolist(), u=u.tolist(), v=v.tolist(),
                        Rs=Rs, rms=rms, ell=[el[0], el[1], el[3]], dome_h=float((V[F[fi]].reshape(-1, 3) - base).dot(ax).max()),
                        area=float(w.sum()), base_spread=float(np.ptp((B - base) @ ax))))
    if not out:
        die(f"зон филамента {code} площадью от {min_area} мм² нет")
    return out


def cmd_fit(a):
    V, F, lab = load(a.src, a.object)
    up = vec(a.up)
    eyes = []
    if a.at:
        for s in a.at:
            eyes.append(fit_niche(V, F, vec(s), a.reach, up))
    if a.code is not None:
        eyes += fit_zones(V, F, lab, a.code, a.min_area, up)
    if not eyes:
        die("нужна затравка: --at=x,y,z или --code N")
    print(f"{os.path.basename(a.src)}: вершин {len(V):,}, граней {len(F):,}")
    for i, e in enumerate(eyes, 1):
        c, ax = np.round(e['c'], 3), np.round(e['axis'], 4)
        if e['kind'] == 'niche':
            rim = np.array(e['rim'])
            tilt = np.degrees(np.arctan(np.hypot(*e['floor'][1:])))
            print(f"глаз {i}: ниша R {e['R']:.3f} (Ø{2*e['R']:.3f}), невязка стенки {e['rms']*1000:.1f} мкм, "
                  f"граней стенки {e['n_wall']}")
            print(f"   ось {ax}, центр дна {c}")
            if e['ell']:
                print(f"   эллипс стенки {e['ell'][0]:.3f} × {e['ell'][1]:.3f}, "
                      f"сжатие {1 - e['ell'][1]/e['ell'][0]:.3f}")
            print(f"   дно: наклон к оси {tilt:.1f}°, разброс {e['floor_spread']:.3f} мм"
                  + ("  — НЕ ПЛОСКОЕ" if e['floor_spread'] > 0.1 else ""))
            print(f"   глубина устья {rim.min():.3f}..{rim.max():.3f}, средняя {rim.mean():.3f} мм"
                  f" (сглаживание сдвинуло край до {e['rim_raw_dev']:.3f}, промахов лучей {e['misses']})")
        else:
            print(f"глаз {i}: зона краски {e['area']:.1f} мм², сфера R {e['Rs']:.3f} "
                  f"(невязка {e['rms']*1000:.1f} мкм)")
            print(f"   ось {ax}, центр основания {c}")
            print(f"   контур {2*e['ell'][0]:.3f} × {2*e['ell'][1]:.3f} мм, высота купола {e['dome_h']:.3f}, "
                  f"край вне плоскости на {e['base_spread']:.3f} мм")
    if a.out:
        json.dump(dict(src=os.path.abspath(a.src), object=a.object, eyes=eyes),
                  open(a.out, 'w'), ensure_ascii=False, indent=1)
        print('->', a.out)


# ---------------------------------------------------------------- geometry
def outline(a, b, ang, n=NA):
    th = np.linspace(-np.pi, np.pi, n, endpoint=False)
    p = np.c_[a * np.cos(th), b * np.sin(th)]
    c, s = np.cos(ang), np.sin(ang)
    return p @ np.array([[c, s], [-s, c]])


def dome_profile(rb, dome_r=None, dome_h=None):
    """z(s) of a spherical cap over a base of mean radius rb, s = 0 at the edge... 1 at the top."""
    if dome_h is not None:
        dome_r = (rb ** 2 + dome_h ** 2) / (2 * dome_h)
    if dome_r < rb:
        die(f"радиус купола {dome_r:.2f} меньше радиуса глаза {rb:.2f}: купол не замыкается")
    k = np.sqrt(dome_r ** 2 - rb ** 2)
    hc = dome_r - k
    return (lambda s: np.sqrt(dome_r ** 2 - (rb * s) ** 2) - k), hc, dome_r


def eye_mesh(O, z_bot, z_side, dome, n_side=6, n_dome=24):
    """Closed mesh in the local frame: plug with outline O (N,2), bottom z_bot(xy),
    side top z_side (N,), dome(s) added on top with the edge warp fading to the centre."""
    N = len(O)
    zm = z_side.mean()
    rings = []
    s_bot = np.linspace(0, 1, 8)[1:]             # дно: от центра к краю
    for s in s_bot:
        xy = O * s
        rings.append(np.c_[xy, z_bot(xy)])
    zb = z_bot(O)
    for t in np.linspace(0, 1, n_side + 1)[1:]:
        rings.append(np.c_[O, zb + t * (z_side - zb)])
    for s in np.linspace(1, 0, n_dome + 1)[1:-1]:
        rings.append(np.c_[O * s, zm + (z_side - zm) * s ** 2 + dome(s)])
    Vv = np.vstack([[[0, 0, float(z_bot(np.zeros((1, 2)))[0])]], np.vstack(rings),
                    [[0, 0, zm + dome(0.0)]]])
    nr = len(rings)
    Fv = []
    j = np.arange(N)
    jn = (j + 1) % N
    Fv += np.c_[np.zeros(N, int), 1 + jn, 1 + j].tolist()
    for r in range(nr - 1):
        a0, b0 = 1 + r * N, 1 + (r + 1) * N
        Fv += np.c_[a0 + j, a0 + jn, b0 + jn].tolist() + np.c_[a0 + j, b0 + jn, b0 + j].tolist()
    top = len(Vv) - 1
    last = 1 + (nr - 1) * N
    Fv += np.c_[last + j, last + jn, np.full(N, top)].tolist()
    Fv = np.array(Fv, np.int64)
    from partedit import volume
    if volume(Vv, Fv) < 0:
        Fv = Fv[:, [0, 2, 1]]
    return Vv, Fv


def top_of(O, ang, z_side, dome):
    """Height of the dome over a point xy of the eye, exactly as eye_mesh draws it."""
    N = len(O)
    a_ = np.abs(O @ [np.cos(ang), np.sin(ang)]).max()
    b_ = np.abs(O @ [-np.sin(ang), np.cos(ang)]).max()
    zm = z_side.mean()

    def f(xy):
        c, s = np.cos(ang), np.sin(ang)
        q = np.asarray(xy, float) @ np.array([[c, -s], [s, c]])
        e = q / [a_, b_]
        sr = np.linalg.norm(e, axis=1)
        t = np.arctan2(e[:, 1], e[:, 0])
        zs = periodic(np.linspace(-np.pi, np.pi, N, endpoint=False), z_side, t)
        return zm + (zs - zm) * sr ** 2 + dome(np.clip(sr, 0, 1))
    return f


def to_m3(V, F):
    import manifold3d as m3
    return m3.Manifold(m3.Mesh(vert_properties=np.asarray(V, np.float32),
                               tri_verts=np.asarray(F, np.uint32)))


def from_m3(m):
    g = m.to_mesh()
    return np.asarray(g.vert_properties)[:, :3].astype(float), np.asarray(g.tri_verts, np.int64)


def prism(poly, z0, z1, grow=0.0):
    import manifold3d as m3
    cs = m3.CrossSection([np.asarray(poly, float).tolist()])
    if grow:
        cs = cs.offset(grow, m3.JoinType.Round, circular_segments=64)
    return cs.extrude(z1 - z0).translate((0, 0, z0))


def disc(c, r, n=64):
    t = np.linspace(0, 2 * np.pi, n, endpoint=False)
    return np.c_[c[0] + r * np.cos(t), c[1] + r * np.sin(t)]


def to_world(L, e):
    c, u, v, ax = (np.array(e[k]) for k in ('c', 'u', 'v', 'axis'))
    return c + L[:, :1] * u + L[:, 1:2] * v + L[:, 2:3] * ax


def pose_edge(L, down):
    """Axis along X, the outline direction `down` (angle in uv) onto the bed."""
    d = np.array([np.cos(down), np.sin(down)])
    e = np.array([-d[1], d[0]])
    P = np.c_[L[:, 2], L[:, :2] @ e, -(L[:, :2] @ d)]
    return place(P)


def place(P):
    P = P.copy()
    P[:, :2] -= (P[:, :2].max(0) + P[:, :2].min(0)) / 2
    P[:, 2] -= P[:, 2].min()
    return P


def pose_flat(L, plane):
    """Bottom plane z = p0 + gx*x + gy*y onto the bed."""
    n = np.array([-plane[1], -plane[2], 1.0])
    n /= np.linalg.norm(n)
    z = np.array([0, 0, 1.0])
    vv = np.cross(n, z)
    s, cth = np.linalg.norm(vv), n @ z
    if s < 1e-9:
        return place(L)
    K = np.array([[0, -vv[2], vv[1]], [vv[2], 0, -vv[0]], [-vv[1], vv[0], 0]]) / s
    Rm = np.eye(3) + s * K + (1 - cth) * K @ K
    return place(L @ Rm.T)


def write_stl(path, V, F):
    import trimesh
    trimesh.Trimesh(V, F, process=False).export(path)


def wall_gap(V, F, e, L, z_lo, z_hi, n=72):
    """Gap from the eye's side to the head wall, ray by ray, in the radial direction."""
    c, u, v, ax = (np.array(e[k]) for k in ('c', 'u', 'v', 'axis'))
    C = V[F].mean(1)
    sel = F[np.linalg.norm(C - c, axis=1) < 3 * max(np.abs(L[:, :2]).max(), 1) + 5]
    side = L[(L[:, 2] > z_lo) & (L[:, 2] < z_hi)]
    r = np.linalg.norm(side[:, :2], axis=1)
    side = side[r > r.max() - 0.3]
    if len(side) > n * 3:
        side = side[np.linspace(0, len(side) - 1, n * 3).astype(int)]
    gaps = []
    for p in side:
        dl = np.r_[p[:2] / np.linalg.norm(p[:2]), 0]
        dw = dl[0] * u + dl[1] * v
        o = to_world(p[None], e)[0]
        hit = ray_hits(V, sel, o - 0.5 * dw, dw)       # от точки внутри: стенка ближе 0.5 — пересечение
        if len(hit):
            gaps.append(hit[0] - 0.5)
    return np.array(gaps)


# ---------------------------------------------------------------- build
def resolve(a):
    """Eye records: from --fit, or one made of the hand numbers."""
    if a.fit:
        J = json.load(open(a.fit))
        eyes = J['eyes']
        if a.index:
            eyes = [eyes[i - 1] for i in a.index]
        return J, eyes
    if a.niche_r is None and a.niche_ab is None:
        die("нужен --fit или размеры ниши: --niche-r R  (или --niche-ab a,b[,угол])")
    e = dict(kind='niche', c=[0, 0, 0], axis=[0, 0, 1.0], u=[1.0, 0, 0], v=[0, 1.0, 0],
             R=a.niche_r, ell=None, floor=[0, 0, 0], rim=None)
    if a.niche_ab:
        ab = vec(a.niche_ab)
        e['ell'] = [ab[0], ab[1], np.radians(ab[2]) if len(ab) > 2 else 0.0]
        e['R'] = float(np.sqrt(ab[0] * ab[1]))
    return None, [e]


def eye_outline(e, gap):
    """Outline of the plug (N,2) and of the socket around it; eye shrunk by gap
    inside a niche, socket grown by gap around a painted zone."""
    if e['kind'] == 'zone':
        a_, b_, ang = e['ell']
        return outline(a_, b_, ang), outline(a_ + gap, b_ + gap, ang), ang
    el = e.get('ell')
    if el and el[0] - el[1] > 0.05:
        a_, b_, ang = el
        return outline(a_ - gap, b_ - gap, ang), outline(a_, b_, ang), ang
    R = e['R']
    return outline(R - gap, R - gap, 0), outline(R, R, 0), 0.0


def cmd_build(a):
    J, eyes = resolve(a)
    head = None
    if J and not a.no_check and os.path.exists(J['src']):
        head = load(J['src'], J.get('object'))[:2]
    print_edge = a.print == 'edge'
    gap = a.gap if a.gap is not None else (GAP_ACROSS if print_edge else GAP_ALONG)
    igap = a.insert_gap if a.insert_gap is not None else (GAP_ACROSS if print_edge else GAP_ALONG)
    os.makedirs(a.out, exist_ok=True)
    print(f"зазор глаза в нише {gap:.2f} на сторону (печать {'на ребре' if print_edge else 'плашмя'}), "
          f"вкладок {igap:.2f}, по дну {a.floor_gap:.2f}")
    for i, e in enumerate(eyes, 1):
        O, Osock, ang = eye_outline(e, gap)
        th = np.linspace(-np.pi, np.pi, NA, endpoint=False)
        pl = e.get('floor') or [0, 0, 0]
        if e['kind'] == 'zone':
            depth = a.depth
            z_bot = lambda xy: np.full(len(xy), -depth + a.floor_gap)
            z_side = np.zeros(NA)
            dome, hc, dr = dome_profile(np.linalg.norm(O, axis=1).mean(), dome_r=e['Rs'])
        else:
            z_bot = lambda xy, pl=pl: a.floor_gap + pl[0] + pl[1] * xy[:, 0] + pl[2] * xy[:, 1]
            if e.get('rim') and a.rim == 'follow':
                if e.get('rim_param') == 'ellipse' and len(O) == len(e['rim']):
                    z_side = np.array(e['rim']) - a.sink         # тот же параметр эллипса
                else:
                    ang_o = np.arctan2(O[:, 1], O[:, 0])
                    z_side = periodic(np.array(e['rim_th']), np.array(e['rim']), ang_o) - a.sink
            else:
                side = a.side if a.side is not None else (np.mean(e['rim']) - a.sink if e.get('rim') else 1.15)
                z_side = np.full(NA, side)
            rb = np.linalg.norm(O, axis=1).mean()
            dome, hc, dr = dome_profile(rb, dome_r=None if a.dome_h else a.dome_r, dome_h=a.dome_h)
        Vl, Fl = eye_mesh(O, z_bot, z_side, dome)
        top = top_of(O, ang, z_side, dome)
        white = to_m3(Vl, Fl)
        zmax = float(Vl[:, 2].max())
        # вкладки: снаружи внутрь
        layers = []
        for nm in ('iris', 'pupil'):
            ab = getattr(a, nm)
            if not ab:
                continue
            ab = vec(ab)
            at = vec(getattr(a, nm + '_at')) if getattr(a, nm + '_at') else np.zeros(2)
            po = outline(ab[0], ab[1], np.radians(getattr(a, nm + '_ang'))) + at
            s_edge = np.linalg.norm(po, axis=1).max() / np.linalg.norm(O, axis=1).min()
            if s_edge >= 1:
                die(f"{nm} {ab} не помещается в глаз")
            zd = top(po)                     # дно вкладки: edge-t ниже купола у её края
            layers.append(dict(name=nm, poly=po, zb=float(zd.min() - a.edge_t)))
        glint = None
        if layers and a.glint != 'none':
            gc = vec(a.glint_at) if a.glint_at else layers[-1]['poly'].mean(0)
            glint = (gc, a.glint_d / 2)
        parts = {}
        sub_all = []                                  # карманы всех внутренних деталей
        for k in range(len(layers) - 1, -1, -1):
            L = layers[k]
            poly = L['poly']
            body = white ^ prism(poly, L['zb'], zmax + 1)
            if k == len(layers) - 1 and glint:
                gcirc = disc(*glint)
                if a.glint == 'hole' or a.glint == 'white':
                    body = body - prism(gcirc, L['zb'] - 1, zmax + 1)
                else:
                    body = body - prism(gcirc, L['zb'] - 1, zmax + 1, grow=igap)
            for s in sub_all:
                body = body - s
            parts[L['name']] = body
            pocket = prism(poly, L['zb'] - a.floor_gap, zmax + 1, grow=igap)
            if k == len(layers) - 1 and glint and a.glint == 'white':
                # столбик белка через зрачок: карман без круга блика
                pocket = pocket - prism(disc(glint[0], glint[1] - igap), L['zb'] - 2, zmax + 2)
            sub_all.append(pocket)
        if glint and a.glint == 'pin' and layers:
            zb = layers[-1]['zb'] - a.pin_depth
            pin = white ^ prism(disc(*glint), zb, zmax + 1)
            parts['glint'] = pin
            sub_all.append(prism(disc(*glint), zb - a.floor_gap, zmax + 1, grow=igap))
        w = white
        for s in sub_all:
            w = w - s
        if 'glint' in parts:                          # гнездо штырька — во всех, через кого он идёт
            for nm in parts:
                if nm != 'glint':
                    parts[nm] = parts[nm] - sub_all[-1]
        parts = {'white': w, **parts}
        print(f"\nглаз {i}: R пробки {np.linalg.norm(O, axis=1).mean():.3f}, "
              f"контур {2*np.abs(O[:,0]).max():.3f} × {2*np.abs(O[:,1]).max():.3f}, "
              f"купол R {dr:.3f} высотой {hc:.3f}, бок {z_side.min():.3f}..{z_side.max():.3f}, "
              f"высота глаза {zmax - Vl[:,2].min():.3f}")
        for nm, m in parts.items():
            V, F = from_m3(m)
            if not len(F):
                die(f"{nm}: пустое тело")
            from partedit import volume, watertight
            if nm == 'white':
                if print_edge:
                    ang_o = np.arctan2(O[:, 1], O[:, 0])
                    if abs(O[:, 0].max() - O[:, 1].max()) > 0.05:
                        cand = [ang + np.pi / 2, ang - np.pi / 2]       # на плоский бок эллипса
                        down = max(cand, key=lambda t: periodic(ang_o, z_side, np.array([t]))[0])
                    else:
                        down = float(ang_o[np.argmax(z_side)])         # самая длинная образующая
                    Pp = pose_edge(V, down)
                else:
                    Pp = pose_flat(V, [pl[0] + a.floor_gap, pl[1], pl[2]])
            else:
                Pp = place(V)
            f = os.path.join(a.out, f"eye{i}_{nm}.stl")
            write_stl(f, Pp, F)
            write_stl(os.path.join(a.out, f"eye{i}_{nm}_inplace.stl"), to_world(V, e), F)
            ext = np.ptp(Pp, 0)
            print(f"   {nm:6s} объём {volume(V, F):8.2f} мм³, габарит {ext[0]:.2f} × {ext[1]:.2f} × {ext[2]:.2f}, "
                  f"замкнуто {'да' if watertight(V, F) else 'НЕТ'}  -> {f}")
            if nm != 'white':
                Lr = next((L for L in layers if L['name'] == nm), None)
                if Lr is not None:
                    t_e = top(Lr['poly']) - Lr['zb']
                    t_c = top(Lr['poly'].mean(0)[None])[0] - Lr['zb']
                    print(f"          толщина у края {t_e.min():.2f}..{t_e.max():.2f}, в центре {t_c:.2f}")
        names = list(parts)
        worst = 0.0
        for x in range(len(names)):
            for y in range(x + 1, len(names)):
                worst = max(worst, (parts[names[x]] ^ parts[names[y]]).volume())
        if len(names) > 1:
            gaps = [parts['white'].min_gap(parts[nm], 1.0) for nm in names[1:]]
            print(f"   детали друг в друга: худшее пересечение {worst:.4f} мм³, "
                  f"наименьший зазор вкладок {min(gaps):.3f} мм")
        if head is not None and e['kind'] == 'niche':
            Vw, Fw = from_m3(w)
            g = wall_gap(head[0], head[1], e, Vw, float(np.min(z_side)) * 0.2 + 0.1,
                         float(np.min(z_side)) * 0.8)
            if len(g):
                print(f"   зазор по стенке к голове: мин {g.min():.3f}, медиана {np.median(g):.3f}, "
                      f"макс {g.max():.3f} мм ({len(g)} лучей)"
                      + ("  — ГЛАЗ ВРЕЗАЕТСЯ В ГОЛОВУ" if g.min() < 0 else ""))


# ---------------------------------------------------------------- donor
def donor_frame(V, F):
    C, N, A = faces(V, F)
    q = np.round(N * 30)
    _, inv = np.unique(q, axis=0, return_inverse=True)
    inv = inv.ravel()
    k = np.argmax(np.bincount(inv, A))
    back = (N[inv == k] * A[inv == k, None]).sum(0)
    ax = -back / np.linalg.norm(back)
    for _ in range(6):
        s = np.abs(N @ ax) < 0.25
        M = (N[s] * A[s, None]).T @ N[s]
        new = np.linalg.eigh(M)[1][:, 0]
        ax = new if new @ ax > 0 else -new
    u, v, ax = frame(ax)
    s = np.abs(N @ ax) < 0.25
    cc, R = circle_fit(np.c_[C[s] @ u, C[s] @ v], A[s])
    h0 = (V @ ax).min()
    c = cc[0] * u + cc[1] * v + h0 * ax
    return c, u, v, ax


def cmd_donor(a):
    import trimesh
    m = trimesh.load(a.donor, process=False, force='mesh')
    m.merge_vertices()
    V0, F0 = np.asarray(m.vertices, float), np.asarray(m.faces, np.int64)
    c, u, v, ax = donor_frame(V0, F0)
    d = V0 - c
    L = np.c_[d @ u, d @ v, d @ ax]
    r = np.linalg.norm(L[:, :2], axis=1)
    th = np.arctan2(L[:, 1], L[:, 0])
    Rd = float(r.max())
    side = r > Rd - 0.01
    topr = side & (L[:, 2] > 0.3)
    if topr.sum() < 8:
        die("у донора не нашёл боковины: это не пробка с цилиндрическим боком")
    # верх боковины: по каждому углу самая высокая точка бока
    key = np.round(th[topr] / np.radians(0.5)).astype(int)
    order = np.lexsort((L[topr, 2], key))
    last = np.r_[key[order][1:] != key[order][:-1], True]
    th_t, h_t = th[topr][order][last], L[topr, 2][order][last]
    Hd = lambda q: periodic(th_t, h_t, q)
    J = json.load(open(a.fit))
    eyes = J['eyes'] if not a.index else [J['eyes'][i - 1] for i in a.index]
    gap = a.gap if a.gap is not None else GAP_ACROSS
    os.makedirs(a.out, exist_ok=True)
    print(f"донор {os.path.basename(a.donor)}: R {Rd:.3f}, высота {np.ptp(L[:,2]):.3f}, "
          f"бок {Hd(np.linspace(-np.pi, np.pi, 90)).min():.3f}..{Hd(np.linspace(-np.pi, np.pi, 90)).max():.3f}")
    from partedit import volume, watertight
    for i, e in enumerate(eyes, 1):
        if e['kind'] != 'niche':
            die("донор садится только в нишу (fit --at)")
        k = (e['R'] - gap) / Rd
        h2, r2, th2 = k * L[:, 2], k * r, th.copy()
        if a.rim == 'follow' and e.get('rim'):
            grid = np.array(e['rim_th'])
            Hn = lambda q: periodic(grid, np.array(e['rim']), q) - a.sink - a.floor_gap
            best = min(np.radians(np.arange(0, 360, 1.0)),
                       key=lambda p: np.var(Hn(grid + p) - k * Hd(grid)))
            th2 = th + best
            E = Hn(grid + best) - k * Hd(grid)
            Eb = E.mean()
            Et = lambda q: periodic(grid, E - Eb, q)
            h2 = np.where(side & (L[:, 2] <= Hd(th) + 1e-6),
                          k * L[:, 2] * Hn(th2) / (k * Hd(th)),
                          k * L[:, 2] + Eb + Et(th) * (r / Rd) ** 2)
            h2[L[:, 2] < 1e-6] = 0.0
            print(f"глаз {i}: поворот {np.degrees(best):.0f}°, купол поднят на {Eb:+.3f}, "
                  f"остаток седла {np.ptp(E - Eb):.3f}")
        Lw = np.c_[r2 * np.cos(th2), r2 * np.sin(th2), h2 + a.floor_gap]
        Pp = pose_edge(Lw, float(th2[topr][np.argmax(h2[topr])]))
        f = os.path.join(a.out, f"eye{i}_donor.stl")
        write_stl(f, Pp, F0)
        write_stl(os.path.join(a.out, f"eye{i}_donor_inplace.stl"), to_world(Lw, e), F0)
        rim = np.array(e['rim'])
        print(f"глаз {i}: масштаб {k:.4f}, радиус глаза {k*Rd:.3f} в нише {e['R']:.3f}, "
              f"высота {np.ptp(Lw[:,2]):.3f}, объём {volume(Pp, F0):.2f} мм³, "
              f"замкнуто {'да' if watertight(Pp, F0) else 'НЕТ'}  -> {f}")
        if not a.no_check and os.path.exists(J['src']):
            if 'head' not in locals():
                head = load(J['src'], J.get('object'))[:2]
            g = wall_gap(head[0], head[1], e, Lw, 0.1 + 0.2 * rim.min(), 0.8 * rim.min())
            if len(g):
                print(f"   зазор по стенке к голове: мин {g.min():.3f}, медиана {np.median(g):.3f}, "
                      f"макс {g.max():.3f} мм ({len(g)} лучей)"
                      + ("  — ГЛАЗ ВРЕЗАЕТСЯ В ГОЛОВУ" if g.min() < 0 else ""))
        side_top = h2[topr].min()
        if a.rim != 'follow' and side_top > rim.min():
            print(f"   бок {side_top:.3f} выше устья в низшей точке {rim.min():.3f}: "
                  f"порожек над лицом, взять --rim follow")


# ---------------------------------------------------------------- socket
def cmd_socket(a):
    J = json.load(open(a.fit))
    eyes = J['eyes'] if not a.index else [J['eyes'][i - 1] for i in a.index]
    gap = a.gap if a.gap is not None else GAP_ACROSS
    os.makedirs(a.out, exist_ok=True)
    from partedit import volume
    for i, e in enumerate(eyes, 1):
        _, So, _ = eye_outline(e, gap)
        if e['kind'] == 'zone':
            z0 = -a.depth
            z1 = e['dome_h'] + a.above
        else:
            z0 = 0.0
            z1 = max(e['rim']) + a.above
        V, F = from_m3(prism(So, z0, z1))
        f = os.path.join(a.out, f"eye{i}_socket.stl")
        write_stl(f, to_world(V, e), F)
        print(f"глаз {i}: резак ниши {2*np.abs(So[:,0]).max():.3f} × {2*np.abs(So[:,1]).max():.3f}, "
              f"от {z0:.2f} до {z1:.2f} по оси, объём {volume(V, F):.1f} мм³  -> {f}")


# ---------------------------------------------------------------- main
def main():
    p = argparse.ArgumentParser(description=__doc__.split('\n\n')[0],
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = p.add_subparsers(dest='cmd', required=True)
    f = sp.add_parser('fit', help='найти нишу или нарисованный глаз')
    f.add_argument('src')
    f.add_argument('--object')
    f.add_argument('--at', action='append', help='точка у ниши, x,y,z; можно повторять')
    f.add_argument('--code', type=int, help='филамент покраски, каждое пятно — глаз')
    f.add_argument('--reach', type=float, default=9.0)
    f.add_argument('--min-area', type=float, default=3.0)
    f.add_argument('--up', default='0,0,1', help='куда «вверх» у рамки глаза (v)')
    f.add_argument('-o', '--out')

    def common(q):
        q.add_argument('--fit')
        q.add_argument('--index', type=int, action='append', help='какой глаз из fit, с 1')
        q.add_argument('--gap', type=float, help='зазор глаза в нише на сторону')
        q.add_argument('--floor-gap', type=float, default=FLOOR_GAP)
        q.add_argument('--sink', type=float, default=SINK)
        q.add_argument('--rim', choices=('follow', 'flat'), default='follow')
        q.add_argument('-o', '--out', required=True)

    b = sp.add_parser('build', help='построить глаз и вкладки')
    common(b)
    b.add_argument('--niche-r', type=float)
    b.add_argument('--niche-ab', help='a,b[,угол°] эллиптической ниши')
    b.add_argument('--side', type=float, help='высота бока пробки над дном')
    b.add_argument('--depth', type=float, default=1.5, help='пробка под нарисованным глазом')
    b.add_argument('--dome-r', type=float, default=5.8)
    b.add_argument('--dome-h', type=float)
    b.add_argument('--print', choices=('edge', 'flat'), default='edge')
    b.add_argument('--insert-gap', type=float)
    for nm in ('iris', 'pupil'):
        b.add_argument(f'--{nm}', help='полуоси a,b; a — вдоль угла --*-ang')
        b.add_argument(f'--{nm}-at', help='смещение центра u,v')
        b.add_argument(f'--{nm}-ang', type=float, default=90.0, help='угол большой оси от u, °')
    b.add_argument('--edge-t', type=float, default=1.0, help='толщина вкладки у её края')
    b.add_argument('--glint', choices=('none', 'hole', 'white', 'pin'), default='none')
    b.add_argument('--glint-d', type=float, default=2.0)
    b.add_argument('--glint-at')
    b.add_argument('--pin-depth', type=float, default=0.8, help='штырёк блика глубже зрачка')
    b.add_argument('--no-check', action='store_true', help='не мерить зазор по голове')

    d = sp.add_parser('donor', help='подогнать готовый глаз под нишу')
    d.add_argument('donor')
    common(d)
    d.add_argument('--no-check', action='store_true', help='не мерить зазор по голове')
    d.set_defaults(rim='flat')

    s = sp.add_parser('socket', help='резак ниши')
    s.add_argument('--fit', required=True)
    s.add_argument('--index', type=int, action='append')
    s.add_argument('--gap', type=float)
    s.add_argument('--depth', type=float, default=1.5 + FLOOR_GAP)
    s.add_argument('--above', type=float, default=3.0)
    s.add_argument('-o', '--out', required=True)
    a = p.parse_args()
    if a.cmd == 'donor' and not a.fit:
        die("donor: нужен --fit")
    {'fit': cmd_fit, 'build': cmd_build, 'donor': cmd_donor, 'socket': cmd_socket}[a.cmd](a)


if __name__ == '__main__':
    main()
