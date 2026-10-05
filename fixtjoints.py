#!/usr/bin/env python3
"""Unstitch T-joints in a 3MF - WITHOUT losing the paint.

When a model is painted with the brush and the project saved, the slicer bakes
the strokes into the geometry: it splits triangles so that each comes out
single-coloured. The neighbouring triangle is not split, so a foreign vertex is
left on the shared edge - a T-joint. The shape is intact and the volume
correct, while connectivity tears: the slicer reports tens of thousands of open
edges and hundreds of bodies.

The fix: a face carrying a foreign vertex on its edge is split into a fan from
its own centroid. The centroid lies IN THE PLANE of the face, so the shape does
not change at all, and the children inherit the parent's `paint_color`.

    UV="uv run --quiet --with numpy --with scipy python"
    $UV tools/fixtjoints.py in.3mf out.3mf
    $UV tools/fixtjoints.py in.3mf --dry              # diagnosis only
    $UV tools/fixtjoints.py in.3mf out.3mf --tol 0.0001

Against the neighbouring tools:

- `weldmesh.py` welds vertices with identical coordinates (the UV-seam tear of
  meshes coming from GLB). On a T-joint the coordinates DIFFER, so welding
  finds nothing;
- `meshfix.py --holes` plugs a T-joint as if it were a hole and breeds
  non-manifold edges. T-joints first, holes second;
- `meshsolid.py` rebuilds the body with voxels and loses the paint entirely.

Each object of a `.model` is fanned on its own: objects index their vertices
from zero, and pooling them sends a cutter's faces into the main mesh.

Run this first, then `meshdoctor.py` on the result. Holes still left are real
ones, and `weldmesh.py` closes them.

A face already split by the brush carries its subdivision tree inside its own
code. Caught by the fan, it hands each child a FULL copy of that code, which
duplicates the pattern inside it. The tool counts such faces and reports their
total area; when that area is significant, run `paint.py explode` first.
"""
import argparse, re, shutil, sys, zipfile
import numpy as np

VERT = re.compile(r'<vertex x="([-\d.eE+]+)" y="([-\d.eE+]+)" z="([-\d.eE+]+)"')
TRI = re.compile(r'<triangle v1="(\d+)" v2="(\d+)" v3="(\d+)"((?:\s+[\w:]+="[^"]*")*)\s*/>')
MESH = re.compile(r'<mesh>.*?</mesh>', re.S)
PAINT = re.compile(r'paint_color="([^"]*)"')
SIMPLE = {'', '4', '8', '0C', '1C', '2C', '3C', '4C', '5C', '6C', '7C'}


def mesh_members(zf):
    """.model files that carry geometry: 3dmodel.model is usually only the shell."""
    out = [n for n in zf.namelist() if n.endswith('.model') and 'Objects/' in n]
    return out or [n for n in zf.namelist() if n.endswith('.model')]


def open_edges(faces):
    """-> (edges as index pairs, record numbers). Record k: face k % n, slot k // n."""
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
    """-> {face: {slot: [(t, vertex)]}} for faces with a foreign vertex sitting on an edge."""
    from scipy.spatial import cKDTree
    e, idx = open_edges(faces)
    print(f'  открытых рёбер: {len(idx)}')
    tree = cKDTree(v)
    n = len(faces)
    # a real T-vertex ends open edges of the other side of the seam; a vertex merely
    # passing near the edge does not, and fanning it in folds the surface
    on_open = np.zeros(len(v), bool)
    on_open[e[idx].ravel()] = True
    hits = {}
    for k in idx:
        ia, ib = e[k]
        own = set(faces[k % n].tolist())     # a sliver's own corner may lie on its own edge
        a, b = v[ia], v[ib]
        ab = b - a
        l2 = float(ab @ ab)
        if l2 == 0.0:
            continue
        for j in tree.query_ball_point((a + b) / 2, np.sqrt(l2) / 2 + tol):
            if j in own or not on_open[j]:
                continue
            t = float((v[j] - a) @ ab) / l2
            if 0.0 < t < 1.0 and np.linalg.norm(v[j] - (a + t * ab)) < tol:
                hits.setdefault(int(k % n), {}).setdefault(int(k // n), []).append((t, int(j)))
    return hits


def fan(v, faces, attrs, hits):
    """Fan the marked faces out from the centroid. -> (new vertices, faces, attributes)."""
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
    """Every <mesh> of the file on its own: a .model may carry several objects,
    each indexing its own vertices from zero. -> (new xml, {mesh number: faces}) or None."""
    meshes = list(MESH.finditer(xml))
    out, pos, counts = [], 0, {}
    for i, m in enumerate(meshes):
        if len(meshes) > 1:
            print(f'  сетка {i + 1} из {len(meshes)}')
        res = process_mesh(m.group(0), tol, dry)
        out.append(xml[pos:m.start()])
        out.append(res[0] if res else m.group(0))
        pos = m.end()
        if res:
            counts[i] = res[1]
    out.append(xml[pos:])
    return (''.join(out), counts) if counts else None


def process_mesh(xml, tol, dry):
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
    dup = len(tri) - len(np.unique(np.sort(tri, axis=1), axis=0))
    print(f'  выход: вершин {len(v2)}, граней {len(tri)}, открытых рёбер осталось: {left}, '
          f'дублей граней: {dup}')
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
    p.add_argument('--tol', type=float, default=1e-5,
                   help='допуск «вершина лежит на ребре», в единицах файла (по умолчанию 0.00001: '
                        'координаты пишутся с 6 знаками, настоящий T-стык лежит на ребре до округления; '
                        'допуск крупнее ловит соседние вершины и складывает веер в дубли)')
    p.add_argument('--dry', action='store_true', help='только диагноз, файл не писать')
    a = p.parse_args()
    if not a.dry and not a.dst:
        sys.exit('нужен выходной файл (или --dry)')

    zin = zipfile.ZipFile(a.src)
    patched, faces, meshes = {}, {}, 0
    for name in mesh_members(zin):
        print(name)
        xml = zin.read(name).decode('utf-8')
        meshes += len(MESH.findall(xml))
        res = process(xml, a.tol, a.dry)
        if res:
            patched[name], faces[name] = res[0], sum(res[1].values())
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
            elif item.filename == 'Metadata/model_settings.config' and meshes == 1:
                data = re.sub(rb'face_count="\d+"', f'face_count="{total}"'.encode(), data)
            zout.writestr(item, data)
    shutil.move(tmp, a.dst)
    print(f'{a.dst}: граней {total}')


if __name__ == '__main__':
    main()
