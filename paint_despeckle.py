#!/usr/bin/env python3
"""Remove unprintable speckle and hairline strokes from 3MF paint.

A nozzle cannot lay a band narrower than its own line: a colour fragment
thinner than that never reaches the plastic, it only adds a filament change.
Such fragments are given to the neighbour they share the longest border with.

The measure of "thinness" is the effective width w = 2*S/L (for a band of
width w the area is S = w*l and the perimeter L ~ 2*l). Fragments thinner
than --min-width or smaller than --min-area are repainted; the count is in
REAL millimetres — the scale from <build> is supplied with --scale.

    uv run --with numpy --with scipy python tools/paint_despeckle.py \\
        work/p.npz work/p2.npz --scale 3.9397 --min-width 0.6 --min-area 1.0
"""
import argparse, numpy as np, scipy.sparse as sp
from scipy.sparse.csgraph import connected_components

ap = argparse.ArgumentParser()
ap.add_argument('src'); ap.add_argument('dst')
ap.add_argument('--scale', type=float, default=1.0, help='factor by which <build> scales the mesh')
ap.add_argument('--min-width', type=float, default=0.6, help='mm, effective width of a fragment')
ap.add_argument('--min-area',  type=float, default=1.0, help='mm2')
ap.add_argument('--keep', default='', help='filaments to leave alone, comma separated')
ap.add_argument('--rounds', type=int, default=4)
ap.add_argument('--only-on', default='', help='repaint only fragments whose neighbour is in this list')
a = ap.parse_args()

d = dict(np.load(a.src, allow_pickle=True))
lab = d['lab'].astype(np.int16).copy()
ea, eb, elen, area = d['ea'], d['eb'], d['elen'], d['area']
keep = {int(x) for x in a.keep.split(',') if x.strip()}
onlyon = {int(x) for x in a.only_on.split(',') if x.strip()}
S, S2 = a.scale, a.scale**2
n = len(lab)
print(f'граней {n}, масштаб {S}, порог ширины {a.min_width} мм, площади {a.min_area} мм2')

for it in range(a.rounds):
    same = lab[ea] == lab[eb]
    g = sp.coo_matrix((np.ones(same.sum()), (ea[same], eb[same])), shape=(n, n))
    nc, cc = connected_components(g, directed=False)
    A = np.bincount(cc, weights=area, minlength=nc) * S2          # mm2
    # border length per fragment, and the neighbour with the longest shared border
    bnd = ~same
    ca, cb, ln = cc[ea[bnd]], cc[eb[bnd]], elen[bnd] * S
    L = np.bincount(ca, weights=ln, minlength=nc) + np.bincount(cb, weights=ln, minlength=nc)
    w = np.where(L > 0, 2*A/np.maximum(L, 1e-12), np.inf)
    bad = ((w < a.min_width) | (A < a.min_area)) & (L > 0)
    if keep:
        clab = np.zeros(nc, np.int16); clab[cc] = lab
        bad &= ~np.isin(clab, list(keep))
    idx = np.flatnonzero(bad)
    if not len(idx):
        print(f'проход {it+1}: чистить нечего'); break
    # winning neighbour by shared border length
    src = np.r_[ca, cb]; dstl = np.r_[lab[eb[bnd]], lab[ea[bnd]]]; wl = np.r_[ln, ln]
    order = np.lexsort((-wl, dstl.astype(np.int64), src.astype(np.int64)))
    src, dstl, wl = src[order], dstl[order], wl[order]
    grp = np.r_[True, (src[1:] != src[:-1]) | (dstl[1:] != dstl[:-1])]
    gi = np.cumsum(grp) - 1
    tot = np.bincount(gi, weights=wl)
    gsrc, gdst = src[grp], dstl[grp]
    best = {}
    o2 = np.lexsort((-tot, gsrc.astype(np.int64)))
    gs, gd = gsrc[o2], gdst[o2]
    first = np.r_[True, gs[1:] != gs[:-1]]
    win = np.full(nc, -1, np.int16); win[gs[first]] = gd[first]
    take = idx[win[idx] >= 0]
    if onlyon:
        take = take[np.isin(win[take], list(onlyon))]
    m = np.isin(cc, take)
    changed = int(m.sum())
    print(f'проход {it+1}: кусков {len(take)} из {nc}, граней {changed}, '
          f'площадь {round(float(A[take].sum()),2)} мм2')
    lab[m] = win[cc[m]]
    if not changed: break

d['lab'] = lab.astype(np.int8)
np.savez_compressed(a.dst, **d)
u, c = np.unique(lab, return_counts=True)
print('филаменты после:', dict(zip(u.tolist(), c.tolist())))
