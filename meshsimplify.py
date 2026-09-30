#!/usr/bin/env python3
"""Thin a mesh down to a millimetre tolerance: fewer triangles, the same shape.

A mesh out of an image-to-3D generator carries one to three million triangles
with an edge around 0.15 mm. The printer lays a 0.42 mm strand in 0.20 mm
layers, so three to nine triangles fall inside a single extruded line and the
slicer averages them away. They cost file size, slicing time and the running
time of every tool in this repository, and they buy nothing in plastic.

**The target is a deviation in millimetres, not a percentage.** A ratio says
nothing about the object: 10 % of a smooth ball is free, 10 % of a face is a
lost nose. So the reduction is searched for: thin, measure how far the surface
moved, bisect, repeat.

**Thinning is not free, and the price is print time.** Bambu Studio fits arcs
(G2/G3) to a smooth contour, and an arc is printed at full speed; a contour
made of long straight chords is not recognised as an arc and the head slows at
every corner. Measured on a 105 mm figurine: at 400k faces the print grows by
0.6 %, at 120k by 3.4 %, at 58k by 5.7 %, while the plastic stays identical to
the hundredth of a gram. Hence the default tolerance - a twentieth of the layer
height from hardware.json - which lands around a tenth of the original face
count and costs one or two per cent. It also matches the slicer's own contour
tolerance: the stock A1 process carries resolution = 0.012 mm, so detail finer
than that is discarded inside Bambu Studio anyway. Push further only when the file size or
the running time of the other tools is what hurts.

What this does NOT do: it does not smooth. Quadric edge collapse keeps
curvature, including any bumps the generator put there. Generator meshes are in
fact smooth - the triangle count is tesselation, not noise - so there is
normally nothing to smooth. If a surface really is noisy, that is a separate
job and a separate filter.

    python3 tools/meshsimplify.py in.stl                    # report only, writes nothing
    python3 tools/meshsimplify.py in.stl -o out.stl         # tolerance = layer / 20
    python3 tools/meshsimplify.py in.stl -o out.stl --tol 0.02
    python3 tools/meshsimplify.py in.stl -o out.stl --faces 100000

Paint does not survive: a collapse renumbers every triangle. Thin the mesh
before painting it. For a project that is already painted, the route is
meshfix --extract, this tool, meshfix --put, then paint_transfer.py.
"""
import sys, os, time, argparse
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hardware
from meshdoctor import read_any, weld, SCALE_WARN, SCALE
from paint_transfer import point_tri_dist2


# ---------- deviation ----------

def sample_surface(V, F, n, rng):
    """n points spread over the mesh by area, so a big face is not under-sampled."""
    A, B, C = V[F[:, 0]], V[F[:, 1]], V[F[:, 2]]
    area = 0.5 * np.linalg.norm(np.cross(B - A, C - A), axis=1)
    tot = area.sum()
    if tot <= 0:
        return A.copy()
    pick = rng.choice(len(F), size=n, p=area / tot)
    u = rng.random(n)[:, None]
    v = rng.random(n)[:, None]
    flip = (u + v > 1).ravel()
    u[flip], v[flip] = 1 - u[flip], 1 - v[flip]
    return A[pick] + (B - A)[pick] * u + (C - A)[pick] * v


def tri_radius(V, F):
    """Longest centroid-to-vertex distance over the mesh - the slack a centroid
    index needs before it can be trusted."""
    A, B, C = V[F[:, 0]], V[F[:, 1]], V[F[:, 2]]
    cen = (A + B + C) / 3
    return float(max(np.linalg.norm(A - cen, axis=1).max(),
                     np.linalg.norm(B - cen, axis=1).max(),
                     np.linalg.norm(C - cen, axis=1).max()))


def dist_to_mesh(P, V, F, tree, k=16, refine_above=None, wide=128, chunk=200000):
    """Distance from each point to the nearest triangle, not to the nearest centroid.

        The k nearest centroids are only a first guess, and on a mesh whose
        triangles differ in size by two orders of magnitude - which a collapse
        always produces - the true nearest face is regularly not among them. The
        guess is therefore an upper bound, and without a second pass the rare
        large values are the error of the search rather than a loss of shape,
        so a tolerance read off them stops the thinning far too early.

        The refinement is in two steps, and both stay vectorised. Points above
        `refine_above` are redone against `wide` centroids at once; a point is
        then provably correct when the distance to the furthest of those
        centroids already exceeds d plus the largest centroid-to-vertex radius,
        because no centroid beyond it can carry a closer face. Only the few
        points that fail that test get an exact radius query. A per-point radius
        query for every suspect instead - the obvious way to write this - costs
        minutes once the mesh is coarse enough for a third of the samples to be
        suspects.
    """
    A, B, C = V[F[:, 0]], V[F[:, 1]], V[F[:, 2]]

    def probe(Q, kk):
        dc, cand = tree.query(Q, k=min(kk, len(F)))
        cand, dc = np.atleast_2d(cand), np.atleast_2d(dc)
        best = np.full(len(Q), np.inf)
        for j in range(cand.shape[1]):
            c = cand[:, j]
            best = np.minimum(best, point_tri_dist2(Q, A[c], B[c], C[c]))
        return np.sqrt(best), dc[:, -1]

    out = np.empty(len(P))
    for lo in range(0, len(P), chunk):
        hi = min(lo + chunk, len(P))
        out[lo:hi], _ = probe(P[lo:hi], k)

    if refine_above is None:
        return out
    slack = tri_radius(V, F)
    idx = np.nonzero(out > refine_above)[0]
    if not len(idx):
        return out
    d2, far = probe(P[idx], wide)
    out[idx] = np.minimum(out[idx], d2)
    left = idx[far < d2 + slack]            # the wide ring may still hide a closer face
    for i in left[:20000]:
        c = np.fromiter(tree.query_ball_point(P[i], out[i] + slack), dtype=np.int64)
        if len(c) == 0:
            continue
        Q = np.repeat(P[i][None, :], len(c), axis=0)
        out[i] = float(np.sqrt(point_tri_dist2(Q, A[c], B[c], C[c]).min()))
    return out


def deviation(V0, F0, V1, F1, nsample, rng, far_mm):
    """Two-sided: how far the new surface sits from the old one AND the old from the new.

        One side alone lies. Measuring only old->new misses a bulge the collapse
        invented in empty space; measuring only new->old misses a detail it ate.

        The verdict is taken from p99, not from the maximum. On a mesh with
        holes - and a generator mesh usually has them - a collapse closes some
        of them, the new skin has no counterpart in the old one, and the maximum
        reports the width of the hole rather than any loss of shape. p99 steps
        over that and still catches a feature that was eaten.
    """
    from scipy.spatial import cKDTree
    t0 = cKDTree((V0[F0[:, 0]] + V0[F0[:, 1]] + V0[F0[:, 2]]) / 3)
    t1 = cKDTree((V1[F1[:, 0]] + V1[F1[:, 1]] + V1[F1[:, 2]]) / 3)
    ref = far_mm / 8
    fwd = dist_to_mesh(sample_surface(V0, F0, nsample, rng), V1, F1, t1, refine_above=ref)
    bwd = dist_to_mesh(sample_surface(V1, F1, nsample, rng), V0, F0, t0, refine_above=ref)
    d = np.concatenate([fwd, bwd])
    return {"mean": float(d.mean()), "p99": float(np.percentile(d, 99)),
            "max": float(d.max()),
            "far": float((fwd > far_mm).mean() * 100),      # old surface that got eaten
            "new": float((bwd > far_mm).mean() * 100)}      # new skin over a closed hole


# ---------- the search ----------

def thin(V, F, target):
    import fast_simplification
    v2, f2 = fast_simplification.simplify(np.asarray(V, np.float32),
                                          np.asarray(F, np.int32),
                                          max(0.0, 1.0 - target / len(F)))
    return np.asarray(v2, np.float64), np.asarray(f2, np.int64)


FAR_SHARE = 0.01          # % of the surface allowed past one strand width


def search(V, F, tol, far_mm, nsample, rng, log, unit=1.0):
    """The fewest faces whose p99 deviation still fits the tolerance.

        Both tests have to pass: the surface within the tolerance, and
        essentially nothing past one strand width.

        Bisection on log(face count), not extrapolation from one probe: the
        deviation-versus-count curve bends where the collapse starts eating
        features, and an extrapolation overshoots exactly there - on the face,
        the hands, the lettering, which is where it must not.
    """
    lo, hi = 200, len(F)                       # lo: too coarse, hi: known to fit
    best = None
    for _ in range(7):
        mid = int(round((lo * hi) ** 0.5))
        if mid <= lo or mid >= hi:
            break
        Vt, Ft = thin(V, F, mid)
        d = deviation(V, F, Vt, Ft, nsample, rng, far_mm)
        fits = d["p99"] <= tol and d["far"] <= FAR_SHARE
        log(f"  проба {len(Ft):>9d} гр. -> 99 % в {d['p99'] * unit:.4f} мм, "
            f"дальше нитки {d['far']:.3f} %" + ("" if fits else "  — мимо"))
        if fits:
            hi = len(Ft)
            if best is None or len(Ft) < len(best[1]):
                best = (Vt, Ft, d)
        else:
            lo = max(lo + 1, len(Ft))
        if hi <= lo * 1.05:
            break
    return best


# ---------- output ----------

def write_stl(path, V, F):
    import struct
    A, B, C = V[F[:, 0]], V[F[:, 1]], V[F[:, 2]]
    nrm = np.cross(B - A, C - A)
    ln = np.linalg.norm(nrm, axis=1, keepdims=True)
    nrm = np.divide(nrm, ln, out=np.zeros_like(nrm), where=ln > 0)
    rec = np.zeros((len(F), 50), dtype=np.uint8)
    rec[:, :48] = np.hstack([nrm, A, B, C]).astype('<f4').view(np.uint8).reshape(len(F), 48)
    with open(path, 'wb') as f:
        f.write(b'meshsimplify'.ljust(80, b'\0'))
        f.write(struct.pack('<I', len(F)))
        f.write(rec.tobytes())


def volume(V, F):
    """Signed volume through the divergence theorem; meaningless on an open mesh,
    but its drift between before and after still shows whether the shape moved."""
    A, B, C = V[F[:, 0]], V[F[:, 1]], V[F[:, 2]]
    return float(np.einsum('ij,ij->i', A, np.cross(B, C)).sum() / 6)


def open_edges(F):
    e = np.sort(F[:, [0, 1, 1, 2, 2, 0]].reshape(-1, 2), axis=1)
    _, cnt = np.unique(e, axis=0, return_counts=True)
    return int((cnt == 1).sum())


def human(n):
    return f"{n:,}".replace(",", " ")


def plural(n, one, few, many):
    n = abs(int(n)) % 100
    if 11 <= n <= 14:
        return many
    n %= 10
    return one if n == 1 else few if 2 <= n <= 4 else many


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src", help="сетка: STL, 3MF, OBJ, OFF")
    ap.add_argument("-o", "--out", help="куда записать STL; без него — только отчёт")
    ap.add_argument("--tol", type=float, help="допуск на отклонение поверхности, мм (по умолчанию слой/20)")
    ap.add_argument("--faces", type=int, help="сразу столько граней, без подбора по допуску")
    ap.add_argument("--samples", type=int, default=60000, help="точек на замер отклонения (60000)")
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()

    meshes = read_any(a.src)
    if len(meshes) > 1:
        raise SystemExit(f"в файле {len(meshes)} сеток — сперва разложить их:\n"
                         f"    python3 tools/meshfix.py {a.src} --extract")
    label, V, F, indexed = meshes[0]
    for w in SCALE_WARN.values():
        print(w)

    # A 3MF mesh may live in its own units. Everything below is done in those
    # units and the tolerance is converted into them, so the numbers printed
    # stay millimetres and the mesh is never rescaled behind the caller's back.
    k = SCALE.get(label, 1.0) or 1.0
    # Only STL needs welding: it stores three loose vertices per triangle and
    # has no connectivity at all. In 3MF, OBJ and OFF the indices are the
    # author's, and welding there stitches separate sheets into edges that were
    # never in the file.
    if not indexed:
        V, F = weld(V, F, 1e-6 * float(np.ptp(V, axis=0).max()))
    ext = np.ptp(V, axis=0) * k
    A, B, C = V[F[:, 0]], V[F[:, 1]], V[F[:, 2]]
    edge = np.median(np.linalg.norm(np.concatenate([B - A, C - B, A - C]), axis=1)) * k
    lw, lh = hardware.line_width(), hardware.layer_height()
    tol = a.tol if a.tol else max(0.005, round(lh / 20, 4))

    print(f"исходник: {human(len(F))} {plural(len(F), 'грань', 'грани', 'граней')}, "
          f"{human(len(V))} {plural(len(V), 'вершина', 'вершины', 'вершин')}, "
          f"габарит {ext[0]:.1f} x {ext[1]:.1f} x {ext[2]:.1f} мм")
    if edge >= lw:
        print(f"  ребро медиана {edge:.3f} мм — сетка крупнее нитки {lw} мм "
              f"в {edge / lw:.1f} раза, прореживать нечего")
        if not a.out:
            print("\nничего не записано")
            return 0
    else:
        print(f"  ребро медиана {edge:.3f} мм — на нитку шириной {lw} мм приходится "
              f"{lw / edge:.1f} треугольника поперёк")
    print(f"допуск {tol:g} мм" + ("" if a.tol else f" (двадцатая доля слоя {lh})")
          + f", и не дальше нитки {lw} мм ни в одном месте")

    rng = np.random.default_rng(a.seed)
    t0 = time.time()
    if a.faces:
        Vn, Fn = thin(V, F, a.faces)
    else:
        got = search(V, F, tol / k, lw / k, min(a.samples, 20000), rng, print, k)
        if got is None:
            print(f"\nдопуск {tol:g} мм не достигается прореживанием — сетка уже на пределе, "
                  f"оставить как есть")
            return 1
        Vn, Fn, _ = got
    d = deviation(V, F, Vn, Fn, a.samples, np.random.default_rng(a.seed + 1), lw / k)
    for key in ("mean", "p99", "max"):
        d[key] *= k

    times = round(len(F) / len(Fn))
    print(f"\nрезультат: {human(len(Fn))} {plural(len(Fn), 'грань', 'грани', 'граней')} — "
          f"в {times} {plural(times, 'раз', 'раза', 'раз')} меньше, {time.time() - t0:.0f} c")
    print(f"  99 % поверхности уходит не дальше {d['p99']:.4f} мм, среднее {d['mean']:.4f} мм")
    print(f"  дальше нитки {lw} мм ушло {d['far']:.3f} % поверхности")
    if d["new"] > 0.01:
        print(f"  и {d['new']:.3f} % — новая перепонка там, где в исходнике была дыра")
    v0, v1 = volume(V, F) * k ** 3, volume(Vn, Fn) * k ** 3
    print(f"  объём {v1:.1f} мм³, было {v0:.1f}"
          + (f" — разница {abs(v1 - v0) / abs(v0) * 100:.2f} %" if v0 else ""))
    o0, o1 = open_edges(F), open_edges(Fn)
    print(f"  открытых рёбер было {human(o0)}, стало {human(o1)}"
          + (" — прореживание добавило дыр" if o1 > o0 else ""))
    if d["max"] > max(4 * d["p99"], lw / 2):
        why = ("это края дыр, а не потеря формы: схлопнутую дыру не с чем сравнить"
               if o0 else "исходная сетка замкнута, так что это съеденная мелочь — посмотреть глазами")
        print(f"  дальний хвост {d['max']:.2f} мм — {why}")
    if d["p99"] > lh:
        print(f"  !! это выше слоя {lh} мм — на поверхности уже видно, взять допуск меньше")

    print("  цена прореживания — время печати: слайсер хуже собирает дуги из длинных\n"
          "  хорд и тормозит на углах. Пластик не меняется, время растёт на проценты —\n"
          "  если это важно, сверить нарезкой до и после")

    if a.out:
        write_stl(a.out, Vn, Fn)
        print(f"\nзаписано {a.out} ({os.path.getsize(a.out) / 2**20:.1f} МБ "
              f"вместо {os.path.getsize(a.src) / 2**20:.1f} МБ)")
        if abs(k - 1) > 1e-6:
            print(f"  STL в единицах исходного файла, не в миллиметрах: он для "
                  f"meshfix.py --put обратно в тот же 3MF.\n"
                  f"  Отдельно в слайсер его нести нельзя — деталь выйдет в {k:.4g} раза мельче")
        print("покраска при прореживании не переносится — она привязана к нумерации граней")
    else:
        print("\nничего не записано: чтобы записать, добавить -o файл.stl")
    return 0


if __name__ == "__main__":
    sys.exit(main())
