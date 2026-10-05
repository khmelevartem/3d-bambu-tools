"""Painted bodies for relief_cut.py. Deterministic.

    uv run --with numpy --with trimesh --with manifold3d python tests/relief_fixtures.py <outdir>

cap      a sphere r 15 whose top is painted with a wavy border - a hair cap
         removed upwards.
relief   a sphere with a moustache: two ellipsoids sunk into its front, their
         ends drooping away from the skin with air under them.
gauge    a plate with a tunnel: bore r 1.5 through it and a torus lip on the
         front face, the whole thing turned so that no axis is a coordinate one.

Each is written as an npz with V, F, lab - the shape `paint.py parse` gives -
and as a watertight STL of the same mesh.
"""
import sys, pathlib
import numpy as np
import trimesh
import manifold3d as mf


def mfm(m):
    return mf.Manifold(mf.Mesh(vert_properties=np.asarray(m.vertices, np.float32),
                               tri_verts=np.asarray(m.faces, np.uint32)))


def back(m):
    mm = m.to_mesh()
    t = trimesh.Trimesh(np.asarray(mm.vert_properties[:, :3], float), np.asarray(mm.tri_verts))
    t.merge_vertices()
    return t


def cap():
    m = trimesh.creation.icosphere(subdivisions=5, radius=15.0)
    c = m.triangles_center
    phi = np.arctan2(c[:, 1], c[:, 0])
    lab = np.where(c[:, 2] > 4.0 + 1.5 * np.sin(3 * phi), 3, 4)
    return m, lab


def relief():
    s = trimesh.creation.icosphere(subdivisions=5, radius=15.0)
    body = mfm(s)
    for sg in (-1, 1):
        e = trimesh.creation.icosphere(subdivisions=4, radius=1.0)
        e.apply_scale([6.0, 2.2, 2.2])
        R = trimesh.transformations.euler_matrix(0, sg * np.radians(22), sg * np.radians(16), 'sxyz')
        e.apply_transform(R)
        e.apply_translation([sg * 4.0, -14.0, -2.0])
        body = body + mfm(e)
    t = back(body)
    r = np.linalg.norm(t.vertices, axis=1)
    lab = np.where((r[t.faces] < 15.0005).all(1), 4, 3)
    return t, lab


def gauge():
    plate = mfm(trimesh.creation.box([18, 18, 4]))
    lip = trimesh.creation.torus(major_radius=2.3, minor_radius=0.8, major_sections=96, minor_sections=32)
    lip.apply_translation([0, 0, 2.6])
    hole = trimesh.creation.cylinder(radius=1.5, height=20, sections=96)
    t = back((plate + mfm(lip)) - mfm(hole))
    v = t.vertices; r = np.hypot(v[:, 0], v[:, 1])
    rf, zf = r[t.faces], v[:, 2][t.faces]
    lab = np.where((rf < 3.15).all(1) & ((zf > 1.99).all(1) | (rf < 1.55).all(1)), 1, 4)
    R = trimesh.transformations.rotation_matrix(np.radians(37), [0.6, 0.3, 0.74])
    R[:3, 3] = [5.0, -3.0, 10.0]
    t.apply_transform(R)
    return t, lab


def main(out):
    out = pathlib.Path(out)
    out.mkdir(parents=True, exist_ok=True)
    for name, fn in (("cap", cap), ("relief", relief), ("gauge", gauge)):
        m, lab = fn()
        assert m.is_watertight, name
        np.savez(out / f"{name}.npz", V=np.asarray(m.vertices), F=np.asarray(m.faces), lab=lab)
        m.export(out / f"{name}.stl")
        ar = m.area_faces
        print(f"  {name:<7} {len(m.faces):>6} граней  {m.volume:>9.1f} мм3  "
              + "  ".join(f"зона {k}: {ar[lab == k].sum():.1f} мм²" for k in sorted(set(lab.tolist()))))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
