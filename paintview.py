#!/usr/bin/env python3
"""Посмотреть покраску и ткнуть в точку на модели.

Bambu Studio показывает покраску только человеку у экрана. Этот рендерер рисует
её сам: плоская заливка по филаментам с z-буфером, чтобы правку можно было
увидеть, не поднимая интерфейс. Он же умеет обратное — сказать, какая грань
и какие координаты под заданным пикселем.

    uv run --with numpy python tools/paintview.py render work/p.npz вид.png \\
        --eye 0,-150,14 --target 0,-20,14 --fov 30 --size 1100x900
    uv run --with numpy python tools/paintview.py pick work/p.npz \\
        --eye 0,-150,14 --target 0,-20,14 --fov 30 --size 1100x900 --px 520,430
    uv run --with numpy python tools/paintview.py grid work/p.npz … --box 300,700,5 --rows 280,520,6

Цвета здесь условные и подобраны так, чтобы границы были видны: чёрный филамент
рисуется тёмно-синим, иначе на нём не разобрать рельеф.
"""
import argparse, sys, struct, zlib
import numpy as np

PAL = {1: (0.19, 0.19, 0.23), 2: (1.00, 0.88, 0.76), 3: (0.97, 0.97, 0.97),
       4: (1.00, 0.78, 0.00), 5: (0.62, 0.11, 0.15), 6: (0.10, 0.55, 0.85),
       7: (0.20, 0.65, 0.25), -1: (1.00, 0.00, 0.85)}      # -1 — дроблёные, ярко
NAME = {-1: 'дроб', 1: 'чёрн', 2: 'тело', 3: 'бел ', 4: 'зол ', 5: 'красн', 6: 'син', 7: 'зел'}


def palette(npz):
    """Палитра проекта, если paint.py её сохранил, иначе условная PAL выше.

    Раньше палитра была всегда условной, и на чужом проекте цвета оказывались
    переставлены: у Dutch четвёртый филамент телесный (#FFE0C1), а PAL красила
    его золотым. Подмена видна по превью.

    Совсем чёрный поднимаем до различимой яркости: на #010102 рельеф не читается
    вовсе, а превью нужно именно для того, чтобы рельеф было видно."""
    raw = npz['fcol'] if 'fcol' in npz.files else []
    if len(raw) == 0:
        return dict(PAL)
    pal = {-1: PAL[-1]}
    for i, h in enumerate(raw, start=1):
        h = str(h).lstrip('#')[:6]
        try:
            rgb = tuple(int(h[k:k + 2], 16) / 255 for k in (0, 2, 4))
        except ValueError:
            rgb = (0.5, 0.5, 0.5)
        if max(rgb) < 0.22:                      # чёрный -> тёмно-синий, как было
            rgb = (0.19, 0.19, 0.23)
        pal[i] = rgb
    return pal


def look_at(eye, target, up=(0, 0, 1)):
    f = np.array(target, float) - np.array(eye, float); f /= np.linalg.norm(f)
    u = np.array(up, float); s = np.cross(f, u); s /= np.linalg.norm(s); u = np.cross(s, f)
    return np.stack([s, u, -f]), np.array(eye, float)


def save_png(path, arr):
    H, W, _ = arr.shape
    raw = b''.join(b'\x00' + arr[y].tobytes() for y in range(H))
    ch = lambda t, d: struct.pack('>I', len(d)) + t + d + struct.pack('>I', zlib.crc32(t + d))
    open(path, 'wb').write(b'\x89PNG\r\n\x1a\n'
                           + ch(b'IHDR', struct.pack('>IIBBBBB', W, H, 8, 2, 0, 0, 0))
                           + ch(b'IDAT', zlib.compress(raw, 6)) + ch(b'IEND', b''))


def render(V, F, lab, eye, target, fov, W, H, bg=(0.88, 0.88, 0.90), pal=None):
    pal = pal or PAL
    R, e = look_at(eye, target)
    P = (V - e) @ R.T
    P = P[F]
    nrm = np.cross(P[:, 1] - P[:, 0], P[:, 2] - P[:, 0])
    ln = np.linalg.norm(nrm, axis=1); ln[ln == 0] = 1; nrm /= ln[:, None]
    keep = ((-P[:, :, 2]) > 1e-3).all(1) & (nrm[:, 2] > 0)     # спереди и лицом к нам
    P, nrm, lb = P[keep], nrm[keep], lab[keep]
    fpx = (W / 2) / np.tan(np.radians(fov) / 2)
    sx = P[:, :, 0] * fpx / (-P[:, :, 2]) + W / 2
    sy = -P[:, :, 1] * fpx / (-P[:, :, 2]) + H / 2
    zc = -P[:, :, 2]
    ext = np.maximum(sx.max(1) - sx.min(1), sy.max(1) - sy.min(1))
    on = (sx.max(1) >= 0) & (sx.min(1) < W) & (sy.max(1) >= 0) & (sy.min(1) < H)
    sx, sy, zc, nrm, lb, ext = sx[on], sy[on], zc[on], nrm[on], lb[on], ext[on]

    L = np.array([-0.4, 0.35, 0.85]); L /= np.linalg.norm(L)
    sh = 0.32 + 0.68 * np.clip(nrm @ L, 0, 1)
    col = np.array([pal.get(int(l), (0.5, 0.5, 0.5)) for l in lb]) * sh[:, None]

    zbuf = np.full(W * H, np.inf); img = np.tile(np.array(bg), (W * H, 1))
    # плотность выборки — по габариту на экране: тонкие треугольники не должны
    # проваливаться между точками, иначе фон просвечивает и выглядит как крап
    for lo, hi, k in [(0, 3, 6), (3, 8, 16), (8, 20, 40), (20, 60, 120),
                      (60, 200, 400), (200, 1e9, 1200)]:
        g = np.flatnonzero((ext >= lo) & (ext < hi))
        if not len(g): continue
        u = np.linspace(0, 1, k + 1); U, Vv = np.meshgrid(u, u)
        m = (U + Vv) <= 1.0; U, Vv = U[m], Vv[m]; Wt = 1 - U - Vv
        px = (sx[g][:, 0:1] * Wt + sx[g][:, 1:2] * U + sx[g][:, 2:3] * Vv).ravel()
        py = (sy[g][:, 0:1] * Wt + sy[g][:, 1:2] * U + sy[g][:, 2:3] * Vv).ravel()
        pz = (zc[g][:, 0:1] * Wt + zc[g][:, 1:2] * U + zc[g][:, 2:3] * Vv).ravel()
        fi = np.repeat(g, len(U))
        ix = np.floor(px).astype(np.int64); iy = np.floor(py).astype(np.int64)
        ok = (ix >= 0) & (ix < W) & (iy >= 0) & (iy < H)
        ix, iy, pz, fi = ix[ok], iy[ok], pz[ok], fi[ok]
        pix = iy * W + ix
        o = np.lexsort((pz, pix)); pix, pz, fi = pix[o], pz[o], fi[o]
        first = np.ones(len(pix), bool); first[1:] = pix[1:] != pix[:-1]
        pix, pz, fi = pix[first], pz[first], fi[first]
        win = pz < zbuf[pix]
        zbuf[pix[win]] = pz[win]; img[pix[win]] = col[fi[win]]
    return (np.clip(img, 0, 1).reshape(H, W, 3) * 255).astype(np.uint8)


def pick(V, F, eye, target, fov, W, H, px, py):
    R, e = look_at(eye, target)
    f = (W / 2) / np.tan(np.radians(fov) / 2)
    dc = np.array([px - W / 2, -(py - H / 2), -f]); dc /= np.linalg.norm(dc)
    d = R.T @ dc
    P = V[F]; o = np.array(e, float)
    e1, e2 = P[:, 1] - P[:, 0], P[:, 2] - P[:, 0]
    h = np.cross(d, e2); a = (e1 * h).sum(1)
    ok = np.abs(a) > 1e-12
    inv = np.zeros(len(a)); inv[ok] = 1.0 / a[ok]
    s = o - P[:, 0]
    u = inv * (s * h).sum(1)
    q = np.cross(s, e1)
    v = inv * (d * q).sum(1)
    t = inv * (e2 * q).sum(1)
    hit = ok & (u >= 0) & (v >= 0) & (u + v <= 1) & (t > 1e-6)
    if not hit.any(): return None
    i = np.flatnonzero(hit)[np.argmin(t[hit])]
    return int(i), o + t[i] * d


def triple(s): return tuple(float(x) for x in s.split(','))
def size(s): return tuple(int(x) for x in s.lower().split('x'))


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='cmd', required=True)
    for name in ('render', 'pick', 'grid'):
        q = sub.add_parser(name)
        q.add_argument('npz')
        if name == 'render': q.add_argument('out')
        q.add_argument('--eye', type=triple, required=True)
        q.add_argument('--target', type=triple, required=True)
        q.add_argument('--fov', type=float, default=30)
        q.add_argument('--size', type=size, default=(1100, 900))
        if name == 'pick': q.add_argument('--px', type=triple, nargs='+', required=True)
        if name == 'grid':
            q.add_argument('--box', type=triple, required=True, help='x0,x1,кол-во колонок')
            q.add_argument('--rows', type=triple, required=True, help='y0,y1,кол-во строк')
            q.add_argument('--show', default='yz', help='какие две координаты печатать')
    a = p.parse_args()
    d = np.load(a.npz, allow_pickle=True)
    V, F, lab = d['V'], d['F'], d['lab'].astype(int)
    W, H = a.size
    if a.cmd == 'render':
        save_png(a.out, render(V, F, lab, a.eye, a.target, a.fov, W, H, pal=palette(d)))
        print(a.out)
        return
    if a.cmd == 'pick':
        for px, py in a.px:
            r = pick(V, F, a.eye, a.target, a.fov, W, H, px, py)
            if r is None: print(f'  ({px:.0f},{py:.0f}) мимо модели'); continue
            i, q = r
            print(f'  ({px:.0f},{py:.0f}) грань {i}  {NAME.get(lab[i], lab[i])}  xyz {q.round(2)}')
        return
    I = {'x': 0, 'y': 1, 'z': 2}
    x0, x1, nx = a.box; y0, y1, ny = a.rows
    for py in np.linspace(y0, y1, int(ny)):
        row = []
        for px in np.linspace(x0, x1, int(nx)):
            r = pick(V, F, a.eye, a.target, a.fov, W, H, px, py)
            row.append('   ---     ' if r is None else
                       f'{NAME.get(lab[r[0]], lab[r[0]]):5s}{r[1][I[a.show[0]]]:5.1f}/{r[1][I[a.show[1]]]:5.1f}')
        print(f'{py:4.0f}|' + '|'.join(row))


if __name__ == '__main__':
    main()
