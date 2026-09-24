#!/usr/bin/env python3
"""Перенести покраску со старой сетки на новую по геометрии.

Недостающее звено между ремонтом и покраской. Ремонт строит треугольники
заново, а `paint_color` привязан к номеру треугольника в списке — после
починки привязка бессмысленна. Здесь цвет берётся не по номеру, а по месту:
для каждой новой грани ищется ближайшая точка на старой поверхности.

Выравнивание границ и запись делает tools/paint.py, а не этот файл.

    UV="uv run --quiet --with numpy --with scipy --with PyMaxflow python"

    $UV tools/paint.py parse  оригинал.3mf    work/old.npz
    $UV tools/paint.py parse  починенный.3mf  work/new.npz
    $UV tools/paint_transfer.py work/old.npz work/new.npz work/moved.npz
    $UV tools/paint.py smooth work/moved.npz work/smooth.npz --band 4 --lam 1.0 --core 3
    $UV tools/paint.py write  починенный.3mf work/smooth.npz готовый.3mf

Про ручную кисть. Составной код (`1C01C1C3`) — это дерево разбиения ИМЕННО
того треугольника, на котором он стоит: его вершины, его порядок обхода.
На другую грань он не переносится ни при каких условиях, декодируй его или
нет. Поэтому дроблёные грани исключаются из источника, и приёмник берёт цвет
у ближайшей одноцветной. Подтреугольная точность ручной кисти теряется — она
и не могла пережить перестройку сетки. Выравниванием потом граница садится
на геометрическое ребро, что для сопла 0.4 и есть предел.
"""
import sys, argparse
import numpy as np

SPLIT = -1


def point_tri_dist2(P, A, B, C):
    """Квадрат расстояния от точек P до треугольников (A,B,C), по строкам.

    Честное расстояние до треугольника, а не до его центра: иначе на крупных
    гранях (механика, плоские панели) цвет уезжает на соседнюю деталь.
    Алгоритм Эриксона: разбор по областям Вороного вершин, рёбер и плоскости.
    """
    AB, AC, AP = B - A, C - A, P - A
    d1 = np.einsum('ij,ij->i', AB, AP)
    d2 = np.einsum('ij,ij->i', AC, AP)
    BP = P - B
    d3 = np.einsum('ij,ij->i', AB, BP)
    d4 = np.einsum('ij,ij->i', AC, BP)
    CP = P - C
    d5 = np.einsum('ij,ij->i', AB, CP)
    d6 = np.einsum('ij,ij->i', AC, CP)

    vc = d1 * d4 - d3 * d2
    vb = d5 * d2 - d1 * d6
    va = d3 * d6 - d5 * d4
    denom = va + vb + vc
    denom = np.where(np.abs(denom) < 1e-30, 1e-30, denom)

    Q = A + AB * (vb / denom)[:, None] + AC * (vc / denom)[:, None]   # внутри
    m = (d1 <= 0) & (d2 <= 0);                       Q[m] = A[m]      # вершина A
    m = (d3 >= 0) & (d4 <= d3);                      Q[m] = B[m]      # вершина B
    m = (d6 >= 0) & (d5 <= d6);                      Q[m] = C[m]      # вершина C
    m = (vc <= 0) & (d1 >= 0) & (d3 <= 0)                             # ребро AB
    t = np.zeros(len(P)); dd = d1 - d3; dd[dd == 0] = 1e-30
    t[m] = (d1 / dd)[m];                             Q[m] = (A + AB * t[:, None])[m]
    m = (vb <= 0) & (d2 >= 0) & (d6 <= 0)                             # ребро AC
    t = np.zeros(len(P)); dd = d2 - d6; dd[dd == 0] = 1e-30
    t[m] = (d2 / dd)[m];                             Q[m] = (A + AC * t[:, None])[m]
    m = (va <= 0) & ((d4 - d3) >= 0) & ((d5 - d6) >= 0)               # ребро BC
    t = np.zeros(len(P)); dd = (d4 - d3) + (d5 - d6); dd[dd == 0] = 1e-30
    t[m] = ((d4 - d3) / dd)[m];                      Q[m] = (B + (C - B) * t[:, None])[m]

    D = P - Q
    return np.einsum('ij,ij->i', D, D)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('src', help='npz старой сетки с покраской (paint.py parse)')
    ap.add_argument('dst', help='npz новой сетки — покраска в ней игнорируется')
    ap.add_argument('out', help='куда положить npz новой сетки с перенесённым цветом')
    ap.add_argument('--k', type=int, default=8,
                    help='сколько ближайших граней-кандидатов проверять точно (8)')
    ap.add_argument('--chunk', type=int, default=200_000, help='размер порции (200000)')
    a = ap.parse_args()

    try:
        from scipy.spatial import cKDTree
    except ImportError:
        sys.exit('нужен scipy: uv run --with numpy --with scipy python tools/paint_transfer.py …')

    S = np.load(a.src, allow_pickle=True)
    D = np.load(a.dst, allow_pickle=True)
    sV, sF, slab, sarea = S['V'], S['F'], S['lab'].astype(np.int32), S['area']
    dV, dF, darea = D['V'], D['F'], D['area']

    solid = slab != SPLIT
    n_split = int((~solid).sum())
    if not solid.any():
        sys.exit('в источнике нет ни одной одноцветной грани — переносить нечего')
    sF, slab = sF[solid], slab[solid]
    A, B, C = sV[sF[:, 0]], sV[sF[:, 1]], sV[sF[:, 2]]
    cent = (A + B + C) / 3.0

    print(f'источник: {len(slab)} одноцветных граней'
          + (f', {n_split} дроблёных исключено из источника' if n_split else ''))
    print(f'приёмник: {len(dF)} граней')

    tree = cKDTree(cent)
    dc = dV[dF].mean(1)
    k = max(1, min(a.k, len(slab)))
    lab = np.empty(len(dF), dtype=np.int32)
    dist = np.empty(len(dF))

    for lo in range(0, len(dF), a.chunk):
        hi = min(lo + a.chunk, len(dF))
        P = dc[lo:hi]
        _, idx = tree.query(P, k=k, workers=-1)
        idx = idx.reshape(len(P), k)
        best = np.full(len(P), np.inf)
        pick = np.zeros(len(P), dtype=np.int64)
        for j in range(k):
            cand = idx[:, j]
            d2 = point_tri_dist2(P, A[cand], B[cand], C[cand])
            better = d2 < best
            best[better] = d2[better]
            pick[better] = cand[better]
        lab[lo:hi] = slab[pick]
        dist[lo:hi] = np.sqrt(best)
        print(f'  {hi}/{len(dF)}', end='\r', flush=True)
    print(' ' * 40, end='\r')

    q = np.percentile(dist, [50, 95, 99, 100])
    print(f'расстояние до старой поверхности: медиана {q[0]:.4f}, 95% {q[1]:.4f}, '
          f'99% {q[2]:.4f}, максимум {q[3]:.4f} мм')
    if q[3] > 1.0:
        far = int((dist > 1.0).sum())
        print(f'  !! {far} граней дальше 1 мм от старой сетки — там цвет угадан, а не перенесён.'
              '\n     Обычно это места, которых в оригинале не было: заросшие дырки, сросшиеся щели.')

    print('\nплощади по филаментам, мм2:')
    fils = sorted(set(slab.tolist()) | set(lab.tolist()))
    print(f'  {"филамент":<10}{"было":>12}{"стало":>12}{"разница":>10}')
    for f in fils:
        was = float(sarea[solid][slab == f].sum())
        now = float(darea[lab == f].sum())
        d = (now - was) / was * 100 if was else float('nan')
        print(f'  {f:<10}{was:>12.1f}{now:>12.1f}{d:>9.1f}%')

    d2 = {kk: D[kk] for kk in D.files}
    d2['lab'] = lab.astype(np.int8)
    np.savez_compressed(a.out, **d2)
    print(f'\n{a.out}')
    print('дальше — выравнивание и запись готовыми инструментами:')
    print('  tools/paint.py smooth … --band 4 --lam 1.0 --core 3   потом   tools/paint.py write')


if __name__ == '__main__':
    main()
