#!/usr/bin/env python3
"""Резка тела правильными телами: плоскости и цилиндры, ниши и штифты.

Здесь не «срезают поверхность одного цвета», а делят **объём**: каждая деталь
получается пересечением исходного тела с полупространствами и цилиндрами, так
что сумма деталей и есть исходное тело. Все внутренние поверхности заданы
числами (плоскость, цилиндр), а не срисованы с дрожащей границы покраски.

    uv run --with numpy --with scipy python tools/solid_cut.py fit work/e.npz
    uv run --with numpy --with scipy --with shapely --with mapbox_earcut \
           --with scikit-image python tools/solid_cut.py inlay work/e.npz \
           --filament 4 --scale 2 --assembly 0,0,1 --out work/parts/штрих
    python3 tools/solid_cut.py cut   work/spec.json
    python3 tools/solid_cut.py check исходник.stl деталь1.stl деталь2.stl …

Порядок работы
--------------
1. `paint.py explode` — покраска кистью в сетку, где грань одноцветна;
2. `solid_cut.py fit` — по границам цвета находятся плоскости и окружности;
3. руками пишется спецификация: какие плоскости режут, где штифты и ниши;
4. `solid_cut.py cut` — Blender считает булевы точным решателем;
5. `solid_cut.py check` — детали не лезут друг в друга и складываются в исходное.

Спецификация (JSON)
-------------------
    {"src": "тело.stl",
     "parts": [{"name": "…", "out": "…stl", "src": "своё_тело.stl", "ops": [
         {"kind":"plane",  "n":[0,0,-1], "d":2.9647, "op":"INTERSECT"},
         {"kind":"cyl",    "axis":[0,0,1], "t0":-3.5, "t1":0.74, "r":3.1,
          "at":[0,0,0], "op":"DIFFERENCE"},
         {"kind":"region", "planes":[[[0,0,1],10.95], [[-1,-1,0],0]],
          "op":"DIFFERENCE"},
         {"kind":"mesh",   "path":"другое_тело.stl", "op":"UNION"}]}]}

`src` у детали перекрывает общий: у сборки из перекрывающихся тел каждая
деталь режется из своего. `plane` — полупространство n·x ≤ d.
`mesh` — резак из готового файла; им же собирается объединение сборки для
проверки. **Объединение считать именно сетками, а не примитивами:** цилиндр
из 192 сегментов и цилиндр сетки из 360 почти совпадают, и булев решатель
на таких поверхностях выдаёт мусор — проверено 22.09.2026, разность дала
496 мм³ «наружу» там, где наружу нет ничего. `cyl` — цилиндр радиуса r вдоль axis,
от t0 до t1 вдоль оси, отсчёт от точки at. `region` — пересечение нескольких
полупространств одним телом (клин, слой). Операции: INTERSECT, DIFFERENCE
(ниша), UNION (штифт).

Ловушки, на которых уже обожглись
---------------------------------
* Резак должен **высовываться** из детали: ниша, начатая ровно на плоскости
  реза, даёт булеву решателю совпадающие грани. Начинать на 0.5 мм раньше.
* Штифт растить не от плоскости, а изнутри детали — по той же причине.
* Проверка «объединение минус исходник» на кубе даёт весь куб, если исходник
  не раздуть: все шесть плоскостей совпадают. `check` раздувает сам.
* Порог пересечения пары 1e-3 мм³ — это кубик 0.1 мм ребром. Меньшее выдаёт
  сам решатель на совпадающих плоскостях, к посадке деталей отношения не имеет.
* Зазор вставки задан под печать **плашмя**: лицо к столу, направление
  выемки вверх, боковые стенки призмы вертикальны. Лицом вертикально —
  боковая грань выходит лестницей, и 0.15 мм не хватает: проверено сборкой
  22.09.2026, кольцо пришлось подтачивать наждачкой.
* У детали, в которую вдавливают вставку, заполнение должно быть
  решётчатым («Куб», «Сетка», «Гироид»): спираль и концентрика не держат
  стенку кармана поперёк, и слой рядом с нишей расходится.

Допуски, ориентация и заполнение — references/split-to-parts.md скилла 3mf-paint.
"""
import sys, os, json, subprocess, itertools

BLENDER = "/Applications/Blender.app/Contents/MacOS/Blender"
CUT_PAIR = 1e-3          # мм³: ниже этого пересечение пары — шум решателя
LIP_COS  = 0.5           # n·a = 0.5 -> кромка 30°, тоньше линии сопла на 0.7 мм
LIP_SHARE= 5.0           # % площади с кромкой острее 30°, после которых прилив не годится
NECK_MIN = 5.0           # мм²: сечение перемычки, ниже которого накладка ломается в руках
NECK_TIP = 10.0          # % зоны: меньший кусок мельче — это кончик, а не разлом пополам
                         # (на волне test 3 сечение 2.66 мм² сломалось при вставке)


# ======================= часть, работающая внутри Blender =======================

def _bl():
    import bpy, bmesh
    from mathutils import Vector, Matrix
    return bpy, bmesh, Vector, Matrix


def _load(path, name=None):
    bpy, _, _, _ = _bl()
    ext = os.path.splitext(path)[1].lower()
    if ext == '.stl':
        bpy.ops.wm.stl_import(filepath=path)
    elif ext == '.obj':
        bpy.ops.wm.obj_import(filepath=path)
    else:
        raise SystemExit(f'не умею читать {ext}')
    o = bpy.context.selected_objects[0]
    o.name = name or os.path.basename(path)
    return o


def _rot_to(axis):
    _, _, Vector, _ = _bl()
    v = Vector(axis).normalized()
    return Vector((0, 0, 1)).rotation_difference(v).to_matrix().to_4x4()


def _halfspace(n, d, size=400.0):
    """Тело, занимающее n·x ≤ d."""
    bpy, _, Vector, Matrix = _bl()
    bpy.ops.mesh.primitive_cube_add(size=size)
    o = bpy.context.object
    o.matrix_world = (Matrix.Translation(Vector(n).normalized() * (d - size / 2))
                      @ _rot_to(n))
    return o


def _cylinder(axis, t0, t1, r, at=(0, 0, 0), seg=192):
    """Цилиндр радиуса r вдоль axis, от t0 до t1 вдоль оси, отсчёт от at."""
    bpy, _, Vector, Matrix = _bl()
    u = Vector(axis).normalized()
    bpy.ops.mesh.primitive_cylinder_add(vertices=seg, radius=r, depth=(t1 - t0))
    o = bpy.context.object
    o.matrix_world = (Matrix.Translation(Vector(at) + u * ((t0 + t1) / 2)) @ _rot_to(u))
    return o


def _boolean(obj, cutter, op):
    bpy, _, _, _ = _bl()
    m = obj.modifiers.new('b', 'BOOLEAN')
    m.operation, m.solver, m.object = op, 'EXACT', cutter
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.modifier_apply(modifier=m.name)
    bpy.data.objects.remove(cutter, do_unlink=True)


def _region(planes, size=400.0):
    """Резак как пересечение полупространств: клин, слой, четверть."""
    bpy, _, _, _ = _bl()
    bpy.ops.mesh.primitive_cube_add(size=size)
    o = bpy.context.object
    for n, d in planes:
        _boolean(o, _halfspace(n, d, size * 1.5), 'INTERSECT')
    return o


def _stats(o):
    _, bmesh, _, _ = _bl()
    bm = bmesh.new(); bm.from_mesh(o.data); bm.transform(o.matrix_world)
    v = bm.calc_volume(signed=True)
    op = sum(1 for e in bm.edges if len(e.link_faces) != 2)
    nm = sum(1 for e in bm.edges if len(e.link_faces) > 2)
    bm.free()
    return v, op, nm


def _cutter(c):
    k = c['kind']
    if k == 'plane':
        return _halfspace(c['n'], c['d'])
    if k == 'cyl':
        return _cylinder(c['axis'], c['t0'], c['t1'], c['r'], c.get('at', (0, 0, 0)))
    if k == 'region':
        return _region(c['planes'])
    if k == 'mesh':
        return _load(c['path'], 'cutter')
    raise SystemExit('не знаю резак ' + k)


def _copy(o):
    bpy, _, _, _ = _bl()
    d = o.copy(); d.data = o.data.copy()
    bpy.context.collection.objects.link(d)
    return d


def _combine(a, b, op):
    d, c = _copy(a), _copy(b)
    _boolean(d, c, op)
    return d


def do_cut(spec):
    bpy, _, _, _ = _bl()
    out = []
    for p in spec['parts']:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        o = _load(p.get('src') or spec['src'], p['name'])
        for c in p.get('ops', []):
            _boolean(o, _cutter(c), c['op'])
        v, op, nm = _stats(o)
        bpy.ops.object.select_all(action='DESELECT')
        o.select_set(True)
        bpy.context.view_layer.objects.active = o
        bpy.ops.wm.stl_export(filepath=p['out'], export_selected_objects=True,
                              global_scale=1.0, apply_modifiers=True)
        print(f'  {p["name"]:24s} {v:10.3f} мм³  граней {len(o.data.polygons):5d}  '
              f'открытых рёбер {op}  non-manifold {nm}  -> {p["out"]}')
        if op or nm:
            print('    !! деталь не замкнута — резать так нельзя')
        out.append(v)
    print(f'  сумма деталей {sum(out):.3f} мм³')


def do_check(src_path, part_paths):
    bpy, _, _, _ = _bl()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    src = _load(src_path)
    parts = [_load(p) for p in part_paths]
    V0 = _stats(src)[0]
    print(f'исходное тело: {V0:.3f} мм³')
    s = 0.0
    for p in parts:
        v, op, nm = _stats(p)
        s += v
        flag = '' if not (op or nm) else f'  !! открытых рёбер {op}, non-manifold {nm}'
        print(f'  {p.name:28s} {v:10.3f} мм³{flag}')
    print(f'сумма деталей {s:.3f} мм³, недостача {V0-s:.3f} '
          f'({100*(V0-s)/V0:.3f} %) — это зазоры')

    bad = 0
    print('пересечения пар (должны быть нулевые):')
    for a, b in itertools.combinations(parts, 2):
        d = _combine(a, b, 'INTERSECT')
        v = _stats(d)[0]
        bpy.data.objects.remove(d, do_unlink=True)
        hit = abs(v) >= CUT_PAIR
        bad += hit
        print(f'  {a.name:24s} × {b.name:24s} {v:10.4f} мм³'
              + ('  <<< ДЕТАЛИ ЛЕЗУТ ДРУГ В ДРУГА' if hit else ''))

    acc = _copy(parts[0])
    for p in parts[1:]:
        acc = _combine(acc, p, 'UNION')
    vu = _stats(acc)[0]
    print(f'объединение деталей {vu:.3f} мм³ против исходного {V0:.3f} '
          f'({100*(V0-vu)/V0:+.3f} %)')

    big = _copy(src)
    big.scale = (1.000002,) * 3
    bpy.context.view_layer.objects.active = big
    bpy.ops.object.transform_apply(scale=True)
    outv = _stats(_combine(acc, big, 'DIFFERENCE'))[0]
    print(f'выступает за исходное тело: {outv:.4f} мм³')
    print('ИТОГ: ' + ('все пары чистые' if not bad else f'{bad} пар пересекаются'))


def run_in_blender(argv):
    a = json.loads(argv[0])
    if a['cmd'] == 'cut':
        do_cut(json.load(open(a['spec'])))
    else:
        do_check(a['src'], a['parts'])


# ======================= часть, работающая снаружи =======================

def cmd_fit(argv):
    """Плоскости и окружности по границам цвета в разложенной сетке."""
    import numpy as np
    from scipy.spatial import cKDTree
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    npz = argv[0]
    minpts = int(argv[argv.index('--min') + 1]) if '--min' in argv else 40
    d = np.load(npz, allow_pickle=True)
    V, F, lab = d['V'], d['F'], d['lab'].astype(int)
    P = V[F]
    C = P.mean(1)
    size = np.sqrt(0.5 * np.linalg.norm(np.cross(P[:, 1] - P[:, 0], P[:, 2] - P[:, 0]), axis=1))
    print(f'граней {len(F)}, размер подтреугольника {size.min():.3f}..{size.max():.3f} мм')

    # точка границы — между разноцветными гранями, чьи центроиды рядом.
    # По рёбрам искать нельзя: после explode в сетке T-стыки.
    t = cKDTree(C)
    k = 8
    dist, nb = t.query(C, k=k + 1)
    src = np.repeat(np.arange(len(C)), k)
    dst = nb[:, 1:].ravel()
    ok = ((lab[src] != lab[dst]) &
          (dist[:, 1:].ravel() < 3 * np.maximum(size[src], size[dst])))
    B = 0.5 * (C[src[ok]] + C[dst[ok]])
    key = np.sort(np.stack([lab[src[ok]], lab[dst[ok]]], 1), 1)
    print(f'точек границы {len(B)}')
    res = []
    for pair in np.unique(key, axis=0):
        Q = np.unique(np.round(B[np.all(key == pair, axis=1)], 6), axis=0)
        pr = cKDTree(Q).query_pairs(0.8, output_type='ndarray')
        g = coo_matrix((np.ones(len(pr)), (pr[:, 0], pr[:, 1])), shape=(len(Q),) * 2)
        nc, cc = connected_components(g, directed=False)
        sz = np.bincount(cc)
        print(f'--- граница филаментов {pair[0]}|{pair[1]}: точек {len(Q)}, кусков {nc}')
        for ci in np.argsort(-sz):
            if sz[ci] < minpts:
                break
            X = Q[cc == ci]
            g0 = X.mean(0)
            n = np.linalg.svd(X - g0)[2][2]
            if n @ g0 < 0:
                n = -n
            dev = (X - g0) @ n
            r = np.linalg.norm(X - g0 - np.outer(dev, n), axis=1)
            print(f'    {sz[ci]:6d} точек  центр ({g0[0]:8.4f},{g0[1]:8.4f},{g0[2]:8.4f})  '
                  f'нормаль ({n[0]:7.4f},{n[1]:7.4f},{n[2]:7.4f})  n·x = {n@g0:8.4f}')
            print(f'            плоскость: rms {dev.std():.4f}, макс {np.abs(dev).max():.4f} мм;  '
                  f'окружность: R {r.mean():.3f} ± {r.std():.3f} ({r.min():.3f}..{r.max():.3f})')
            res.append(dict(filaments=[int(pair[0]), int(pair[1])], points=int(sz[ci]),
                            centre=g0.tolist(), normal=n.tolist(), offset=float(n @ g0),
                            plane_rms=float(dev.std()), radius=float(r.mean()),
                            radius_spread=float(r.std())))
    print('\nrms по плоскости меньше высоты слоя — режется плоскостью; '
          'разброс радиуса больше десятой доли R — кругом это не было')
    if '--emit' in argv:
        out = argv[argv.index('--emit') + 1]
        json.dump(res, open(out, 'w'), ensure_ascii=False, indent=1)
        print(f'записано: {out}')


def cmd_inlay(argv):
    """Из нарисованной зоны на кривой поверхности — тела вставки и кармана.

    Зона, обёрнутая вокруг выпуклой поверхности, не вынимается из кармана
    с радиальными стенками: с одной стороны она упрётся. От масштаба это не
    зависит — разворот по дуге при увеличении не меняется. Поэтому стенки
    кармана делаются призмой вдоль ОДНОГО направления (средняя нормаль
    зоны), дно — плоскостью перпендикулярно ему, а глубина в самом глубоком
    месте берётся как толщина по краю плюс размах зоны вдоль этого
    направления. Иначе вставка на краях сходит на нет.

    Резать ли зону в отдельную деталь — отдельный вопрос, и он возникает,
    когда зона СОПРИКАСАЕТСЯ с телом того же цвета (на шаре test 3 белая
    волна вырастала из белого низа: одна связная зона 1163 мм²). Тогда её
    можно оставить приливом соседней детали, а не резать. `--assembly` даёт
    направление, по которому сосед садится на место, и печатает вердикт:

    * min(n·a) <= 0 — часть зоны смотрит назад, сосед на такой прилив
      не наденется: только вставка;
    * доля площади с n·a < LIP_COS больше LIP_SHARE % — стенка призмы
      встречает поверхность по касательной, вдоль контура идёт лезвие
      (на волне test 3 было 28 % площади с кромкой острее 6°): только вставка;
    * прилив смотрит в стол при печати соседа — этого инструмент не знает,
      это вопрос к человеку: у шапки, которая ложится плоскостью реза на стол,
      любой прилив на этой плоскости означает печать купола на поддержках;
    * если ни одно не сработало — **резать нельзя**, зона остаётся приливом.

    И обратная сторона: если зону всё-таки режем, новый шов обязан лечь на
    уже существующий (на плоскость главного реза) — иначе внутри одного цвета
    появляется лишняя видимая линия, и это надо не решать самому, а спросить."""
    import numpy as np
    from scipy import ndimage
    from scipy.spatial import cKDTree
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    from skimage import measure
    from shapely.geometry import Polygon
    from shapely.geometry.polygon import orient
    import mapbox_earcut as earcut

    def opt(k, d, t=float):
        return t(argv[argv.index(k) + 1]) if k in argv else d

    npz = argv[0]
    fil   = int(opt('--filament', 4, int))
    scale = opt('--scale', 1.0)
    edge  = opt('--edge', 1.2)       # толщина вставки в самом тонком месте
    fit   = opt('--fit', 0.20)   # зазор вбок на сторону; 0.20 проверено печатью,
                                 # 0.30 дало видимые щели
    dfit  = opt('--depth-fit', 0.05)  # зазор по глубине: он весь уходит в то,
                                 # насколько накладка утонет ниже поверхности
    smooth= opt('--smooth', 0.24)    # радиус чистки контура от зубцов кисти
    asm   = None
    if '--assembly' in argv:
        asm = np.array([float(x) for x in argv[argv.index('--assembly') + 1].split(',')])
        asm = asm / np.linalg.norm(asm)
    minar = opt('--min-area', 1.0)
    out   = argv[argv.index('--out') + 1] if '--out' in argv else 'work/inlay'
    px    = smooth / 6.0

    d0 = np.load(npz, allow_pickle=True)
    V, F, lab = d0['V'] * scale, d0['F'], d0['lab'].astype(int)
    P = V[F]
    sel = np.flatnonzero(lab == fil)
    if not len(sel):
        raise SystemExit(f'филамента {fil} в {npz} нет')
    nrm = np.cross(P[:, 1] - P[:, 0], P[:, 2] - P[:, 0])
    ar = 0.5 * np.linalg.norm(nrm, axis=1)
    nrm = nrm / np.maximum(np.linalg.norm(nrm, axis=1), 1e-12)[:, None]

    # куски зоны: подтреугольники, склеенные по общим вершинам
    Vw = P[sel].reshape(-1, 3)
    pr = cKDTree(Vw).query_pairs(1e-6, output_type='ndarray')
    g = coo_matrix((np.ones(len(pr)), (pr[:, 0], pr[:, 1])), shape=(len(Vw),) * 2)
    _, cc = connected_components(g, directed=False)
    par = np.arange(cc.max() + 1)
    def find(x):
        while par[x] != x:
            par[x] = par[par[x]]; x = par[x]
        return x
    for row in cc.reshape(-1, 3):
        r0 = find(row[0])
        for v in row[1:]:
            r = find(v)
            if r != r0: par[r] = r0
    comp = np.array([find(x) for x in cc.reshape(-1, 3)[:, 0]])
    _, inv = np.unique(comp, return_inverse=True)
    area = np.bincount(inv, weights=ar[sel])
    print(f'{npz}: филамент {fil}, кусков {len(area)}, '
          f'крупнее {minar} мм²: {int((area >= minar).sum())}')

    made = []
    for k in np.argsort(-area):
        if area[k] < minar:
            break
        idx = sel[inv == k]
        Q = P[idx].reshape(-1, 3)
        d = (nrm[idx] * ar[idx][:, None]).sum(0)
        d /= np.linalg.norm(d)
        fold = float((nrm[idx] @ d).min())
        e1 = np.cross(d, [0, 0, 1.0])
        if np.linalg.norm(e1) < 1e-6: e1 = np.cross(d, [1.0, 0, 0])
        e1 /= np.linalg.norm(e1); e2 = np.cross(d, e1)
        uu, vv, ss = Q @ e1, Q @ e2, Q @ d
        spread = float(ss.max() - ss.min())
        h = edge + spread

        U0, V0 = uu.min() - 0.5, vv.min() - 0.5
        W = int((uu.max() - U0 + 0.5) / px) + 2
        H = int((vv.max() - V0 + 0.5) / px) + 2
        img = np.zeros((H, W), bool)
        smax = np.full((H, W), -1e9)
        q2 = np.stack([(uu - U0) / px, (vv - V0) / px], 1).reshape(-1, 3, 2)
        s3 = ss.reshape(-1, 3)
        for it, q in enumerate(q2):
            x0, x1 = int(q[:, 0].min()), int(np.ceil(q[:, 0].max()))
            y0, y1 = int(q[:, 1].min()), int(np.ceil(q[:, 1].max()))
            xs, ys = np.meshgrid(np.arange(x0, x1 + 1) + .5, np.arange(y0, y1 + 1) + .5)
            pp = np.stack([xs.ravel(), ys.ravel()], 1)
            a, b = q[1] - q[0], q[2] - q[0]; c = pp - q[0]
            den = a[0] * b[1] - b[0] * a[1]
            if abs(den) < 1e-12:
                img[y0:y1 + 1, x0:x1 + 1] = True; continue
            w1 = (c[:, 0] * b[1] - b[0] * c[:, 1]) / den
            w2 = (a[0] * c[:, 1] - c[:, 0] * a[1]) / den
            m = (w1 >= -0.02) & (w2 >= -0.02) & (w1 + w2 <= 1.02)
            if m.any():
                yi, xi = pp[m, 1].astype(int), pp[m, 0].astype(int)
                img[yi, xi] = True
                sv = s3[it, 0] + w1[m] * (s3[it, 1] - s3[it, 0]) + w2[m] * (s3[it, 2] - s3[it, 0])
                np.maximum.at(smax, (yi, xi), sv)
        r = max(1, int(smooth / px))
        yy, xx = np.mgrid[-r:r + 1, -r:r + 1]
        disk = xx ** 2 + yy ** 2 <= r * r
        img = ndimage.binary_opening(ndimage.binary_closing(img, disk), disk)
        polys = []
        for c in measure.find_contours(img.astype(float), 0.5):
            xy = np.stack([c[:, 1] * px + U0, c[:, 0] * px + V0], 1)
            if len(xy) < 4: continue
            p = Polygon(xy)
            if not p.is_valid: p = p.buffer(0)
            if p.area > minar / 2: polys.append(p)
        if not polys:
            print(f'  кусок {area[k]:.1f} мм²: после чистки ничего не осталось'); continue
        polys.sort(key=lambda p: -p.area)
        shell = polys[0]
        poly = Polygon(shell.exterior.coords,
                       [q.exterior.coords for q in polys[1:] if shell.contains(q)])
        poly = poly.simplify(smooth / 8)
        # ширина зоны и её перемычка: радиус эрозии, на котором зона распадается
        dist = ndimage.distance_transform_edt(img) * px
        thick = smax - (ss.min() - edge)
        ridge = (dist >= ndimage.maximum_filter(dist, size=5) - 1e-9) & img
        wid = 2 * dist[ridge].min()
        n0 = ndimage.label(img)[1]
        neck = neck_cs = share = 0.0
        for rr in np.arange(px, 3.0, px):
            lb, nl = ndimage.label(dist > rr)
            if nl > n0:
                sz = np.sort(np.bincount(lb.ravel())[1:])[::-1]
                share = 100.0 * sz[1] / sz.sum()      # доля меньшего куска
                band = img & (np.abs(dist - rr) < 1.5 * px) & (thick > 0)
                neck = 2 * rr
                neck_cs = neck * (thick[band].min() if band.any() else edge)
                break
        name = f'{out}_{len(made)+1}'
        for tag, pg, s0 in (('', poly, ss.min() - edge),
                            ('_pocket', poly.buffer(fit, join_style=2),
                             ss.min() - edge - dfit)):
            T = _prism(pg, d, e1, e2, s0, ss.max() + 2.0, orient, earcut, np)
            _wstl(f'{name}{tag}.stl', T, np)
        made.append(name)
        print(f'  кусок {area[k]:7.1f} мм²: размах вдоль нормали {spread:5.2f} мм -> '
              f'карман {h:5.2f} мм в глубине, {edge:.1f} по краю')
        print(f'      самое узкое место {wid:.2f} мм ({wid/0.42:.1f} линии сопла), '
              f'контур {len(poly.exterior.coords)-1} точек, дырок {len(poly.interiors)}')
        if neck:
            what = ('отломит кончик' if share < NECK_TIP else
                    f'РАЗЛОМИТ ДЕТАЛЬ (меньший кусок {share:.0f} % зоны)')
            bad = ('' if neck_cs >= NECK_MIN else
                   f' — меньше {NECK_MIN:.0f} мм², {what}: поднять --edge')
            print(f'      перемычка {neck:.2f} мм, сечение {neck_cs:.2f} мм²{bad}')
        if fold < 0.0:
            print(f'      !! зона заворачивается за направление выемки '
                  f'(min cos {fold:.2f}) — призмой её не вынуть')
        if asm is not None:
            ca, wa = nrm[idx] @ asm, ar[idx]
            lip = 100 * wa[ca < LIP_COS].sum() / wa.sum()
            sa = Q @ asm
            if ca.min() <= 0:
                why = (f'только вставка: min cos к сборке {ca.min():+.3f} — '
                       f'часть зоны смотрит назад')
            elif lip > LIP_SHARE:
                why = (f'только вставка: {lip:.1f} % площади дало бы кромку острее '
                       f'{np.degrees(np.arcsin(LIP_COS)):.0f}° (min cos {ca.min():+.3f})')
            else:
                why = (f'прилив по сборке проходит: карман {sa.max()-sa.min()+edge:.2f} мм, '
                       f'min cos {ca.min():+.3f}, кромка острее 30° на {lip:.1f} % площади.\n'
                       f'      ЕСЛИ зона касается тела того же цвета — не резать её, '
                       f'оставить приливом (проверив, что прилив не смотрит в стол)')
            print(f'      {why}')
        print(f'      -> {name}.stl (вставка), {name}_pocket.stl (резак кармана)')
    print(f'зазор кармана {fit:.2f} вбок и {dfit:.2f} по глубине. Оба проверены печатью:\n'
          f'  вбок 0.30 дало видимые щели, 0.20 сидит хорошо; глубинный зазор весь\n'
          f'  уходит в то, насколько накладка утонет ниже поверхности, поэтому он\n'
          f'  маленький. Накладка печатается ПЛАШМЯ: дном кармана на стол, выемка вверх.')
    print('в спецификацию: вставка — INTERSECT с телом, карман — DIFFERENCE')
    return 0


def _prism(poly, d, e1, e2, s0, s1, orient, earcut, np):
    poly = orient(poly, 1.0)
    rings = [np.asarray(poly.exterior.coords)[:-1]]
    rings += [np.asarray(r.coords)[:-1] for r in poly.interiors]
    verts = np.concatenate(rings)
    ends = np.cumsum([len(r) for r in rings])
    tri = earcut.triangulate_float64(verts, ends).reshape(-1, 3)
    to3 = lambda uv, s: s * d + uv[:, 0:1] * e1 + uv[:, 1:2] * e2
    A3, B3 = to3(verts, s1), to3(verts, s0)
    T = [A3[tri], B3[tri][:, ::-1]]
    off = 0
    for r in rings:
        n = len(r)
        i = off + np.arange(n); j = off + (np.arange(n) + 1) % n
        T.append(np.stack([B3[i], B3[j], A3[j]], 1))
        T.append(np.stack([B3[i], A3[j], A3[i]], 1))
        off += n
    T = np.concatenate(T)
    if np.einsum('ij,ij->i', T[:, 0], np.cross(T[:, 1], T[:, 2])).sum() < 0:
        T = T[:, ::-1]
    return T


def _wstl(path, T, np):
    import struct
    n = np.cross(T[:, 1] - T[:, 0], T[:, 2] - T[:, 0])
    L = np.linalg.norm(n, axis=1); L[L == 0] = 1; n /= L[:, None]
    with open(path, 'wb') as f:
        f.write(b'\0' * 80 + struct.pack('<I', len(T)))
        for i in range(len(T)):
            f.write(struct.pack('<12fH', *n[i], *T[i, 0], *T[i, 1], *T[i, 2], 0))



def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ('fit', 'inlay', 'cut', 'check'):
        print(__doc__)
        return 1
    cmd, argv = sys.argv[1], sys.argv[2:]
    if cmd == 'fit':
        return cmd_fit(argv) or 0
    if cmd == 'inlay':
        return cmd_inlay(argv) or 0
    if not os.path.exists(BLENDER):
        print(f'Blender не найден: {BLENDER}')
        return 1
    if cmd == 'cut':
        args = dict(cmd='cut', spec=os.path.abspath(argv[0]))
        spec = json.load(open(argv[0]))
        print(f'режу {spec["src"]} на {len(spec["parts"])} деталей')
    else:
        args = dict(cmd='check', src=os.path.abspath(argv[0]),
                    parts=[os.path.abspath(p) for p in argv[1:]])
    p = subprocess.run([BLENDER, '--factory-startup', '--background',
                        '--python', os.path.abspath(__file__), '--', json.dumps(args)],
                       capture_output=True, text=True, cwd=os.getcwd())
    keep = [l for l in p.stdout.splitlines()
            if not l.startswith(('Blender', 'Read ', 'Info:', 'Timer', 'found bundled',
                                 'Writing', 'Saved', 'Warning: '))]
    print('\n'.join(keep).strip())
    if p.returncode:
        print(p.stderr[-2000:])
    return p.returncode


if __name__ == '__main__':
    if 'bpy' in sys.modules:
        run_in_blender(sys.argv[sys.argv.index('--') + 1:])
    else:
        try:
            import bpy  # noqa: F401
            run_in_blender(sys.argv[sys.argv.index('--') + 1:])
        except ImportError:
            sys.exit(main())
