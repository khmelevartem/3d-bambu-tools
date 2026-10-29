"""Socket-and-insert pairs for fitgap.py, in the assembly pose. Deterministic.

    uv run --with numpy --with trimesh --with manifold3d python tests/fitgap_fixtures.py <outdir>

peg / peg_socket    a pin r 3.0 from z 4.2 to 16 in a block 20x20x12 with a
                    blind hole r 3.2 from z 4 up: 0.20 per side on the wall,
                    0.20 under the bottom. Pin and hole share one tessellation
                    (96 segments, rings every 0.4 mm), so the gap is the same
                    at every face.
dome / dome_socket  a ball r 5.0 centred on the top face of a block, in a
                    hemispherical pocket r 5.2: a dome resting in a cup.
peg_zones.npy       one filament per face of the pin, for a painted project.
"""
import sys, pathlib
import numpy as np
import trimesh
import manifold3d as mf


def ringed_cylinder(r, z0, z1, seg=96, step=0.4):
    """A closed cylinder along z with a ring of vertices every `step` mm."""
    nz = max(1, int(round((z1 - z0) / step)))
    zs = np.linspace(z0, z1, nz + 1)
    a = np.linspace(0, 2 * np.pi, seg, endpoint=False)
    ring = np.c_[r * np.cos(a), r * np.sin(a)]
    V = [np.c_[ring, np.full(seg, z)] for z in zs]
    V = np.vstack(V + [[[0, 0, z0]], [[0, 0, z1]]])
    b0, b1 = len(V) - 2, len(V) - 1
    F = []
    for k in range(nz):
        for i in range(seg):
            j = (i + 1) % seg
            p, q = k * seg, (k + 1) * seg
            F += [[p + i, p + j, q + j], [p + i, q + j, q + i]]
    top = nz * seg
    for i in range(seg):
        j = (i + 1) % seg
        F += [[b0, j, i], [b1, top + i, top + j]]
    return trimesh.Trimesh(V, np.array(F), process=False)


def mfm(m):
    return mf.Manifold(mf.Mesh(vert_properties=np.asarray(m.vertices, np.float32),
                               tri_verts=np.asarray(m.faces, np.uint32)))


def back(m):
    mm = m.to_mesh()
    t = trimesh.Trimesh(np.asarray(mm.vert_properties[:, :3], float), np.asarray(mm.tri_verts))
    t.merge_vertices()
    return t


def block(lo, hi):
    return mf.Manifold.cube(np.subtract(hi, lo).tolist()).translate(list(lo))


def main(out):
    out = pathlib.Path(out)
    out.mkdir(parents=True, exist_ok=True)

    peg = ringed_cylinder(3.0, 4.2, 16.0)
    assert peg.is_watertight and peg.volume > 0
    peg.export(out / "peg.stl")
    np.save(out / "peg_zones.npy", np.ones(len(peg.faces), np.int64))
    hole = ringed_cylinder(3.2, 4.0, 13.0)
    sock = back(block((-10, -10, 0), (10, 10, 12)) - mfm(hole))
    sock.export(out / "peg_socket.stl")

    ball = trimesh.creation.icosphere(subdivisions=4, radius=5.0)
    ball.apply_translation([0, 0, 10])
    ball.export(out / "dome.stl")
    cup = trimesh.creation.icosphere(subdivisions=4, radius=5.2)
    cup.apply_translation([0, 0, 10])
    back(block((-10, -10, 0), (10, 10, 10)) - mfm(cup)).export(out / "dome_socket.stl")
    print("ok", out)


if __name__ == "__main__":
    main(sys.argv[1])
