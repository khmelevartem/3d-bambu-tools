#!/usr/bin/env python3
"""Убрать непечатаемый крап и волосяные линии в покраске 3MF.

Сопло не умеет класть полосу уже своей линии: цветной кусок тоньше ширины
линии в пластике не появится, а в нарезке превратится в лишнюю смену филамента.
Здесь такие куски отдаются соседу, с которым у них самая длинная граница.

Мерой «тонкости» взята эффективная ширина w = 2*S/L (для полосы шириной w
площадь S = w*l, периметр L ~ 2*l). Куски тоньше --min-width или мельче
--min-area перекрашиваются; счёт идёт в НАСТОЯЩИХ миллиметрах — масштаб
из <build> берётся ключом --scale.

    uv run --with numpy --with scipy python tools/paint_despeckle.py \\
        work/p.npz work/p2.npz --scale 3.9397 --min-width 0.6 --min-area 1.0
"""
import argparse, numpy as np, scipy.sparse as sp
from scipy.sparse.csgraph import connected_components

ap = argparse.ArgumentParser()
ap.add_argument('src'); ap.add_argument('dst')
ap.add_argument('--scale', type=float, default=1.0, help='во сколько раз <build> увеличивает сетку')
ap.add_argument('--min-width', type=float, default=0.6, help='мм, эффективная ширина куска')
ap.add_argument('--min-area',  type=float, default=1.0, help='мм2')
ap.add_argument('--keep', default='', help='филаменты, которые не трогать, через запятую')
ap.add_argument('--rounds', type=int, default=4)
ap.add_argument('--only-on', default='', help='перекрашивать только куски, чей сосед — из этого списка')
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
    A = np.bincount(cc, weights=area, minlength=nc) * S2          # мм2
    # длина границы куска и сосед с самой длинной общей границей
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
    # сосед-победитель по длине общей границы
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
