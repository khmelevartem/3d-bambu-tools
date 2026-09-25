#!/usr/bin/env python3
"""Compare a 3D model against a reference picture.

Lays an orthographic projection of the model over the photograph at one scale
and measures feature deviations in millimetres.

    UV="uv run --quiet --with numpy --with scipy --with pillow python"
    $UV tools/refcompare.py fit  model.3mf photo.jpg --out work/cmp.npz [--cut-from-top 45]
    $UV tools/refcompare.py sheet   work/cmp.npz work/comparison.png
    $UV tools/refcompare.py measure work/cmp.npz --report work/numbers.txt

The scale is not guessed: three parameters - one scale and two offsets - are
found by maximising silhouette agreement. **That agreement is printed, and it
decides whether the other numbers can be trusted at all.**
"""
import argparse, sys, zipfile, re
import numpy as np
import xml.etree.ElementTree as ET
import scipy.ndimage as ndi
from scipy.optimize import minimize

NS = '{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}'
NSP = '{http://schemas.microsoft.com/3dmanufacturing/production/2015/06}'

# ---------------------------------------------------------------- geometry

def _tf(s):
    a = np.array(s.split(), dtype=np.float64)
    return a[:9].reshape(3, 3), a[9:12]          # p' = p @ M + t


def _read_model_xml(raw):
    """id объекта -> ('mesh', V, F) либо ('components', [(path, id, M, t), ...])."""
    objs = {}
    root = ET.fromstring(raw)
    for ob in root.iter(NS + 'object'):
        oid = ob.get('id')
        mesh = ob.find(NS + 'mesh')
        if mesh is not None:
            V = np.array([(v.get('x'), v.get('y'), v.get('z'))
                          for v in mesh.find(NS + 'vertices')], dtype=np.float64)
            F = np.array([(t.get('v1'), t.get('v2'), t.get('v3'))
                          for t in mesh.find(NS + 'triangles')], dtype=np.int64)
            objs[oid] = ('mesh', V, F)
            continue
        comps = ob.find(NS + 'components')
        if comps is not None:
            lst = []
            for c in comps:
                M, t = _tf(c.get('transform', '1 0 0 0 1 0 0 0 1 0 0 0'))
                lst.append((c.get(NSP + 'path'), c.get('objectid'), M, t))
            objs[oid] = ('components', lst)
    return objs


def load_3mf(path):
    """Вершины в миллиметрах на столе: x вправо, y вглубь, z вверх.

    Трансформация из <build><item> обязательна: в проектах Bambu Studio сетка
    внутри объекта лежит в своих единицах, а в миллиметры её переводит именно
    эта матрица. Без неё все числа будут в «попугаях»."""
    z = zipfile.ZipFile(path)
    cache = {}

    def objects_of(part):
        key = part.lstrip('/')
        if key not in cache:
            cache[key] = _read_model_xml(z.read(key))
        return cache[key]

    root_part = '3D/3dmodel.model'
    tree = ET.fromstring(z.read(root_part))
    VS, FS, n = [], [], 0

    def emit(part, oid, M, t):
        nonlocal n
        kind = objects_of(part)[oid]
        if kind[0] == 'mesh':
            VS.append(kind[1] @ M + t)
            FS.append(kind[2] + n)
            n += len(kind[1])
        else:
            for p2, oid2, M2, t2 in kind[1]:
                emit(p2 or part, oid2, M2 @ M, t2 @ M + t)

    build = tree.find(NS + 'build')
    for it in (build if build is not None else []):
        M, t = _tf(it.get('transform', '1 0 0 0 1 0 0 0 1 0 0 0'))
        emit(root_part, it.get('objectid'), M, t)
    if not VS:
        sys.exit('в 3MF не нашлось ни одного объекта в <build>')
    return np.vstack(VS), np.vstack(FS)


def load_stl(path):
    b = np.fromfile(path, dtype=np.uint8)
    if bytes(b[:5]).lower() == b'solid' and b'facet' in bytes(b[:512]):
        txt = open(path, 'r', errors='ignore').read()
        P = np.array(re.findall(r'vertex\s+([-\deE.+]+)\s+([-\deE.+]+)\s+([-\deE.+]+)', txt),
                     dtype=np.float64).reshape(-1, 3, 3)
    else:
        k = int(b[80:84].view(np.uint32)[0])
        P = b[84:84 + 50 * k].reshape(k, 50)[:, :48].copy().view(
            np.float32).reshape(k, 4, 3)[:, 1:, :].astype(np.float64)
    V, inv = np.unique(P.reshape(-1, 3).round(6), axis=0, return_inverse=True)
    return V, inv.reshape(-1, 3)


def load_mesh(path):
    V, F = (load_3mf(path) if path.lower().endswith('.3mf') else load_stl(path))
    print('сетка: вершин %d, граней %d, габарит %s мм'
          % (len(V), len(F), np.round(V.max(0) - V.min(0), 2)))
    return V, F


VIEWS = {'front': (0, 1, 0), 'back': (0, -1, 0), 'left': (1, 0, 0), 'right': (-1, 0, 0)}


def to_screen(V, view):
    """-> (u вправо, v вверх, d глубина: больше = ближе к камере)."""
    d = np.array(VIEWS[view], float)
    up = np.array([0, 0, 1.0])
    right = np.cross(d, up)
    return V @ right, V @ up, -(V @ d)

# ------------------------------------------------------------- rasterising

def vnormals(V, F):
    P = V[F]
    n = np.cross(P[:, 1] - P[:, 0], P[:, 2] - P[:, 0])
    N = np.zeros_like(V)
    for k in range(3):
        np.add.at(N, F[:, k], n)
    L = np.linalg.norm(N, axis=1)
    L[L == 0] = 1
    return N / L[:, None]


def raster(U, Vv, D, F, N, W, H, s, cx, cy, seed=0):
    """Треугольники засеиваются точками по площади проекции -> z-буфер.

    Сплат по одним вершинам оставляет соль-перец даже на плотной сетке:
    шаг вершин сравним с пикселем только в среднем, а не везде."""
    X = cx + s * U
    Y = cy - s * Vv
    Zb = np.full(W * H, -1e9)
    Nb = np.zeros((W * H, 3))
    ax, ay = X[F[:, 0]], Y[F[:, 0]]
    bx, by = X[F[:, 1]], Y[F[:, 1]]
    ccx, ccy = X[F[:, 2]], Y[F[:, 2]]
    area = np.abs((bx - ax) * (ccy - ay) - (ccx - ax) * (by - ay)) / 2
    mnx = np.minimum(np.minimum(ax, bx), ccx)
    mxx = np.maximum(np.maximum(ax, bx), ccx)
    mny = np.minimum(np.minimum(ay, by), ccy)
    mxy = np.maximum(np.maximum(ay, by), ccy)
    idxs = np.where((mxx >= 0) & (mnx < W) & (mxy >= 0) & (mny < H))[0]
    ns = np.clip(np.ceil(area[idxs] * 3).astype(np.int64) + 1, 1, 64)
    rng = np.random.default_rng(seed)
    for k in np.unique(ns):
        g = idxs[ns == k]
        for ch in range(0, len(g), 400000):
            t = g[ch:ch + 400000]
            u = rng.random((len(t), k))
            v = rng.random((len(t), k))
            fl = u + v > 1
            u[fl] = 1 - u[fl]
            v[fl] = 1 - v[fl]
            w = 1 - u - v
            i0, i1, i2 = F[t, 0][:, None], F[t, 1][:, None], F[t, 2][:, None]
            px = np.round(w * X[i0] + u * X[i1] + v * X[i2]).astype(np.int64).ravel()
            py = np.round(w * Y[i0] + u * Y[i1] + v * Y[i2]).astype(np.int64).ravel()
            pz = (w * D[i0] + u * D[i1] + v * D[i2]).ravel()
            nn = (w[..., None] * N[i0] + u[..., None] * N[i1]
                  + v[..., None] * N[i2]).reshape(-1, 3)
            ok = (px >= 0) & (px < W) & (py >= 0) & (py < H)
            li = py[ok] * W + px[ok]
            pz = pz[ok]
            nn = nn[ok]
            np.maximum.at(Zb, li, pz)
            hit = Zb[li] - pz < 1e-12
            Nb[li[hit]] = nn[hit]
    L = np.linalg.norm(Nb, axis=1)
    L[L == 0] = 1
    return Zb.reshape(H, W), (Nb / L[:, None]).reshape(H, W, 3)

# -------------------------------------------------------------------- photo

def photo_mask(img, tol=14):
    """Фон у студийного рендера ровный: берём медиану рамки."""
    A = img.astype(np.float64)
    bg = np.median(np.concatenate([A[:5].reshape(-1, 3), A[-5:].reshape(-1, 3),
                                   A[:, :5].reshape(-1, 3), A[:, -5:].reshape(-1, 3)]), axis=0)
    return np.abs(A - bg).max(2) > tol, bg

# --------------------------------------------------------------------- fit

def do_fit(a):
    from PIL import Image
    img = np.asarray(Image.open(a.photo).convert('RGB'))
    H, W = img.shape[:2]
    P, _ = photo_mask(img, a.bg_tol)
    V, F = load_mesh(a.model)
    U, Vv, D = to_screen(V, a.view)
    U = U - (U.min() + U.max()) / 2      # a part on the bed sits at the bed centre:
                                         # without this the initial guess flies off-frame
    Nu, Nvv, Nd = to_screen(vnormals(V, F), a.view)
    N = np.stack([Nu, Nvv, Nd], 1)
    top = Vv.max()
    cut = (top - a.cut_from_top) if a.cut_from_top else -1e9
    SC = a.fit_downscale
    h, w = H // SC, W // SC
    Pm = P[:h * SC, :w * SC].reshape(h, SC, w, SC).mean((1, 3)) > 0.5

    def iou(p):
        s, cx, cy = p
        if s <= 0:
            return 1.0
        Z, _ = raster(U, Vv, D, F, N, w, h, s, cx, cy)
        M = Z > -1e8
        rc = h if cut < -1e8 else max(10, min(h, int(round(cy - s * cut))))
        A, B = M[:rc], Pm[:rc]
        return 1 - (A & B).sum() / max((A | B).sum(), 1)

    if a.scale:
        x = np.array([a.scale / SC, a.cx / SC, a.cy / SC])
        best = type('R', (), {'x': x, 'fun': iou(x)})
    else:
        rows = np.nonzero(P.any(1))[0]
        cols = np.nonzero(P.any(0))[0]
        s0 = (rows[-1] - rows[0]) / (Vv.max() - Vv.min()) / SC
        x0 = [s0, (cols[0] + cols[-1]) / 2 / SC, rows[0] / SC + s0 * top]
        print('старт: %.4f px/мм, несовпадение %.4f' % (s0 * SC, iou(x0)))
        best = minimize(iou, x0, method='Nelder-Mead',
                        options={'xatol': 0.02, 'fatol': 1e-5, 'maxiter': 500})
    s, cx, cy = best.x * SC
    print('подгонка: %.4f px/мм (%.4f мм/px), центр %.2f, %.2f, совпадение силуэтов %.1f %%'
          % (s, 1 / s, cx, cy, (1 - best.fun) * 100))
    if 1 - best.fun < 0.9:
        print('ВНИМАНИЕ: силуэты сошлись плохо — числа дальше ничего не значат.')
        print('  проверить: тот ли вид (--view), не перспективная ли картинка,')
        print('  не обрезана ли фигура, тот ли --cut-from-top.')
    Z, NB = raster(U, Vv, D, F, N, W, H, s, cx, cy)
    mask = Z > -1e8
    hole = ndi.binary_fill_holes(mask) & ~mask
    Zs = ndi.median_filter(np.where(mask, Z, -1e9), size=3)      # raster speckle
    if hole.any():
        Zs[hole] = ndi.maximum_filter(Zs, size=5)[hole]
        for k in range(3):
            c = ndi.maximum_filter(np.where(mask, NB[..., k], -9), size=3)
            NB[hole, k] = c[hole]
        mask |= hole
    mask &= Zs > -1e8
    np.savez_compressed(a.out, Z=np.where(mask, Zs, -1e9).astype(np.float32),
                        N=NB.astype(np.float16), mask=mask, photo=a.photo,
                        fit=np.array([s, cx, cy]), iou=1 - best.fun,
                        cut=cut, view=a.view)
    print('сохранено:', a.out)


def load_state(p):
    from PIL import Image
    d = np.load(p, allow_pickle=True)
    img = np.asarray(Image.open(str(d['photo'])).convert('RGB')).astype(np.float64)
    return d, img

# -------------------------------------------------------- relief and features

def relief(Z, mask, box, sigma, facing=None, nz=0.6):
    """Высота над сглаженной поверхностью: так проступают глаза, брови, усы.

    sigma брать крупнее самой большой детали и мельче самого лица. Меньше —
    деталь уйдёт в «базу» вместе со своим рельефом и окажется меньше, чем есть;
    больше — в рельеф полезет общая форма головы.

    Считать только по поверхности, повёрнутой к камере (facing = nz нормали):
    там, где щека или подбородок заворачивают от зрителя, глубина обрушивается
    и тянет базу вниз — рельеф вокруг рта раздувается, усы слипаются со щеками
    в одно пятно, и детали перестают различаться."""
    W = np.zeros_like(mask)
    W[box[0]:box[1], box[2]:box[3]] = True
    W &= mask
    if facing is not None:
        W &= facing > nz
    num = ndi.gaussian_filter(np.where(W, Z, 0.0), sigma)
    den = ndi.gaussian_filter(W.astype(float), sigma)
    return np.where(W, Z - num / np.maximum(den, 1e-6), np.nan), W


def match_shift(R, valid, m, maxsh, ringw):
    """Куда уехала деталь: ищем сдвиг, при котором силуэт детали с картинки
    ложится на самое выпуклое место рельефа модели.

    Сегментировать рельеф и сопоставлять пятна — заманчиво, но ломается:
    вылепленное пятно слипается со щекой, режется краем окна, и главное —
    качество пары нечем мерить, ведь перекрытие съедает как раз тот сдвиг,
    который мы ищем. Корреляция ничего не сегментирует и отвечает прямо.

    Кольцо вокруг детали вычитается, чтобы убрать общий наклон лица: важно,
    насколько деталь выступает над своим окружением, а не её высота.

    Считать надо по тем пикселям, где рельеф есть: снаружи окна дырка, и если
    её молча взять за ноль, корреляция уедет туда, где дырка побольше."""
    from scipy.signal import fftconvolve
    ys, xs = np.nonzero(m)
    ring = ndi.binary_dilation(m, np.ones((ringw, ringw), bool)) & ~m
    y0, y1 = max(0, ys.min() - ringw), min(m.shape[0], ys.max() + ringw + 1)
    x0, x1 = max(0, xs.min() - ringw), min(m.shape[1], xs.max() + ringw + 1)
    T = m[y0:y1, x0:x1].astype(float)
    G = ring[y0:y1, x0:x1].astype(float)
    V = valid.astype(float)
    R0 = np.where(valid, R, 0.0)

    def avg(K):
        num = fftconvolve(R0, K[::-1, ::-1], mode='same')
        den = fftconvolve(V, K[::-1, ::-1], mode='same')
        ok = den > 0.9 * K.sum()      # a feature must lie almost entirely inside
                                      # the measured region, or the correlation
                                      # crawls past the window edge
        return np.where(ok, num / np.maximum(den, 1e-9), -1e9), ok

    aT, okT = avg(T)
    aG, okG = avg(G)
    S = np.where(okT & okG, aT - aG, -1e9)
    p0 = (y0 + (T.shape[0] - 1) // 2, x0 + (T.shape[1] - 1) // 2)   # zero offset
    win = S[p0[0] - maxsh:p0[0] + maxsh + 1, p0[1] - maxsh:p0[1] + maxsh + 1]
    k = np.unravel_index(np.argmax(win), win.shape)
    return k[0] - maxsh, k[1] - maxsh, float(win[k]), float(S[p0])


def photo_features(img, mask, dark, amin, amax):
    """Тёмные компактные пятна на светлой картинке: глаза, брови, усы, бородка."""
    lum = img @ np.array([.299, .587, .114])
    lab, n = ndi.label((lum < dark) & mask)
    out = []
    for i in range(1, n + 1):
        m = lab == i
        if not (amin <= m.sum() <= amax):
            continue
        ys, xs = np.nonzero(m)
        if ys.min() == 0 or xs.min() == 0:
            continue
        out.append(m)
    return out


def bbox(m):
    ys, xs = np.nonzero(m)
    return ys.min(), ys.max(), xs.min(), xs.max()


def bbox_iou(a, b):
    y0, y1, x0, x1 = a
    z0, z1, w0, w1 = b
    ih = max(0, min(y1, z1) - max(y0, z0))
    iw = max(0, min(x1, w1) - max(x0, w0))
    inter = ih * iw
    return inter / ((y1 - y0) * (x1 - x0) + (z1 - z0) * (w1 - w0) - inter + 1e-9)


def do_measure(a):
    d, img = load_state(a.state)
    Z = d['Z'].astype(np.float64)
    mask = d['mask']
    s, cx, cy = d['fit']
    mmpx = 1 / s
    P, _ = photo_mask(img, a.bg_tol)
    cut = float(d['cut'])
    band = mask.shape[0] if cut < -1e8 else int(round(cy - s * cut))
    lines = []

    def say(t=''):
        print(t)
        lines.append(t)

    say('совмещение: %.4f px/мм, совпадение силуэтов %.1f %%, вид %s'
        % (s, float(d['iou']) * 100, str(d['view'])))
    say()
    say('СИЛУЭТ ПО СЕЧЕНИЯМ')
    pr = np.nonzero(P.any(1))[0][0]
    mr = np.nonzero(mask.any(1))[0][0]
    say('  верх: картинка строка %d, модель %d -> %+.2f мм' % (pr, mr, (pr - mr) * mmpx))
    worst = 0
    for r in np.linspace(min(pr, mr) + 20, band - 10, a.sections).astype(int):
        x1 = np.nonzero(P[r])[0]
        x2 = np.nonzero(mask[r])[0]
        if not len(x1) or not len(x2):
            continue
        dw = ((x2.max() - x2.min()) - (x1.max() - x1.min())) * mmpx
        dc = ((x2.max() + x2.min()) - (x1.max() + x1.min())) / 2 * mmpx
        worst = max(worst, abs(dw))
        say('  строка %4d: ширина модели %6.2f против %6.2f мм (%+5.2f), центр %+5.2f мм'
            % (r, (x2.max() - x2.min()) * mmpx, (x1.max() - x1.min()) * mmpx, dw, dc))
    say('  наибольшее расхождение ширины: %.2f мм' % worst)

    PF = photo_features(img, P, a.dark, a.feat_min, a.feat_max)
    if a.feature:
        wins = [tuple(int(v) for v in w.split(',')) for w in a.feature]
        sel = []
        for r0, r1, c0, c1 in wins:
            inside = [m for m in PF if r0 <= np.nonzero(m)[0].mean() <= r1
                      and c0 <= np.nonzero(m)[1].mean() <= c1]
            if inside:
                sel.append(max(inside, key=lambda m: m.sum()))
            else:
                print('в окне %d,%d,%d,%d тёмной детали нет' % (r0, r1, c0, c1))
        PF = sel
    if not PF:
        say('\nтёмных деталей на картинке не нашлось — подкрутить --dark / --feat-min')
    else:
        # the relief window covers only the features themselves. The margin
        # follows the smoothing window: any wider and a hat brim or shoulders
        # get into the relief and outweigh the face, stealing the assignment
        bs = [bbox(m) for m in PF]
        pad = int(round(a.base_sigma * s))
        box = (max(0, min(b[0] for b in bs) - pad),
               min(mask.shape[0], max(b[1] for b in bs) + pad),
               max(0, min(b[2] for b in bs) - pad),
               min(mask.shape[1], max(b[3] for b in bs) + pad))
        NB = d['N'].astype(np.float64)
        NB = np.stack([ndi.median_filter(NB[..., k], size=3) for k in range(3)], -1)
        Ln = np.linalg.norm(NB, axis=2)
        Ln[Ln == 0] = 1
        R, Wf = relief(Z, mask, box, a.base_sigma * s, NB[..., 2] / Ln, a.facing)
        Rn = np.nan_to_num(R, nan=-9)
        say('\nДЕТАЛИ (сдвиг модели относительно картинки), найдено %d' % len(PF))
        balls = []
        maxsh = int(round(a.max_shift * s))
        found = {}
        for i, pm in enumerate(PF):
            found[i] = match_shift(Rn, Wf, pm, maxsh, int(round(a.ring * s)))
        order = sorted(range(len(PF)), key=lambda i: (np.nonzero(PF[i])[0].mean(),
                                                      np.nonzero(PF[i])[1].mean()))
        for i in order:
            ys, xs = np.nonzero(PF[i])
            pc = (ys.mean(), xs.mean())
            dy, dx, peak, at0 = found[i]
            say('  деталь x=%4.0f y=%4.0f: модель %+5.2f мм вправо, %+5.2f мм вверх'
                '   (рельеф под ней %.2f мм)'
                % (pc[1], pc[0], dx * mmpx, -dy * mmpx, peak))
            if peak < a.min_peak:
                say('      рельефа почти нет (%.2f мм) — деталь у модели не вылеплена '
                    'или ушла дальше окна поиска' % peak)
            elif abs(dy) >= maxsh - 1 or abs(dx) >= maxsh - 1:
                say('      сдвиг упёрся в границу поиска — увеличить --max-shift')
            # a round feature is an inserted ball: if the surface beneath it
            # really is spherical, its diameter is an honest number, unlike the
            # "edge of the patch", which depends on the chosen threshold
            fill = PF[i].sum() / ((ys.max() - ys.min() + 1) * (xs.max() - xs.min() + 1))
            ar = (xs.max() - xs.min() + 1) / (ys.max() - ys.min() + 1)
            if 0.8 < ar < 1.25 and fill > 0.7 and peak >= a.min_peak:
                sel = np.roll(PF[i], (dy, dx), (0, 1)) & mask
                sel = ndi.binary_erosion(sel, np.ones((9, 9), bool))
                yy, xx = np.nonzero(sel)
                if len(yy) > 500:
                    P3 = np.stack([(xx - cx) / s, (cy - yy) / s, Z[yy, xx]], 1)
                    A = np.c_[2 * P3, np.ones(len(P3))]
                    sol, *_ = np.linalg.lstsq(A, (P3 ** 2).sum(1), rcond=None)
                    c3, rr2 = sol[:3], sol[3] + (sol[:3] ** 2).sum()
                    if rr2 > 0:
                        rad3 = np.sqrt(rr2)
                        res = np.abs(np.linalg.norm(P3 - c3, axis=1) - rad3)
                        dph = ((xs.max() - xs.min()) + (ys.max() - ys.min())) / 2 / s
                        # what is visible is a cap, not the whole ball: the rest
                        # is sunk into the surface. What to compare with the drawn
                        # circle is that cap, the part genuinely lying on the sphere
                        r0 = max(0, yy.min() - 120); r1 = min(Z.shape[0], yy.max() + 120)
                        cc0 = max(0, xx.min() - 120); cc1 = min(Z.shape[1], xx.max() + 120)
                        Yg, Xg = np.mgrid[r0:r1, cc0:cc1]
                        Q = np.stack([(Xg - cx) / s, (cy - Yg) / s, Z[r0:r1, cc0:cc1]], -1)
                        dev = np.abs(np.linalg.norm(Q - c3, axis=-1) - rad3)
                        lab2, _ = ndi.label(ndi.binary_opening(
                            (dev < a.ball_tol) & mask[r0:r1, cc0:cc1], np.ones((3, 3), bool)))
                        cap = lab2 == lab2[int(yy.mean()) - r0, int(xx.mean()) - cc0]
                        vis = 2 * np.sqrt(cap.sum() / np.pi) / s
                        dph = ((xs.max() - xs.min()) + (ys.max() - ys.min())) / 2 / s
                        say('      шарик: сфера Ø %.2f мм, из лица выходит кругом Ø %.2f мм'
                            ' — на картинке нарисован круг Ø %.2f мм' % (2 * rad3, vis, dph))
                        balls.append((pc, (pc[0] + dy, pc[1] + dx)))
                        if res.mean() > 0.1:
                            say('      отклонение от сферы %.3f мм: это не шарик, '
                                'диаметр считать нельзя' % res.mean())
        if len(balls) == 2:
            (p1, m1), (p2, m2) = balls
            say('  расстояние между шариками: у модели %.2f мм, на картинке %.2f мм (%+.2f)'
                % (np.hypot(*(np.array(m1) - m2)) * mmpx,
                   np.hypot(*(np.array(p1) - p2)) * mmpx,
                   (np.hypot(*(np.array(m1) - m2)) - np.hypot(*(np.array(p1) - p2))) * mmpx))
        if a.debug_png:
            from PIL import Image
            base = np.repeat((np.clip(Rn, -1, 2) + 1) / 3 * 255, 3).reshape(Z.shape + (3,))
            base[~Wf] = 255
            out = lambda m: ndi.binary_dilation(m, np.ones((5, 5), bool)) & ~ndi.binary_erosion(m, np.ones((5, 5), bool))
            for i in range(len(PF)):
                base[out(PF[i])] = [220, 0, 0]
                dy, dx, _, _ = found[i]
                base[out(np.roll(PF[i], (dy, dx), (0, 1)))] = [0, 160, 0]
            Image.fromarray(base[box[0]:box[1], box[2]:box[3]].astype(np.uint8)).save(a.debug_png)
            print('\nкарта деталей (красное — картинка, зелёное — модель):', a.debug_png)
    if a.report:
        open(a.report, 'w').write('\n'.join(lines) + '\n')
        print('\nотчёт:', a.report)

# -------------------------------------------------------------------- sheet

def do_sheet(a):
    from PIL import Image, ImageDraw, ImageFont
    FB = '/System/Library/Fonts/Supplemental/Arial Bold.ttf'
    FR = '/System/Library/Fonts/Supplemental/Arial.ttf'
    d, img = load_state(a.state)
    Z = d['Z'].astype(np.float64)
    mask = d['mask']
    s, cx, cy = d['fit']
    mmpx = 1 / s
    NB = d['N'].astype(np.float64)
    NB = np.stack([ndi.median_filter(NB[..., k], size=3) for k in range(3)], -1)
    L = np.linalg.norm(NB, axis=2, keepdims=True)
    L[L == 0] = 1
    NB /= L
    lamp = np.array([-0.32, 0.42, 0.85])
    lamp /= np.linalg.norm(lamp)
    idx = ndi.distance_transform_edt(~mask, return_distances=False, return_indices=True)
    Zf = np.where(mask, Z, Z[tuple(idx)])
    # light shading of the hollows: without it a face looks like a flat pancake.
    # 1 mm window, with the factor chosen so a 0.5 mm crease reads
    ao = np.clip(0.72 + 0.43 * (Zf - ndi.gaussian_filter(Zf, max(2.0, s))), 0.55, 1.12)
    sh = np.clip((0.16 + 0.84 * np.clip(NB @ lamp, 0, 1) ** 0.75) * ao, 0, 1)
    mdl = np.where(mask[..., None], np.repeat(sh[..., None], 3, 2), 1.0) * 255
    gy, gx = np.gradient(np.where(mask, Z, np.nan))
    g = np.hypot(np.nan_to_num(gx), np.nan_to_num(gy))
    edge = (g > np.percentile(g[mask], 96)) & mask
    edge |= mask & ~ndi.binary_erosion(mask, np.ones((3, 3), bool))
    lab, k = ndi.label(edge)
    sz = ndi.sum(edge, lab, range(1, k + 1))
    edge &= ~np.isin(lab, np.where(sz < 60)[0] + 1)
    if a.crop:
        R0, R1, C0, C1 = [int(v) for v in a.crop.split(',')]
    else:
        P, _ = photo_mask(img, 14)
        both = P | mask
        rows = np.nonzero(both.any(1))[0]
        cols = np.nonzero(both.any(0))[0]
        cut = float(d['cut'])
        R0 = max(0, rows[0] - 40)
        R1 = min(mask.shape[0], int(round(cy - s * cut)) + 40 if cut > -1e8 else rows[-1])
        C0, C1 = max(0, cols[0] - 30), min(mask.shape[1], cols[-1] + 30)
    cr = lambda A: A[R0:R1, C0:C1]
    P1, M1 = cr(img), cr(mdl)
    cont = P1.copy()
    cont[ndi.binary_dilation(cr(edge), np.ones((3, 3), bool))] = [0, 215, 255]

    def panel(arr, title):
        im = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
        dr = ImageDraw.Draw(im)
        dr.rectangle([0, 0, im.width, 62], fill=(24, 24, 24))
        dr.text((16, 12), title, font=ImageFont.truetype(FB, 36), fill=(255, 255, 255))
        return im

    ps = [panel(P1, '1. исходная картинка'),
          panel(M1, '2. модель: та же проекция, тот же масштаб'),
          panel(0.45 * P1 + 0.55 * M1, '3. наложение 50 / 50'),
          panel(cont, '4. контур модели поверх картинки')]
    w, h = ps[0].size
    pad = 22
    note = None
    if a.notes:
        txt = [l for l in open(a.notes).read().split('\n')]
        fs = max(18, min(30, int((h - 110) / max(len(txt), 1) * 0.82)))
        note = panel(np.ones((h, w * 2 + pad, 3)) * 255, '5. цифры')
        dr = ImageDraw.Draw(note)
        y = 84
        for ln in txt:
            dr.text((24, y), ln, fill=(20, 20, 20),
                    font=ImageFont.truetype(FB if ln[:1] not in (' ', '') else FR, fs))
            y += int(fs * 1.28)
    rows_n = (len(ps) + 1) // 2 + (1 if note else 0)
    G = Image.new('RGB', (w * 2 + pad * 3, h * rows_n + pad * (rows_n + 1)), (255, 255, 255))
    for i, im in enumerate(ps):
        G.paste(im, (pad + (i % 2) * (w + pad), pad + (i // 2) * (h + pad)))
    if note:
        G.paste(note, (pad, pad + ((len(ps) + 1) // 2) * (h + pad)))
    G.save(a.out)
    print('лист: %s %s  обрезка %d,%d,%d,%d' % (a.out, G.size, R0, R1, C0, C1))

# --------------------------------------------------------------------- CLI

def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='cmd', required=True)

    f = sub.add_parser('fit', help='подогнать проекцию модели под картинку')
    f.add_argument('model')
    f.add_argument('photo')
    f.add_argument('--out', default='work/cmp.npz')
    f.add_argument('--view', default='front', choices=list(VIEWS))
    f.add_argument('--cut-from-top', type=float, default=0.0,
                   help='мм от макушки: сверять только эту часть (для головы)')
    f.add_argument('--fit-downscale', type=int, default=4)
    f.add_argument('--bg-tol', type=float, default=14)
    f.add_argument('--scale', type=float, help='задать подгонку руками, px/мм')
    f.add_argument('--cx', type=float)
    f.add_argument('--cy', type=float)
    f.set_defaults(func=do_fit)

    m = sub.add_parser('measure', help='числа: силуэт по сечениям и сдвиги деталей')
    m.add_argument('state')
    m.add_argument('--report')
    m.add_argument('--sections', type=int, default=10)
    m.add_argument('--dark', type=float, default=95, help='порог яркости тёмных деталей')
    m.add_argument('--feat-min', type=int, default=800)
    m.add_argument('--feat-max', type=int, default=60000)
    m.add_argument('--base-sigma', type=float, default=3.0,
                   help='мм: окно сглаживания базовой поверхности под рельеф')
    m.add_argument('--max-shift', type=float, default=4.0,
                   help='мм: докуда искать уехавшую деталь')
    m.add_argument('--ring', type=float, default=1.2,
                   help='мм: ширина кольца вокруг детали, по которому берётся фон')
    m.add_argument('--ball-tol', type=float, default=0.05,
                   help='мм: с какой точностью поверхность считается лежащей на шарике')
    m.add_argument('--min-peak', type=float, default=0.15,
                   help='мм: ниже этого считаем, что рельефа под деталью нет')
    m.add_argument('--facing', type=float, default=0.6,
                   help='нижняя граница nz нормали: рельеф считается только '
                        'по поверхности, повёрнутой к камере')
    m.add_argument('--bg-tol', type=float, default=14)
    m.add_argument('--debug-png', help='карта: что с чем сопоставилось')
    m.add_argument('--feature', action='append', default=[],
                   help='r0,r1,c0,c1 — задать деталь окном вручную; можно несколько раз')
    m.set_defaults(func=do_measure)

    s = sub.add_parser('sheet', help='лист сравнения PNG')
    s.add_argument('state')
    s.add_argument('out')
    s.add_argument('--crop', help='r0,r1,c0,c1')
    s.add_argument('--notes', help='текстовый файл с числами -> пятая панель')
    s.set_defaults(func=do_sheet)

    a = p.parse_args()
    a.func(a)


if __name__ == '__main__':
    main()
