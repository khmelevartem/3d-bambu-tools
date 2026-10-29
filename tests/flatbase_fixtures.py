"""Fixtures for flatbase: a box with a wavy bottom, a flat one, and a painted
two-foot project in a non-trivial pose.

    uv run --with numpy --with trimesh python tests/flatbase_fixtures.py <dir>

wave.stl   30 x 20 x 8 box, bottom z = 0.3 + 0.3 sin(2 pi x / 10): amplitude 0.3
flat.stl   the same box with a flat bottom
feet.3mf   both boxes as two shells of ONE object (wavy left foot, flat right
           foot), every face painted (bottom of the wavy foot 1, of the flat
           foot 2, the rest 3); the component lifts the mesh by 0.5 and the
           build item turns it 90 degrees about Z and moves it to the bed centre
"""
import pathlib, sys, zipfile
import numpy as np
import trimesh

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import partedit

AMP, WAVE = 0.3, 10.0
WALL_ROWS = (0.5, 1.5, 3.0)       # wall vertex rows above the bottom, mm


def hbox(x0, x1, y0, y1, top, bottom, step=0.5):
    """A closed box whose bottom is the height field bottom(x, y)."""
    xs = np.linspace(x0, x1, int(round((x1 - x0) / step)) + 1)
    ys = np.linspace(y0, y1, int(round((y1 - y0) / step)) + 1)
    nx, ny = len(xs), len(ys)
    X, Y = np.meshgrid(xs, ys, indexing='ij')
    B = np.c_[X.ravel(), Y.ravel(), bottom(X, Y).ravel()]
    T = np.c_[X.ravel(), Y.ravel(), np.full(nx * ny, top)]
    V = np.vstack([B, T])
    idx = lambda i, j: i * ny + j
    F, kind = [], []
    for i in range(nx - 1):
        for j in range(ny - 1):
            a, b, c, d = idx(i, j), idx(i + 1, j), idx(i + 1, j + 1), idx(i, j + 1)
            F += [[a, c, b], [a, d, c]]; kind += [0, 0]
            o = nx * ny
            F += [[a + o, b + o, c + o], [a + o, c + o, d + o]]; kind += [1, 1]
    ring = ([idx(i, 0) for i in range(nx)] + [idx(nx - 1, j) for j in range(1, ny)]
            + [idx(i, ny - 1) for i in range(nx - 2, -1, -1)]
            + [idx(0, j) for j in range(ny - 2, 0, -1)])
    # The walls carry rows at fixed heights above the bottom, so a flatten has a
    # rim to fade out in: the row 0.5 above the bottom sits inside a 1 mm rim.
    ring = np.array(ring)
    rows = [ring]
    for h in WALL_ROWS:
        R = B[ring].copy(); R[:, 2] += h
        rows.append(np.arange(len(V), len(V) + len(ring)))
        V = np.vstack([V, R])
    rows.append(ring + nx * ny)
    k = len(ring)
    for lo, hi in zip(rows, rows[1:]):
        for s in range(k):
            p, q = lo[s], lo[(s + 1) % k]
            P, Q = hi[s], hi[(s + 1) % k]
            F += [[p, q, Q], [p, Q, P]]; kind += [1, 1]
    F = np.array(F, np.int64)
    m = trimesh.Trimesh(V, F, process=False)
    assert m.is_watertight
    if m.volume < 0:
        F = F[:, [0, 2, 1]]
    return V, F, np.array(kind)


def wave_bottom(X, Y):
    return AMP + AMP * np.sin(2 * np.pi * X / WAVE)


def flat_bottom(X, Y):
    return np.zeros_like(X)


def write_project(path, V, F, codes):
    obj = partedit.mesh_xml(V, F, 1, codes)
    root = ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<model unit="millimeter" xml:lang="en-US" '
            'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" '
            'xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06" '
            'requiredextensions="p">\n <resources>\n'
            '  <object id="2" type="model">\n   <components>\n'
            '    <component p:path="/3D/Objects/object_1.model" objectid="1" '
            'transform="1 0 0 0 1 0 0 0 1 0 0 0.5"/>\n'
            '   </components>\n  </object>\n </resources>\n <build>\n'
            '  <item objectid="2" transform="0 1 0 -1 0 0 0 0 1 128 100 0" printable="1"/>\n'
            ' </build>\n</model>\n')
    cfg = ('<?xml version="1.0" encoding="UTF-8"?>\n<config>\n  <object id="2">\n'
           '    <metadata key="name" value="feet"/>\n    <metadata key="extruder" value="3"/>\n'
           f'    <metadata face_count="{len(F)}"/>\n'
           '    <part id="1" subtype="normal_part">\n      <metadata key="name" value="feet"/>\n'
           '    </part>\n  </object>\n  <plate>\n    <metadata key="plater_id" value="1"/>\n'
           '    <model_instance>\n      <metadata key="object_id" value="2"/>\n'
           '      <metadata key="instance_id" value="0"/>\n    </model_instance>\n'
           '  </plate>\n</config>\n')
    ct = ('<?xml version="1.0" encoding="UTF-8"?>\n<Types xmlns="http://schemas.openxmlformats.org/'
          'package/2006/content-types"><Default Extension="rels" ContentType="application/'
          'vnd.openxmlformats-package.relationships+xml"/><Default Extension="model" '
          'ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/></Types>\n')
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml', ct)
        z.writestr('3D/3dmodel.model', root)
        z.writestr('3D/Objects/object_1.model', obj)
        z.writestr('Metadata/model_settings.config', cfg)


def main(out):
    out = pathlib.Path(out); out.mkdir(parents=True, exist_ok=True)
    Vw, Fw, kw = hbox(0, 30, 0, 20, 8, wave_bottom)
    Vf, Ff, kf = hbox(0, 30, 0, 20, 8, flat_bottom)
    trimesh.Trimesh(Vw, Fw, process=False).export(out / 'wave.stl')
    trimesh.Trimesh(Vf, Ff, process=False).export(out / 'flat.stl')
    Vf2 = Vf + [40, 0, 0]
    V = np.vstack([Vw, Vf2]); F = np.vstack([Fw, Ff + len(Vw)])
    paint = {1: '4', 2: '8', 3: '0C'}           # filament 1, 2, 3
    codes = [paint[1] if k == 0 else paint[3] for k in kw] + \
            [paint[2] if k == 0 else paint[3] for k in kf]
    write_project(out / 'feet.3mf', V, F, codes)
    print(f'{out}: wave.stl, flat.stl, feet.3mf ({len(F)} граней)')


if __name__ == '__main__':
    main(sys.argv[1])
