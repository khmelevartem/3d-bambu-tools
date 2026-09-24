#!/usr/bin/env python3
"""Пересобрать рваную сетку в сплошное тело: где внутренность — решают лучи,
где именно проходит поверхность — знаковое поле расстояний.

Когда нужен: `meshfix.py --remesh` (воксели Blender / OpenVDB) на сетке с
большим числом открытых рёбер строит не тело, а КОРКУ — замкнутую плёнку
вокруг поверхности. Признак: объём после ремонта близок к нулю (на `adam
guitar 2.3mf` — 28.8 мм³ вместо 105 000 при 39 тыс. открытых рёбер), а в
превью модель просвечивает кружевом. Объём корки растёт с вокселем линейно:
0.15 мм -> 28.8 мм³, 0.3 мм -> 95.9 мм³ — это и есть диагноз.

Два шага, и каждый закрывает свою беду.

1. ЗАНЯТОСТЬ — лучами с учётом ЗНАКА пересечения (число оборотов). Рваный шов
   даёт нулевой вклад, перекрывающиеся тела объединяются правильно — в отличие
   от правила чёт-нечет, которое на перекрытии оставляет дыру. Лучи пускаются
   по ВСЕМ ТРЁМ осям, и воксель считается внутренним по большинству (2 из 3).
   Одной оси мало: там, где оболочка разорвана поперёк луча, он не находит
   входа вовсе и оставляет сквозной тоннель через всю модель.

2. ПОВЕРХНОСТЬ — по знаковому полю расстояний в узкой полосе: расстояние от
   центра вокселя до ближайшего треугольника, знак из шага 1. Бинарная
   занятость (внутри/снаружи, без полутонов) даёт марширующим кубам лесенку
   террас в половину вокселя, и на пологих местах — лбу, щеке — она читается
   как горизонтали на топографической карте. У поля расстояний подвоксельное
   положение поверхности сохранено, и колец не возникает.

    uv run --with numpy --with scipy --with scikit-image python \\
        tools/meshsolid.py вход.stl выход.stl --voxel 0.15

Единицы — те же, в которых лежит вход: у сетки прямо из 3MF они свои, строка
`!!` в `meshdoctor` говорит масштаб из <build>. Дальше `meshdoctor.py`
подтверждает «ЧИСТО», а объём сверяется с исходным.
"""
import argparse, struct, sys
import numpy as np
import scipy.sparse as sp
from scipy.sparse.csgraph import connected_components
from scipy.ndimage import gaussian_filter, binary_dilation
from skimage.measure import marching_cubes


# ------------------------------------------------------------------ ввод-вывод
def read_mesh(path):
    if path.endswith('.npz'):
        d = np.load(path, allow_pickle=True)
        return d['V'].astype(np.float64), d['F']
    buf = open(path, 'rb').read()
    n = struct.unpack('<I', buf[80:84])[0]
    rec = np.frombuffer(buf[84:84 + n*50],
                        dtype=np.dtype([('n', '<3f4'), ('v', '<9f4'), ('a', '<u2')]))
    P = rec['v'].reshape(-1, 3, 3).astype(np.float64)
    V, idx = np.unique(np.round(P.reshape(-1, 3), 5), axis=0, return_inverse=True)
    return V, idx.reshape(-1, 3)


def write_stl(path, V, F):
    P = V[F].astype(np.float32)
    n = np.cross(P[:, 1]-P[:, 0], P[:, 2]-P[:, 0])
    ln = np.linalg.norm(n, axis=1); ln[ln == 0] = 1
    rec = np.zeros(len(F), dtype=np.dtype([('n', '<3f4'), ('v', '<9f4'), ('a', '<u2')]))
    rec['n'] = (n/ln[:, None]).astype(np.float32); rec['v'] = P.reshape(-1, 9)
    with open(path, 'wb') as fh:
        fh.write(b'\0'*80); fh.write(struct.pack('<I', len(F))); fh.write(rec.tobytes())


def signed_volume(V, F):
    P = V[F]
    return float(np.einsum('ij,ij->i', P[:, 0], np.cross(P[:, 1], P[:, 2])).sum()/6)


def orient_outward(V, F):
    """Развернуть куски, у которых знаковый объём отрицательный."""
    n = len(F)
    E = np.sort(np.concatenate([F[:, [0, 1]], F[:, [1, 2]], F[:, [2, 0]]]), axis=1)
    key = E[:, 0].astype(np.int64)*(1 << 32) + E[:, 1]
    o = np.argsort(key, kind='stable'); ks = key[o]; fid = np.tile(np.arange(n), 3)[o]
    i = np.flatnonzero(ks[:-1] == ks[1:])
    nc, cc = connected_components(
        sp.coo_matrix((np.ones(len(i)), (fid[i], fid[i+1])), shape=(n, n)), directed=False)
    P = V[F]
    vol = np.einsum('ij,ij->i', P[:, 0], np.cross(P[:, 1], P[:, 2]))/6
    bad = np.flatnonzero(np.bincount(cc, weights=vol, minlength=nc) < 0)
    flip = np.isin(cc, bad)
    F = F.copy(); F[flip] = F[flip][:, [0, 2, 1]]
    return F, nc, int(flip.sum())


# ------------------------------------------------------- занятость лучами
def occupancy_axis(V, F, lo, N, H, r):
    """Занятость по лучам вдоль оси r. Массив bool в раскладке (nz, ny, nx)."""
    u, v = (r+1) % 3, (r+2) % 3
    Nr, Nu, Nv = N[r], N[u], N[v]
    P = V[F]
    n = np.cross(P[:, 1]-P[:, 0], P[:, 2]-P[:, 0])
    keep = np.abs(n[:, r]) > 1e-12
    P, n = P[keep], n[keep]
    pu, pv = P[:, :, u], P[:, :, v]
    i0 = np.clip(np.ceil((pu.min(1)-lo[u])/H - 0.5).astype(np.int32), 0, Nu-1)
    i1 = np.clip(np.floor((pu.max(1)-lo[u])/H - 0.5).astype(np.int32), -1, Nu-1)
    j0 = np.clip(np.ceil((pv.min(1)-lo[v])/H - 0.5).astype(np.int32), 0, Nv-1)
    j1 = np.clip(np.floor((pv.max(1)-lo[v])/H - 0.5).astype(np.int32), -1, Nv-1)
    ni, nj = np.maximum(i1-i0+1, 0), np.maximum(j1-j0+1, 0)
    cnt = ni*nj; ok = cnt > 0
    P, n, i0, j0, nj, cnt = P[ok], n[ok], i0[ok], j0[ok], nj[ok], cnt[ok]
    tot = int(cnt.sum())
    ti = np.repeat(np.arange(len(P), dtype=np.int64), cnt)
    off = np.arange(tot, dtype=np.int64) - np.repeat(np.cumsum(cnt)-cnt, cnt)
    ii = i0[ti] + (off // nj[ti]).astype(np.int32)
    jj = j0[ti] + (off % nj[ti]).astype(np.int32)
    del off
    U = lo[u] + (ii+0.5)*H; W = lo[v] + (jj+0.5)*H
    a, b, c = P[ti, 0], P[ti, 1], P[ti, 2]
    d1 = (b[:, u]-a[:, u])*(W-a[:, v]) - (b[:, v]-a[:, v])*(U-a[:, u])
    d2 = (c[:, u]-b[:, u])*(W-b[:, v]) - (c[:, v]-b[:, v])*(U-b[:, u])
    d3 = (a[:, u]-c[:, u])*(W-c[:, v]) - (a[:, v]-c[:, v])*(U-c[:, u])
    hit = ((d1 >= 0) & (d2 >= 0) & (d3 >= 0)) | ((d1 <= 0) & (d2 <= 0) & (d3 <= 0))
    del d1, d2, d3
    ti, ii, jj, U, W = ti[hit], ii[hit], jj[hit], U[hit], W[hit]
    nn, aa = n[ti], P[ti, 0]
    T = aa[:, r] - (nn[:, u]*(U-aa[:, u]) + nn[:, v]*(W-aa[:, v]))/nn[:, r]
    s = np.where(nn[:, r] < 0, np.int8(1), np.int8(-1))     # входим / выходим
    del nn, aa, U, W, a, b, c, hit, P, n

    o = np.lexsort((T, ii.astype(np.int64), jj.astype(np.int64)))
    T, s, col = T[o], s[o], jj[o].astype(np.int64)*Nu + ii[o]
    w = np.cumsum(s.astype(np.int32))
    start = np.r_[0, np.flatnonzero(col[1:] != col[:-1])+1]
    length = np.diff(np.r_[start, len(col)])
    base = np.repeat(np.r_[0, w[:-1]][start], length)
    w = w - base                                             # обороты внутри столбца
    # сдвиг на минимум столбца: луч, начавшийся «внутри» вывернутого лоскута,
    # иначе уводит весь столбец в минус и оставляет тоннель
    cmin = np.minimum(np.minimum.reduceat(w, start), 0)
    w = w - np.repeat(cmin, length)
    last = np.r_[col[1:] != col[:-1], True]
    si = np.flatnonzero((w > 0) & ~last)
    ia = np.maximum(np.ceil((T[si]-lo[r])/H - 0.5).astype(np.int64), 0)
    ib = np.minimum(np.floor((T[si+1]-lo[r])/H - 0.5).astype(np.int64), Nr-1)
    g = ib >= ia; ia, ib, cl = ia[g], ib[g], col[si][g]
    diff = np.zeros(Nv*Nu*(Nr+1), dtype=np.int16)
    np.add.at(diff, cl*(Nr+1)+ia, np.int16(1))
    np.add.at(diff, cl*(Nr+1)+ib+1, np.int16(-1))
    occ = (np.cumsum(diff.reshape(Nv*Nu, Nr+1), axis=1)[:, :Nr] > 0).reshape(Nv, Nu, Nr)
    del diff
    order = [v, u, r]
    return np.transpose(occ, [order.index(2), order.index(1), order.index(0)])


# ------------------------------------------------- расстояние точка-треугольник
def point_tri_dist2(p, a, b, c):
    """Квадрат расстояния от точек до треугольников (Ericson, ClosestPtPointTriangle)."""
    ab, ac, ap = b-a, c-a, p-a
    d1 = np.einsum('ij,ij->i', ab, ap); d2 = np.einsum('ij,ij->i', ac, ap)
    bp = p-b
    d3 = np.einsum('ij,ij->i', ab, bp); d4 = np.einsum('ij,ij->i', ac, bp)
    cp = p-c
    d5 = np.einsum('ij,ij->i', ab, cp); d6 = np.einsum('ij,ij->i', ac, cp)
    vc = d1*d4 - d3*d2
    vb = d5*d2 - d1*d6
    va = d3*d6 - d5*d4
    den = va + vb + vc
    den = np.where(np.abs(den) < 1e-30, 1e-30, den)
    vv, ww = vb/den, vc/den
    q = a + ab*vv[:, None] + ac*ww[:, None]                  # общий случай: внутри
    # рёбра и вершины
    m = (va <= 0) & ((d4-d3) >= 0) & ((d5-d6) >= 0)          # ребро BC
    if m.any():
        t = (d4[m]-d3[m])/np.maximum((d4[m]-d3[m]) + (d5[m]-d6[m]), 1e-30)
        q[m] = b[m] + (c[m]-b[m])*t[:, None]
    m = (vb <= 0) & (d2 >= 0) & (d6 <= 0)                    # ребро AC
    if m.any():
        q[m] = a[m] + ac[m]*(d2[m]/np.maximum(d2[m]-d6[m], 1e-30))[:, None]
    m = (vc <= 0) & (d1 >= 0) & (d3 <= 0)                    # ребро AB
    if m.any():
        q[m] = a[m] + ab[m]*(d1[m]/np.maximum(d1[m]-d3[m], 1e-30))[:, None]
    m = (d1 <= 0) & (d2 <= 0); q[m] = a[m]                   # вершина A
    m = (d3 >= 0) & (d4 <= d3); q[m] = b[m]                  # вершина B
    m = (d6 >= 0) & (d5 <= d6); q[m] = c[m]                  # вершина C
    dq = p - q
    return np.einsum('ij,ij->i', dq, dq)


def band_distance(P, bb_lo, bb_hi, lo, N, H, k0, k1, band, chunk=3_000_000):
    """Незнаковое расстояние до поверхности в полосе, для плиты вокселей [k0,k1)."""
    nzs = k1 - k0
    out = np.full(nzs*N[1]*N[0], (band*H)**2, np.float32)
    zlo = lo[2] + (k0-0.5)*H - band*H
    zhi = lo[2] + (k1+0.5)*H + band*H
    sel = np.flatnonzero((bb_hi[:, 2] >= zlo) & (bb_lo[:, 2] <= zhi))
    if not len(sel):
        return np.sqrt(out.reshape(nzs, N[1], N[0]))
    e = int(np.ceil(band))
    r0 = np.empty((len(sel), 3), np.int64); r1 = np.empty((len(sel), 3), np.int64)
    for ax, (nlo, nhi) in enumerate(((0, N[0]-1), (0, N[1]-1), (k0, k1-1))):
        r0[:, ax] = np.clip(np.ceil((bb_lo[sel, ax]-lo[ax])/H - 0.5).astype(np.int64) - e, nlo, nhi)
        r1[:, ax] = np.clip(np.floor((bb_hi[sel, ax]-lo[ax])/H - 0.5).astype(np.int64) + e, nlo, nhi)
    n3 = np.maximum(r1 - r0 + 1, 0)
    cnt = n3[:, 0]*n3[:, 1]*n3[:, 2]
    ok = cnt > 0
    sel, r0, n3, cnt = sel[ok], r0[ok], n3[ok], cnt[ok]
    edge = np.r_[0, np.cumsum(cnt)]
    step = np.searchsorted(edge, np.arange(0, edge[-1] + chunk, chunk))
    step = np.unique(np.clip(np.r_[0, step, len(cnt)], 0, len(cnt)))
    for s0, s1 in zip(step[:-1], step[1:]):
        if s1 <= s0:
            continue
        cs, cr0, cn3, cc = sel[s0:s1], r0[s0:s1], n3[s0:s1], cnt[s0:s1]
        tot = int(cc.sum())
        ti = np.repeat(np.arange(len(cs), dtype=np.int64), cc)
        off = np.arange(tot, dtype=np.int64) - np.repeat(np.cumsum(cc)-cc, cc)
        njk = (cn3[:, 1]*cn3[:, 2])[ti]
        ix = cr0[ti, 0] + off // njk
        rem = off % njk
        jy = cr0[ti, 1] + rem // cn3[ti, 2]
        kz = cr0[ti, 2] + rem % cn3[ti, 2]
        del off, rem, njk
        pt = np.empty((tot, 3))
        pt[:, 0] = lo[0] + (ix+0.5)*H
        pt[:, 1] = lo[1] + (jy+0.5)*H
        pt[:, 2] = lo[2] + (kz+0.5)*H
        tri = P[cs[ti]]
        d2 = point_tri_dist2(pt, tri[:, 0], tri[:, 1], tri[:, 2]).astype(np.float32)
        del pt, tri
        idx = (kz-k0)*(N[1]*N[0]) + jy*N[0] + ix
        del ix, jy, kz, ti
        if not len(idx):
            continue
        o = np.argsort(idx, kind='stable')
        idx, d2 = idx[o], d2[o]
        st = np.r_[0, np.flatnonzero(idx[1:] != idx[:-1])+1]
        ui, um = idx[st], np.minimum.reduceat(d2, st)
        out[ui] = np.minimum(out[ui], um)
    return np.sqrt(out.reshape(nzs, N[1], N[0]))



def extrapolate(sdf, synth, occ, H, sigma_fill=2.5, rounds=4):
    """Достроить поле там, где рядом не было ни одного треугольника.

    В дырке, которую заросла только логика заливки, расстояния до поверхности
    нет: поле упирается в край полосы, и марширующие кубы дают ступеньку в
    воксель. Взять значения оттуда неоткуда, поэтому они строятся из САМОЙ
    занятости, размытой гауссом: нулевой уровень `0.5 - размытая занятость`
    идёт по сглаженной границе тела. Деталей в дырке нет по определению, так
    что сглаживание там ничего не теряет, а лесенку снимает.

    Дальше несколько проходов диффузии сшивают заплатку с настоящим полем по
    краю дырки, чтобы на стыке не было излома. Значения вне `synth` при этом
    держатся жёстко — иначе диффузия расползается по всему объёму и рожает
    поверхность на пустом месте (на эталонной сфере габарит уезжал с 20.0 на
    20.9 мм).
    """
    occf = gaussian_filter(occ.astype(np.float32), sigma_fill, mode='nearest')
    np.copyto(sdf, (0.5 - occf)*(2.0*H), where=synth)
    del occf
    keep = ~synth
    for _ in range(rounds):
        tmp = gaussian_filter(sdf, 1.0, mode='nearest')
        np.copyto(tmp, sdf, where=keep)
        sdf = tmp
    return sdf


def collapse_degenerate(V, F, ndigits=6):
    """Сварить вершины, совпадающие после округления, и выбросить грани,
    у которых после этого два индекса совпали.

    Это СХЛОПЫВАНИЕ, а не удаление: соседи теряют не ребро, а нулевой
    треугольник между ними, и сетка остаётся замкнутой. Нужно потому, что
    сетка потом переводится в единицы файла 3MF (они бывают в разы мельче),
    и там округление слепляет вершины, которых в миллиметрах хватало.
    """
    key, inv = np.unique(np.round(V, ndigits), axis=0, return_inverse=True)
    F2 = inv[F]
    good = (F2[:, 0] != F2[:, 1]) & (F2[:, 1] != F2[:, 2]) & (F2[:, 2] != F2[:, 0])
    F2 = F2[good]
    used, F2 = np.unique(F2, return_inverse=True)
    return key[used], F2.reshape(-1, 3), int((~good).sum())


def drop_junk(V, F, frac=1e-3):
    """Выбросить отдельные тельца мельче доли главного: марширующие кубы
    оставляют их на одиночных вокселях, а в слайсере это крошки рядом с деталью."""
    n = len(F)
    E = np.sort(np.concatenate([F[:, [0, 1]], F[:, [1, 2]], F[:, [2, 0]]]), axis=1)
    key = E[:, 0].astype(np.int64)*(1 << 32) + E[:, 1]
    o = np.argsort(key, kind='stable'); ks = key[o]; fid = np.tile(np.arange(n), 3)[o]
    i = np.flatnonzero(ks[:-1] == ks[1:])
    nc, cc = connected_components(
        sp.coo_matrix((np.ones(len(i)), (fid[i], fid[i+1])), shape=(n, n)), directed=False)
    if nc == 1:
        return V, F, 0
    P = V[F]
    ar = 0.5*np.linalg.norm(np.cross(P[:, 1]-P[:, 0], P[:, 2]-P[:, 0]), axis=1)
    a = np.bincount(cc, weights=ar, minlength=nc)
    keep = np.isin(cc, np.flatnonzero(a >= a.max()*frac))
    F = F[keep]
    used, F = np.unique(F, return_inverse=True)
    return V[used], F.reshape(-1, 3), int(nc - 1 - (a < a.max()*frac).sum() + (a < a.max()*frac).sum())


def build(V, F, H, band, sigma, slab):
    lo = V.min(0) - (band+3)*H
    hi = V.max(0) + (band+3)*H
    N = tuple(int(x) for x in np.ceil((hi-lo)/H).astype(int) + 1)
    nx, ny, nz = N
    print(f'сетка {nx}x{ny}x{nz} = {nx*ny*nz/1e6:.1f} млн вокселей, сторона {H}')

    acc = np.zeros((nz, ny, nx), np.uint8)
    for r, nm in ((0, 'X'), (1, 'Y'), (2, 'Z')):
        o = occupancy_axis(V, F, lo, N, H, r)
        print(f'  лучи вдоль {nm}: внутри {int(o.sum())} вокселей')
        acc += o
        del o
    occ = acc >= 2                       # большинство двух осей из трёх
    print(f'занято {int(occ.sum())} вокселей => объём {occ.sum()*H**3:.1f}; '
          f'оси разошлись на {int(((acc > 0) & (acc < 3)).sum())} вокселях')
    del acc

    # Поле расстояний считается ЦЕЛИКОМ, а не по плитам: доращивание в дырках
    # (extrapolate) размазывается на десятки вокселей, и у соседних плит
    # значения на общей плоскости разошлись бы. Проверено 20.09.2026: при
    # поплитном доращивании сетка рассыпалась на 12 тел и 9 non-manifold рёбер.
    P = V[F]
    bb_lo, bb_hi = P.min(1), P.max(1)
    sdf = np.empty((nz, ny, nx), np.float32)
    for k0 in range(0, nz, slab):
        k1 = min(k0+slab, nz)
        sdf[k0:k1] = band_distance(P, bb_lo, bb_hi, lo, N, H, k0, k1, band)
    known = sdf < (band-0.05)*H
    np.negative(sdf, out=sdf, where=occ)
    nb = np.ones((3, 3, 3), bool)
    edge = occ ^ binary_dilation(occ, structure=nb)
    synth = (~known) & binary_dilation(edge, structure=nb, iterations=3)
    del edge, known
    ns = int(synth.sum())
    if ns:
        print(f'  поверхность без треугольника рядом: {ns} вокселей — доращиваю поле')
        sdf = extrapolate(sdf, synth, occ, H)
    del synth, occ
    if sigma > 0:
        sdf = gaussian_filter(sdf, sigma, mode='nearest')

    Vs, Fs, base = [], [], 0
    for z0 in range(0, nz-1, slab):
        z1 = min(z0+slab, nz-1)
        blk = sdf[z0:z1+1]
        if blk.min() > 0 or blk.max() < 0:
            continue
        vv, ff, _, _ = marching_cubes(blk, 0.0, spacing=(H, H, H))
        vv = vv + np.array([lo[2] + z0*H, lo[1], lo[0]])
        Vs.append(vv[:, ::-1]); Fs.append(ff + base); base += len(vv)
        print(f'  плита z {z0}..{z1}: граней {len(ff)}')
    return np.concatenate(Vs), np.concatenate(Fs)


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('src'); ap.add_argument('dst')
    ap.add_argument('--voxel', type=float, required=True, help='сторона вокселя в единицах файла')
    ap.add_argument('--band', type=float, default=2.0, help='полоса поля расстояний, вокселей')
    ap.add_argument('--sigma', type=float, default=0.6,
                    help='сглаживание поля расстояний, вокселей; на линейном поле '
                         'нулевой уровень не сдвигает, только скругляет острые кромки')
    ap.add_argument('--slab', type=int, default=96, help='высота плиты, слоёв вокселей')
    a = ap.parse_args()

    V, F = read_mesh(a.src)
    v0 = signed_volume(V, F)
    F, nb, nflip = orient_outward(V, F)
    print(f'{len(F)} граней, тел {nb}, развёрнуто {nflip}; объём до {v0:.1f}')
    V2, F2 = build(V, F, a.voxel, a.band, a.sigma, a.slab)
    n0 = len(F2)
    V2, F2, _ = drop_junk(V2, F2)
    if len(F2) != n0:
        print(f'выброшено мусорных тел: граней {n0-len(F2)}')
    V2, F2, nd = collapse_degenerate(V2, F2)
    if nd:
        print(f'схлопнуто вырожденных граней: {nd}')
    v2 = signed_volume(V2, F2)
    if v2 < 0:
        F2 = F2[:, [0, 2, 1]]; v2 = -v2
    write_stl(a.dst, V2, F2)
    print(f'стало: вершин {len(V2)}, граней {len(F2)}, объём {v2:.1f} '
          f'({(v2/v0-1)*100:+.2f} %), габарит {np.round(V2.max(0)-V2.min(0), 3)}')
    print('записано', a.dst)
