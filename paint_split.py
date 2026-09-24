#!/usr/bin/env python3
"""Разрезать покрашенный 3MF на отдельные детали по филаментам.

Смысл: вместо смен филамента внутри одной печати печатать каждый цвет
отдельной деталью и склеивать. Экономит промывку и башню очистки, но взамен
даёт шов по каждой границе цвета — поэтому сначала `plan`, и только потом
`cut`.

Режется не объём, а поверхность: куски одного цвета вырезаются из сетки и
закрываются крышкой — веером на общую вершину внутри тела. Наружная
поверхность при этом не трогается вовсе, деталь получается точной снаружи
и плоской по разрезу. Стенки у детали нет: это замкнутая скорлупа, и
слайсер печатает её сплошной, как обычное тело.

    uv run --quiet --with numpy --with scipy python tools/paint_split.py \\
        plan work/p.npz --min-area 20
    uv run … python tools/paint_split.py joints work/p.npz --emit work/j.json
    uv run … python tools/paint_split.py cut work/p.npz work/parts --min-area 20

`plan` печатает по каждому филаменту число кусков, площадь, крупнейший кусок,
непечатаемую мелочь и длину шва — то есть цену резки в миллиметрах склейки.

`cut` пишет STL по филаменту и проверяет каждую деталь: замкнутость, объём,
не вылезла ли крышка наружу и сходится ли сумма объёмов с исходной. Печатает
ещё и среднюю толщину детали — по ней сразу видно, возможен ли на ней стык
с нишей: ниша живёт в теле, а у цвета, размазанного пятном по поверхности,
тела нет.

`joints` меряет каждый шов и называет те, что годятся в настоящий стык:
петля почти лежит в плоскости, в сечение влезает штифт со стенкой, и по обе
стороны хватает глубины. С ключом `--emit` пишет готовое задание для
`tools/pivot_joint.py` — тот сверлит парные слепые ниши и печатает штифты
отдельными телами.

`--plane-cut auto` переводит швы со стыком с контура цвета на плоскость:
грани, которые плоскость пересекает, ею же и разрезаются. Без этого
поверхность стыка повторяет нарисованную границу цвета и плоской не бывает,
а две волнистые крышки, напечатанные слоями, друг к другу не прилегают.
Ключ обязан быть одинаковый у `joints` и у `cut`: он меняет сетку.

**Зачем это нужно.** Крышка на крышку не держится ничем: печать идёт слоями,
у каждой крышки своя ступенчатая поверхность, и поймать взаимное положение
деталей руками нечем. Ориентир — Funko Spider-Man с MakerWorld, разобранный
21.09.2026: там у каждого стыка плоский рез, слепая ниша в обеих половинах
и штифт отдельным телом, тоньше ниши на 0.2 мм и короче суммы глубин на
миллиметр. Подробности, допуски и замеры — в скилле **3mf-paint**,
`references/split-to-parts.md`.

**Где стык неточен.** Крышка считается один раз на петлю и достаётся обеим
соседним деталям, поэтому вдоль границы двух цветов они сходятся грань
в грань. Но там, где сходятся три цвета, петли у соседей разные: у одной
детали граница идёт вокруг обоих соседей сразу, у других — только по своему
участку. Крышки в таком месте расходятся, и детали либо чуть налезают друг
на друга, либо оставляют внутреннюю пустоту. Мера этого — строка «сумма
деталей против исходных» в конце: на фигурке Робби расхождение 0.6 %,
на Датче со стулом 1.8 %. На модели с частыми тройными точками резка
по цвету не годится.

Масштаб <build> берётся ключом --scale, как в paint_despeckle.py.
"""
import argparse, os, struct, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hardware
import numpy as np
import scipy.sparse as sp
from scipy.sparse.csgraph import connected_components

SPLIT = -1                      # грань, покрашенная кистью в несколько цветов


def load(path, scale):
    d = np.load(path, allow_pickle=True)
    V = d['V'] * scale
    F = d['F'].astype(np.int64)
    lab = d['lab'].astype(np.int32)
    P = V[F]
    area = 0.5 * np.linalg.norm(np.cross(P[:, 1] - P[:, 0], P[:, 2] - P[:, 0]), axis=1)
    ea, eb = d['ea'], d['eb']
    elen = d['elen'] * scale
    fcol = list(d['fcol']) if 'fcol' in d else []
    return V, F, lab, ea, eb, elen, area, fcol


def absorb(lab, ea, eb, elen, area, bad_of, rounds=12):
    """Отдать каждый негодный кусок соседу с самой длинной общей границей.

    Тем же приёмом, что и paint_despeckle: у мелочи нет своего цвета, она
    прилипает к тому, с кем граничит длиннее всего. Перекрашивается кусок
    целиком, а не его кромка, иначе внутри остаётся ядро прежнего цвета.

    bad_of(lab, cc, A, clab) -> булев массив по кускам."""
    lab = lab.copy()
    n = len(lab)
    for _ in range(rounds):
        nc, cc = patches(lab, ea, eb, n)
        A = np.bincount(cc, weights=area, minlength=nc)
        clab = np.zeros(nc, np.int32); clab[cc] = lab
        bad = bad_of(lab, cc, A, clab)
        if not bad.any():
            break
        bnd = cc[ea] != cc[eb]
        src = np.r_[cc[ea[bnd]], cc[eb[bnd]]]
        dst = np.r_[cc[eb[bnd]], cc[ea[bnd]]]
        ln = np.r_[elen[bnd], elen[bnd]]
        # Сосед годится, если он сам не мелочь либо крупнее — иначе две мелкие
        # соседки бесконечно меняются цветом местами.
        rank = A * nc + np.arange(nc)
        keep = (bad[src] & (clab[dst] > 0) & (clab[dst] != clab[src])
                & (~bad[dst] | (rank[dst] > rank[src])))
        src, dst, ln = src[keep], dst[keep], ln[keep]
        if not len(src):
            break
        # победитель по суммарной длине общей границы
        o = np.lexsort((dst, src))
        src, dst, ln = src[o], dst[o], ln[o]
        grp = np.r_[True, (src[1:] != src[:-1]) | (dst[1:] != dst[:-1])]
        tot = np.bincount(np.cumsum(grp) - 1, weights=ln)
        gs, gd = src[grp], dst[grp]
        o2 = np.lexsort((-tot, gs))
        gs, gd = gs[o2], gd[o2]
        first = np.r_[True, gs[1:] != gs[:-1]]
        win = np.full(nc, -1, np.int32)
        win[gs[first]] = clab[gd[first]]
        take = np.flatnonzero(bad & (win >= 0))
        if not len(take):
            break
        m = np.isin(cc, take)
        lab[m] = win[cc[m]]
    return lab


def patches(lab, ea, eb, n):
    """Связные куски одного цвета: номер куска для каждой грани."""
    same = lab[ea] == lab[eb]
    g = sp.coo_matrix((np.ones(int(same.sum())), (ea[same], eb[same])), shape=(n, n))
    nc, cc = connected_components(g, directed=False)
    return nc, cc


def shells(ea, eb, n):
    """Связные скорлупы сетки безотносительно цвета: отдельные тела."""
    g = sp.coo_matrix((np.ones(len(ea)), (ea, eb)), shape=(n, n))
    return connected_components(g, directed=False)


def drop_faces(keep, F, lab, ea, eb, elen, area):
    """Выбросить грани и перенумеровать смежность."""
    idx = np.full(len(F), -1, np.int64)
    idx[keep] = np.arange(int(keep.sum()))
    e = keep[ea] & keep[eb]
    return (F[keep], lab[keep], idx[ea[e]], idx[eb[e]], elen[e], area[keep])


def coarsen(lab, ea, eb, elen, area, min_area):
    """Съесть куски мельче порога — укрупнить зоны до печатаемых."""
    return absorb(lab, ea, eb, elen, area, lambda l, cc, A, cl: A < min_area)


def loops_of(F, sel):
    """Граничные петли куска sel: списки вершин в порядке обхода.

    Полуребро (a→b) грани куска граничное, если обратного (b→a) в куске нет.
    Петли сшиваются по концу: из вершины b выходит следующее граничное
    полуребро. На вырожденной сетке вершина может иметь несколько выходов —
    берётся любой неиспользованный, петля всё равно замыкается."""
    f = F[sel]
    he = np.concatenate([f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]]])
    own = np.tile(sel, 3)                       # чья это грань
    key = he[:, 0].astype(np.int64) * (1 << 32) + he[:, 1]
    rev = he[:, 1].astype(np.int64) * (1 << 32) + he[:, 0]
    inner = np.isin(rev, key)
    bnd, bown = he[~inner], own[~inner]
    if not len(bnd):
        return []
    # индекс выходов из вершины
    o = np.argsort(bnd[:, 0], kind='stable')
    bs, bo = bnd[o], bown[o]
    starts = {}
    for i, v in enumerate(bs[:, 0]):
        starts.setdefault(int(v), []).append(i)
    used = np.zeros(len(bs), bool)
    out = []
    for i in range(len(bs)):
        if used[i]:
            continue
        loop, own_f = [], []
        j = i
        while not used[j]:
            used[j] = True
            loop.append(int(bs[j, 0]))
            own_f.append(int(bo[j]))
            nxt = starts.get(int(bs[j, 1]), [])
            nxt = [k for k in nxt if not used[k]]
            if not nxt:
                break
            j = nxt[0]
        if len(loop) >= 3:
            out.append((loop, own_f))
    return out


def patch_axis(V, F, sel, area, nrm):
    """Ось вставки, «нет ли поднутрений» и эффективная ширина куска.

    Ось — средняя нормаль куска по площади. Поднутрений нет, если ни одна
    грань не отвернулась от оси: тогда кусок виден целиком с одной стороны
    и вынимается призмой. Ширина `2S/L` — та же мерка, что у крапа
    (для полосы шириной w площадь S = w·l, периметр L ≈ 2·l): ею отличают
    воротник, который станет вставкой, от цепочки, которая не станет."""
    n = (nrm[sel] * area[sel, None]).sum(0)
    ln = np.linalg.norm(n)
    if ln < 1e-12:
        return None, -1.0, 0.0
    d = n / ln
    c = nrm[sel] @ d
    A = area[sel]
    # Доля площади, отвернувшейся от оси. Минимум по граням для этого не
    # годится: одна шумная чешуйка после ремонта сетки обнуляет вердикт —
    # у Робби кусок с +0.27 стал −0.97, не поменяв формы. Призме мешает
    # не единичный треугольник, а заметная площадь поднутрения.
    back = float(A[c <= 0].sum() / A.sum())
    return d, back, float(A.sum())


def loop_frame(L, d):
    """Базис плоскости, перпендикулярной оси, и петля в нём."""
    u = np.cross(d, [0, 0, 1.0])
    if np.linalg.norm(u) < 1e-6:
        u = np.cross(d, [0, 1.0, 0])
    u /= np.linalg.norm(u)
    w = np.cross(d, u)
    return u, w, np.c_[L @ u, L @ w]


def inward(P):
    """Направление внутрь контура в каждой его вершине (2D).

    По нормалям двух соседних рёбер, знак — по знаку площади контура:
    считать «к центру тяжести» нельзя, у вогнутой формы это уводит наружу."""
    A, B = P, np.roll(P, -1, axis=0)
    e = B - A
    ln = np.linalg.norm(e, axis=1); ln[ln == 0] = 1
    e = e / ln[:, None]
    nrm = np.c_[-e[:, 1], e[:, 0]]                 # нормаль ребра слева
    s = np.sign(np.sum(A[:, 0] * B[:, 1] - B[:, 0] * A[:, 1]))
    nrm *= (s if s else 1.0)
    v = nrm + np.roll(nrm, 1, axis=0)
    ln = np.linalg.norm(v, axis=1); ln[ln == 0] = 1
    return v / ln[:, None]


def prism_verts(V, loop, d, q0, lip=0.4, shrink=0.0):
    """Вершины призматической крышки: поясок под кромкой и дно.

    Так сделаны глаза у паука: снаружи исходная поверхность, внутрь уходит
    строго призматический бок и плоское дно. Деталь становится пробкой,
    соседняя получает точный негатив, и позиционировать вставку руками
    не надо — её держит стенка кармана. Веер на точку внутри тела, которым
    петля закрывалась раньше, такой опоры не даёт: поверхность стыка
    повторяет нарисованную границу цвета и никуда не вставляется.

    `shrink` — зазор посадки, и он **только у пробки**: карман строится
    тем же кодом с нулём. Первые `lip` миллиметров стенка идёт точно
    по кромке, и только ниже уходит внутрь: на видимом стыке щели нет,
    а глубже пробка входит свободно."""
    L = V[loop]
    u, w, P = loop_frame(L, d)
    ins = inward(P)
    t = L @ d
    off = (ins * shrink) if shrink else np.zeros_like(ins)
    r1 = L - d[None, :] * np.minimum(lip, (t - q0) * 0.5)[:, None]
    r1 = r1 + off[:, 0:1] * u[None, :] + off[:, 1:2] * w[None, :]
    r2 = L + off[:, 0:1] * u[None, :] + off[:, 1:2] * w[None, :]
    r2 = r2 - d[None, :] * ((r2 @ d) - (q0 + shrink))[:, None]
    cen = inscribed(r2, r2.mean(0), d)[0]
    return np.concatenate([r1, r2, cen[None, :]])


def prism_tris(loop, base):
    """Грани призматической крышки: два яруса стенки и веер по плоскому дну."""
    k = len(loop)
    A = np.arange(k); B = (A + 1) % k
    i0 = np.array(loop, np.int64)
    i1 = base + A
    i2 = base + k + A
    ci = base + 2 * k
    tri = []
    for a, b in ((i0, i1), (i1, i2)):
        tri.append(np.stack([b[B], a[B], a[A]], 1))
        tri.append(np.stack([b[B], a[A], b[A]], 1))
    tri.append(np.stack([i2[B], i2[A], np.full(k, ci)], 1))
    return np.concatenate(tri)


def build_part(V, F, sel, nrm=None, probe=None, cache=None,
               prism=None, mylab=None, fit=0.12):
    """Скорлупа куска плюс крышки по петлям. Возвращает вершины и грани.

    Вершина веера — середина петли, утопленная внутрь тела вдоль средней
    нормали кромки. Без утопления она у трети петель оказывается снаружи:
    у петли, обходящей вогнутое место или тонкий выступ, середина хорды
    лежит в воздухе, и крышка вылезает за поверхность.

    Вершина считается один раз на петлю и запоминается в cache: петля у двух
    соседних деталей одна и та же, и веер обязан получиться тот же самый,
    иначе детали перестают стыковаться."""
    faces = [F[sel]]
    Vx = [V]
    nv = len(V)
    caps = 0
    cen = []
    for loop, own in loops_of(F, sel):
        key = tuple(sorted(loop))
        pr = prism.get(key) if prism else None
        if pr is not None:
            d, q0, owner = pr
            sh = fit if owner == mylab else 0.0
            ck = (key, sh)
            if cache is not None and ck in cache:
                nvts = cache[ck]
            else:
                nvts = prism_verts(V, loop, d, q0, shrink=sh)
                if cache is not None:
                    cache[ck] = nvts
            Vx.append(nvts)
            faces.append(prism_tris(loop, nv))
            nv += len(nvts)
            caps += len(loop) * 5
            continue
        if cache is not None and key in cache:
            c = cache[key]
        else:
            c = V[loop].mean(0)
            if nrm is not None and probe is not None:
                n = nrm[own].mean(0)
                ln = np.linalg.norm(n)
                r = np.linalg.norm(V[loop] - c, axis=1).mean()
                if ln > 1e-9:
                    n = n / ln
                    for k in (0.0, 0.25, 0.5, 1.0, 1.5):
                        p = c - n * (k * r)
                        if probe(p[None, :])[0]:
                            c = p
                            break
            if cache is not None:
                cache[key] = c
        cen.append(c)
        Vx.append(c[None, :])
        ci = nv
        nv += 1
        L = np.array(loop, np.int64)
        # полуребро скорлупы идёт a→b, крышка обязана дать b→a
        tri = np.stack([np.roll(L, -1), L, np.full(len(L), ci)], axis=1)
        faces.append(tri)
        caps += len(L)
    return np.concatenate(Vx), np.concatenate(faces), caps, np.array(cen).reshape(-1, 3)


def inside(V, F, pts):
    """Лежит ли точка внутри тела: луч вверх, чётность пересечений.

    Нужно для крышек. Крышка — веер на центр граничной петли, и если петля
    вьётся по вогнутому месту, её центр оказывается снаружи тела: деталь
    тогда торчит наружу и налезает на соседнюю. Такую петлю веером не
    закрыть."""
    P = V[F]
    lo, hi = P.min(1), P.max(1)
    out = np.zeros(len(pts), bool)
    for i, p in enumerate(pts):
        m = ((lo[:, 0] <= p[0]) & (hi[:, 0] >= p[0]) &
             (lo[:, 1] <= p[1]) & (hi[:, 1] >= p[1]) & (hi[:, 2] >= p[2]))
        T = P[m]
        if not len(T):
            continue
        # барицентрические координаты точки в проекции на XY
        a, b, c = T[:, 0, :2], T[:, 1, :2], T[:, 2, :2]
        d = ((b[:, 1] - c[:, 1]) * (a[:, 0] - c[:, 0]) +
             (c[:, 0] - b[:, 0]) * (a[:, 1] - c[:, 1]))
        d[d == 0] = 1e-30
        u = ((b[:, 1] - c[:, 1]) * (p[0] - c[:, 0]) +
             (c[:, 0] - b[:, 0]) * (p[1] - c[:, 1])) / d
        v = ((c[:, 1] - a[:, 1]) * (p[0] - c[:, 0]) +
             (a[:, 0] - c[:, 0]) * (p[1] - c[:, 1])) / d
        w = 1 - u - v
        hit = (u >= 0) & (v >= 0) & (w >= 0)
        if not hit.any():
            continue
        z = (u[hit] * T[hit][:, 0, 2] + v[hit] * T[hit][:, 1, 2] + w[hit] * T[hit][:, 2, 2])
        out[i] = ((z > p[2]).sum() % 2) == 1
    return out


def check(V, F):
    """Замкнутость по рёбрам и объём со знаком."""
    E = np.concatenate([F[:, [0, 1]], F[:, [1, 2]], F[:, [2, 0]]])
    E = np.sort(E, axis=1)
    key = E[:, 0].astype(np.int64) * (1 << 32) + E[:, 1]
    _, cnt = np.unique(key, return_counts=True)
    P = V[F]
    vol = np.einsum('ij,ij->i', P[:, 0], np.cross(P[:, 1], P[:, 2])).sum() / 6.0
    return int((cnt == 1).sum()), int((cnt > 2).sum()), vol


def write_stl(path, V, F):
    P = V[F].astype(np.float32)
    nr = np.cross(P[:, 1] - P[:, 0], P[:, 2] - P[:, 0])
    ln = np.linalg.norm(nr, axis=1); ln[ln == 0] = 1
    nr = (nr / ln[:, None]).astype(np.float32)
    buf = np.zeros((len(F), 50), np.uint8)
    buf[:, 0:12] = nr.view(np.uint8).reshape(-1, 12)
    buf[:, 12:48] = P.reshape(len(F), 9).view(np.uint8).reshape(-1, 36)
    with open(path, 'wb') as fh:
        fh.write(b'\0' * 80)
        fh.write(struct.pack('<I', len(F)))
        fh.write(buf.tobytes())


def report(lab, ea, eb, elen, area, fcol, title, speck=1.0):
    """Кусков, площадь, крупнейший кусок, мелочь и длина шва по филаментам.

    Мелочь — куски мельче speck мм2: отдельной деталью такое не напечатать
    и не приклеить, значит либо укрупнять порогом, либо оставлять смену
    филамента."""
    n = len(lab)
    nc, cc = patches(lab, ea, eb, n)
    A = np.bincount(cc, weights=area, minlength=nc)
    clab = np.zeros(nc, np.int32); clab[cc] = lab
    bnd = lab[ea] != lab[eb]
    ln = elen[bnd]
    L = (np.bincount(cc[ea[bnd]], weights=ln, minlength=nc) +
         np.bincount(cc[eb[bnd]], weights=ln, minlength=nc))
    alive = A > 0
    print(f'\n{title}: шов {ln.sum():.0f} мм по {int(bnd.sum())} рёбрам, '
          f'кусков {int(alive.sum())}')
    print(f'{"фил":>4} {"цвет":>9} {"кусков":>7} {"площадь":>9} {"крупнейший":>11} '
          f'{"мелочи":>7} {"шов":>8}')
    for f in sorted({int(x) for x in lab if x > 0}):
        m = np.flatnonzero(alive & (clab == f))
        tiny = m[A[m] < speck]
        col = fcol[f - 1] if len(fcol) >= f else ''
        print(f'{f:>4} {col:>9} {len(m):>7} {A[m].sum():>8.0f}² {A[m].max():>10.0f}² '
              f'{len(tiny):>7} {L[m].sum()/2:>7.0f}мм')
    tiny = np.flatnonzero(alive & (A < speck))
    print(f'мельче {speck} мм2: {len(tiny)} кусков, {A[tiny].sum():.1f} мм2 '
          f'({100*A[tiny].sum()/A.sum():.2f} % поверхности)')
    return nc, cc, A


# ============================ швы как стыки деталей ============================
#
# Крышка по петле — ещё не стык. Две детали, сведённые крышка в крышку,
# держатся только клеем и ничем не выровнены: печать слоями даёт на каждой
# крышке свою ступенчатую поверхность, и поймать взаимное положение на глаз
# нечем. Ориентир — фигурка Spider-Man с MakerWorld (разобрана 21.09.2026):
# там каждый стык цветов сделан плоским резом поперёк конечности, в обеих
# половинах слепая ниша, а штифт печатается отдельным телом.
#
# Отсюда три вещи, которых у резки по цвету не было:
#   1. петли шва надо померить и назвать те, что уже почти плоские, —
#      именно они и есть стыки (запястье, щиколотка, шея);
#   2. шов подвинуть точно на плоскость, иначе крышка выходит волнистой;
#   3. в обе детали посадить нишу и напечатать штифт отдельно.


def twin_lookup(F):
    """Поиск грани с другой стороны ребра.

    Возвращает функцию (a, b, своя грань) → соседняя грань или -1. Нужна,
    чтобы у граничной петли куска узнать, с каким цветом она граничит:
    сама петля знает только свою сторону."""
    n = len(F)
    he = np.concatenate([F[:, [0, 1]], F[:, [1, 2]], F[:, [2, 0]]])
    lo = np.minimum(he[:, 0], he[:, 1]).astype(np.int64)
    hi = np.maximum(he[:, 0], he[:, 1]).astype(np.int64)
    key = lo * (1 << 32) + hi
    o = np.argsort(key, kind='stable')
    ks = key[o]

    def look(a, b, own):
        k = (min(a, b)) * (1 << 32) + max(a, b)
        i = np.searchsorted(ks, k)
        out = -1
        while i < len(ks) and ks[i] == k:
            f = int(o[i] % n)
            if f != own:
                out = f
            i += 1
        return out
    return look


def fit_plane(L):
    """Плоскость наименьших квадратов по вершинам петли: точка, нормаль, отклонения."""
    c = L.mean(0)
    n = np.linalg.svd(L - c, full_matrices=False)[2][2]
    dev = (L - c) @ n
    return c, n, dev


def inscribed(L, c, n, step=0.2):
    """Самый большой круг, влезающий в петлю: центр в 3D и радиус.

    Петля проецируется на свою плоскость, растрируется и берётся максимум
    расстояния до края. Центр тяжести не годится: у вогнутого сечения
    (щиколотка, кисть) он лежит близко к кромке или вовсе вне контура."""
    u = np.cross(n, [0, 0, 1.0])
    if np.linalg.norm(u) < 1e-6:
        u = np.cross(n, [0, 1.0, 0])
    u /= np.linalg.norm(u)
    v = np.cross(n, u)
    P = np.c_[(L - c) @ u, (L - c) @ v]
    lo, hi = P.min(0) - step, P.max(0) + step
    w = np.maximum(((hi - lo) / step).astype(int) + 3, 4)
    img = np.zeros((w[1], w[0]), bool)
    # заливка контура: чётность пересечений горизонтальным лучом
    ys = lo[1] + (np.arange(w[1]) + 0.5) * step
    xs = lo[0] + (np.arange(w[0]) + 0.5) * step
    A, B = P, np.roll(P, -1, axis=0)
    for j, y in enumerate(ys):
        m = (A[:, 1] > y) != (B[:, 1] > y)
        if not m.any():
            continue
        t = (y - A[m, 1]) / (B[m, 1] - A[m, 1])
        x = A[m, 0] + t * (B[m, 0] - A[m, 0])
        img[j] = (np.searchsorted(np.sort(x), xs) % 2) == 1
    from scipy.ndimage import distance_transform_edt
    d = distance_transform_edt(img) * step
    if not d.size or d.max() <= 0:
        return c, 0.0
    j, i = np.unravel_index(np.argmax(d), d.shape)
    p = c + u * xs[i] + v * ys[j]
    return p, float(d.max())


def ray_hits(P0, E1, E2, q, dirs):
    """Расстояние от точки q до первой грани по каждому из направлений dirs.

    Мёллер — Трумбор, векторизованный по граням. Грани подаются уже
    подготовленной окрестностью: гонять 2.6 млн треугольников на каждый луч
    незачем, стенка ищется в пределах сантиметра."""
    out = np.full(len(dirs), np.inf)
    S = q - P0
    for i, d in enumerate(dirs):
        h = np.cross(d, E2)
        a = np.einsum('ij,ij->i', E1, h)
        ok = np.abs(a) > 1e-12
        f = 1.0 / np.where(ok, a, 1.0)
        u = f * np.einsum('ij,ij->i', S, h)
        qq = np.cross(S, E1)
        v = f * (qq @ d)
        t = f * np.einsum('ij,ij->i', E2, qq)
        m = ok & (u >= 0) & (u <= 1) & (v >= 0) & (u + v <= 1) & (t > 1e-6)
        if m.any():
            out[i] = t[m].min()
    return out


def wall_profile(V, F, ftree, p, n, limit=20.0, step=0.25, rays=16, near=12.0):
    """Толщина стенки вокруг оси по мере ухода вглубь тела.

    Меряется лучами поперёк оси, а не расстоянием до ближайшей вершины:
    у сидящей фигуры рядом с запястьем проходит пола пальто, и ближайшая
    вершина оказывается на ней, а не на стенке рукава. Луч изнутри всегда
    упирается в свою стенку первым.

    Марш идёт от шва вглубь и обрывается сам, когда стенка кончилась, —
    поэтому проверять, не вышла ли ось наружу, отдельно не нужно.

    Чего этот замер НЕ видит: деталь ограничена не только сеткой, но и
    покраской, а тело под цветным пятном тянется дальше. У белой рубашки
    Датча ниша так просится на 5 мм вглубь плеча, которое уже чёрное.
    Ловить это здесь пробовал по ближайшей грани — замер шумный, на двух
    зеркальных плечах давал разные ответы. Ловится ниже по конвейеру,
    точным булевым в pivot_joint.py: там деталь уже собрана."""
    u = np.cross(n, [0, 0, 1.0])
    if np.linalg.norm(u) < 1e-6:
        u = np.cross(n, [0, 1.0, 0])
    u /= np.linalg.norm(u)
    w = np.cross(n, u)
    ang = np.arange(rays) * (2 * np.pi / rays)
    dirs = np.cos(ang)[:, None] * u + np.sin(ang)[:, None] * w
    ts, out = [], []
    t = 0.0
    while t <= limit:
        q = p + n * t
        idx = ftree.query_ball_point(q, near)
        if not idx:
            break
        f = F[np.asarray(idx, np.int64)]
        P0 = V[f[:, 0]]
        r = ray_hits(P0, V[f[:, 1]] - P0, V[f[:, 2]] - P0, q, dirs).min()
        ts.append(t); out.append(r)
        if r < 0.2:
            break
        t += step
    return np.array(ts), np.array(out)


def in_loop(V, o, q):
    """Лежит ли точка q внутри петли o, спроецированной на свою плоскость."""
    u = np.cross(o['n'], [0, 0, 1.0])
    if np.linalg.norm(u) < 1e-6:
        u = np.cross(o['n'], [0, 1.0, 0])
    u /= np.linalg.norm(u)
    w = np.cross(o['n'], u)
    L = V[o['loop']] - o['c']
    P = np.c_[L @ u, L @ w]
    x, y = float((q - o['c']) @ u), float((q - o['c']) @ w)
    A, B = P, np.roll(P, -1, axis=0)
    m = (A[:, 1] > y) != (B[:, 1] > y)
    if not m.any():
        return False
    t = (y - A[m, 1]) / (B[m, 1] - A[m, 1])
    return bool((A[m, 0] + t * (B[m, 0] - A[m, 0]) > x).sum() % 2)


def cap_limit(V, S, k, p, n, wall):
    """Докуда ось идёт, не протыкая крышку соседнего шва.

    Деталь ограничена не только наружной поверхностью: по каждому своему
    шву она закрыта крышкой, а крышки в исходной сетке нет, и лучами её
    не поймать. Зато крышка — это почти диск в плоскости своей петли,
    и пересечение оси с ним считается напрямую. Без этого предела ниша
    на плече уходила из жилета в пальто: жилет весь 0.5 см³, а ниша
    просилась на 5 мм вглубь."""
    lp = lm = np.inf
    for j, o in enumerate(S):
        if j == k:
            continue
        den = float(n @ o['n'])
        if abs(den) < 1e-6:
            continue
        t = float((o['c'] - p) @ o['n'] / den)
        q = p + n * t
        if np.linalg.norm(q - o['c']) > o['rad'] + 0.5 or not in_loop(V, o, q):
            continue
        if t > 0:
            lp = min(lp, t)
        elif t < 0:
            lm = min(lm, -t)
    return max(lp - wall, 0.0), max(lm - wall, 0.0)


def local_faces(V, F, ftree, p, n, near=8.0, span=20.0):
    """Грани вокруг отрезка оси — заготовка под лучи, чтобы не гонять всю сетку."""
    idx = set()
    for t in np.arange(-span, span, near * 0.8):
        idx.update(ftree.query_ball_point(p + n * t, near))
    if not idx:
        return None
    f = F[np.fromiter(idx, np.int64, len(idx))]
    P0 = V[f[:, 0]]
    return P0, V[f[:, 1]] - P0, V[f[:, 2]] - P0


def ahead(tri, p, n, r, k=8):
    """Докуда вдоль оси есть тело — по всему сечению ниши, а не по одной оси.

    Стенку вокруг ниши меряют лучи поперёк, а дно под ней не мерил никто:
    ниша упиралась в дальнюю поверхность детали и становилась сквозной.
    На Датче так выходило у пальто — от шва до наружной стороны 1.9 мм,
    а ниша просилась на 1.5.

    Лучей несколько: дно у ниши шириной в несколько миллиметров, и над её
    краем поверхность бывает ближе, чем над осью. Пальто с плоским резом
    дало по оси запас, а по краю сечения — дно на 77 %."""
    P0, E1, E2 = tri
    out = float(ray_hits(P0, E1, E2, p, n[None, :])[0])
    if r <= 0:
        return out
    u = np.cross(n, [0, 0, 1.0])
    if np.linalg.norm(u) < 1e-6:
        u = np.cross(n, [0, 1.0, 0])
    u /= np.linalg.norm(u)
    w = np.cross(n, u)
    for a in np.arange(k) * (2 * np.pi / k):
        q = p + r * (np.cos(a) * u + np.sin(a) * w)
        out = min(out, float(ray_hits(P0, E1, E2, q, n[None, :])[0]))
    return out


def pick_dowel(V, F, ftree, p, n, R, wall, caps=(np.inf, np.inf), rmax=4.0):
    """Подобрать радиус и глубину ниши: самый толстый штифт, который лезет.

    Сверху радиус ограничен вписанным в шов кругом R минус стенка; глубину
    даёт профиль стенки вдоль оси и расстояние до дальней поверхности.
    Ровно на R − wall брать нельзя: у самого шва стенка тогда равна wall
    впритык, и любая ямка поверхности в десятую миллиметра обнуляет
    глубину — отсюда ещё 0.15 мм запаса и шаг 0.25 мм.

    Глубина каждой половины — не меньше 1.5 мм и не меньше радиуса, иначе
    штифт не держит поворот; глубже 2.5 радиусов смысла нет: дальше работает
    клей, а длинная ниша в тонкой конечности только рвёт стенку."""
    tp, dp = wall_profile(V, F, ftree, p, n)
    tm, dm = wall_profile(V, F, ftree, p, -n)
    tri = local_faces(V, F, ftree, p, n)

    def reach(t, prof, r):
        """Докуда ниша радиуса r идёт, не подойдя к стенке ближе wall."""
        bad = np.flatnonzero(prof < r + wall)
        if not len(bad):
            return float(t[-1]) if len(t) else 0.0
        return float(t[bad[0] - 1]) if bad[0] else 0.0

    r = min(np.floor((R - wall - 0.15) / 0.25) * 0.25, rmax)
    while r >= 1.0:
        lim = ((min(caps[0], ahead(tri, p, n, r * 0.9) - wall),
                min(caps[1], ahead(tri, p, -n, r * 0.9) - wall))
               if tri else caps)
        d1 = min(reach(tp, dp, r), lim[0])
        d2 = min(reach(tm, dm, r), lim[1])
        if min(d1, d2) >= max(1.5, r):
            return r, min(d1, 2.5 * r), min(d2, 2.5 * r)
        r -= 0.25
    return None


def seam_loops(V, F, lab, cc, clab, area, look, min_len=5.0):
    """Все петли шва: кто с кем граничит, длина, плоскость, отклонение кромки."""
    out = []
    seen = set()
    for i in np.unique(cc):
        sel = np.flatnonzero(cc == i)
        if area[sel].sum() < 1.0:
            continue
        for loop, own in loops_of(F, sel):
            if len(loop) < 8:
                continue
            key = tuple(sorted(loop))
            if key in seen:
                continue
            seen.add(key)
            L = V[loop]
            per = np.linalg.norm(np.diff(np.r_[L, L[:1]], axis=0), axis=1).sum()
            if per < min_len:
                continue
            nb, opp = {}, []
            for k in range(len(loop)):
                f = look(loop[k], loop[(k + 1) % len(loop)], own[k])
                opp.append(f)
                if f >= 0:
                    e = float(np.linalg.norm(V[loop[(k + 1) % len(loop)]] - V[loop[k]]))
                    nb[int(cc[f])] = nb.get(int(cc[f]), 0.0) + e
            c, n, dev = fit_plane(L)
            rad = np.linalg.norm(L - L.mean(0), axis=1)
            out.append(dict(patch=int(i), fil=int(clab[i]), loop=loop,
                            own=own, opp=opp, nblen=nb, nb=sorted(nb),
                            per=float(per), dia=float(2 * rad.mean()),
                            rad=float(rad.max()), c=c, n=n,
                            rms=float(dev.std()), dmax=float(np.abs(dev).max())))
    return out


def rebuild(V, F):
    """Площади граней и смежность по рёбрам заново — после правки сетки."""
    P = V[F]
    area = 0.5 * np.linalg.norm(np.cross(P[:, 1] - P[:, 0], P[:, 2] - P[:, 0]), axis=1)
    n = len(F)
    he = np.concatenate([F[:, [0, 1]], F[:, [1, 2]], F[:, [2, 0]]])
    lo = np.minimum(he[:, 0], he[:, 1]).astype(np.int64)
    hi = np.maximum(he[:, 0], he[:, 1]).astype(np.int64)
    key = lo * (1 << 32) + hi
    o = np.argsort(key, kind='stable')
    pair = np.flatnonzero(key[o][1:] == key[o][:-1])
    i1, i2 = o[pair], o[pair + 1]
    ea, eb = (i1 % n).astype(np.int64), (i2 % n).astype(np.int64)
    elen = np.linalg.norm(V[he[i1, 0]] - V[he[i1, 1]], axis=1)
    return ea, eb, elen, area


def plane_cut_seam(V, F, lab, ea, eb, s, sa, la, lb, margin):
    """Перевести шов с контура цвета на плоскость, разрезав треугольники.

    Контур цвета рисовал человек или генератор, и плоским он быть не обязан:
    крышка, затягивающая такую петлю, выходит волнистой, а две волнистые
    крышки, напечатанные слоями, друг к другу не прилегают.

    Здесь шов переносится на плоскость по-честному: грани, которые плоскость
    пересекает, **разрезаются** ею, и каждый кусок получает цвет по свою
    сторону. Переносить грань целиком по её центру нельзя — пробовал
    21.09.2026 на Датче: граница начинает метаться на один треугольник
    туда-сюда, шов 905 → 1342 мм, красный жилет распадается с двух кусков
    на тридцать два.

    Правится только полоса вокруг самой петли, и только в двух цветах,
    которые на этом шве встречаются: та же плоскость, продлённая через
    фигуру, режет и туловище, и стул, а третий цвет, попавший в полосу,
    она бы просто съела.

    **Резать приходится шире, чем красить.** Ребро делится пополам новой
    вершиной, и грань по ту сторону ребра обязана узнать о ней, даже если
    сама она вне полосы и цвет ей не меняют: иначе на краю полосы остаётся
    висячая вершина, петля шва раздваивается и деталь перестаёт быть
    замкнутой. Поэтому цвет меняется внутри полосы, а делятся все грани,
    которых коснулся рез.

    Возвращает новые V, F, lab и число разрезанных граней."""
    c, n = s['c'], s['n']
    h = s['dmax'] + margin
    R = s['rad'] + margin
    c0 = V[s['loop']].mean(0)

    sv = (V - c) @ n                       # сторона каждой ВЕРШИНЫ
    sv = np.where(np.abs(sv) < 1e-9, 1e-9, sv)
    fs = sv[F]
    C = V[F].mean(1)
    cand = ((np.abs(fs).max(1) < h) &
            (np.linalg.norm(C - c0, axis=1) < R) &
            np.isin(lab, [la, lb]))
    # Затравка обхода — грани самой петли. Берутся по её ВЕРШИНАМ, а не по
    # номерам граней: после первого же реза нумерация граней меняется,
    # а вершины только дописываются в конец.
    isv = np.zeros(len(V), bool)
    isv[np.asarray(s['loop'], np.int64)] = True
    cur = isv[F].any(1) & cand
    m = cand[ea] & cand[eb]
    xa, xb = ea[m], eb[m]
    for _ in range(400):
        nxt = cur.copy()
        nxt[xa[cur[xb]]] = True
        nxt[xb[cur[xa]]] = True
        if (nxt == cur).all():
            break
        cur = nxt

    up, dn = (la, lb) if sa > 0 else (lb, la)

    # 1. рёбра, которые режет плоскость у граней полосы
    mid, Vx, nv = {}, [V], len(V)
    def edge_point(i, k):
        nonlocal nv
        key = (i, k) if i < k else (k, i)
        if key not in mid:
            t = sv[i] / (sv[i] - sv[k])
            Vx.append((V[i] + t * (V[k] - V[i]))[None, :])
            mid[key] = nv
            nv += 1
        return mid[key]

    for f in np.flatnonzero(cur & (fs > 0).any(1) & (fs < 0).any(1)):
        tri = F[f]
        for i in range(3):
            a, b = int(tri[i]), int(tri[(i + 1) % 3])
            if (sv[a] > 0) != (sv[b] > 0):
                edge_point(a, b)

    # 2. все грани, которых коснулся рез, плюс вся полоса
    touched = np.zeros(len(V), bool)
    for (a, b) in mid:
        touched[a] = touched[b] = True
    work = np.flatnonzero(cur | (touched[F].sum(1) >= 2))

    newF, newL, split = [], [], 0
    drop = np.zeros(len(F), bool)
    for f in work:
        tri = F[f]
        inband = bool(cur[f])
        cutedge = [mid.get((int(tri[i]), int(tri[(i + 1) % 3]))
                           if tri[i] < tri[(i + 1) % 3] else
                           (int(tri[(i + 1) % 3]), int(tri[i])))
                   for i in range(3)]
        k = sum(x is not None for x in cutedge)
        if k == 0:
            if not inband:
                continue                       # грань не трогаем вовсе
            drop[f] = True
            newF.append(tri)
            newL.append(up if fs[f][0] > 0 else dn)
            continue
        drop[f] = True
        split += 1
        def col(v_idx_list, fallback):
            """Цвет куска: по стороне плоскости, если мы в полосе."""
            if not inband:
                return fallback
            t = [sv[v] for v in v_idx_list if v < len(V)]
            return up if (max(t) if t else 0) > 0 else dn
        if k == 2:
            one = [i for i in range(3)
                   if cutedge[i] is not None and cutedge[(i + 2) % 3] is not None][0]
            a = int(tri[one]); b = int(tri[(one + 1) % 3]); cc_ = int(tri[(one + 2) % 3])
            pab, pca = cutedge[one], cutedge[(one + 2) % 3]
            lone = (up if sv[a] > 0 else dn) if inband else lab[f]
            loth = (dn if sv[a] > 0 else up) if inband else lab[f]
            newF += [[a, pab, pca], [pab, b, cc_], [pab, cc_, pca]]
            newL += [lone, loth, loth]
        else:                                   # k == 1: простое деление ребра
            i = [x for x in range(3) if cutedge[x] is not None][0]
            a = int(tri[i]); b = int(tri[(i + 1) % 3]); cc_ = int(tri[(i + 2) % 3])
            pab = cutedge[i]
            la_ = (up if sv[a] > 0 else dn) if inband else lab[f]
            lb_ = (up if sv[b] > 0 else dn) if inband else lab[f]
            newF += [[a, pab, cc_], [pab, b, cc_]]
            newL += [la_, lb_]
    F2 = np.concatenate([F[~drop], np.array(newF, np.int64).reshape(-1, 3)])
    L2 = np.concatenate([lab[~drop], np.array(newL, np.int8)])
    return np.concatenate(Vx), F2, L2, split


def flatten_seams(V, F, lab, ea, eb, elen, area, cc, clab, S, which, flat,
                  margin=0.6):
    """Перенести выбранные швы на их плоскости. Возвращает обновлённую сетку.

    Все задания готовятся до первого реза: после него нумерация граней
    меняется, а `c`, `n`, петля и цвета сторон — нет."""
    C = V[F].mean(1)
    jobs = []
    for k in which:
        if k >= len(S) or S[k]['rms'] > flat:
            continue
        s = S[k]
        side = (C - s['c']) @ s['n']
        c0 = V[s['loop']].mean(0)
        near = np.linalg.norm(C - c0, axis=1) < 2 * s['dia']
        sa = float(side[near & (cc == s['patch'])].mean() or 0.0)
        cand = [(l, j) for j, l in s['nblen'].items()
                if j != s['patch'] and near[cc == j].any()
                and np.sign(side[near & (cc == j)].mean()) != np.sign(sa)]
        if not cand:
            continue
        jobs.append((k, s, sa, int(clab[s['patch']]), int(clab[max(cand)[1]])))
    for k, s, sa, la, lb in jobs:
        V, F, lab, split = plane_cut_seam(V, F, lab, ea, eb, s, sa, la, lb,
                                          margin)
        ea, eb, elen, area = rebuild(V, F)
        print(f'шов {k} (фил {la} ↔ {lb}): плоскость, разрезано граней {split}, '
              f'кромка гуляла на {s["dmax"]:.2f} мм')
    return V, F, lab, ea, eb, elen, area


def report_inlay(V, F, lab, cc, clab, area, nrm, fcol, depth, minw, wall,
                 want=None):
    """Какие куски годятся во вставку, и назвать вслух те, что не годятся.

    Вставка — цветное пятно, которое уходит в соседнюю деталь пробкой
    с прямыми стенками и плоским дном. Для этого нужны три вещи:
    одна граничная петля (у пояса их две, и он режется иначе), отсутствие
    поднутрений вдоль оси и **ширина**. Ширина и есть тот случай, о котором
    надо говорить прямо: у белой цепочки на груди 2S/L меньше миллиметра,
    карман под неё будет тоньше стенки, и вставки не выйдет — там честнее
    оставить смену филамента, AMS её отработает.

    Возвращает словарь «петля → (ось, дно, чей это цвет)» для build_part."""
    out, rows = {}, []
    for i in np.unique(cc):
        sel = np.flatnonzero(cc == i)
        A = area[sel].sum()
        if A < 1.0:
            continue
        loops = loops_of(F, sel)
        d, back, _ = patch_axis(V, F, sel, area, nrm)
        plen = []
        for loop, _ in loops:
            L = V[loop]
            plen.append(float(np.linalg.norm(np.diff(np.r_[L, L[:1]], axis=0),
                                             axis=1).sum()))
        per = sum(plen)
        wid = 2 * A / per if per else 0.0
        why = ''
        # Островок чужого цвета внутри пятна — не повод отказать: он станет
        # дыркой и в пробке, и в кармане, а сам сядет в свой карман. Пробку
        # задаёт самая длинная петля, внутренние остаются как есть.
        if not loops:
            why = 'нет границы'
        elif d is None or back > 0.01:
            why = f'поднутрений {100*back:.0f} % площади — призмой не вынуть'
        elif wid < minw:
            why = f'узко: 2S/L = {wid:.2f} мм — оставить сменой филамента'
        rows.append((A, int(clab[i]), int(i), len(loops), wid, back, why))
        if why or (want is not None and int(i) not in want):
            continue
        loop = loops[int(np.argmax(plen))][0]
        q0 = float((V[np.unique(F[sel])] @ d).min()) - depth
        out[tuple(sorted(loop))] = (d, q0, int(clab[i]))
    rows.sort(reverse=True)
    print(f'\nвставки: пятно с одной петлёй, без поднутрений и шире {minw} мм')
    print(f'{"кусок":>6} {"фил":>3} {"площадь":>9} {"петель":>6} {"2S/L":>7} '
          f'{"назад":>7}  вердикт')
    for A, f, i, nl, wid, mc, why in rows[:24]:
        print(f'{i:>6} {f:>3} {A:>8.0f}² {nl:>6} {wid:>6.2f}мм {100*mc:>6.1f}%  '
              f'{why or f"ВСТАВКА, дно на {depth} мм" + (f", островков {nl-1}" if nl > 1 else "")}')
    return out


def report_joints(V, F, lab, cc, clab, area, fcol, flat, wall, args):
    """Список швов с вердиктом «плоский рез» и подобранным штифтом."""
    from scipy.spatial import cKDTree
    C = V[F].mean(1)
    ftree = cKDTree(C)
    S = seam_loops(V, F, lab, cc, clab, area, twin_lookup(F))
    S.sort(key=lambda s: -s['per'])
    print(f'\nшвов {len(S)}; плоским считается шов с отклонением кромки '
          f'от своей плоскости меньше {flat} мм')
    print(f'{"шов":>4} {"фил":>3} {"соседи":>8} {"длина":>8} {"Ø":>7} {"rms":>7} '
          f'{"макс":>7} {"вердикт":>12} {"штифт":>22}')
    jobs = []
    for k, s in enumerate(S):
        nbf = sorted({int(clab[j]) for j in s['nb'] if clab[j] != s['fil']})
        ok = s['rms'] <= flat
        dow = ''
        if ok:
            p, r = inscribed(V[s['loop']], s['c'], s['n'])
            side = (C - s['c']) @ s['n']
            c0 = V[s['loop']].mean(0)
            near = np.linalg.norm(C - c0, axis=1) < 2 * s['dia']

            # Знак нормали, посчитанной по петле, произволен, а ниша сверлится
            # от шва вглубь конкретной детали: без разбора сторон ниши уезжают
            # мимо тела. Сторона берётся у самих кусков — с какой стороны
            # плоскости лежит кусок рядом с петлёй.
            sa = float(side[near & (cc == s['patch'])].mean() or 0.0)
            # Сосед по шву — не самый длинный из граничащих, а тот, кто лежит
            # по другую сторону плоскости. На плече у белой рубашки самая
            # длинная граница с красным жилетом, но жилет там же, где рубашка.
            cand = [(l, j) for j, l in s['nblen'].items()
                    if j != s['patch'] and near[cc == j].any()
                    and np.sign(side[near & (cc == j)].mean()) != np.sign(sa)]
            if not cand:
                dow = 'соседа с той стороны нет'
            else:
                pb = max(cand)[1]
                pa = s['patch']
                own = (pa, pb) if sa > 0 else (pb, pa)
                caps = cap_limit(V, S, k, p, s['n'], wall)
                got = pick_dowel(V, F, ftree, p, s['n'], r, wall, caps)
                if got:
                    rr, d1, d2 = got
                    dow = f'Ø{2 * rr:.1f}×{d1 + d2 - 1:.1f}'
                    # Кандидатов на вторую половину может быть несколько:
                    # у шва бывает три соседа, и какой из них реально стоит
                    # по ту сторону — здесь видно только приблизительно.
                    # Перебор доводится до конца в pivot_joint.py, где
                    # булево скажет точно.
                    alts = ([int(clab[s['patch']])] +
                            [int(clab[j]) for _, j in sorted(cand, reverse=True)])
                    fa = int(clab[own[0]])
                    rest = []
                    for x in alts:                     # без повторов и без A
                        if x != fa and x not in rest:
                            rest.append(x)
                    if not rest:
                        continue
                    jobs.append(dict(seam=k, dia=float(2 * rr), p=p.tolist(),
                                     n=s['n'].tolist(),
                                     depth=[float(d1), float(d2)],
                                     fil=fa, nb=rest))
                else:
                    dow = f'вписан Ø{2 * r:.1f} — мало'
        print(f'{k:>4} {s["fil"]:>3} {",".join(map(str, nbf)) or "—":>8} '
              f'{s["per"]:>6.1f}мм {s["dia"]:>6.1f}мм {s["rms"]:>6.2f}мм '
              f'{s["dmax"]:>6.2f}мм {"плоский" if ok else "по цвету":>12} {dow:>22}')
    return S, jobs


def flatten_seam(V, s, tol):
    """Положить вершины петли точно на плоскость шва, но не дальше tol.

    Крышка строится веером по петле, поэтому ровной крышка бывает только
    у ровной петли, а ровная крышка — единственное, что на печати слоями
    сходится само. Сдвиг идёт вдоль нормали плоскости, наружную поверхность
    трогает на те же доли миллиметра и одинаково у обеих деталей, так что
    силуэт на стыке не рвётся.

    Шов, у которого кромка гуляет сильнее tol, не трогается вовсе: это
    уже не плоский стык, и притягивать его к плоскости — менять форму."""
    L = np.array(s['loop'], np.int64)
    d = (V[L] - s['c']) @ s['n']
    if np.abs(d).max() > tol:
        return V, 0.0, False
    V = V.copy()
    V[L] -= d[:, None] * s['n'][None, :]
    return V, float(np.abs(d).max()), True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['plan', 'cut', 'joints', 'inlay'])
    ap.add_argument('npz')
    ap.add_argument('out', nargs='?', help='папка для STL (для cut)')
    ap.add_argument('--scale', type=float, default=1.0)
    ap.add_argument('--crumb', type=float, default=1.0,
                    help='мм2: скорлупы мельче — мусор сетки, выбрасываются')
    ap.add_argument('--min-area', type=float, default=0.0,
                    help='мм2: куски мельче отдаются соседу перед резкой')
    ap.add_argument('--pieces', action='store_true',
                    help='каждый связный кусок цвета — отдельным STL, крупные первыми')
    ap.add_argument('--preview', action='store_true',
                    help='рядом с каждым STL положить npz для tools/paintview.py')
    ap.add_argument('--only', default='', help='резать только эти филаменты, через запятую')
    ap.add_argument('--flat', type=float, default=0.5,
                    help='мм: шов с отклонением кромки меньше — считается плоским')
    ap.add_argument('--wall', type=float, default=round(3 * hardware.line_width(), 2),
                    help='мм: стенка, которую ниша обязана оставить до поверхности; '
                         'по умолчанию три линии установленного сопла')
    ap.add_argument('--shape', choices=['rect', 'round'], default='rect',
                    help='сечение ниши и штифта')
    ap.add_argument('--fit', type=float, default=0.15,
                    help='мм на сторону: зазор ниши против штифта на клеевой посадке')
    ap.add_argument('--plane-cut', default='',
                    help='номера швов через запятую или auto (только те, где '
                         'встанет штифт): перевести шов с контура цвета на его '
                         'плоскость, разрезав треугольники')
    ap.add_argument('--inlay', default='',
                    help='номера кусков через запятую или auto: закрыть пятно '
                         'призмой (пробка и карман) вместо веера')
    ap.add_argument('--inlay-depth', type=float, default=1.8, metavar='ММ',
                    help='насколько дно вставки ниже самой глубокой её точки')
    ap.add_argument('--inlay-fit', type=float, default=0.12, metavar='ММ',
                    help='зазор посадки вставки в карман, на сторону')
    ap.add_argument('--inlay-width', type=float, default=0.0, metavar='ММ',
                    help='минимальная ширина 2S/L; 0 — четыре линии сопла')
    ap.add_argument('--plane-margin', type=float, default=0.6, metavar='ММ',
                    help='насколько полоса реза шире самой петли')
    ap.add_argument('--flatten', type=float, default=0.0, metavar='ММ',
                    help='положить кромки плоских швов точно на их плоскость, '
                         'сдвигая вершины не дальше указанного (обычно 0.2 — слой)')
    ap.add_argument('--emit', default='',
                    help='куда записать задание на ниши и штифты для pivot_joint.py')
    a = ap.parse_args()

    V, F, lab, ea, eb, elen, area, fcol = load(a.npz, a.scale)
    print(f'вершин {len(V)}, граней {len(F)}, поверхность {area.sum():.0f} мм2, '
          f'габарит {" × ".join(f"{x:.1f}" for x in (V.max(0) - V.min(0)))} мм')

    ns, sc = shells(ea, eb, len(F))
    SA = np.bincount(sc, weights=area, minlength=ns)
    print(f'скорлуп сетки {ns}: ' +
          ', '.join(f'{x:.0f} мм2' for x in np.sort(SA)[::-1][:4]) +
          (' …' if ns > 4 else ''))
    crumb = np.flatnonzero(SA < a.crumb)
    if len(crumb):
        keep = ~np.isin(sc, crumb)
        print(f'крошек мельче {a.crumb} мм2: {len(crumb)} скорлуп, '
              f'{SA[crumb].sum():.1f} мм2, {int((~keep).sum())} граней — выброшены')
        F, lab, ea, eb, elen, area = drop_faces(keep, F, lab, ea, eb, elen, area)
        ns, sc = shells(ea, eb, len(F))

    if (lab == SPLIT).any():
        k = int((lab == SPLIT).sum())
        lab = absorb(lab, ea, eb, elen, area, lambda l, cc, A, cl: cl == SPLIT)
        print(f'дроблёных граней {k} — отданы соседям')

    # Скорлупа целиком одного цвета — подарок: её не надо резать вовсе,
    # она уже отдельная деталь без единого шва.
    def free_bodies(lab):
        SA = np.bincount(sc, weights=area, minlength=ns)
        out = []
        for i in np.flatnonzero(SA > 0):
            u = np.unique(lab[sc == i])
            if len(u) == 1 and u[0] > 0:
                out.append((int(u[0]), float(SA[i])))
        return out

    def say_free(lab):
        for f, ar in sorted(free_bodies(lab), key=lambda x: -x[1]):
            print(f'филамент {f}: отдельная скорлупа {ar:.0f} мм2 — это уже готовая '
                  f'деталь, резать и клеить не нужно')

    report(lab, ea, eb, elen, area, fcol, 'как есть')
    say_free(lab)
    if a.min_area > 0:
        lab = coarsen(lab, ea, eb, elen, area, a.min_area)
        report(lab, ea, eb, elen, area, fcol, f'после укрупнения до {a.min_area} мм2')
        say_free(lab)

    nc, cc = patches(lab, ea, eb, len(F))
    clab = np.zeros(nc, np.int32); clab[cc] = lab

    if a.plane_cut:
        S0 = seam_loops(V, F, lab, cc, clab, area, twin_lookup(F))
        S0.sort(key=lambda x: -x['per'])
        if a.plane_cut == 'auto':
            # Плоскость меняет авторский рисунок, поэтому переводятся на неё
            # только те швы, ради которых это и делается, — где встанет
            # штифт. Остальные плоские швы (край воротника, кончик сигары)
            # ничего от плоскости не выигрывают, а цвет сдвигают.
            which = [j['seam'] for j in
                     report_joints(V, F, lab, cc, clab, area, fcol,
                                   a.flat, a.wall, a)[1]]
            print(f'\nна плоскость переводятся только швы со штифтом: '
                  f'{", ".join(map(str, which)) or "нет таких"}')
        else:
            which = [int(x) for x in a.plane_cut.split(',') if x.strip()]
        V, F, lab, ea, eb, elen, area = flatten_seams(
            V, F, lab, ea, eb, elen, area, cc, clab, S0, which, a.flat,
            a.plane_margin)
        # Плоскость отрезает от соседних зон тонкие лоскуты — там, где
        # рисунок подходил к шву под малым углом. Напечатать такой лоскут
        # нельзя (он уже линии сопла), поэтому он отдаётся соседу тем же
        # приёмом, что и крап.
        thr = max(a.min_area, 1.0)
        lab = coarsen(lab, ea, eb, elen, area, thr)
        nc, cc = patches(lab, ea, eb, len(F))
        clab = np.zeros(nc, np.int32); clab[cc] = lab
        report(lab, ea, eb, elen, area, fcol,
               f'после перевода швов на плоскость и чистки лоскутов < {thr} мм2')

    if a.flatten > 0:
        look = twin_lookup(F)
        S = seam_loops(V, F, lab, cc, clab, area, look)
        S.sort(key=lambda x: -x['per'])
        done = 0
        for k, sm in enumerate(S):
            V, mv, ok = flatten_seam(V, sm, a.flatten)
            if ok:
                done += 1
        print(f'\nкромки выровнены на {done} швах из {len(S)}; остальные гуляют '
              f'сильнее {a.flatten} мм и оставлены как есть')

    if a.cmd == 'inlay' or a.inlay:
        P = V[F]
        nr = np.cross(P[:, 1] - P[:, 0], P[:, 2] - P[:, 0])
        nl = np.linalg.norm(nr, axis=1); nl[nl == 0] = 1
        minw = a.inlay_width or round(4 * hardware.line_width(), 2)
        want = (None if a.inlay in ('', 'auto') else
                {int(x) for x in a.inlay.split(',') if x.strip()})
        PRISM = report_inlay(V, F, lab, cc, clab, area, nr / nl[:, None], fcol,
                             a.inlay_depth, minw, a.wall, want)
        if a.cmd == 'inlay':
            return
    else:
        PRISM = {}

    if a.cmd == 'joints':
        S, jobs = report_joints(V, F, lab, cc, clab, area, fcol, a.flat, a.wall, a)
        if a.emit:
            import json
            spec = {'out': 'work/joints', 'parts': {}, 'cuts': [], 'joints': []}
            for jb in jobs:
                if not jb['nb']:
                    continue
                a1 = f'filament{jb["fil"]}'
                alts = [f'filament{x}' for x in jb['nb']]
                spec['joints'].append(dict(
                    parts=[a1, alts[0]], alts=alts, p=jb['p'], d=jb['n'],
                    shape=a.shape, size=round(jb['dia'], 2),
                    depth=[round(jb['depth'][0], 1), round(jb['depth'][1], 1)],
                    fit=[a.fit, round(a.fit * 2, 2)], n=2))
                for nm in [a1] + alts:
                    spec['parts'][nm] = f'work/parts/{nm}.stl'
            json.dump(spec, open(a.emit, 'w', encoding='utf-8'),
                      ensure_ascii=False, indent=1)
            print(f'\nзадание на {len(spec["joints"])} стыков записано: {a.emit}\n'
                  f'пути к деталям в нём проставлены на глаз — сверить с тем, '
                  f'что написал cut, и запустить tools/pivot_joint.py')
        return

    if a.cmd == 'plan':
        return

    os.makedirs(a.out, exist_ok=True)
    only = {int(x) for x in a.only.split(',') if x.strip()}
    whole = check(V, F)[2]
    P = V[F]
    nrm = np.cross(P[:, 1] - P[:, 0], P[:, 2] - P[:, 0])
    nl = np.linalg.norm(nrm, axis=1); nl[nl == 0] = 1
    nrm = nrm / nl[:, None]
    probe = lambda pts: inside(V, F, pts)
    cache = {}
    total = 0.0
    print()
    nc, cc = patches(lab, ea, eb, len(F))
    Ac = np.bincount(cc, weights=area, minlength=nc)
    jobs = []
    for f in sorted({int(x) for x in lab if x > 0}):
        if only and f not in only:
            continue
        if a.pieces:
            ids = [i for i in np.argsort(-Ac) if Ac[i] > 0 and lab[cc == i][0] == f]
            for k, i in enumerate(ids, 1):
                jobs.append((f'filament{f}_{k}', np.flatnonzero(cc == i), Ac[i]))
        else:
            jobs.append((f'filament{f}', np.flatnonzero(lab == f),
                         area[lab == f].sum()))
    for nm, sel, ar in jobs:
        f = int(lab[sel[0]])
        Vp, Fp, caps, cen = build_part(V, F, sel, nrm, probe, cache,
                                       PRISM, f, a.inlay_fit)
        used, inv = np.unique(Fp, return_inverse=True)
        Vp, Fp = Vp[used], inv.reshape(Fp.shape)
        open_e, nonman, vol = check(Vp, Fp)
        path = os.path.join(a.out, f'{nm}.stl')
        write_stl(path, Vp, Fp)
        if a.preview:                       # npz под tools/paintview.py
            np.savez_compressed(path[:-4] + '.npz', V=Vp, F=Fp,
                                lab=np.full(len(Fp), f, np.int8), fcol=np.array(fcol, object))
        ok = 'замкнута' if open_e == 0 and nonman == 0 else \
             f'дыр {open_e}, склеек по ребру {nonman}'
        bad = int((~inside(V, F, cen)).sum()) if len(cen) else 0
        total += vol
        # Толщина решает, возможен ли на этой детали стык с нишей: ниша
        # живёт в теле, а у цвета, размазанного пятном по поверхности,
        # тела нет — деталь выходит коркой в полтора миллиметра.
        print(f'{nm}: {ar:.0f} мм2, граней {len(Fp)}, петель {len(cen)}, крышки {caps}, '
              f'объём {vol/1000:.2f} см3, средняя толщина {2*vol/ar:.1f} мм, '
              f'{ok}, центр петли снаружи: {bad} → {path}')

    # Детали обязаны сложиться обратно в исходное тело: крышки соседей строятся
    # по общей петле, поэтому стыкуются грань в грань. Расхождение объёмов —
    # это либо дыра между деталями, либо взаимное наложение крышек.
    if not only:
        print(f'\nсумма деталей {total/1000:.2f} см3 против исходных {whole/1000:.2f} см3, '
              f'расхождение {100*abs(total-whole)/abs(whole):.2f} %')


if __name__ == '__main__':
    main()
