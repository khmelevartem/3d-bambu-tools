#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "scipy", "trimesh", "shapely", "networkx", "rtree"]
# ///
"""Check that what a part stands on is a plane, and press it flat when it is not.

A generated mesh has no planes. Where the shape is meant to be flat - a sole,
the bottom of a base, the face of a cut - it carries a wave of a few tenths of
a millimetre that the eye does not see. The first layer then prints only on the
crests, the rest of the sole is laid over air: junk under the part, or a part
that lets go of the plate.

    uv run tools/flatbase.py check   model.stl
    uv run tools/flatbase.py check   project.3mf --object "Left boot" --object "Right boot"
    uv run tools/flatbase.py flatten model.stl  -o model_flat.stl
    uv run tools/flatbase.py flatten project.3mf --object "Left boot" -o fixed.3mf

`check` works in the PRINT POSE: a 3MF object is taken through its component
and build-item transforms, exactly as it lies on the plate; an STL as it is.
The bed is the object's lowest point (0 when the object is sunk below the
plate). The support zone is every face whose normal is within `--angle` of
straight down and whose centre lies within `--depth` mm of the bed. Each
connected patch of that zone (two soles - two patches) gets:

* a least-squares plane and the deviation from it, max and p95;
* the tilt of that plane to the bed and how much it rises across the patch;
* the first-layer footprint - the section at half a layer above the bed, where
  the slicer cuts the first layer - against the patch's projected area.

Verdict per patch, against half the layer height of the installed nozzle
(`hardware.py`): p95 deviation above it - not a plane, junk or lift-off; a flat
patch rising more than that across itself - tilted, touches by an edge; a flat
patch lying more than that above the bed - hangs, another part carries the
weight.

`flatten` moves only the patch vertices, straight down or up onto a horizontal
plane (`--to bed`, the default: the plane of the bed; `--to mean`: the patch's
mean height, volume-neutral), and a narrow rim around the patch (`--rim` mm)
with a smooth cosine falloff so the wall above does not get a step. XY never
changes, the vertex count and the face numbering never change, so the paint of
a 3MF survives: the file is rewritten through `writeverts.py`, which carries
untouched vertices byte for byte. A patch that would need a shift larger than
`--max-shift` is left alone: that is a shape, not a wave.
"""
import argparse, os, re, subprocess, sys, tempfile, zipfile
import numpy as np
import hardware
import partedit

HERE = os.path.dirname(os.path.abspath(__file__))
VERT = partedit.VERT
TRI = re.compile(r'<triangle v1="(\d+)" v2="(\d+)" v3="(\d+)"')


# ------------------------------------------------------------------ loading
def _tf(s):
    a = np.array((s or '1 0 0 0 1 0 0 0 1 0 0 0').split(), dtype=np.float64)
    return a[:9].reshape(3, 3), a[9:12]                 # world = local @ M + t


def _attr(tag, name):
    m = re.search(r'\s%s="([^"]*)"' % re.escape(name), tag)
    return m.group(1) if m else None


def parse_model(raw):
    """Object id -> ('mesh', offset of its first vertex in the FILE, V, F)
                  or ('components', [(path, objectid, M, t), ...])."""
    out, off = {}, 0
    for m in re.finditer(r'<object\s([^>]*)>(.*?)</object>', raw, re.S):
        oid, body = _attr(' ' + m.group(1), 'id'), m.group(2)
        if '<mesh' in body:
            V = np.array(VERT.findall(body), dtype=np.float64).reshape(-1, 3)
            F = np.array(TRI.findall(body), dtype=np.int64).reshape(-1, 3)
            out[oid] = ('mesh', off, V, F)
            off += len(V)
        else:
            comps = []
            for c in re.findall(r'<component\s[^>]*/>', body):
                M, t = _tf(_attr(c, 'transform'))
                comps.append((_attr(c, 'p:path'), _attr(c, 'objectid'), M, t))
            out[oid] = ('components', comps)
    return out


class Obj:
    """One printable object in world coordinates, with the way back to its files."""

    def __init__(self, name):
        self.name, self.parts = name, []     # part: dict(entry, off, n, M, t, V, F)

    @property
    def V(self):
        return np.vstack([p['V'] @ p['M'] + p['t'] for p in self.parts])

    @property
    def F(self):
        out, n = [], 0
        for p in self.parts:
            out.append(p['F'] + n); n += len(p['V'])
        return np.vstack(out)


def load_3mf(path, names):
    pr = partedit.Project(path)
    cache = {}

    def model(entry):
        if entry not in cache:
            cache[entry] = parse_model(pr.files[entry].decode('utf-8'))
        return cache[entry]

    title = {}
    for oid, blk in re.findall(r'<object id="(\d+)">(.*?)</object>', pr.cfg, re.S):
        nm = re.search(r'<metadata key="name" value="([^"]*)"', blk.split('<part')[0])
        title[oid] = nm.group(1) if nm else oid
    root = '3D/3dmodel.model'
    objs = []
    for it in re.findall(r'<item\s[^>]*/>', pr.top):
        oid = _attr(it, 'objectid')
        name = title.get(oid, oid)
        if names and name not in names:
            continue
        o = Obj(name)

        def emit(entry, iid, M, t):
            kind = model(entry)[iid]
            if kind[0] == 'mesh':
                o.parts.append(dict(entry=entry, off=kind[1], n=len(kind[2]),
                                    V=kind[2], F=kind[3], M=M, t=t))
            else:
                for p2, iid2, M2, t2 in kind[1]:
                    emit(p2.lstrip('/') if p2 else entry, iid2, M2 @ M, t2 @ M + t)

        M, t = _tf(_attr(it, 'transform'))
        emit(root, oid, M, t)
        objs.append(o)
    if names:
        miss = set(names) - {o.name for o in objs}
        if miss:
            sys.exit(f'в проекте нет объектов: {", ".join(sorted(miss))}. '
                     f'Есть: {", ".join(sorted(set(title.values())))}')
    return objs


def load_stl(path):
    import trimesh
    m = trimesh.load(path, process=False, force='mesh')
    m.merge_vertices()                   # STL has no shared vertices; faces stay as they are
    o = Obj(os.path.splitext(os.path.basename(path))[0])
    o.parts.append(dict(entry=None, off=0, n=len(m.vertices), V=np.asarray(m.vertices, float),
                        F=np.asarray(m.faces, np.int64), M=np.eye(3), t=np.zeros(3)))
    return [o]


def load(path, names):
    return load_3mf(path, names) if path.lower().endswith('.3mf') else load_stl(path)


# ------------------------------------------------------------------ analysis
def bed_level(V):
    zmin = V[:, 2].min()
    return max(zmin, 0.0), zmin


def patches(V, F, a):
    """Support patches: list of (face indices, vertex indices)."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    z0, _ = bed_level(V)
    P = V[F]
    n = np.cross(P[:, 1] - P[:, 0], P[:, 2] - P[:, 0])
    area = np.linalg.norm(n, axis=1) / 2
    nz = -n[:, 2] / np.maximum(2 * area, 1e-12)
    sel = np.where((nz >= np.cos(np.radians(a.angle))) &
                   (P[:, :, 2].mean(1) <= z0 + a.depth) & (area > 0))[0]
    if not len(sel):
        return []
    E = np.concatenate([F[sel][:, [0, 1]], F[sel][:, [1, 2]]])
    g = coo_matrix((np.ones(len(E)), (E[:, 0], E[:, 1])), shape=(len(V), len(V)))
    _, lab = connected_components(g, directed=False)
    flab = lab[F[sel, 0]]
    out = []
    for L in np.unique(flab):
        fs = sel[flab == L]
        if area[fs].sum() < a.min_area:
            continue
        out.append((fs, np.unique(F[fs])))
    out.sort(key=lambda p: (-area[p[0]].sum()))
    return out


def plane_fit(Q):
    A = np.c_[Q[:, 0], Q[:, 1], np.ones(len(Q))]
    (pa, pb, pc), *_ = np.linalg.lstsq(A, Q[:, 2], rcond=None)
    r = (Q[:, 2] - (pa * Q[:, 0] + pb * Q[:, 1] + pc)) / np.sqrt(1 + pa * pa + pb * pb)
    return pa, pb, pc, np.abs(r)


def section_area(V, F, z, region):
    import trimesh, shapely
    m = trimesh.Trimesh(V, F, process=False)
    s = m.section(plane_origin=[0, 0, z], plane_normal=[0, 0, 1])
    if s is None:
        return 0.0
    T = np.eye(4); T[2, 3] = -z                        # keep world XY in the 2D section
    p, _ = s.to_2D(to_2D=T)
    try:
        polys = p.polygons_full
    except ModuleNotFoundError as e:
        sys.exit(f"нет зависимости {e.name or e}: у скрипта свои зависимости в заголовке, "
                 f"запускать без --with:\n    uv run tools/flatbase.py ...")
    if not polys:
        return 0.0
    return shapely.union_all(polys).intersection(region).area


def measure(V, F, a, layer):
    import shapely
    z0, zmin = bed_level(V)
    res = []
    for fs, vs in patches(V, F, a):
        Q = V[vs]
        pa, pb, pc, r = plane_fit(Q)
        tilt = np.degrees(np.arctan(np.hypot(pa, pb)))
        fitz = pa * Q[:, 0] + pb * Q[:, 1] + pc
        tri = V[F[fs]][:, :, :2]
        proj = shapely.union_all(shapely.polygons(np.concatenate([tri, tri[:, :1]], 1)))
        foot = section_area(V, F, z0 + layer / 2, proj.buffer(0.05))
        res.append(dict(faces=fs, verts=vs, dmax=r.max(), d95=np.percentile(r, 95),
                        tilt=tilt, rise=fitz.max() - fitz.min(), above=Q[:, 2].min() - z0,
                        area=proj.area, foot=foot, c=Q[:, :2].mean(0), zmean=Q[:, 2].mean()))
    return z0, zmin, res


def verdict(p, half):
    if p['d95'] > half:
        return False, (f"не плоскость: волна {p['d95']:.2f} мм больше половины слоя "
                       f"{half:.2f} — под ней будет мусор или отрыв от стола")
    if p['rise'] > half:
        return False, (f"плоская, но наклонена на {p['tilt']:.1f}° и поднимается на "
                       f"{p['rise']:.2f} мм — стоит на краю")
    if p['above'] > half:
        return False, (f"плоская, но висит над столом на {p['above']:.2f} мм — "
                       f"держит другая опора")
    return True, "плоскость"


def report(o, V, F, a, layer, head=True):
    z0, zmin, res = measure(V, F, a, layer)
    half = layer / 2
    if head:
        print(f"объект «{o.name}»: граней {len(F)}, вершин {len(V)}, стол z={z0:.3f}, "
              f"слой {layer:.2f} (порог {half:.2f} мм), площадок опоры {len(res)}")
        if zmin < -1e-3:
            print(f"  утоплен в стол на {-zmin:.2f} мм: по столу его срежет слайсер, "
                  f"это плоскость по определению")
    if not res:
        print(f"  !! нет граней, смотрящих вниз в пределах {a.angle:.0f}° на нижних "
              f"{a.depth:.1f} мм: деталь стоит на ребре или на точке")
    for i, p in enumerate(res, 1):
        ok, why = verdict(p, half)
        frac = 100 * p['foot'] / p['area'] if p['area'] else 0
        print(f"  площадка {i}: {p['area']:.1f} мм², {len(p['faces'])} граней, "
              f"центр ({p['c'][0]:.1f}, {p['c'][1]:.1f})")
        print(f"    отклонение от своей плоскости: max {p['dmax']:.3f} мм, p95 {p['d95']:.3f} мм")
        print(f"    наклон к столу {p['tilt']:.2f}°, подъём по площадке {p['rise']:.2f} мм, "
              f"над столом {p['above']:.3f} мм")
        print(f"    след первого слоя (z={z0 + half:.2f}): {p['foot']:.1f} мм² "
              f"из {p['area']:.1f} мм² проекции ({frac:.0f}%)")
        print(f"    ВЕРДИКТ: {why}")
    return z0, res


# ------------------------------------------------------------------ commands
def cmd_check(a, layer):
    bad = 0
    for o in load(a.src, a.object):
        _, res = report(o, o.V, o.F, a, layer)
        bad += sum(not verdict(p, layer / 2)[0] for p in res)
    print(f"ИТОГ: {'не плоских площадок ' + str(bad) if bad else 'все площадки плоские'}")
    return 1 if bad and a.strict else 0


def cmd_flatten(a, layer):
    from scipy.spatial import cKDTree
    objs = load(a.src, a.object)
    if a.src.lower().endswith('.3mf') and not a.object and len(objs) > 1:
        sys.exit('в проекте несколько объектов: назвать нужные через --object. '
                 f'Есть: {", ".join(o.name for o in objs)}')
    if os.path.abspath(a.out) == os.path.abspath(a.src):
        sys.exit('-o совпадает с исходником: писать в новый файл')
    edits = {}                                          # entry -> (V of the whole file, moved)
    stl_out = None
    for o in objs:
        V, F = o.V, o.F
        z0, res = report(o, V, F, a, layer)
        pick = [i for i in range(len(res)) if not a.patch or (i + 1) in a.patch]
        D = np.zeros(len(V))
        moved_patch = []
        for i in pick:
            p = res[i]
            target = z0 if a.to == 'bed' else p['zmean']
            d = target - V[p['verts'], 2]
            if np.abs(d).max() > a.max_shift:
                print(f"  площадка {i+1}: нужен сдвиг {np.abs(d).max():.2f} мм больше "
                      f"--max-shift {a.max_shift:.2f} — это форма, а не волна, не трогаю")
                continue
            D[p['verts']] = d
            moved_patch.append(p['verts'])
        if not moved_patch:
            print("  править нечего")
            continue
        pv = np.concatenate(moved_patch)
        inp = np.zeros(len(V), bool); inp[pv] = True
        if a.rim > 0:
            tree = cKDTree(V[pv])
            others = np.where(~inp)[0]
            dist, j = tree.query(V[others], distance_upper_bound=a.rim)
            near = np.isfinite(dist)
            w = 0.5 * (1 + np.cos(np.pi * dist[near] / a.rim))
            D[others[near]] = w * D[pv[j[near]]]
        mv = np.abs(D) > 1e-9
        W = V.copy(); W[:, 2] += D
        # A face turned over by the shift is a fold in the wall: worth knowing at once.
        def nrm(X):
            P = X[F]
            return np.cross(P[:, 1] - P[:, 0], P[:, 2] - P[:, 0])
        n0, n1 = nrm(V), nrm(W)
        flipped = int(((n0 * n1).sum(1) < 0).sum())
        vol0, vol1 = partedit.volume(V, F), partedit.volume(W, F)
        print(f"  сдвинуто вершин {mv.sum()} (площадка {inp.sum()}, каёмка {mv.sum() - (mv & inp).sum()}), "
              f"сдвиг max {np.abs(D[mv]).max():.3f} мм, средний {np.abs(D[mv]).mean():.3f} мм")
        print(f"  объём {vol0:.1f} -> {vol1:.1f} мм³ ({vol1 - vol0:+.1f}), граней {len(F)} -> {len(F)}, "
              f"вывернуто граней {flipped}")
        print("  после правки:")
        report(o, W, F, a, layer, head=False)
        # back to each part's own coordinates: the shift is a vector, so only M acts on it
        k = 0
        for part in o.parts:
            dw = np.zeros((part['n'], 3)); dw[:, 2] = D[k:k + part['n']]
            dl = dw @ np.linalg.inv(part['M'])
            sel = np.abs(D[k:k + part['n']]) > 1e-9
            k += part['n']
            if part['entry'] is None:
                stl_out = (part['V'] + dl, part['F'])
                continue
            if not sel.any():
                continue
            if part['entry'] not in edits:
                raw = zipfile.ZipFile(a.src).read(part['entry']).decode('utf-8')
                allv = np.array(VERT.findall(raw), dtype=np.float64).reshape(-1, 3)
                edits[part['entry']] = [allv, np.zeros(len(allv), bool), set()]
            allv, moved, seen = edits[part['entry']]
            key = (part['off'], part['n'])
            if key in seen:
                sys.exit(f"одна сетка {part['entry']} входит в объект дважды — "
                         f"сдвиг одной копии сдвинет и другую, не правлю")
            seen.add(key)
            rows = np.arange(part['off'], part['off'] + part['n'])[sel]
            allv[rows] += dl[sel]
            moved[rows] = True
    if stl_out is not None:
        import trimesh
        trimesh.Trimesh(stl_out[0], stl_out[1], process=False).export(a.out)
        print(f"-> {a.out}")
        return 0
    if not edits:
        print("файл не записан: ничего не сдвинулось")
        return 0
    with tempfile.TemporaryDirectory() as td:
        cur = a.src
        for i, (entry, (allv, moved, _)) in enumerate(edits.items()):
            npz = os.path.join(td, f'e{i}.npz')
            np.savez(npz, V=allv, moved=moved, entry=np.array(entry))
            nxt = a.out if i == len(edits) - 1 else os.path.join(td, f's{i}.3mf')
            r = subprocess.run([sys.executable, os.path.join(HERE, 'writeverts.py'), cur, npz, nxt],
                               capture_output=True, text=True)
            print('  ' + (r.stdout + r.stderr).strip())
            if r.returncode:
                sys.exit('writeverts.py не записал файл')
            cur = nxt
    print(f"-> {a.out}")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    for nm, hl in (('check', 'найти опорные площадки и проверить, плоские ли они'),
                   ('flatten', 'прижать опорные площадки к плоскости, не трогая нумерацию граней')):
        q = sub.add_parser(nm, help=hl)
        q.add_argument('src', help='STL или 3MF в позе печати')
        q.add_argument('--object', action='append', help='имя объекта 3MF, можно несколько раз')
        q.add_argument('--depth', type=float, default=1.5,
                       help='искать опору на нижних N мм (1.5)')
        q.add_argument('--angle', type=float, default=15.0,
                       help='нормаль грани не дальше стольких градусов от -Z (15)')
        q.add_argument('--min-area', type=float, default=2.0,
                       help='площадки меньше стольких мм² не считать (2)')
        q.add_argument('--layer', type=float, default=None,
                       help='высота слоя; по умолчанию из hardware.py')
        if nm == 'check':
            q.add_argument('--strict', action='store_true', help='код 1, если есть не плоская')
        else:
            q.add_argument('-o', '--out', required=True)
            q.add_argument('--to', choices=('bed', 'mean'), default='bed',
                           help='bed — на плоскость стола; mean — на среднюю высоту площадки')
            q.add_argument('--rim', type=float, default=1.0,
                           help='ширина каёмки с плавным спадом сдвига, мм (1.0)')
            q.add_argument('--max-shift', type=float, default=None,
                           help='больший сдвиг не делать; по умолчанию = --depth')
            q.add_argument('--patch', type=int, action='append',
                           help='номер площадки из check; по умолчанию все')
    a = ap.parse_args()
    layer = a.layer or hardware.layer_height()
    if a.cmd == 'check':
        return cmd_check(a, layer)
    if a.max_shift is None:
        a.max_shift = a.depth
    return cmd_flatten(a, layer)


if __name__ == '__main__':
    sys.exit(main())
