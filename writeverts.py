"""Write changed vertex coordinates back into a 3MF, keeping the paint.

The only way to fix the geometry of someone else's project without losing
colour: `paint_color` is bound to the triangle's INDEX, so vertices may be
moved, but their number and order may not. Mesh repair (`meshfix.py --put`)
breaks the numbering and needs a paint transfer; here the paint stays put.

    uv run --with numpy python3 tools/writeverts.py source.3mf edit.npz new.3mf

Three arrays in the npz:
    V      (N,3) float — new coordinates of ALL vertices, in file order
    moved  (N,)  bool  — which of them to overwrite
    entry  str         — path inside the archive, usually 3D/3dmodel.model
                         or 3D/Objects/object_1.model

Lines of untouched vertices are carried over verbatim; the order of archive
entries and the compression method are kept, so the diff stays where geometry moved.

Verified 2026-09-19 on a triple rail junction (4926 vertices, 9852 faces):
with moved=False the XML comes out byte-identical to the source; with
moved=True on every vertex the coordinates match exactly (0 mm), and a 1 mm
shift reproduces to 2.8e-14 mm; face order and archive contents never change.

`<vertex .../>` is parsed by regex against the format Bambu Studio writes.
If the number of vertices found does not match the npz, the script dies on
an assert — there will be no silently mangled file.
"""
import re, shutil, sys, zipfile
import numpy as np

src, npz, dst = sys.argv[1], sys.argv[2], sys.argv[3]
d = np.load(npz, allow_pickle=True)
V, moved = d['V'], d['moved']
ent = str(d['entry'])

z = zipfile.ZipFile(src)
raw = z.read(ent).decode('utf-8')
VERT = re.compile(r'<vertex x="([^"]*)" y="([^"]*)" z="([^"]*)"\s*/>')

out, pos, i, changed = [], 0, 0, 0
for m in VERT.finditer(raw):
    if moved[i]:
        out.append(raw[pos:m.start()])
        x, y, zz = V[i]
        out.append(f'<vertex x="{x:.9g}" y="{y:.9g}" z="{zz:.9g}"/>')
        pos = m.end(); changed += 1
    i += 1
out.append(raw[pos:])
assert i == len(V), (i, len(V))
new = ''.join(out).encode('utf-8')

with zipfile.ZipFile(dst, 'w') as w:
    for it in z.infolist():
        data = new if it.filename == ent else z.read(it.filename)
        zi = zipfile.ZipInfo(it.filename, date_time=it.date_time)
        zi.compress_type = it.compress_type
        zi.external_attr = it.external_attr
        w.writestr(zi, data)
print(f'{ent}: вершин всего {len(V)}, переписано {changed}, дословно {len(V)-changed}')
