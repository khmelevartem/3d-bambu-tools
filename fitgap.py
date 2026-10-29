#!/usr/bin/env python3
"""Measure and set the clearance between a socket and the insert or pin in it.

The clearance a fit needs depends on how its walls are printed, not on the
part. **A wall that stands vertical in the print pose and is laid in thin
layers comes out true, and 0.05 mm per side is enough. A wall at an angle to
the layer, a dome, a plug printed on its edge - all come out as a staircase,
and need 0.15.** In between, 0.10. The insert and the socket are printed
separately, each in its own pose, so each zone takes the worse of the two.

    UV="uv run --quiet --with numpy --with scipy --with trimesh --with rtree python"
    $UV tools/fitgap.py measure peg.stl body.stl --up-insert 1,0,0
    $UV tools/fitgap.py measure peg.stl body.stl \
        --print-insert project.3mf:Peg --print-socket project.3mf:Body
    $UV tools/fitgap.py apply   peg.stl body.stl --move insert --out peg_fit.stl
    $UV tools/fitgap.py apply   peg.stl body.stl --move insert \
        --put project.3mf:Peg --out project_fit.3mf

Both parts are STL files **in the assembly pose**: the insert sits in the
socket the way it will after gluing. argparse takes a negative vector only
through `=`: `--up-insert=-1,0,0`.

The print pose
--------------
Only the up direction of the print matters - a turn about the vertical does
not change a wall's angle to the layer. It is given per part, in the
coordinates of that part's STL:

* `--up-insert X,Y,Z`, `--up-socket X,Y,Z` - by hand. Without anything, +Z:
  the part is printed the way it stands in the assembly.
* `--print-insert project.3mf:Object` - from a Bambu Studio project: the
  rotation of the object's build item and its component. The mesh inside the
  project must be the STL itself, moved but not turned - which is what Bambu
  Studio does on import, keeping every turn in the transform. The extents are
  compared, and a mismatch stops the run.

What is measured
----------------
The contact is the faces of one part within `--near` of the other part's
surface and facing it (normals opposed by more than 60 degrees). The gap at a
face is the signed distance from its centre to the other surface: positive is
air, negative is interference - the part sits inside the other's material.

The insertion axis is the area-weighted sum of the insert's contact normals
(the bottom and a dome add up, side walls cancel); when it is too short - a
through pin with no bottom contact - the direction of least normal spread.
`--axis` overrides both.

Faces whose normal is within `--bottom-deg` (20) of the axis are the bottom. Its
gap is measured along the axis with a ray and has its own target `--bottom`:
it is the travel left before the insert bottoms out, not the fit. All other
contact faces are walls, classed by their angle to the layer plane in the
print pose of each part (90 degrees = vertical):

    >= --vertical-deg (75)   vertical      target --vertical (0.05)
    <= --slope-deg    (45)   slope         target --slope    (0.15)
    otherwise                intermediate  target --mid      (0.10)

The clearance of the pair is the target of the most demanding wall zone that
holds at least `--zone-min` (10) per cent of the wall area: a plug jams at its
tightest zone, and a small chamfer does not decide the fit. `apply` does not
use this single number - it sets every zone to its own target.

Interference is reported as area, depth and the volume it adds up to
(depth times area), without a boolean.

Apply
-----
`apply` moves the walls of ONE part (`--move insert` or `--move socket`) along
their normal until each zone is at its target, and its bottom along the axis
until the bottom gap is `--bottom` (`--keep-bottom` leaves it). Each vertex
takes the least-squares shift that satisfies all contact faces around it, so a
rim between wall and bottom moves both ways at once without overshoot.
Measure and move repeat `--iter` times: near an edge the gap is not linear in
the shift.

**Faces and vertices are neither added nor renumbered**, so paint bound to the
face index survives. `--out x.stl` writes the moved mesh; `--put
project.3mf:Object --out new.3mf` writes the moved vertices back into that
object of the project through writeverts.py, the rest of the archive byte for
byte. The object's mesh must be the same mesh as the STL (same face count and
order), moved and turned rigidly; anything else is refused.

Only contact vertices move. Faces leaving the socket at its mouth stretch over
one row to the unmoved surface - the report gives how far the mouth edge went.
"""
import argparse, pathlib, re, subprocess, sys, tempfile, zipfile

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
FACING = -0.5            # cos 120 deg: normals opposed enough to be a mating pair
SQUARE = 0.95           # cos 18 deg: the gap vector along the other face's normal
AXIS_MIN = 0.15          # |sum n dA| / sum dA below this: no bottom to find the axis by
CLASS_NAMES = ("вертикальные", "промежуточные", "наклонные")


# ------------------------------------------------------------------ input

def vec(s):
    v = np.array([float(x) for x in s.split(",")], float)
    if v.shape != (3,) or np.linalg.norm(v) == 0:
        raise SystemExit(f"вектор из трёх чисел, а не «{s}»")
    return v / np.linalg.norm(v)


def load_stl(path):
    import trimesh
    m = trimesh.load(path, force="mesh")
    if not len(m.faces):
        raise SystemExit(f"{path}: пустая сетка")
    return m


def split_ref(ref):
    if ":" not in ref or not ref.split(":", 1)[0].lower().endswith(".3mf"):
        raise SystemExit(f"нужно «проект.3mf:Имя объекта», а не «{ref}»")
    return ref.split(":", 1)


def mat3mf(s):
    """3MF transform: 12 numbers, rows of a 4x3 applied to row vectors."""
    a = np.array(s.split(), float)
    return a[:9].reshape(3, 3), a[9:12]


def project_object(ref):
    """(local vertices, faces, entry, rotation local->bed as a row matrix, src)."""
    sys.path.insert(0, str(HERE))
    from partedit import Project
    src, name = split_ref(ref)
    p = Project(src)
    try:
        oid = p.object_id(name)
    except KeyError:
        names = re.findall(r'<object id="\d+">\s*<metadata key="name" value="([^"]*)"', p.cfg)
        raise SystemExit(f"в {src} нет объекта «{name}». Есть: {', '.join(names)}")
    ent = p.entry_of(oid)
    raw = p.files[ent].decode()
    V = np.array(re.findall(r'<vertex x="([^"]*)" y="([^"]*)" z="([^"]*)"', raw), float)
    F = np.array(re.findall(r'<triangle v1="(\d+)" v2="(\d+)" v3="(\d+)"', raw), np.int64)
    blk = re.search(r'<object id="%s"[^>]*>(.*?)</object>' % oid, p.top, re.S).group(1)
    comps = re.findall(r'<component [^>]*transform="([^"]+)"', blk)
    if len(comps) != 1:
        raise SystemExit(f"«{name}»: частей в объекте {len(comps)}, умею только одну")
    C, _ = mat3mf(comps[0])
    item = re.search(r'<item objectid="%s"[^>]*transform="([^"]+)"' % oid, p.top)
    M, _ = mat3mf(item.group(1)) if item else (np.eye(3), None)
    return V, F, ent, C @ M, src


def print_up(m, up, ref, who):
    """Up direction of the print in the STL frame of part m, and where it came from."""
    if ref:
        V, F, _, R, _ = project_object(ref)
        ext_p = np.ptp(V, axis=0)
        ext_s = np.ptp(m.vertices, axis=0)
        if np.abs(ext_p - ext_s).max() > 0.5:
            raise SystemExit(f"{who}: габарит сетки в проекте {ext_p.round(2)} не равен габариту "
                             f"STL {ext_s.round(2)} — сетка в проекте повёрнута или другая; "
                             f"задайте --up-{who} руками")
        u = R[:, 2]                      # z of the bed, as a row vector applied to local coords
        return u / np.linalg.norm(u), f"из {ref}"
    if up is not None:
        return up, "задано"
    return np.array([0, 0, 1.0]), "по умолчанию +Z"


# ------------------------------------------------------------------ geometry

def crop(m, other, pad):
    """Faces of m whose bounding box touches other's bounding box grown by pad."""
    lo, hi = other.bounds[0] - pad, other.bounds[1] + pad
    T = m.triangles
    keep = np.all(T.max(1) >= lo, 1) & np.all(T.min(1) <= hi, 1)
    return np.where(keep)[0]


def signed_gap(P, Q, idx, Qsub):
    """Signed distance from centres of P faces idx to Q: + air, - inside Q."""
    from trimesh.proximity import ProximityQuery
    c = P.triangles_center[idx]
    cp, d, tri = ProximityQuery(Qsub).on_surface(c)
    nq = Qsub.face_normals[tri]
    s = np.einsum("ij,ij->i", c - cp, nq)
    # the closest point on a face interior lies along that face's normal; on a
    # rim or a corner it does not, and that face sits at the mouth, not in the fit
    flat = np.abs(s) >= SQUARE * d
    return np.where(s < 0, -d, d), nq, flat


def axial_gap(P, Q, idx, a, back):
    """Gap along +a from centres of P faces idx to the first hit on Q."""
    import trimesh
    if not len(idx):
        return np.zeros(0)
    c = P.triangles_center[idx]
    ri = trimesh.ray.ray_triangle.RayMeshIntersector(Q)
    o = c - a * back
    loc, ir, _ = ri.intersects_location(o, np.repeat(a[None], len(o), 0), multiple_hits=True)
    g = np.full(len(o), np.inf)
    t = np.einsum("ij,j->i", loc - o[ir], a)
    for k, tt in zip(ir, t):
        if tt > 1e-9 and tt < g[k]:
            g[k] = tt
    return g - back


class Contact:
    """Contact faces of P against Q, with the axis that points P into Q."""

    def __init__(self, P, Q, near):
        self.P, self.Q, self.near = P, Q, near
        qi = crop(Q, P, near + 1.0)
        pi = crop(P, Q, near + 1.0)
        if not len(qi) or not len(pi):
            raise SystemExit("детали не касаются: габариты не пересекаются")
        import trimesh
        self.Qsub = trimesh.Trimesh(Q.vertices, Q.faces[qi], process=False)
        g, nq, flat = signed_gap(P, Q, pi, self.Qsub)
        facing = np.einsum("ij,ij->i", P.face_normals[pi], nq) < FACING
        ok = facing & flat & (np.abs(g) < near)
        self.idx = pi[ok]
        if not len(self.idx):
            raise SystemExit(f"нет граней ближе {near} мм, смотрящих друг на друга: "
                             "детали не в позе сборки?")

    def gaps(self, P=None):
        P = self.P if P is None else P
        return signed_gap(P, self.Q, self.idx, self.Qsub)[0]


def find_axis(P, idx):
    """Insertion direction of the insert P from its contact faces."""
    n, w = P.face_normals[idx], P.area_faces[idx]
    s = (n * w[:, None]).sum(0)
    if np.linalg.norm(s) > AXIS_MIN * w.sum():
        return s / np.linalg.norm(s), "по сумме нормалей контакта"
    vals, vecs = np.linalg.eigh((n * w[:, None]).T @ n)
    a = vecs[:, 0]
    return a, "по наименьшему разбросу нормалей (дна нет, знак условный)"


# ------------------------------------------------------------------ zones

class Rules:
    def __init__(self, a):
        self.t = (a.vertical, a.mid, a.slope)
        self.vdeg, self.sdeg = a.vertical_deg, a.slope_deg
        self.bottom, self.bottom_cos = a.bottom, np.cos(np.radians(a.bottom_deg))
        self.zone_min = a.zone_min

    def cls(self, layer_deg):
        return np.where(layer_deg >= self.vdeg, 0, np.where(layer_deg <= self.sdeg, 2, 1))


def layer_angle(n, up):
    """Angle between a surface with normal n and the layer plane, degrees."""
    return np.degrees(np.arccos(np.clip(np.abs(n @ up), 0, 1)))


def zones(P, idx, aP, upP, upQ, rules):
    """bottom mask, wall class and target per contact face of P."""
    n = P.face_normals[idx]
    bottom = n @ aP > rules.bottom_cos
    angP, angQ = layer_angle(n, upP), layer_angle(n, upQ)
    k = np.maximum(rules.cls(angP), rules.cls(angQ))
    target = np.array(rules.t)[k]
    return bottom, k, target, angP, angQ


def stats(x):
    if not len(x):
        return "—"
    return (f"{x.min():6.3f} {np.percentile(x, 10):6.3f} {np.median(x):6.3f} "
            f"{np.percentile(x, 90):6.3f} {x.max():6.3f}")


def report(P, Q, ct, aP, upP, upQ, rules, roleP, roleQ, back):
    idx = ct.idx
    g = ct.gaps(P)
    bottom, k, target, angP, angQ = zones(P, idx, aP, upP, upQ, rules)
    A = P.area_faces[idx]
    gb = axial_gap(P, ct.Qsub, idx[bottom], aP, back)
    print(f"контакт: граней {len(idx)}, площадь {A.sum():.2f} мм²")
    print(f"{'зона':26s} {'площадь':>8s} {'угол к слою':>14s}   "
          f"{'зазор min    p10    p50    p90    max':38s} {'цель':>5s}  {'лишнее':>7s}")
    rows = []
    for c in range(3):
        m = (~bottom) & (k == c)
        if not m.any():
            continue
        tg = rules.t[c]
        print(f"стенки {CLASS_NAMES[c]:19s} {A[m].sum():8.2f} "
              f"{np.median(angP[m]):5.0f}°/{np.median(angQ[m]):3.0f}°    "
              f"{stats(g[m]):38s} {tg:5.2f}  {np.median(g[m]) - tg:+7.3f}")
        rows.append((A[m].sum(), c, np.median(g[m]), tg))
    hit = np.isfinite(gb)
    if bottom.any():
        print(f"{'дно (вдоль оси)':26s} {A[bottom].sum():8.2f} {'':14s}   "
              f"{stats(gb[hit]):38s} {rules.bottom:5.2f}  "
              f"{(np.median(gb[hit]) - rules.bottom) if hit.any() else 0:+7.3f}")
        if (~hit).any():
            acc = {"ниша": "нишу", "вставка": "вставку"}[roleQ]
            print(f"   у {(~hit).sum()} граней дна луч вдоль оси не встретил {acc}")
    print(f"углы к слою — медиана в позе печати: {roleP} / {roleQ}")
    neg = g < 0
    if neg.any():
        vol = float((-g[neg] * A[neg]).sum())
        print(f"НАТЯГ: {neg.sum()} граней, площадь {A[neg].sum():.2f} мм², глубина до "
              f"{-g.min():.3f} мм, ≈ объём {vol:.3f} мм³")
    else:
        print("натяг: нет")
    if rows:
        tot = sum(r[0] for r in rows)
        share = ", ".join(f"{CLASS_NAMES[c]} {100 * ar / tot:.0f} %" for ar, c, _, _ in rows)
        big = [r for r in rows if 100 * r[0] / tot >= rules.zone_min]
        ar, c, p50, tg = max(big, key=lambda r: r[3])
        print(f"ИТОГ: площадь стенок — {share}; зазор пары {tg:.2f} на сторону "
              f"(по зоне «{CLASS_NAMES[c]}», факт p50 {p50:.3f}); apply ставит каждой зоне свою цель")
    return g


# ------------------------------------------------------------------ apply

def vertex_shift(F, idx, dirs, mags, nv):
    """Least-squares vertex displacement: for every contact face around a vertex,
    displacement . dir = mag. Faces count equally, so a rim between a wall of
    long faces and a bottom of small ones still moves both ways."""
    A = np.zeros((nv, 3, 3))
    b = np.zeros((nv, 3))
    G = np.einsum("ij,ik->ijk", dirs, dirs)
    for j in range(3):
        v = F[idx, j]
        np.add.at(A, v, G)
        np.add.at(b, v, dirs * mags[:, None])
    used = np.where(A.reshape(nv, -1).any(1))[0]
    D = np.zeros((nv, 3))
    D[used] = np.einsum("ijk,ik->ij", np.linalg.pinv(A[used], rcond=0.05), b[used])
    return D, used


def cmd_apply(a, P, Q, ct, aP, upP, upQ, rules, roleP, roleQ, back):
    import trimesh
    idx = ct.idx
    bottom, k, target, _, _ = zones(P, idx, aP, upP, upQ, rules)
    if a.keep_bottom:
        sel = ~bottom
    else:
        sel = np.ones(len(idx), bool)
    V0 = P.vertices.copy()
    F = P.faces
    cur = trimesh.Trimesh(V0.copy(), F, process=False)
    for it in range(a.iter):
        g = ct.gaps(cur)
        nrm = cur.face_normals[idx]
        mags = g - target
        dirs = nrm.copy()
        if bottom.any():
            gb = axial_gap(cur, ct.Qsub, idx[bottom], aP, back)
            fin = np.isfinite(gb)
            bi = np.where(bottom)[0]
            dirs[bi] = aP
            mags[bi] = np.where(fin, gb - rules.bottom, 0.0)
        mags = np.clip(mags, -a.max_shift, a.max_shift)
        D, used = vertex_shift(F, idx[sel], dirs[sel], mags[sel], len(V0))
        cur = trimesh.Trimesh(cur.vertices + D, F, process=False)
        print(f"проход {it + 1}: сдвинуто вершин {len(used)}, ход до {np.abs(mags[sel]).max():.3f} мм")
    shift = np.linalg.norm(cur.vertices - V0, axis=1)
    moved = shift > 1e-7
    rim = np.zeros(len(V0), bool)
    other = np.setdiff1d(np.arange(len(F)), idx)
    rim[np.unique(F[other])] = True
    rim &= moved
    print(f"\nвершин сдвинуто {moved.sum()} из {len(V0)}, ход до {shift.max():.3f} мм; "
          f"край контакта у устья ушёл до {shift[rim].max() if rim.any() else 0:.3f} мм")
    print(f"граней {len(P.faces)} -> {len(cur.faces)}, нумерация та же; "
          f"объём {P.volume:.3f} -> {cur.volume:.3f} мм³"
          + ("" if P.is_watertight else " (сетка не замкнута — объём условный)"))
    print("\nпосле правки:")
    report(cur, Q, ct, aP, upP, upQ, rules, roleP, roleQ, back)

    if a.put:
        Vl, Fl, ent, R, src = project_object(a.put)
        if len(Fl) != len(F):
            raise SystemExit(f"в {a.put} граней {len(Fl)}, в STL {len(F)}: это другая сетка, "
                             "запишите --out в STL")
        mp = np.full(len(Vl), -1)
        mp[Fl.ravel()] = F.ravel()
        used = mp >= 0
        X, Y = V0[mp[used]], Vl[used]
        cx, cy = X.mean(0), Y.mean(0)
        U, _, Wt = np.linalg.svd((X - cx).T @ (Y - cy))
        Rk = U @ np.diag([1, 1, np.sign(np.linalg.det(U @ Wt))]) @ Wt   # x @ Rk ~ y
        res = np.abs((X - cx) @ Rk + cy - Y).max()
        if res > 1e-3:
            raise SystemExit(f"сетка в {a.put} не совпадает с STL жёстким переносом "
                             f"(расхождение {res:.4f} мм): записывать некуда")
        Vn = Vl.copy()
        mv = np.zeros(len(Vl), bool)
        mv[used] = moved[mp[used]]
        Vn[mv] = (cur.vertices[mp[mv]] - cx) @ Rk + cy
        with tempfile.TemporaryDirectory() as td:
            npz = pathlib.Path(td) / "v.npz"
            np.savez(npz, V=Vn, moved=mv, entry=ent)
            r = subprocess.run([sys.executable, str(HERE / "writeverts.py"), src, str(npz), a.out],
                               capture_output=True, text=True)
            print(r.stdout.strip() or r.stderr.strip())
            if r.returncode:
                raise SystemExit("writeverts.py не записал проект")
        print(f"записано: {a.out}")
    else:
        cur.export(a.out)
        print(f"записано: {a.out}")


# ------------------------------------------------------------------ CLI

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("measure", "apply"):
        q = sub.add_parser(name)
        q.add_argument("insert", help="вставка / штифт, STL в позе сборки")
        q.add_argument("socket", help="деталь с нишей, STL в позе сборки")
        q.add_argument("--up-insert", type=vec, help="верх печати вставки X,Y,Z")
        q.add_argument("--up-socket", type=vec, help="верх печати ниши X,Y,Z")
        q.add_argument("--print-insert", help="поза печати вставки: проект.3mf:Объект")
        q.add_argument("--print-socket", help="поза печати ниши: проект.3mf:Объект")
        q.add_argument("--axis", type=vec, help="ось вставки X,Y,Z, внутрь ниши")
        q.add_argument("--near", type=float, default=0.5, help="контакт ближе, мм (0.5)")
        q.add_argument("--vertical", type=float, default=0.05)
        q.add_argument("--mid", type=float, default=0.10)
        q.add_argument("--slope", type=float, default=0.15)
        q.add_argument("--bottom", type=float, default=0.20, help="зазор по дну вдоль оси (0.20)")
        q.add_argument("--vertical-deg", type=float, default=75.0)
        q.add_argument("--slope-deg", type=float, default=45.0)
        q.add_argument("--bottom-deg", type=float, default=20.0,
                       help="грань дна: нормаль ближе этого угла к оси (20)")
        q.add_argument("--zone-min", type=float, default=10.0,
                       help="зона меньше этой доли площади стенок, %%, зазор пары не задаёт (10)")
        if name == "apply":
            q.add_argument("--move", choices=("insert", "socket"), default="insert")
            q.add_argument("--out", required=True)
            q.add_argument("--put", help="вернуть вершины в проект.3mf:Объект (--out — новый 3MF)")
            q.add_argument("--iter", type=int, default=3)
            q.add_argument("--max-shift", type=float, default=0.5)
            q.add_argument("--keep-bottom", action="store_true", help="дно не двигать")
    a = ap.parse_args()
    if a.slope_deg >= a.vertical_deg:
        raise SystemExit("--slope-deg должен быть меньше --vertical-deg")

    ins, soc = load_stl(a.insert), load_stl(a.socket)
    up_i, src_i = print_up(ins, a.up_insert, a.print_insert, "insert")
    up_s, src_s = print_up(soc, a.up_socket, a.print_socket, "socket")
    rules = Rules(a)
    back = a.near + 0.1
    print(f"вставка «{pathlib.Path(a.insert).name}»: граней {len(ins.faces)}; "
          f"печать вверх {up_i.round(3)} ({src_i})")
    print(f"ниша «{pathlib.Path(a.socket).name}»: граней {len(soc.faces)}; "
          f"печать вверх {up_s.round(3)} ({src_s})")

    ct_i = Contact(ins, soc, a.near)
    if a.axis is not None:
        axis, how = a.axis, "задана"
    else:
        axis, how = find_axis(ins, ct_i.idx)
    print(f"ось вставки {axis.round(3) + 0.0} ({how})")
    print(f"цели: вертикальные {a.vertical:.2f}, промежуточные {a.mid:.2f}, "
          f"наклонные {a.slope:.2f} на сторону; дно {a.bottom:.2f}\n")

    if a.cmd == "measure" or a.move == "insert":
        print("— со стороны вставки —")
        report(ins, soc, ct_i, axis, up_i, up_s, rules, "вставка", "ниша", back)
        if a.cmd == "apply":
            print()
            cmd_apply(a, ins, soc, ct_i, axis, up_i, up_s, rules, "вставка", "ниша", back)
    else:
        ct_s = Contact(soc, ins, a.near)
        print("— со стороны ниши —")
        report(soc, ins, ct_s, -axis, up_s, up_i, rules, "ниша", "вставка", back)
        print()
        cmd_apply(a, soc, ins, ct_s, -axis, up_s, up_i, rules, "ниша", "вставка", back)


if __name__ == "__main__":
    main()
