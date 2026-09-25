#!/usr/bin/env python3
"""Put a 3MF object at the centre of the bed and drop it onto the plate.

Why: in a project that went through a third-party export the offset in
`<build><item transform=...>` is reset, the object ends up off the bed, and
`--slice` answers "Nothing to be sliced, either the print is empty or no
object is fully inside the print volume". A placement problem, not a mesh
one: `meshdoctor` and `--info` are clean on such a file.

Only the last three numbers of the matrix (the offset) change — in
`3D/3dmodel.model` and `Metadata/model_settings.config`. Rotation and scale stay.

    python3 tools/place3mf.py in.3mf out.3mf --bed 256 256
"""
import argparse, re, zipfile
import numpy as np

ap = argparse.ArgumentParser()
ap.add_argument('src'); ap.add_argument('dst')
ap.add_argument('--bed', nargs=2, type=float, default=[256.0, 256.0])
a = ap.parse_args()

with zipfile.ZipFile(a.src) as z:
    items = z.infolist()
    data = {it.filename: z.read(it.filename) for it in items}

root = data['3D/3dmodel.model'].decode('utf-8')
m = re.search(r'<item[^>]*transform="([^"]+)"', root)
T = [float(x) for x in m.group(1).split()]
M = np.array(T[:9]).reshape(3, 3)

mesh = next(k for k in data if k.endswith('.model') and b'<vertex' in data[k][:200000])
V = np.array(re.findall(r'<vertex x="([^"]*)" y="([^"]*)" z="([^"]*)"\s*/>',
                        data[mesh].decode('utf-8')), dtype=np.float64)
V = V[np.isfinite(V).all(1)]        # exports sometimes leave inf/nan in vertices
W = V @ M
lo, hi = W.min(0), W.max(0)
new = [a.bed[0]/2 - (lo[0]+hi[0])/2, a.bed[1]/2 - (lo[1]+hi[1])/2, -lo[2]]
print(f'габарит {np.round(hi-lo,3)} мм; сдвиг {np.round(T[9:],3)} -> {np.round(new,3)}')

old_s = ' '.join(f'{x:.9g}' for x in T)
new_s = ' '.join(f'{x:.9g}' for x in T[:9] + new)
for k in ('3D/3dmodel.model', 'Metadata/model_settings.config'):
    if k in data:
        txt = data[k].decode('utf-8')
        n = 0
        for mm in re.finditer(r'transform="([^"]+)"', txt):
            vals = [float(x) for x in mm.group(1).split()]
            if len(vals) == 12 and np.allclose(vals[:9], T[:9], rtol=1e-6):
                txt = txt.replace(mm.group(0), f'transform="{new_s}"'); n += 1
        data[k] = txt.encode('utf-8')
        print(f'  {k}: поправлено матриц {n}')

with zipfile.ZipFile(a.dst, 'w', zipfile.ZIP_DEFLATED) as zo:
    for it in items:
        zo.writestr(it, data[it.filename])
print('записано', a.dst)
