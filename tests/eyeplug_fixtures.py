#!/usr/bin/env python3
"""Fixtures for eyeplug: heads with an eye socket of known size.

    uv run --with numpy --with trimesh --with manifold3d python tests/eyeplug_fixtures.py <outdir>

head40.stl  sphere R 15 with a cylindrical socket R 4.00 parallel to AXIS, 2 mm off
            the centre (so the mouth is a slanted saddle), floor
            13 mm from the centre (mouth 1.457..2.0 deep: the sphere's curve)
head35.stl  the same with a socket R 3.50
painted.3mf sphere R 12 with a cap within 18 degrees of -Y painted with
            filament 2: an eye that is only painted on, outline R 3.708
"""
import sys, pathlib, zipfile
import numpy as np
import manifold3d as m3
import trimesh

AXIS = np.array([1.0, 0.3, 0.2]) / np.linalg.norm([1.0, 0.3, 0.2])


def rot_to(ax):
    z = np.array([0, 0, 1.0])
    v = np.cross(z, ax); s = np.linalg.norm(v); c = z @ ax
    K = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]]) / s
    return np.eye(3) + s * K + (1 - c) * K @ K


def head(R):
    ball = m3.Manifold.sphere(15.0, 256)
    cyl = m3.Manifold.cylinder(8.0, R, R, 512).translate((2.0, 0, 13.0))
    M = np.c_[rot_to(AXIS), np.zeros(3)]
    g = (ball - cyl.transform(M)).to_mesh()
    return trimesh.Trimesh(np.asarray(g.vert_properties)[:, :3], np.asarray(g.tri_verts), process=False)


def painted(path):
    m = trimesh.creation.icosphere(subdivisions=6, radius=12.0)
    C = m.triangles_center / 12.0
    eye = C @ np.array([0, -1.0, 0]) > np.cos(np.radians(18))
    vs = ''.join(f'<vertex x="{x:.6f}" y="{y:.6f}" z="{z:.6f}"/>' for x, y, z in m.vertices)
    ts = ''.join(f'<triangle v1="{a}" v2="{b}" v3="{c}"{" paint_color=\"8\"" if e else ""}/>'
                 for (a, b, c), e in zip(m.faces, eye))
    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n<model unit="millimeter" '
           'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
           f'<resources><object id="1" type="model"><mesh><vertices>{vs}</vertices>'
           f'<triangles>{ts}</triangles></mesh></object></resources>'
           '<build><item objectid="1"/></build></model>')
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('3D/3dmodel.model', xml)


def main(out):
    out = pathlib.Path(out); out.mkdir(parents=True, exist_ok=True)
    for R, nm in ((4.0, 'head40.stl'), (3.5, 'head35.stl')):
        h = head(R)
        h.export(out / nm)
        print(nm, 'граней', len(h.faces), 'замкнута', h.is_watertight)
    painted(out / 'painted.3mf')
    print('painted.3mf')


if __name__ == '__main__':
    main(sys.argv[1])
