#!/usr/bin/env python3
"""Graft a missing piece onto a foreign mesh without losing the paint.

The case it was written for: a downloaded figurine has a broken strap (a sling,
a cable, a chain, a handle) - the feature exists on one side and is absent
across a span. It has to be drawn in so that the joint is invisible while the
rest of the model keeps its `paint_color` in place.

The key to all of it: **faces are appended at the END** of `<triangles>`. Paint
is bound to a triangle's index, so the old numbering must stay untouched. The
new shell lives in the same object as a separate body and simply overlaps the
existing ones - the slicer merges intersecting bodies of one object itself.

Three steps, one subcommand each:

    UV="uv run --quiet --with numpy --with scipy python"

    # 1. take a section of the existing feature at the joint: width, thickness, facing
    $UV tools/graft.py section model.3mf --at X,Y,Z --dir DX,DY,DZ

    # 2. sweep that section along an arc from the joint to the second support
    $UV tools/graft.py ribbon model.3mf work/ribbon.npz \
        --at X,Y,Z --dir DX,DY,DZ --up UX,UY,UZ --to X,Y,Z --to-dir DX,DY,DZ

    # 3. write it into the 3MF: vertices and faces at the end, face_count updated
    $UV tools/graft.py put model.3mf work/ribbon.npz new.3mf --filament 1

Coordinates everywhere are **raw file units**, the same ones `meshdoctor.py`
prints; the <build> multiplier is given with `--scale` and only serves to make
the report read in millimetres.

Take the profile from the mesh itself rather than inventing it: a real strap
has a flattened section sitting off-centre, and a guessed capsule leaves a
visible step at the joint. Both ends must run a millimetre or two inside
existing bodies. Prove the merge by slicing - `Inner wall` must not grow.
"""
import argparse, os, re, sys, zipfile
import numpy as np

TRI = re.compile(r'<triangle v1="(\d+)" v2="(\d+)" v3="(\d+)"(?:\s+paint_color="([^"]*)")?\s*/>')
VTX = re.compile(r'<vertex x="([^"]+)" y="([^"]+)" z="([^"]+)"\s*/>')
FIL2CODE = {1: '4', 2: '8', 3: '0C', 4: '1C', 5: '2C', 6: '3C', 7: '4C'}


# ----------------------------------------------------------------- reading
def model_entry(z):
    names = [n for n in z.namelist() if n.startswith('3D/') and n.endswith('.model')]
    big = [n for n in names if 'Objects/' in n]
    return (big or names)[0]


def load(path):
    """Vertices and faces from a 3MF, or from an npz built by paint.py parse."""
    if path.endswith('.npz'):
        d = np.load(path, allow_pickle=True)
        return d['V'].astype(float), d['F'].astype(np.int64)
    z = zipfile.ZipFile(path)
    raw = z.read(model_entry(z)).decode('utf-8')
    V = np.array([[float(a), float(b), float(c)] for a, b, c in VTX.findall(raw)])
    F = np.array([[int(m.group(1)), int(m.group(2)), int(m.group(3))]
                  for m in TRI.finditer(raw)], np.int64)
    z.close()
    return V, F


def triple(s):
    v = np.array([float(x) for x in s.split(',')], float)
    assert len(v) == 3, s
    return v


# ----------------------------------------------------------------- section
def cross_section(V, F, p0, nrm, maxext=2.0):
    """Section contours by the plane (p0, nrm), no wider than maxext across.

        Returns a list of dicts: centre, width, thickness, width vector, contour
        points in the plane and its basis. The small contours are the feature being
        looked for — a strap, a cord, a bow; large ones (body, leg) are cut off by
        maxext."""
    import scipy.sparse as sp
    from scipy.sparse.csgraph import connected_components
    nrm = nrm / np.linalg.norm(nrm)
    P = V[F]
    s = (P - p0) @ nrm
    n = (s > 0).sum(1)
    m = (n == 1) | (n == 2)
    T, S = P[m], s[m]
    segs = []
    for tri, sv in zip(T, S):
        pts = []
        for i in range(3):
            a, b = tri[i], tri[(i + 1) % 3]
            sa, sb = sv[i], sv[(i + 1) % 3]
            if sa * sb < 0:
                t = sa / (sa - sb)
                pts.append(a + t * (b - a))
        if len(pts) == 2:
            segs.append(pts)
    if not segs:
        return [], None, None
    S2 = np.array(segs)
    e1 = np.cross(nrm, [0, 0, 1.0])
    if np.linalg.norm(e1) < 1e-6:
        e1 = np.cross(nrm, [1.0, 0, 0])
    e1 /= np.linalg.norm(e1)
    e2 = np.cross(nrm, e1)
    pts2 = (S2.reshape(-1, 3) - p0) @ np.stack([e1, e2], 1)
    uq, inv = np.unique(np.round(pts2, 4), axis=0, return_inverse=True)
    a, b = inv[0::2], inv[1::2]
    g = sp.coo_matrix((np.ones(len(a)), (a, b)), shape=(len(uq), len(uq)))
    nc, lbl = connected_components(g + g.T, directed=False)
    out = []
    for c in range(nc):
        q = uq[lbl == c]
        if len(q) < 4:
            continue
        ext = q.max(0) - q.min(0)
        if ext.max() > maxext:
            continue
        ctr = q.mean(0)
        X = q - ctr
        _, _, vt = np.linalg.svd(X, full_matrices=False)
        out.append(dict(centre=p0 + ctr[0] * e1 + ctr[1] * e2,
                        width=float(np.ptp(X @ vt[0])), thick=float(np.ptp(X @ vt[1])),
                        wvec=vt[0][0] * e1 + vt[0][1] * e2, pts=q, e1=e1, e2=e2))
    return out, e1, e2


def cmd_section(a):
    V, F = load(a.src)
    res, _, _ = cross_section(V, F, triple(a.at), triple(a.dir), a.max)
    if not res:
        print('в этой плоскости ничего мельче', a.max, 'нет'); return 1
    for r in res:
        n = np.cross(triple(a.dir) / np.linalg.norm(triple(a.dir)), r['wvec'])
        n /= np.linalg.norm(n)
        print(f"центр {np.round(r['centre'],3)}  "
              f"ширина {r['width']:.3f} × толщина {r['thick']:.3f} ед = "
              f"{r['width']*a.scale:.2f} × {r['thick']*a.scale:.2f} мм")
        print(f"   вектор ширины {np.round(r['wvec'],3)}   нормаль ленты {np.round(n,3)}")
    return 0


# ------------------------------------------------------------------ ribbon
def profile_from_mesh(V, F, at, dirv, up, M, maxext):
    """Profile of the ribbon = the feature's real section at the joint.

        Take the profile off the mesh rather than inventing a rectangle: a real
        strap is not a capsule but a flattened oval with its own seating on the
        body, and an invented one leaves a step at the joint."""
    res, _, _ = cross_section(V, F, at, dirv, maxext)
    if not res:
        raise SystemExit('в точке --at нет контура мельче --max: проверь точку и направление')
    r = max(res, key=lambda r: r['width'])
    t = dirv / np.linalg.norm(dirv)
    n0 = up - (up @ t) * t
    n0 /= np.linalg.norm(n0)
    w0 = np.cross(n0, t)
    w0 /= np.linalg.norm(w0)
    P3 = np.array([at + q[0] * r['e1'] + q[1] * r['e2'] for q in r['pts']])
    ctr3 = P3.mean(0)
    loc = np.stack([(P3 - ctr3) @ w0, (P3 - ctr3) @ n0], 1)
    sc = loc / np.array([np.ptp(loc[:, 0]) or 1, np.ptp(loc[:, 1]) or 1])
    C = loc[np.argsort(np.arctan2(sc[:, 1], sc[:, 0]))]
    C = np.vstack([C, C[:1]])
    seg = np.linalg.norm(np.diff(C, axis=0), axis=1)
    s = np.r_[0, np.cumsum(seg)]
    tt = np.linspace(0, s[-1], M, endpoint=False)
    prof = np.stack([np.interp(tt, s, C[:, 0]), np.interp(tt, s, C[:, 1])], 1)
    return prof, ctr3, w0, n0


def sweep(prof, A, tA, n0, B, tB, bulge, nseg):
    """Sweep the profile along a cubic Bezier with tangents fixed at the ends.

        The frame is carried along the curve by minimal rotation: the section does
        not spin about its own axis, so the ribbon does not skew."""
    L = np.linalg.norm(B - A)
    P1 = A + bulge[0] * L * tA
    P2 = B - bulge[1] * L * tB
    bez = lambda s: ((1 - s[:, None]) ** 3) * A + 3 * ((1 - s[:, None]) ** 2) * s[:, None] * P1 \
        + 3 * (1 - s[:, None]) * (s[:, None] ** 2) * P2 + (s[:, None] ** 3) * B
    s = np.linspace(0, 1, nseg)
    P = bez(s)
    h = 1e-4
    T = bez(np.clip(s + h, 0, 1)) - bez(np.clip(s - h, 0, 1))
    T /= np.linalg.norm(T, axis=1)[:, None]
    M = len(prof)
    n = n0.copy()
    rings = []
    for i in range(nseg):
        n = n - (n @ T[i]) * T[i]
        n /= np.linalg.norm(n)
        w = np.cross(n, T[i])
        w /= np.linalg.norm(w)
        rings.append(P[i][None, :] + prof[:, 0:1] * w[None, :] + prof[:, 1:2] * n[None, :])
    V = np.concatenate(rings, 0)
    F = []
    for i in range(nseg - 1):
        for j in range(M):
            a0, b0 = i * M + j, i * M + (j + 1) % M
            a1, b1 = (i + 1) * M + j, (i + 1) * M + (j + 1) % M
            F.append((a0, b0, b1)); F.append((a0, b1, a1))
    c0, c1 = len(V), len(V) + 1
    V = np.vstack([V, P[0][None, :], P[-1][None, :]])
    for j in range(M):
        F.append((c0, (j + 1) % M, j))
        F.append((c1, (nseg - 1) * M + j, (nseg - 1) * M + (j + 1) % M))
    F = np.array(F, np.int64)
    Pf = V[F]
    vol = np.einsum('ij,ij->i', Pf[:, 0], np.cross(Pf[:, 1], Pf[:, 2])).sum() / 6
    if vol < 0:                       # normals outward, or the slicer sees a hole
        F = F[:, ::-1]; vol = -vol
    return V, F, P, vol


def cmd_ribbon(a):
    V, F = load(a.src)
    A, tA, up = triple(a.at), triple(a.dir), triple(a.up)
    B, tB = triple(a.to), triple(a.to_dir)
    tA /= np.linalg.norm(tA); tB /= np.linalg.norm(tB)
    prof, ctr3, w0, n0 = profile_from_mesh(V, F, A, tA, up, a.points, a.max)
    bulge = [float(x) for x in a.bulge.split(',')]
    nV, nF, P, vol = sweep(prof, ctr3, tA, n0, B, tB, bulge, a.segments)
    length = np.linalg.norm(np.diff(P, axis=0), axis=1).sum()
    print(f"профиль {np.ptp(prof[:,0]):.3f} × {np.ptp(prof[:,1]):.3f} ед = "
          f"{np.ptp(prof[:,0])*a.scale:.2f} × {np.ptp(prof[:,1])*a.scale:.2f} мм")
    print(f"длина {length:.3f} ед = {length*a.scale:.2f} мм, "
          f"объём {vol*a.scale**3:.2f} мм³, вершин {len(nV)}, граней {len(nF)}")
    np.savez(a.out, V=nV, F=nF)
    print(a.out)
    print('СТЫК И ОПОРА: оба конца обязаны уходить ВНУТРЬ существующих тел — '
          'иначе останется щель. Смотреть рендером, а не по числам.')
    return 0


# -------------------------------------------------------- write into the 3MF
def cmd_put(a):
    d = np.load(a.add, allow_pickle=True)
    V, F = d['V'], d['F']
    zin = zipfile.ZipFile(a.src)
    ent = model_entry(zin)
    raw = zin.read(ent).decode('utf-8')
    nv, nf = raw.count('<vertex '), raw.count('<triangle ')
    code = FIL2CODE[a.filament]
    vtx = ''.join(f'     <vertex x="{p[0]:.8f}" y="{p[1]:.8f}" z="{p[2]:.8f}"/>\n' for p in V)
    tri = ''.join(f'     <triangle v1="{x+nv}" v2="{y+nv}" v3="{z+nv}" paint_color="{code}"/>\n'
                  for x, y, z in F)
    assert raw.count('</vertices>') == 1 and raw.count('</triangles>') == 1, \
        'в этом .model несколько сеток — вписывать надо руками'
    raw = raw.replace('    </vertices>', vtx + '    </vertices>', 1)
    raw = raw.replace('    </triangles>', tri + '    </triangles>', 1)
    assert raw.count('<vertex ') == nv + len(V) and raw.count('<triangle ') == nf + len(F)

    ms = None
    if 'Metadata/model_settings.config' in zin.namelist():
        ms = zin.read('Metadata/model_settings.config').decode('utf-8')
        ms = ms.replace(f'face_count="{nf}"', f'face_count="{nf+len(F)}"')

    tmp = a.dst + '.tmp'
    with zipfile.ZipFile(tmp, 'w') as z:
        for it in zin.infolist():
            if it.filename == ent:
                data = raw.encode('utf-8')
            elif ms is not None and it.filename == 'Metadata/model_settings.config':
                data = ms.encode('utf-8')
            else:
                data = zin.read(it.filename)
            zi = zipfile.ZipInfo(it.filename, date_time=it.date_time)
            zi.compress_type = it.compress_type
            zi.external_attr = it.external_attr
            z.writestr(zi, data)
    zin.close()
    os.replace(tmp, a.dst)
    print(f'вершин {nv} -> {nv+len(V)}, граней {nf} -> {nf+len(F)}, '
          f'новые грани филамент {a.filament} (код {code})')
    print(f'{a.dst}  {os.path.getsize(a.dst)/1e6:.2f} МБ')
    print('ДАЛЬШЕ: нарезать и сверить с нарезкой оригинала. Inner wall не должен '
          'вырасти — это и есть доказательство, что слайсер слил тела, а не '
          'напечатал стенку внутри детали.')
    return 0


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(required=True)

    q = sub.add_parser('section', help='сечение детали в точке: ширина, толщина, ориентация')
    q.add_argument('src'); q.add_argument('--at', required=True)
    q.add_argument('--dir', required=True, help='нормаль плоскости = ось детали')
    q.add_argument('--max', type=float, default=2.0, help='отсечь контуры крупнее, ед.')
    q.add_argument('--scale', type=float, default=1.0, help='множитель <build>, для отчёта в мм')
    q.set_defaults(fn=cmd_section)

    q = sub.add_parser('ribbon', help='замести сечение по дуге от стыка до второй опоры')
    q.add_argument('src'); q.add_argument('out')
    q.add_argument('--at', required=True, help='точка на оси ВНУТРИ существующей детали')
    q.add_argument('--dir', required=True, help='куда деталь идёт в этой точке')
    q.add_argument('--up', required=True, help='нормаль ленты (плашмя), из section')
    q.add_argument('--to', required=True, help='конец, ВНУТРИ второго тела')
    q.add_argument('--to-dir', required=True, help='направление входа в него')
    q.add_argument('--bulge', default='0.40,0.35', help='вынос опорных точек Безье')
    q.add_argument('--segments', type=int, default=80)
    q.add_argument('--points', type=int, default=40, help='точек в профиле')
    q.add_argument('--max', type=float, default=2.0)
    q.add_argument('--scale', type=float, default=1.0)
    q.set_defaults(fn=cmd_ribbon)

    q = sub.add_parser('put', help='дописать сетку в 3MF: грани в конец, покраска цела')
    q.add_argument('src'); q.add_argument('add'); q.add_argument('dst')
    q.add_argument('--filament', type=int, default=1, help='каким филаментом красить новые грани')
    q.set_defaults(fn=cmd_put)

    a = p.parse_args()
    sys.exit(a.fn(a) or 0)


if __name__ == '__main__':
    main()
