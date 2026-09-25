#!/usr/bin/env -S uv run --quiet --with numpy --with trimesh --script
# /// script
# dependencies = ["numpy", "trimesh"]
# ///
"""Build the regression fixtures. Deterministic: same bytes on every run.

    uv run --with numpy --with trimesh python tests/fixtures.py <outdir>

Real models cannot ship with the repository, so the fixtures are generated to
exercise the same code paths: an organic shape with overhangs, a mesh carrying
one defect of each class, several bodies in one file, and painted projects.
The numbers each one comes out at are printed on build and asserted by the
cases in tests/README.md.
"""
import sys, pathlib
import numpy as np
import trimesh


def ball():
    """Clean closed sphere: the baseline every mesh tool must agree on."""
    m = trimesh.creation.icosphere(subdivisions=3, radius=15.0)
    m.apply_translation([0, 0, 15.0])
    return m


def figurine():
    """Organic shape with a real overhang: body, head and a jutting arm.

    figcheck and the support logic need something that is not a sphere -
    a horizontal arm is what puts area under an overhang."""
    body = trimesh.creation.capsule(height=26.0, radius=9.0, count=[16, 24])
    head = trimesh.creation.icosphere(subdivisions=2, radius=7.0)
    head.apply_translation([0, 0, 40.0])
    arm = trimesh.creation.cylinder(radius=2.6, height=18.0, sections=20)
    arm.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, [0, 1, 0]))
    arm.apply_translation([9.0, 0, 30.0])
    m = trimesh.util.concatenate([body, head, arm])
    m.merge_vertices()
    m.apply_translation([0, 0, -m.bounds[0][2]])
    return m


def broken():
    """One defect of each class meshdoctor reports, on a single mesh.

    Built by damaging a sphere on purpose: faces removed leave holes, a face
    reversed leaves a wrong normal, a detached tetrahedron is a junk island."""
    m = ball()
    f = m.faces.copy()
    keep = np.ones(len(f), bool)
    keep[[10, 11, 12]] = False              # three holes
    f = f[keep]
    f[5] = f[5][::-1]                       # one reversed face
    v = m.vertices.copy()
    junk = trimesh.creation.icosphere(subdivisions=0, radius=0.35)
    junk.apply_translation([26.0, 0, 15.0])  # island far from the body
    nv = len(v)
    v = np.vstack([v, junk.vertices])
    f = np.vstack([f, junk.faces + nv])
    return trimesh.Trimesh(vertices=v, faces=f, process=False)


def two_bodies():
    """Two separate closed shells: genus is only right when shells are counted."""
    a = trimesh.creation.icosphere(subdivisions=2, radius=8.0)
    a.apply_translation([-12.0, 0, 8.0])
    b = trimesh.creation.icosphere(subdivisions=2, radius=8.0)
    b.apply_translation([12.0, 0, 8.0])
    return trimesh.util.concatenate([a, b])


def torus():
    """One through hole: genus 1, and a shape whose inside rays must not lie."""
    m = trimesh.creation.torus(major_radius=12.0, minor_radius=4.0,
                               major_sections=48, minor_sections=24)
    m.apply_translation([0, 0, 4.0])
    return m


def main(out):
    out = pathlib.Path(out)
    out.mkdir(parents=True, exist_ok=True)
    made = []
    for name, fn in (("ball", ball), ("figurine", figurine), ("broken", broken),
                     ("two_bodies", two_bodies), ("torus", torus)):
        m = fn()
        p = out / f"{name}.stl"
        m.export(p)
        made.append((name, len(m.faces), float(m.volume)))

    # Paint zones, one array per mesh that gets painted.
    m = ball()
    np.save(out / "ball_zones.npy",
            np.where(m.triangles_center[:, 2] > 15.0, 2, 1).astype(np.int32))
    m = figurine()
    z = m.triangles_center[:, 2]
    np.save(out / "figurine_zones.npy",
            np.where(z > 33.0, 2, np.where(z > 12.0, 1, 3)).astype(np.int32))

    for name, nf, vol in made:
        print(f"  {name:<11} {nf:>6} граней  {vol:>10.1f} мм3")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "tests/.fixtures"))
