#!/usr/bin/env python3
"""Fixtures for markercut: a painted sphere with a seam drawn by a marker filament.

    uv run --with numpy --with trimesh python tests/markercut_fixtures.py <outdir>

Sphere R 15 (icosphere, 20480 faces), a Bambu-style project with three
filaments: 1 the object's own, 2 painted on the half x > 0, 3 the marker.

band.3mf  a closed strip of filament 3 around the plane n.x = D, |n.x - D| < W/2.
          Faces just outside the strip are split by the brush: a quarter of each
          is marker, the rest keeps its colour.
gap.3mf   the same strip with the sector of azimuth 0..GAP_DEG left unpainted.
fill.3mf  the whole cap n.x > D flooded with filament 3.

The true seam is the circle n.x = D on the sphere: radius sqrt(R^2 - D^2).
"""
import json, sys, pathlib, zipfile
import numpy as np
import trimesh

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import patch3mf

R, D, W, GAP_DEG = 15.0, 4.0, 2.0, 20.0
N = np.array([0.3, 0.2, 0.93]) / np.linalg.norm([0.3, 0.2, 0.93])
MARKER = 3


def frame(n):
    e1 = np.cross([0.0, 0.0, 1.0], n); e1 /= np.linalg.norm(e1)
    return e1, np.cross(n, e1)


def leaf(s):
    return '' if s == 0 else patch3mf.paint_code(('leaf', s))


def project(path, V, F, codes):
    vs = ''.join(f'<vertex x="{x:.6f}" y="{y:.6f}" z="{z:.6f}"/>' for x, y, z in V)
    ts = ''.join(f'<triangle v1="{a}" v2="{b}" v3="{c}"'
                 + (f' paint_color="{k}"' if k else '') + '/>' for (a, b, c), k in zip(F, codes))
    ns = ('xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" '
          'xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/1015/06"')
    obj = (f'<?xml version="1.0" encoding="UTF-8"?>\n<model unit="millimeter" {ns}>'
           f'<resources><object id="1" type="model"><mesh><vertices>{vs}</vertices>'
           f'<triangles>{ts}</triangles></mesh></object></resources><build/></model>')
    top = (f'<?xml version="1.0" encoding="UTF-8"?>\n<model unit="millimeter" {ns}>\n'
           ' <resources>\n  <object id="2" type="model">\n   <components>\n'
           '    <component p:path="/3D/Objects/object_1.model" objectid="1" '
           'transform="1 0 0 0 1 0 0 0 1 0 0 0"/>\n   </components>\n  </object>\n </resources>\n'
           ' <build>\n  <item objectid="2" transform="1 0 0 0 1 0 0 0 1 128 128 15" printable="1"/>\n'
           ' </build>\n</model>\n')
    cfg = ('<?xml version="1.0" encoding="UTF-8"?>\n<config>\n  <object id="2">\n'
           '    <metadata key="name" value="ball"/>\n    <metadata key="extruder" value="1"/>\n'
           '    <part id="1" subtype="normal_part">\n      <metadata key="name" value="ball"/>\n'
           '    </part>\n  </object>\n  <plate>\n    <metadata key="plater_id" value="1"/>\n'
           '    <metadata key="filament_maps" value="1 1 1"/>\n  </plate>\n</config>\n')
    settings = {'filament_colour': ['#FFFFFF', '#C12E1F', '#00FF00'],
                'filament_type': ['PLA'] * 3,
                'filament_settings_id': ['Generic PLA'] * 3,
                'nozzle_temperature': ['220'] * 3,
                'different_settings_to_system': [''] * 5}
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('3D/3dmodel.model', top)
        z.writestr('3D/Objects/object_1.model', obj)
        z.writestr('Metadata/model_settings.config', cfg)
        z.writestr('Metadata/project_settings.config', json.dumps(settings, indent=4))


def main(out):
    out = pathlib.Path(out); out.mkdir(parents=True, exist_ok=True)
    m = trimesh.creation.icosphere(subdivisions=5, radius=R)
    V, F = np.asarray(m.vertices), np.asarray(m.faces)
    C = V[F].mean(1)
    h = C @ N - D
    e1, e2 = frame(N)
    az = np.degrees(np.arctan2(C @ e2, C @ e1)) % 360
    own = np.where(C[:, 0] > 0, 2, 0)
    base = [leaf(s) for s in own]

    def band(skip):
        codes = list(base)
        strip = (np.abs(h) < W / 2) & ~skip
        edge = (np.abs(h) >= W / 2) & (np.abs(h) < W / 2 + 0.35) & ~skip
        for i in np.flatnonzero(strip):
            codes[i] = leaf(MARKER)
        for i in np.flatnonzero(edge):
            s = int(own[i])
            codes[i] = patch3mf.paint_code(('node', 3, 0, [('leaf', MARKER), ('leaf', s),
                                                            ('leaf', s), ('leaf', s)]))
        return codes, int(strip.sum()), int(edge.sum())

    codes, ns, ne = band(np.zeros(len(F), bool))
    project(out / 'band.3mf', V, F, codes)
    print(f'band.3mf: полоса {ns} граней, дроблёных у края {ne}')
    codes, ns, _ = band(az < GAP_DEG)
    project(out / 'gap.3mf', V, F, codes)
    print(f'gap.3mf: полоса {ns} граней, разрыв азимут 0..{GAP_DEG}')
    codes = [leaf(MARKER) if hh > 0 else c for hh, c in zip(h, base)]
    project(out / 'fill.3mf', V, F, codes)
    print(f'fill.3mf: заливка {int((h > 0).sum())} граней')


if __name__ == '__main__':
    main(sys.argv[1])
