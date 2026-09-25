#!/usr/bin/env python3
"""Weld mesh vertices in a 3MF and patch the seams - WITHOUT losing paint.

A mesh that came from GLB is torn along the UV-unwrap seams: vertices on a seam
have identical coordinates but different indices. Bambu Studio writes it into
the 3MF as is, and the slicer sees hundreds of separate bodies with tens of
thousands of "holes" on a shape that is in fact closed.

Why not `meshfix.py`: it repairs through Blender and changes the number and the
order of triangles, while `paint_color` is bound to a triangle's INDEX. Here
the order of existing faces does not change at all - only vertex indices are
rewritten. Patches for the remaining holes are appended at the END of the list
and inherit the colour of the neighbouring face across the edge.

    python3 tools/weldmesh.py in.3mf out.3mf
    python3 tools/weldmesh.py in.3mf out.3mf --round 5   # coarser welding
    python3 tools/weldmesh.py in.3mf out.3mf --no-fill   # weld only

Welding goes by exact coordinate match, rounded to `--round` decimals in file
units. Do not go coarser than 6: looser rounding starts producing degenerate
faces and extra non-manifold edges without closing any holes.

Afterwards run `meshdoctor.py` on the result: the body count must equal the
number of parts, and the volume must match the original.
"""
import argparse, re, shutil, sys, zipfile
import numpy as np

TRI_RE = re.compile(r'<triangle v1="(\d+)" v2="(\d+)" v3="(\d+)"((?:\s+[\w:]+="[^"]*")*)\s*/>')
VERT_RE = re.compile(r'<vertex x="([-\d.eE+]+)" y="([-\d.eE+]+)" z="([-\d.eE+]+)"')


def mesh_members(zf):
    """.model files that carry geometry: 3dmodel.model is usually only the shell."""
    out = [n for n in zf.namelist() if n.endswith('.model') and 'Objects/' in n]
    return out or [n for n in zf.namelist() if n.endswith('.model')]


def weld(xml, decimals, fill):
    v = np.array(VERT_RE.findall(xml), dtype=np.float64)
    tri_raw = TRI_RE.findall(xml)
    if not len(v) or not tri_raw:
        return None
    t = np.array([(a, b, c) for a, b, c, _ in tri_raw], dtype=np.int64)
    attrs = [x for *_, x in tri_raw]
    print(f'  вход: вершин {len(v)}, граней {len(t)}')

    uq, inv = np.unique(np.round(v, decimals), axis=0, return_inverse=True)
    t2 = inv[t]
    print(f'  сварка: вершин {len(uq)} (схлопнулось {len(v) - len(uq)})')

    e_dir = np.concatenate([t2[:, [0, 1]], t2[:, [1, 2]], t2[:, [2, 0]]])
    face_of = np.tile(np.arange(len(t2)), 3)
    ek = np.sort(e_dir, axis=1)
    order = np.lexsort((ek[:, 1], ek[:, 0]))
    eks = ek[order]
    first = np.ones(len(eks), bool)
    first[1:] = (eks[1:] != eks[:-1]).any(axis=1)
    grp = np.cumsum(first) - 1
    open_idx = order[np.bincount(grp)[grp] == 1]
    print(f'  открытых рёбер после сварки: {len(open_idx)}')

    if fill and len(open_idx):
        from scipy import sparse
        oe = e_dir[open_idx]
        g = sparse.coo_matrix((np.ones(len(oe)), (oe[:, 0], oe[:, 1])), shape=(len(uq),) * 2)
        _, lab = sparse.csgraph.connected_components(g, directed=False)
        centre, new_v = {}, []
        for lp in np.unique(lab[oe[:, 0]]):
            pts = np.unique(oe[lab[oe[:, 0]] == lp])
            centre[lp] = len(uq) + len(new_v)
            new_v.append(uq[pts].mean(axis=0))
        # a boundary face's edge runs a->b, so the patch takes it as b->a:
        # that way the patch normal points the same way as its neighbour's
        new_t = [(b, a, centre[lab[a]]) for a, b in oe]
        attrs = attrs + [attrs[face_of[i]] for i in open_idx]
        uq = np.vstack([uq, np.array(new_v)])
        t2 = np.vstack([t2, np.array(new_t, dtype=np.int64)])
        print(f'  заплаток: {len(new_t)} граней на {len(new_v)} дырках')

    head = xml[:xml.index('<vertices>')]
    tail = xml[xml.index('</triangles>'):]
    body = [head, '<vertices>\n',
            ''.join(f'<vertex x="{x:.6f}" y="{y:.6f}" z="{z:.6f}"/>\n' for x, y, z in uq),
            '</vertices>\n<triangles>\n',
            ''.join(f'<triangle v1="{a}" v2="{b}" v3="{c}"{at}/>\n'
                    for (a, b, c), at in zip(t2, attrs)),
            tail]
    return ''.join(body), len(t2)


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('src'); p.add_argument('dst')
    p.add_argument('--round', type=int, default=6, help='знаков округления при сварке (по умолчанию 6)')
    p.add_argument('--no-fill', action='store_true', help='не затыкать оставшиеся дырки')
    a = p.parse_args()

    zin = zipfile.ZipFile(a.src)
    targets = mesh_members(zin)
    patched, faces = {}, {}
    for name in targets:
        print(name)
        res = weld(zin.read(name).decode('utf-8'), a.round, not a.no_fill)
        if res:
            patched[name], faces[name] = res

    if not patched:
        sys.exit('геометрии в файле не нашлось')

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
