#!/usr/bin/env python3
"""Расшить T-стыки в 3MF — БЕЗ потери покраски.

Зачем. Когда модель красят кистью в Bambu Studio и сохраняют проект, слайсер
**запекает мазки в геометрию**: дробит треугольники так, чтобы каждый вышел
одноцветным. Соседний треугольник при этом не дробится, и на общем ребре
остаётся чужая вершина — T-стык. Форма цела, объём верный, а связность рвётся:
слайсер видит десятки тысяч «открытых рёбер» и сотни «тел». На
`robbie albert.3mf` (24.09.2026) — 11 058 открытых рёбер и 822 тела при
идеальной форме.

Как чинится. Грань, на ребре которой сидит чужая вершина, разбивается веером
из собственного центроида: контур грани обходится уже с учётом вставленных
вершин, и каждая пара соседей замыкается на центроид. Центроид лежит
**в плоскости грани**, поэтому форма не меняется ни на микрон, а потомки
наследуют `paint_color` родителя — цвет остаётся на месте.

    python3 tools/fixtjoints.py вход.3mf выход.3mf
    python3 tools/fixtjoints.py вход.3mf --dry          # только диагноз
    python3 tools/fixtjoints.py вход.3mf выход.3mf --tol 0.005

Чем отличается от соседей по `tools/`:

- `weldmesh.py` сваривает вершины с совпадающими координатами (разрыв по швам
  развёртки у сеток из GLB). На T-стыке координаты РАЗНЫЕ, и сварка не находит
  ничего — на robbie albert схлопнулось 0 вершин из 1.23 млн;
- `meshfix.py --holes` затыкает T-стык как дырку и плодит non-manifold
  (на торсе Датча — 22 тыс. рёбер). Сперва T-стыки, потом дырки;
- `meshsolid.py` пересобирает тело вокселями — покраску теряет целиком.

Порядок: сперва этот инструмент, потом `meshdoctor.py` на результат. Если
после него остались дырки — они настоящие, их закрывает `weldmesh.py`.

Грабли. Грань, дроблёная кистью на подтреугольники (длинный код вида
`4444844344343`), несёт дерево разбиения внутри себя. Попав под веер, она
отдаёт потомкам ПОЛНУЮ копию своего кода — рисунок внутри неё дублируется.
Инструмент считает такие грани и говорит их суммарную площадь: на robbie
albert под веер попали 10 таких граней общей площадью 0.08 мм² из 19 459 —
тысячные доли процента поверхности. Если цифра окажется крупной, сперва
`paint.py explode`: он разложит кисть на одноцветные подтреугольники (и сам
наделает T-стыков, которые расшивает этот же инструмент).
"""
import argparse, re, shutil, sys, zipfile
import numpy as np

VERT = re.compile(r'<vertex x="([-\d.eE+]+)" y="([-\d.eE+]+)" z="([-\d.eE+]+)"')
TRI = re.compile(r'<triangle v1="(\d+)" v2="(\d+)" v3="(\d+)"((?:\s+[\w:]+="[^"]*")*)\s*/>')
PAINT = re.compile(r'paint_color="([^"]*)"')
SIMPLE = {'', '4', '8', '0C', '1C', '2C', '3C', '4C', '5C', '6C', '7C'}


def mesh_members(zf):
    """Файлы .model с геометрией: 3dmodel.model обычно только оболочка."""
    out = [n for n in zf.namelist() if n.endswith('.model') and 'Objects/' in n]
    return out or [n for n in zf.namelist() if n.endswith('.model')]


def open_edges(faces):
    """-> (рёбра как пары индексов, номера записей). Запись k: грань k % n, слот k // n."""
    e = np.concatenate([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]])
    key = np.sort(e, axis=1)
    order = np.lexsort((key[:, 1], key[:, 0]))
    ks = key[order]
    first = np.ones(len(ks), bool)
    first[1:] = (ks[1:] != ks[:-1]).any(axis=1)
    grp = np.cumsum(first) - 1
    return e, order[np.bincount(grp)[grp] == 1]


def area(v, faces):
    return 0.5 * np.linalg.norm(np.cross(v[faces[:, 1]] - v[faces[:, 0]],
                                         v[faces[:, 2]] - v[faces[:, 0]]), axis=1)


def find_tjoints(v, faces, tol):
    """-> {грань: {слот: [(t, вершина)]}} для граней, на рёбрах которых сидит чужая вершина."""
    from scipy.spatial import cKDTree
    e, idx = open_edges(faces)
    print(f'  открытых рёбер: {len(idx)}')
    tree = cKDTree(v)
    n = len(faces)
    hits = {}
    for k in idx:
        ia, ib = e[k]
        a, b = v[ia], v[ib]
        ab = b - a
        l2 = float(ab @ ab)
        if l2 == 0.0:
            continue
        for j in tree.query_ball_point((a + b) / 2, np.sqrt(l2) / 2 + tol):
            if j == ia or j == ib:
                continue
            t = float((v[j] - a) @ ab) / l2
            if 0.0 < t < 1.0 and np.linalg.norm(v[j] - (a + t * ab)) < tol:
                hits.setdefault(int(k % n), {}).setdefault(int(k // n), []).append((t, int(j)))
    return hits


def fan(v, faces, attrs, hits):
    """Разбить помеченные грани веером из центроида. -> (новые вершины, грани, атрибуты)."""
    extra, tri, out_attr = [], [], []
    base = len(v)
    for f in range(len(faces)):
        if f not in hits:
            tri.append(tuple(faces[f]))
            out_attr.append(attrs[f])
            continue
        a, b, c = faces[f]
        centre = base + len(extra)
        extra.append((v[a] + v[b] + v[c]) / 3.0)
        ring = []
        for slot, (p, _q) in enumerate(((a, b), (b, c), (c, a))):
            ring.append(int(p))
            ring += [j for _t, j in sorted(hits[f].get(slot, []))]
        for i in range(len(ring)):
            tri.append((ring[i], ring[(i + 1) % len(ring)], centre))
            out_attr.append(attrs[f])
    return np.array(extra, dtype=np.float64).reshape(-1, 3), np.array(tri, dtype=np.int64), out_attr


def process(xml, tol, dry):
    v = np.array(VERT.findall(xml), dtype=np.float64)
    raw = TRI.findall(xml)
    if not len(v) or not raw:
        return None
    faces = np.array([(int(a), int(b), int(c)) for a, b, c, _ in raw], dtype=np.int64)
    attrs = [x for *_, x in raw]
    print(f'  вход: вершин {len(v)}, граней {len(faces)}')

    hits = find_tjoints(v, faces, tol)
    count = sum(len(s) for d in hits.values() for s in d.values())
    print(f'  граней с T-стыком: {len(hits)}, вставленных вершин: {count}')
    if not hits:
        print('  T-стыков нет — этот инструмент тут ни при чём')
        return None

    split = [f for f in hits if (PAINT.search(attrs[f]).group(1).upper() if PAINT.search(attrs[f]) else '') not in SIMPLE]
    if split:
        a = area(v, faces[split]).sum()
        print(f'  ВНИМАНИЕ: под веер попало {len(split)} граней, дроблёных кистью '
              f'({a:.2f} мм² = {100 * a / area(v, faces).sum():.3f} % поверхности) — '
              f'их рисунок продублируется в потомках')

    extra, tri, out_attr = fan(v, faces, attrs, hits)
    v2 = np.vstack([v, extra]) if len(extra) else v
    left = len(open_edges(tri)[1])
    print(f'  выход: вершин {len(v2)}, граней {len(tri)}, открытых рёбер осталось: {left}')
    if dry:
        return None

    head = xml[:xml.index('<vertices>')]
    tail = xml[xml.index('</triangles>'):]
    body = [head, '<vertices>\n',
            ''.join(f'<vertex x="{x:.6f}" y="{y:.6f}" z="{z:.6f}"/>\n' for x, y, z in v2),
            '</vertices>\n<triangles>\n',
            ''.join(f'<triangle v1="{a}" v2="{b}" v3="{c}"{at}/>\n'
                    for (a, b, c), at in zip(tri, out_attr)),
            tail]
    return ''.join(body), len(tri)


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('src')
    p.add_argument('dst', nargs='?', help='не нужен с --dry')
    p.add_argument('--tol', type=float, default=1e-3,
                   help='допуск «вершина лежит на ребре», в единицах файла (по умолчанию 0.001)')
    p.add_argument('--dry', action='store_true', help='только диагноз, файл не писать')
    a = p.parse_args()
    if not a.dry and not a.dst:
        sys.exit('нужен выходной файл (или --dry)')

    zin = zipfile.ZipFile(a.src)
    patched, faces = {}, {}
    for name in mesh_members(zin):
        print(name)
        res = process(zin.read(name).decode('utf-8'), a.tol, a.dry)
        if res:
            patched[name], faces[name] = res
    if a.dry:
        return
    if not patched:
        sys.exit('чинить нечего — файл не тронут')

    total = sum(faces.values())
    tmp = a.dst + '.tmp'
    with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename in patched:
                data = patched[item.filename].encode('utf-8')
            elif item.filename == 'Metadata/model_settings.config' and len(faces) == 1:
                data = re.sub(rb'face_count="\d+"', f'face_count="{total}"'.encode(), data)
            zout.writestr(item, data)
    shutil.move(tmp, a.dst)
    print(f'{a.dst}: граней {total}')


if __name__ == '__main__':
    main()
