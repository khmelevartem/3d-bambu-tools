#!/usr/bin/env python3
"""Turn a seam drawn with a marker filament into a cut, then take the marker out.

The person draws the cut line in Bambu Studio with the paint brush, using a
filament the model does not otherwise carry - the marker. Two ways of drawing
it are understood:

band   a narrow strip along the surface where the seam must run, closed on
       itself. The seam is the strip's middle line: every marker face goes to
       the side whose unmarked surface is geodesically nearest, and the border
       between the two sides is the seam.
fill   the whole future part flooded with the marker. The seam is the border of
       the flood; every separate flood is its own part.

    uv run --with numpy --with scipy python tools/markercut.py \\
        seam  model.3mf --marker 5 --out w/cut               # band
    uv run ... python tools/markercut.py \\
        seam  model.3mf --marker 5 --mode fill --out w/cut   # fill
    uv run ... python tools/markercut.py clean model.3mf clean.3mf --marker 5

`seam` reads the project and writes nothing into it:

* `<out>_seam.json` - each part's seam as closed curves on the surface,
  resampled and smoothed (`--smooth` rounds), with length and closure;
* `<out>_zones.npy` - a zone per face of the object's mesh: 1 the body,
  2, 3, ... the parts. It is `--labels` for `relief_cut.py` (`--zone 2`);
* `<out>_zones.npz` - the mesh with that zone as `lab` and the face adjacency,
  in the layout of `paint.py parse`: `paint_split.py` and `solid_cut.py inlay`
  read it as is (`--filament 2`).

The faces are those of the source triangles, in file order, so the zones line
up face for face with `paint.py parse` of the same object, before or after
`clean`. A strip that does not close on itself is refused: the seam would not
divide the body. The report gives each gap as two points and the width
between them; paint the gap shut and run again.

On a band the part is the smaller side; `--part x,y,z` picks the side holding
the point instead (mesh coordinates, the ones `paint.py parse` gives).

`clean` repaints the marker and writes a new project. A face painted entirely
with the marker takes the colour that holds most of the border it shares with
unmarked faces, layer by layer inward. A face the brush split into pieces keeps
every piece and gives its marker pieces the colour of its own largest unmarked
piece. Afterwards the marker filament is dropped from the project when nothing
else uses it (any other object, an extruder setting, a layer change) - see
`patch3mf.py`: **the filaments above it move down by one**, so the AMS slots
need remapping when the project is opened. `--keep-filament` leaves it in.

Cutting itself is done by the existing commands - `relief_cut.py sheet|drape`
on a curved surface, `paint_split.py` and `solid_cut.py inlay` for flat
borders; joints and pins as for any colour border. Tolerances: references/
split-to-parts.md of the 3mf-paint skill.
"""
import argparse, json, os, re, sys, zipfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import scipy.sparse as sp
from scipy.sparse.csgraph import connected_components, dijkstra
from scipy.spatial import cKDTree

import paint, patch3mf
from paint_split import loops_of
from partedit import Project

MARKER_SHARE = 0.5      # a split face is a marker face when the marker covers this much of it


def die(msg, code=2):
    print(msg)
    sys.exit(code)


# ---------------------------------------------------------------- reading
def leaf_shares(code, cache):
    """paint_color code -> {state: share of the face area}. State 0 is the
        object's own filament, N is filament N."""
    if code in cache:
        return cache[code]
    if code == '':
        r = {0: 1.0}
    else:
        r = {}
        for st, bc in paint.paint_tree(code):
            r[st] = r.get(st, 0.0) + abs(np.linalg.det(bc))
    cache[code] = r
    return r


def read(src, obj, marker):
    z = zipfile.ZipFile(src)
    name, ext, ent = paint.pick_object(z, obj)
    raw = z.read(ent).decode('utf-8')
    V = np.array(paint.VERT.findall(raw), dtype=np.float64)
    F, codes = [], []
    for m in paint.TRI.finditer(raw):
        F.append((int(m.group(1)), int(m.group(2)), int(m.group(3))))
        codes.append(paint.code_of(m))
    F = np.array(F, dtype=np.int64)
    cache = {}
    share = np.zeros(len(F))            # marker share of each face
    own = np.full(len(F), -1)           # largest unmarked state of the face, -1 if none
    for i, c in enumerate(codes):
        s = leaf_shares(c, cache)
        share[i] = s.get(marker, 0.0)
        rest = {k: v for k, v in s.items() if k != marker and v > 1e-9}
        if rest:
            own[i] = max(rest, key=rest.get)
    ea, eb, elen, area = paint.adjacency(V, F)
    try:
        fcol = np.array(json.loads(z.read('Metadata/project_settings.config'))
                        .get('filament_colour', []), dtype=object)
    except Exception:
        fcol = np.array([], dtype=object)
    return dict(name=name or ent, ext=ext, ent=ent, raw=raw, V=V, F=F, codes=codes,
                share=share, own=own, ea=ea, eb=eb, elen=elen, area=area, fcol=fcol)


# ---------------------------------------------------------------- graph helpers
def components(mask, ea, eb, n):
    """Connected components of the faces in mask (others get -1)."""
    e = mask[ea] & mask[eb]
    g = sp.coo_matrix((np.ones(int(e.sum())), (ea[e], eb[e])), shape=(n, n))
    _, cc = connected_components(g, directed=False)
    cc = np.where(mask, cc, -1)
    ids, cc2 = np.unique(cc[mask], return_inverse=True)
    out = np.full(n, -1); out[mask] = cc2
    return len(ids), out


def flood_majority(known, val, ea, eb, w):
    """Fill unknown faces layer by layer: each takes the value holding most of
        the border it shares with already known faces."""
    known, val = known.copy(), val.copy()
    if not known.any():
        return known, val
    while True:
        m = known[ea] ^ known[eb]
        if not m.any():
            break
        a, b = ea[m], eb[m]
        t = np.where(known[a], b, a)
        v = val[np.where(known[a], a, b)]
        K = int(val[known].max()) + 1
        uk, inv = np.unique(t.astype(np.int64) * K + v, return_inverse=True)
        tot = np.bincount(inv, weights=w[m])
        tt, vv = uk // K, uk % K
        o = np.lexsort((-tot, tt))
        tt, vv = tt[o], vv[o]
        first = np.r_[True, tt[1:] != tt[:-1]]
        val[tt[first]] = vv[first]
        known[tt[first]] = True
    return known, val


def centroid_graph(C, ea, eb, keep, n):
    e = keep
    w = np.linalg.norm(C[ea[e]] - C[eb[e]], axis=1) + 1e-9
    return sp.coo_matrix((w, (ea[e], eb[e])), shape=(n, n)).tocsr()


# ---------------------------------------------------------------- curve helpers
def closest_on_mesh(p, V, F, tree, C, k=12):
    """Closest point of the mesh surface to each of p (candidates: k nearest faces)."""
    k = min(k, len(F))
    _, idx = tree.query(p, k=k)
    idx = idx.reshape(len(p), k)
    A, B, Cc = V[F[idx, 0]], V[F[idx, 1]], V[F[idx, 2]]
    q = p[:, None, :]

    def seg(a, b):
        d = b - a
        t = np.clip(((q - a) * d).sum(-1) / np.maximum((d * d).sum(-1), 1e-18), 0, 1)
        return a + t[..., None] * d

    nrm = np.cross(B - A, Cc - A)
    nrm /= np.maximum(np.linalg.norm(nrm, axis=-1), 1e-18)[..., None]
    pr = q - ((q - A) * nrm).sum(-1)[..., None] * nrm
    v0, v1, v2 = B - A, Cc - A, pr - A
    d00, d01, d11 = (v0 * v0).sum(-1), (v0 * v1).sum(-1), (v1 * v1).sum(-1)
    d20, d21 = (v2 * v0).sum(-1), (v2 * v1).sum(-1)
    den = np.maximum(d00 * d11 - d01 * d01, 1e-18)
    bv = (d11 * d20 - d01 * d21) / den
    bw = (d00 * d21 - d01 * d20) / den
    inside = (bv >= 0) & (bw >= 0) & (bv + bw <= 1)
    cands = [np.where(inside[..., None], pr, np.inf), seg(A, B), seg(B, Cc), seg(Cc, A)]
    best, bd = None, None
    for c in cands:
        d = np.linalg.norm(c - q, axis=-1)
        d = np.where(np.isfinite(d), d, np.inf)
        if best is None:
            best, bd = c, d
        else:
            take = d < bd
            best = np.where(take[..., None], c, best); bd = np.minimum(d, bd)
    j = np.argmin(bd, axis=1)
    return best[np.arange(len(p)), j]


def resample(P, step, closed=True):
    Q = np.vstack([P, P[:1]]) if closed else P
    s = np.r_[0, np.cumsum(np.linalg.norm(np.diff(Q, axis=0), axis=1))]
    n = max(int(round(s[-1] / step)), 8)
    t = np.linspace(0, s[-1], n, endpoint=not closed)
    return np.c_[[np.interp(t, s, Q[:, i]) for i in range(3)]].T


def smooth_loop(P, rounds, project):
    """Taubin smoothing of a closed polyline, put back on the surface every round."""
    for _ in range(rounds):
        for lam in (0.5, -0.53):
            P = P + lam * (0.5 * (np.roll(P, 1, 0) + np.roll(P, -1, 0)) - P)
        P = project(P)
    return P


# ---------------------------------------------------------------- seam
def analyse(d, marker, speck):
    """Marker faces after cleaning specks off both sides."""
    n = len(d['F'])
    mk = d['share'] >= MARKER_SHARE
    ea, eb, area = d['ea'], d['eb'], d['area']
    # stray dots of marker are not a drawing: they are only repainted
    nm, cm = components(mk, ea, eb, n)
    if nm:
        A = np.bincount(cm[mk], weights=area[mk], minlength=nm)
        stray = mk & (A[np.maximum(cm, 0)] < speck)
        mk &= ~stray
    else:
        stray = np.zeros(n, bool)
    # unmarked pinholes inside the stroke belong to the stroke
    nu, cu = components(~mk, ea, eb, n)
    A = np.bincount(cu[~mk], weights=area[~mk], minlength=nu)
    hole = ~mk & (A[np.maximum(cu, 0)] < speck)
    mk |= hole
    return mk, int(stray.sum()), int(hole.sum())


def strip_ends(d, comp, G):
    """The two far ends of a strip of marker faces and the length between them."""
    f = np.flatnonzero(comp)
    dist = dijkstra(G, directed=False, indices=f[0])
    dist = np.where(comp & np.isfinite(dist), dist, -1)
    e1 = int(np.argmax(dist))
    dist = dijkstra(G, directed=False, indices=e1)
    dist = np.where(comp & np.isfinite(dist), dist, -1)
    e2 = int(np.argmax(dist))
    return e1, e2, float(dist[e2])


def report_gaps(d, mk, C):
    n = len(d['F'])
    ea, eb = d['ea'], d['eb']
    G = centroid_graph(C, ea, eb, mk[ea] & mk[eb], n)
    nm, cm = components(mk, ea, eb, n)
    ends, rings = [], 0
    for k in range(nm):
        comp = cm == k
        if len(loops_of(d['F'], np.flatnonzero(comp))) >= 2:
            rings += 1
            continue
        e1, e2, L = strip_ends(d, comp, G)
        ends += [(k, e1), (k, e2)]
        print(f'  кусок полосы {k + 1}: длина {L:.1f} мм, площадь {d["area"][comp].sum():.1f} мм²')
    if rings:
        print(f'  замкнутых колец {rings}, но тело они не делят: кольцо обходит ручку '
              f'или сквозное отверстие')
    used, gaps = set(), []
    pairs = sorted(((np.linalg.norm(C[a] - C[b]), i, j)
                    for i, (ka, a) in enumerate(ends) for j, (kb, b) in enumerate(ends)
                    if i < j and (ka != kb or nm == 1)), key=lambda t: t[0])
    for dd, i, j in pairs:
        if i in used or j in used:
            continue
        used |= {i, j}
        gaps.append((dd, C[ends[i][1]], C[ends[j][1]]))
    for dd, a, b in gaps:
        mid = (a + b) / 2
        print(f'  РАЗРЫВ {dd:.1f} мм: между ({a[0]:.2f}, {a[1]:.2f}, {a[2]:.2f}) и '
              f'({b[0]:.2f}, {b[1]:.2f}, {b[2]:.2f}), середина '
              f'({mid[0]:.2f}, {mid[1]:.2f}, {mid[2]:.2f})')
    return gaps


def cmd_seam(a):
    d = read(a.src, a.object, a.marker)
    V, F, ea, eb, area = d['V'], d['F'], d['ea'], d['eb'], d['area']
    n = len(F)
    C = V[F].mean(1)
    mk, stray, hole = analyse(d, a.marker, a.speck)
    print(f'{d["name"]}: граней {n}, маркер — филамент {a.marker}: {int(mk.sum())} граней, '
          f'{area[mk].sum():.1f} мм²; крапа маркера {stray}, дырок в штрихе {hole}')
    if not mk.any():
        die(f'граней филамента {a.marker} нет: маркер не нарисован или номер не тот')
    open_e = 3 * n - 2 * len(ea)
    if open_e:
        print(f'  ВНИМАНИЕ: у сетки {open_e} открытых рёбер — шов может упереться в дыру')

    zone = np.ones(n, np.int64)
    if a.mode == 'band':
        nu, cu = components(~mk, ea, eb, n)
        if nu < 2:
            print('полоса НЕ замкнута — тело она не делит, шов не построен:')
            report_gaps(d, mk, C)
            print('дорисовать разрыв маркером и запустить снова')
            return 1
        # every marker face goes to the side whose unmarked face is nearest
        G = centroid_graph(C, ea, eb, mk[ea] | mk[eb], n)
        src = np.unique(np.r_[ea[mk[eb] & ~mk[ea]], eb[mk[ea] & ~mk[eb]]])
        _, _, near = dijkstra(G, directed=False, indices=src, min_only=True,
                              return_predecessors=True)
        side = cu.copy()
        got = mk & (near >= 0)
        side[got] = cu[near[got]]
        side[mk & (near < 0)] = 0
        A = np.bincount(side, weights=area, minlength=nu)
        if a.part:
            p = np.array([float(x) for x in a.part.split(',')])
            pick = side[int(np.argmin(np.linalg.norm(C - p, axis=1)))]
            zone[side == pick] = 2
        else:
            order = np.argsort(-A)
            for z, s in enumerate(order[1:], start=2):
                zone[side == s] = z
        width = area[mk].sum()
    else:
        nm, cm = components(mk, ea, eb, n)
        A = np.bincount(cm[mk], weights=area[mk], minlength=nm)
        for z, s in enumerate(np.argsort(-A), start=2):
            zone[cm == s] = z
        width = None

    tree = cKDTree(C)
    proj = lambda P: closest_on_mesh(P, V, F, tree, C)
    step = float(np.median(d['elen']))
    parts, seam_len = [], 0.0
    for z in range(2, int(zone.max()) + 1):
        sel = np.flatnonzero(zone == z)
        loops = []
        for loop, _ in loops_of(F, sel):
            raw = V[loop]
            closed = bool(np.linalg.norm(raw[-1] - raw[0]) <= 2.5 * step)
            P = resample(raw, step, closed)
            if closed and a.smooth:
                P = smooth_loop(P, a.smooth, proj)
            L = float(np.linalg.norm(np.diff(np.vstack([P, P[:1]]) if closed else P, axis=0),
                                     axis=1).sum())
            shift = float(cKDTree(raw).query(P)[0].max())
            loops.append(dict(closed=closed, length=round(L, 3), points=np.round(P, 4).tolist()))
            seam_len += L
            print(f'  деталь {z - 1}: шов {L:.1f} мм, точек {len(P)}, '
                  f'замкнут {"да" if closed else "НЕТ"}, сглаживание сдвинуло до {shift:.2f} мм')
        parts.append(dict(zone=z, area=round(float(area[sel].sum()), 2), faces=int(len(sel)),
                          loops=loops))
        print(f'  деталь {z - 1} (зона {z}): {area[sel].sum():.1f} мм², граней {len(sel)}, '
              f'контуров {len(loops)}')
    if width is not None and seam_len:
        width = width / seam_len
        print(f'  ширина полосы в среднем {width:.2f} мм: шов прошёл по её середине')
    print(f'  тело (зона 1): {area[zone == 1].sum():.1f} мм²')

    os.makedirs(os.path.dirname(os.path.abspath(a.out)) or '.', exist_ok=True)
    json.dump(dict(source=os.path.basename(a.src), object=d['name'], entry=d['ent'],
                   marker=a.marker, mode=a.mode,
                   band_width=None if width is None else round(width, 3),
                   parts=parts), open(a.out + '_seam.json', 'w'), indent=1)
    np.save(a.out + '_zones.npy', zone.astype(np.int32))
    np.savez_compressed(a.out + '_zones.npz', V=V, F=F, lab=zone.astype(np.int8), ea=ea, eb=eb,
                        elen=d['elen'], area=area, entry=np.array(d['ent']))
    print(f'-> {a.out}_seam.json, {a.out}_zones.npy, {a.out}_zones.npz '
          f'(зона 1 — тело, 2.. — детали)')
    return 0


# ---------------------------------------------------------------- clean
def repaint_targets(d, marker):
    """The colour (paint state) that replaces the marker on every face that has it."""
    own = d['own']
    known = own >= 0
    _, val = flood_majority(known, np.maximum(own, 0), d['ea'], d['eb'], d['elen'])
    return val


def recode(code, marker, target):
    """A paint_color code with the marker state replaced by target ('' = no paint)."""
    if code == '':
        return code
    t = patch3mf.paint_tree(code)
    if marker not in patch3mf.leaves(t):
        return code
    t = patch3mf.remap_tree(t, {marker: target})
    st = set(patch3mf.leaves(t))
    if len(st) == 1:
        s = st.pop()
        return '' if s == 0 else patch3mf.paint_code(('leaf', s))
    return patch3mf.paint_code(t)


def cmd_clean(a):
    d = read(a.src, a.object, a.marker)
    target = repaint_targets(d, a.marker)
    has = d['share'] > 0
    print(f'{d["name"]}: граней с маркером {int(has.sum())} '
          f'(целиком {int((d["share"] > 1 - 1e-9).sum())}, частью {int((has & (d["share"] < 1 - 1e-9)).sum())})')
    st = dict(i=0, n=0)
    by = {}

    def sub(m):
        i = st['i']; st['i'] += 1
        code = paint.code_of(m)
        if not has[i]:
            return m.group(0)
        tg = int(d['own'][i]) if d['own'][i] >= 0 else int(target[i])
        new = recode(code, a.marker, tg)
        st['n'] += 1
        by[tg] = by.get(tg, 0) + 1
        if new == '':
            return paint.PAINT.sub('', m.group(0), count=1)
        return paint.with_code(m, new)

    raw = paint.TRI.sub(sub, d['raw'])
    assert st['i'] == len(d['F'])
    left = sum(1 for m in paint.TRI.finditer(raw)
               if a.marker in leaf_shares(paint.code_of(m), {}))
    names = ', '.join(f'{"свой филамент объекта" if k == 0 else f"филамент {k}"}: {v}'
                      for k, v in sorted(by.items()))
    print(f'перекрашено граней {st["n"]} ({names}); с маркером осталось {left}')

    pr = Project(a.src)
    pr.files[d['ent']] = raw.encode('utf-8')
    if a.keep_filament:
        print(f'филамент {a.marker} оставлен в проекте (--keep-filament)')
    elif patch3mf.CFG not in pr.files:
        print(f'в проекте нет {patch3mf.CFG}: филамент выкидывать неоткуда')
    else:
        cfg = json.loads(pr.files[patch3mf.CFG])
        rc = patch3mf.drop_filament(pr.files, cfg, a.marker, None)
        if rc == 0:
            pr.files[patch3mf.CFG] = json.dumps(cfg, indent=4, ensure_ascii=True,
                                                sort_keys=True).encode('utf-8')
            pr.cfg = pr.files[patch3mf.MODEL_CFG].decode('utf-8')
            if a.marker < len(cfg['filament_colour']) + 1:
                print(f'  филаменты выше {a.marker} сдвинулись на один номер вниз: '
                      f'в Bambu Studio проверить, какой пруток в каком слоте AMS')
        else:
            print(f'филамент {a.marker} оставлен: он ещё используется (список выше)')
    pr.save(a.dst)
    print(f'-> {a.dst}')
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd')
    p = sub.add_parser('seam', help='шов по маркеру: кривая и зоны граней')
    p.add_argument('src')
    p.add_argument('--marker', type=int, required=True, help='номер маркерного филамента')
    p.add_argument('--object', help='имя или номер объекта, если их несколько')
    p.add_argument('--mode', choices=('band', 'fill'), default='band',
                   help='band — полоса по линии шва, fill — заливка всей детали')
    p.add_argument('--part', help='x,y,z: деталь — та сторона полосы, где эта точка')
    p.add_argument('--smooth', type=int, default=10, help='раундов сглаживания кривой')
    p.add_argument('--speck', type=float, default=1.0,
                   help='мм²: пятна маркера и дырки в штрихе мельче этого не в счёт')
    p.add_argument('--out', required=True, help='префикс выходных файлов')
    q = sub.add_parser('clean', help='перекрасить маркер в цвет соседей и выкинуть филамент')
    q.add_argument('src'); q.add_argument('dst')
    q.add_argument('--marker', type=int, required=True)
    q.add_argument('--object')
    q.add_argument('--keep-filament', action='store_true')
    if len(sys.argv) < 2 or sys.argv[1] not in ('seam', 'clean'):
        print(__doc__)
        return 1
    a = ap.parse_args()
    return {'seam': cmd_seam, 'clean': cmd_clean}[a.cmd](a) or 0


if __name__ == '__main__':
    sys.exit(main())
