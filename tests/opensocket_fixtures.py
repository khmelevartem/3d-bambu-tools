"""Open bodies for opensocket.py. Deterministic.

    uv run --with numpy --with trimesh python tests/opensocket_fixtures.py <outdir>

open_ball   a sphere r 15 centred at (0,0,15), icosphere of 20480 faces, torn the
            way a generator tears it: one face cut out at the equator (a hole far
            from the socket) and a seam of duplicated vertices along the plane
            x = 0 - coincident but unconnected, both sides open - running right
            through the top where the socket goes. The copies sit 4 um apart, so an
            STL keeps them as separate vertices.
plug        an insert: a cylinder r 2 on the axis, from z 27 to z 31.5.
open_ball_zones.npy   filament per face: 2 on a cap of radius 2.5 around the top,
            3 on a patch at the side, 1 elsewhere.
"""
import sys, pathlib
import numpy as np
import trimesh


def open_ball():
    m = trimesh.creation.icosphere(subdivisions=5, radius=15.0)
    m.apply_translation([0, 0, 15.0])
    V, F = m.vertices.copy(), m.faces.copy()
    cen = V[F].mean(1)
    # the seam: faces on the x > 0 side get their own copies of the shared vertices
    east = cen[:, 0] > 0
    shared = np.intersect1d(np.unique(F[east]), np.unique(F[~east]))
    dup = {int(v): len(V) + k for k, v in enumerate(shared)}
    V = np.vstack([V, V[shared] + [4e-6, 0, 0]])     # 4 um apart: STL keeps them apart
    Fe = F[east]
    F[east] = np.vectorize(lambda v: dup.get(int(v), int(v)))(Fe)
    # a hole far from the socket: the face nearest (15, 0, 15) is cut out
    hole = int(np.argmin(np.linalg.norm(cen - [15.0, 0, 15.0], axis=1)))
    F = np.delete(F, hole, axis=0)
    return trimesh.Trimesh(V, F, process=False)


def main(out):
    out = pathlib.Path(out)
    out.mkdir(parents=True, exist_ok=True)
    m = open_ball()
    m.export(out / "open_ball.stl")
    c = m.triangles_center
    r = np.hypot(c[:, 0], c[:, 1])
    z = np.where((r < 2.5) & (c[:, 2] > 25), 2, 1)
    z[np.linalg.norm(c - [0, -15.0, 15.0], axis=1) < 2.0] = 3
    np.save(out / "open_ball_zones.npy", z.astype(np.int32))
    p = trimesh.creation.cylinder(radius=2.0, height=4.5, sections=96)
    p.apply_translation([0, 0, 29.25])
    p.export(out / "plug.stl")
    print(f"open_ball: {len(m.faces)} граней, {len(m.vertices)} вершин")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
