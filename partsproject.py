#!/usr/bin/env python3
"""Keep a 3MF project and a folder of STL parts in agreement, without losing poses.

A multi-part figurine lives in two places: the geometry of every part is a file
in a parts folder, and the print project holds a copy of each mesh together with
what was done to it by hand - where it sits on the plate, how it is turned, its
own layer height, supports and filament. The two drift apart: a part is fixed in
the folder and the project still prints the old one, or the project is rebuilt
from the folder and every pose set by hand is gone. The rule here is that **the
pose and the settings in the project are the source of truth, the shape in the
folder is the source of truth**, and neither is ever overwritten by the other.

    uv run --with numpy --with scipy python tools/partsproject.py <command> ...

Commands
--------
diff    PROJECT.3mf PARTS_DIR
        Pair objects with STL files and say for each pair whether the shape is
        the same. Shapes are compared up to a rigid motion - volume, area and the
        principal radii of gyration - because a file in the folder may be stored
        in the assembly pose while the project holds it turned for printing.
        Pairing goes by name (case, `_` and a prefix shared by every file are
        ignored; the `source_file` a part was imported from counts as a name),
        then by shape among what is left. A file holding several bodies is also
        tried body by body (`legs.stl#1`). Mirror twins (left and right) have
        equal invariants and are told apart by a rigid fit. `--write-map` saves
        the pairing for `sync`.

sync    PROJECT.3mf PARTS_DIR -o OUT.3mf
        Put the folder's mesh into every paired object whose shape differs. The
        build item, the assembly pose and every object setting stay as they are.
        The new mesh is fitted onto the old one rigidly (trimmed point-to-plane
        ICP from the identity, from the inverse of the plate pose and from every
        principal-axis guess), so a part stored in another pose lands exactly
        where the old one was and the visible pose does not move. Paint stays only
        when the face count is the same and the faces still lie where the old
        ones were; otherwise it is dropped with a warning. `--add` brings files
        missing from the project in as new objects (`--filament N`).

set     PROJECT.3mf --object NAME key=value ... -o OUT.3mf
        Per-object print settings in `Metadata/model_settings.config`:
        layer_height, enable_support, support_type, extruder, brim_type... A key
        that the project's own process settings do not know is refused, because
        the slicer ignores it silently. `key=` removes the override.

export  PROJECT.3mf -o DIR
        One STL per object, in the print pose: turned as on the plate, centred,
        bottom at z = 0 (`--world` keeps the plate coordinates). Named
        `<filament>_<object>`, identical parts of one filament as one file with
        `_xN`. Filament names come from the project's colours or `--names 1=white`.

Format notes:

* An object's mesh file holds vertices in **object** coordinates; the world pose
  is `item transform * component transform * vertex`. The part `matrix` in the
  config is what Bambu Studio un-applies on load and needs no change when the
  vertices are rewritten in the same frame.
* A 3MF transform `m00 m01 m02 m10 ... m32` acts on a row vector, so the
  rotation for column vectors is the transpose of the first nine numbers.
"""

import argparse, json, pathlib, re, struct, sys
import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from partedit import Project  # noqa: E402

TRI = re.compile(r'<triangle v1="(\d+)" v2="(\d+)" v3="(\d+)"((?:\s+[\w:]+="[^"]*")*)\s*/>')
VERT = re.compile(r'<vertex x="([^"]*)" y="([^"]*)" z="([^"]*)"\s*/>')


# ------------------------------------------------------------------ geometry
def read_stl(path):
    raw = pathlib.Path(path).read_bytes()
    n = struct.unpack('<I', raw[80:84])[0] if len(raw) >= 84 else -1
    if len(raw) == 84 + 50 * n:
        a = np.frombuffer(raw, dtype=np.dtype([('n', '<f4', 3), ('v', '<f4', (3, 3)),
                                               ('a', '<u2')]), count=n, offset=84)
        P = a['v'].astype(np.float64)
    else:
        txt = raw.decode('utf-8', 'replace')
        P = np.array(re.findall(r'vertex\s+(\S+)\s+(\S+)\s+(\S+)', txt), float).reshape(-1, 3, 3)
    V, inv = np.unique(P.reshape(-1, 3), axis=0, return_inverse=True)
    return V, inv.reshape(-1, 3).astype(np.int64)


def write_stl(path, V, F):
    P = V[F].astype(np.float32)
    n = np.cross(P[:, 1] - P[:, 0], P[:, 2] - P[:, 0])
    n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
    rec = np.zeros(len(F), dtype=np.dtype([('n', '<f4', 3), ('v', '<f4', (3, 3)), ('a', '<u2')]))
    rec['n'] = n; rec['v'] = P
    with open(path, 'wb') as f:
        f.write(b'\0' * 80 + struct.pack('<I', len(F)) + rec.tobytes())


def bodies(V, F):
    """Connected bodies of a mesh, ordered by centroid (x, then y, then z)."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    n = len(V)
    r = np.r_[F[:, 0], F[:, 1], F[:, 2]]; c = np.r_[F[:, 1], F[:, 2], F[:, 0]]
    k, lab = connected_components(coo_matrix((np.ones(len(r)), (r, c)), shape=(n, n)),
                                  directed=False)
    out = []
    for i in range(k):
        fi = np.where(lab[F[:, 0]] == i)[0]
        if not len(fi):
            continue
        used, Fi = np.unique(F[fi], return_inverse=True)
        out.append((V[used], Fi.reshape(-1, 3)))
    out.sort(key=lambda m: tuple(np.round(m[0].mean(0), 3)))
    return out


def adjacency(V, F):
    """Pairs of faces sharing an edge, by face index: blind to how vertices are numbered."""
    _, inv = np.unique(np.round(V, 5), axis=0, return_inverse=True)
    G = inv.reshape(-1)[F]
    E = np.sort(np.r_[G[:, [0, 1]], G[:, [1, 2]], G[:, [2, 0]]], axis=1)
    f = np.tile(np.arange(len(F)), 3)
    o = np.lexsort((f, E[:, 1], E[:, 0]))
    E, f = E[o], f[o]
    same = (E[1:] == E[:-1]).all(1)
    pairs = np.c_[f[:-1][same], f[1:][same]]
    return pairs[np.lexsort((pairs[:, 1], pairs[:, 0]))]


def invariants(V, F):
    """Volume, area and sorted principal radii of gyration: blind to rigid motion."""
    P = V[F]
    a, b, c = P[:, 0], P[:, 1], P[:, 2]
    area = float(np.linalg.norm(np.cross(b - a, c - a), axis=1).sum() / 2)
    v = np.einsum('ij,ij->i', a, np.cross(b, c)) / 6
    vol = v.sum()
    if abs(vol) < 1e-9:
        return dict(vol=0.0, area=area, rad=np.zeros(3), cen=P.mean((0, 1)))
    cen = (v[:, None] * (a + b + c)).sum(0) / 4 / vol
    s = a + b + c
    M2 = (np.einsum('i,ij,ik->jk', v, a, a) + np.einsum('i,ij,ik->jk', v, b, b)
          + np.einsum('i,ij,ik->jk', v, c, c) + np.einsum('i,ij,ik->jk', v, s, s)) / 20
    C = M2 / vol - np.outer(cen, cen)
    rad = np.sqrt(np.clip(np.sort(np.linalg.eigvalsh(C)), 0, None))
    return dict(vol=float(abs(vol)), area=area, rad=rad, cen=cen)


def same_shape(i, j, rel=2e-3):
    def ok(x, y, floor):
        return abs(x - y) <= rel * max(abs(x), abs(y)) + floor
    return (ok(i['vol'], j['vol'], 1e-3) and ok(i['area'], j['area'], 1e-3)
            and all(ok(x, y, 1e-3) for x, y in zip(i['rad'], j['rad'])))


def sample(V, F, n, seed=0):
    P = V[F]
    cr = np.cross(P[:, 1] - P[:, 0], P[:, 2] - P[:, 0])
    A = np.linalg.norm(cr, axis=1)
    rng = np.random.default_rng(seed)
    idx = rng.choice(len(F), n, p=A / A.sum())
    r1, r2 = np.sqrt(rng.random(n)), rng.random(n)
    pts = ((1 - r1)[:, None] * P[idx, 0] + (r1 * (1 - r2))[:, None] * P[idx, 1]
           + (r1 * r2)[:, None] * P[idx, 2])
    return pts, cr[idx] / A[idx, None], float(A.sum() / 2)


def rodrigues(w):
    th = np.linalg.norm(w)
    if th < 1e-12:
        return np.eye(3)
    k = w / th
    K = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    return np.eye(3) + np.sin(th) * K + (1 - np.cos(th)) * K @ K


def _icp(src, tree, dst, dn, R, t, iters, tol):
    for _ in range(iters):
        q = src @ R.T + t
        d, j = tree.query(q)
        r = ((q - dst[j]) * dn[j]).sum(1)
        keep = (np.abs(r) < max(tol, 3 * np.median(np.abs(r)))) & (d < max(4 * tol, 3 * np.median(d)))
        if keep.sum() < 12:
            break
        qk, nk = q[keep], dn[j[keep]]
        A = np.c_[np.cross(qk, nk), nk]
        x = np.linalg.solve(A.T @ A + 1e-9 * np.eye(6), -A.T @ r[keep])
        dR = rodrigues(x[:3])
        R, t = dR @ R, dR @ t + x[3:]
        if np.linalg.norm(x) < 1e-7:
            break
    q = src @ R.T + t
    d, j = tree.query(q)
    r = np.abs(((q - dst[j]) * dn[j]).sum(1))
    good = (r < tol) & (d < 4 * tol)
    return R, t, float(good.mean()), float(np.sqrt(np.mean(r[good] ** 2))) if good.any() else 9e9


def rigid_fit(Vs, Fs, Vd, Fd, starts=(), n=6000):
    """Rigid (R, t) laying mesh s onto mesh d. Returns R, t, share of s within
    tolerance of d, rms of that share. Only proper rotations: a mirror twin fits badly."""
    from scipy.spatial import cKDTree
    src, _, _ = sample(Vs, Fs, n, 1)
    dst, dn, area = sample(Vd, Fd, 4 * n, 2)
    diag = float(np.linalg.norm(Vd.max(0) - Vd.min(0)))
    spacing = np.sqrt(area / len(dst))
    tol = max(0.02, 0.002 * diag, 1.5 * spacing)
    tree = cKDTree(dst)
    cands = [(np.asarray(R, float), np.asarray(t, float)) for R, t in starts]
    cs, cd = src.mean(0), dst.mean(0)
    es, Es = np.linalg.eigh(np.cov((src - cs).T))
    ed, Ed = np.linalg.eigh(np.cov((dst - cd).T))
    perms = [(0, 1, 2)]
    if min(np.diff(es) / max(es[-1], 1e-12)) < 0.05:
        perms = [(0, 1, 2), (0, 2, 1), (1, 0, 2), (1, 2, 0), (2, 0, 1), (2, 1, 0)]
    for p in perms:
        for s1 in (1, -1):
            for s2 in (1, -1):
                S = np.diag([s1, s2, 1.0])
                R = Ed @ S @ Es[:, list(p)].T
                if np.linalg.det(R) < 0:
                    R = Ed @ np.diag([s1, s2, -1.0]) @ Es[:, list(p)].T
                cands.append((R, cd - R @ cs))
    small = src[:: max(1, len(src) // 1500)]
    scored = []
    for k, (R, t) in enumerate(cands):
        R1, t1, share, rms = _icp(small, tree, dst, dn, R, t, 20, tol)
        scored.append((share, -k, R1, t1))
    best = max(s[0] for s in scored)
    # a pose given by the caller wins a tie: a symmetric part must not spin for nothing
    share, _, R, t = next(s for s in scored if s[0] >= best - 0.02)
    R, t, share, rms = _icp(src, tree, dst, dn, R, t, 40, tol)
    return R, t, share, rms, tol


# ------------------------------------------------------------------ project
def tmat(s):
    a = [float(x) for x in s.split()]
    M = np.eye(4)
    M[:3, :3] = np.array(a[:9]).reshape(3, 3).T
    M[:3, 3] = a[9:12]
    return M


def apply(M, V):
    return V @ M[:3, :3].T + M[:3, 3]


class Obj:
    pass


def read_project(path):
    p = Project(path)
    objs = []
    for oid, blk in re.findall(r'<object id="(\d+)">(.*?)</object>', p.cfg, re.S):
        o = Obj()
        o.id = oid
        head = blk.split('<part')[0]
        kv = dict(re.findall(r'<metadata key="([^"]+)" value="([^"]*)"/>', head))
        o.name = kv.get('name', f'объект {oid}')
        o.extruder = int(kv.get('extruder', '1') or 1)
        o.parts = []
        for pid, sub, body in re.findall(r'<part id="(\d+)" subtype="(\w+)"[^>]*>(.*?)</part>', blk, re.S):
            src = re.search(r'key="source_file" value="([^"]*)"', body)
            o.parts.append(dict(id=pid, subtype=sub, source=src.group(1) if src else ''))
        m = re.search(r'<object id="%s"[^>]*>(.*?)</object>' % oid, p.top, re.S)
        o.comps = re.findall(r'<component p:path="([^"]+)" objectid="(\d+)"[^>]*?transform="([^"]+)"',
                             m.group(1)) if m else []
        o.items = [(tmat(tr), pr) for tr, pr in re.findall(
            r'<item objectid="%s"[^>]*?transform="([^"]+)"[^>]*?printable="(\d)"' % oid, p.top)]
        asm = re.search(r'<assemble_item object_id="%s" instance_id="0" transform="([^"]+)"' % oid, p.cfg)
        o.assemble = tmat(asm.group(1)) if asm else None
        objs.append(o)
    return p, objs


def load_mesh(p, path, inner):
    raw = p.files[path.lstrip('/')].decode()
    m = re.search(r'<object id="%s"[^>]*>(.*?)</object>' % inner, raw, re.S)
    body = m.group(1)
    V = np.array(VERT.findall(body), float).reshape(-1, 3)
    T = TRI.findall(body)
    F = np.array([t[:3] for t in T], np.int64).reshape(-1, 3)
    return V, F, [t[3] for t in T]


def object_mesh(p, o, normal_only=True):
    """Mesh of an object in object coordinates, all normal parts together."""
    sub = {q['id']: q['subtype'] for q in o.parts}
    Vs, Fs, n = [], [], 0
    for path, inner, tr in o.comps:
        if normal_only and sub.get(inner, 'normal_part') != 'normal_part':
            continue
        V, F, _ = load_mesh(p, path, inner)
        Vs.append(apply(tmat(tr), V)); Fs.append(F + n); n += len(V)
    if not Vs:
        return np.zeros((0, 3)), np.zeros((0, 3), np.int64)
    return np.vstack(Vs), np.vstack(Fs)


def put_mesh(p, o, V, F, attrs=None):
    """Rewrite the one mesh of a single-part object; ids, UUID and the rest stay."""
    path, inner, tr = o.comps[0]
    ent = path.lstrip('/')
    V = apply(np.linalg.inv(tmat(tr)), V)
    raw = p.files[ent].decode()
    attrs = attrs or [''] * len(F)
    mesh = ['<mesh>\n    <vertices>\n']
    mesh += [f'     <vertex x="{x:.6f}" y="{y:.6f}" z="{z:.6f}"/>\n' for x, y, z in V]
    mesh.append('    </vertices>\n    <triangles>\n')
    mesh += [f'     <triangle v1="{a}" v2="{b}" v3="{c}"{e}/>\n' for (a, b, c), e in zip(F, attrs)]
    mesh.append('    </triangles>\n   </mesh>')
    m = re.search(r'(<object id="%s"[^>]*>.*?)<mesh>.*?</mesh>' % inner, raw, re.S)
    raw = raw[:m.end(1)] + ''.join(mesh) + raw[m.end():]
    p.files[ent] = raw.encode()
    k = str(len(F))
    p.cfg = re.sub(r'(<object id="%s">.*?<metadata face_count=")\d+(")' % o.id,
                   lambda m: m.group(1) + k + m.group(2), p.cfg, count=1, flags=re.S)
    p.cfg = re.sub(r'(<part id="%s".*?<mesh_stat face_count=")\d+(")' % inner,
                   lambda m: m.group(1) + k + m.group(2), p.cfg, count=1, flags=re.S)


# ------------------------------------------------------------------ pairing
def norm(s):
    s = re.sub(r'\.(stl|obj|3mf)$', '', s.strip(), flags=re.I)
    return re.sub(r'\s+', ' ', s.replace('_', ' ')).strip().lower()


def folder(parts_dir):
    files = sorted(pathlib.Path(parts_dir).glob('*.stl')) + sorted(pathlib.Path(parts_dir).glob('*.STL'))
    stems = [norm(f.name) for f in files]
    pre = ''
    if len(stems) > 1:
        words = [s.split(' ') for s in stems]
        k = 0
        while all(len(w) > k + 1 for w in words) and len({w[k] for w in words}) == 1:
            k += 1
        pre = ' '.join(words[0][:k])
    out = {}
    for f, s in zip(files, stems):
        key = s[len(pre):].strip() if pre and s.startswith(pre + ' ') else s
        out[f.name] = dict(path=f, key=key)
    return out


class Lib:
    """STL files and their bodies, read once."""
    def __init__(self, files):
        self.files, self.cache = files, {}

    def mesh(self, ref):
        if ref not in self.cache:
            name, _, k = ref.partition('#')
            V, F = read_stl(self.files[name]['path'])
            if k:
                V, F = bodies(V, F)[int(k)]
            self.cache[ref] = (V, F, invariants(V, F))
        return self.cache[ref]

    def nbodies(self, name):
        key = name + '#n'
        if key not in self.cache:
            V, F, _ = self.mesh(name)
            self.cache[key] = len(bodies(V, F))
        return self.cache[key]


def pair(objs, meshes, lib, given):
    """{object id: (ref, how)}; how is 'имя', 'карта', 'форма'."""
    out = {}
    by_key = {v['key']: n for n, v in lib.files.items()}
    by_full = {norm(n): n for n in lib.files}
    for o in objs:
        if o.name in given:
            out[o.id] = (given[o.name], 'карта'); continue
        for cand in [norm(o.name)] + [norm(q['source']) for q in o.parts if q['source']]:
            hit = by_key.get(cand) or by_full.get(cand)
            if hit:
                out[o.id] = (hit, 'имя'); break
    taken = {r for r, _ in out.values()}
    left = [o for o in objs if o.id not in out and len(meshes[o.id][1])]
    if not left:
        return out
    refs = []
    for n in lib.files:
        if n in taken:
            continue
        refs.append(n)
        if lib.nbodies(n) > 1:
            refs += [f'{n}#{k}' for k in range(lib.nbodies(n)) if f'{n}#{k}' not in taken]
    for o in left:
        oi = meshes[o.id][2]
        hits = [r for r in refs if r not in taken and same_shape(oi, lib.mesh(r)[2])]
        if len(hits) > 1:      # mirror twins: only one of them fits with a proper rotation
            Vo, Fo, _ = meshes[o.id]
            fit = [(rigid_fit(lib.mesh(r)[0], lib.mesh(r)[1], Vo, Fo, n=2500)[2], r) for r in hits]
            fit.sort(reverse=True)
            hits = [r for s, r in fit if s > fit[0][0] - 0.05 and s > 0.8] or hits[:1]
        if hits:
            out[o.id] = (hits[0], 'форма')
            taken.add(hits[0])
            taken.add(hits[0].split('#')[0])
    return out


def suggest(left, meshes, lib, taken, limit=0.25):
    """Unpaired objects against unpaired files: the closest by invariants, as a hint only."""
    refs = [n for n in lib.files if n not in taken]
    refs += [f'{n}#{k}' for n in list(refs) if lib.nbodies(n) > 1 for k in range(lib.nbodies(n))]

    def gap(i, j):
        xs = [(i['vol'], j['vol']), (i['area'], j['area'])] + list(zip(i['rad'], j['rad']))
        return max(abs(x - y) / max(abs(x), abs(y), 1e-9) for x, y in xs)
    pairs = sorted((gap(meshes[o.id][2], lib.mesh(r)[2]), k, r)
                   for k, o in enumerate(left) for r in refs)
    out, used_o, used_r = [], set(), set()
    for sc, k, r in pairs:
        if sc > limit:
            break
        if k in used_o or r in used_r or r.split('#')[0] in used_r:
            continue
        used_o.add(k); used_r.add(r); used_r.add(r.split('#')[0])
        out.append((left[k], r, sc))
    return out


def compare(oi, si):
    d = []
    if abs(oi['vol'] - si['vol']) > 1e-3:
        d.append(f"объём {oi['vol']:.1f} → {si['vol']:.1f} мм³ "
                 f"({100 * (si['vol'] - oi['vol']) / max(oi['vol'], 1e-9):+.1f}%)")
    if abs(oi['area'] - si['area']) > 1e-3:
        d.append(f"площадь {oi['area']:.1f} → {si['area']:.1f} мм²")
    d.append('радиусы инерции ' + ' '.join(f'{x:.2f}' for x in oi['rad']) + ' → '
             + ' '.join(f'{x:.2f}' for x in si['rad']) + ' мм')
    return '; '.join(d)


def survey(project, parts_dir, mapfile=None, pairs=()):
    p, objs = read_project(project)
    lib = Lib(folder(parts_dir))
    given = json.loads(pathlib.Path(mapfile).read_text(encoding='utf-8')) if mapfile else {}
    for s in pairs:
        k, v = s.split('=', 1); given[k] = v
    meshes = {}
    for o in objs:
        V, F = object_mesh(p, o)
        meshes[o.id] = (V, F, invariants(V, F) if len(F) else
                        dict(vol=0.0, area=0.0, rad=np.zeros(3), cen=np.zeros(3)))
    return p, objs, lib, meshes, pair(objs, meshes, lib, given)


# ------------------------------------------------------------------ commands
def cmd_diff(a):
    p, objs, lib, meshes, pr = survey(a.project, a.parts, a.map, a.pair)
    print(f'проект: объектов {len(objs)}; папка: STL {len(lib.files)}')
    same = 0
    for o in objs:
        if o.id not in pr:
            continue
        ref, how = pr[o.id]
        oV, oF, oi = meshes[o.id]
        sV, sF, si = lib.mesh(ref)
        tag = '' if how == 'имя' else f'  [{how}]'
        if '#' in ref:
            name, k = ref.split('#')
            tag += f'  (тело {int(k) + 1} из {lib.nbodies(name)})'
        if same_shape(oi, si):
            same += 1
            print(f'  =  {o.name:<24} ↔ {ref:<28} та же форма, объём {oi["vol"]:.1f} мм³{tag}')
        else:
            print(f'  ≠  {o.name:<24} ↔ {ref:<28} ДРУГАЯ: {compare(oi, si)}{tag}')
    miss_dir = [o.name for o in objs if o.id not in pr]
    used = {r.split('#')[0] for r, _ in pr.values()}
    miss_prj = [n for n in lib.files if n not in used]
    guess = suggest([o for o in objs if o.id not in pr and len(meshes[o.id][1])],
                    meshes, lib, {r.split('#')[0] for r, _ in pr.values()})
    for o, ref, sc in guess:
        print(f'  ?  {o.name:<24} ≈ {ref:<28} похоже (расхождение {100 * sc:.0f}%): '
              f'{compare(meshes[o.id][2], lib.mesh(ref)[2])} — подтвердить: --pair "{o.name}={ref}"')
    print(f'\nсовпало {same}, различается {len(pr) - same}, похожих без пары {len(guess)}')
    print('нет в папке: ' + (', '.join(miss_dir) or '—'))
    print('нет в проекте: ' + (', '.join(miss_prj) or '—'))
    if a.write_map:
        pathlib.Path(a.write_map).write_text(
            json.dumps({o.name: pr[o.id][0] for o in objs if o.id in pr},
                       ensure_ascii=False, indent=1), encoding='utf-8')
        print(f'карта пар → {a.write_map}')


def cmd_sync(a):
    p, objs, lib, meshes, pr = survey(a.project, a.parts, a.map, a.pair)
    changed = 0
    for o in objs:
        if o.id not in pr:
            continue
        ref = pr[o.id][0]
        oV, oF, oi = meshes[o.id]
        sV, sF, si = lib.mesh(ref)
        if same_shape(oi, si) and not a.force:
            continue
        if len(o.comps) != 1:
            print(f'  ПРОПУСК {o.name}: частей {len(o.comps)}, заменяю только объект из одной части')
            continue
        item = o.items[0][0] if o.items else np.eye(4)
        wi = np.linalg.inv(item)
        starts = [(np.eye(3), np.zeros(3)), (wi[:3, :3], wi[:3, 3])]
        if o.assemble is not None:
            ai = np.linalg.inv(o.assemble); starts.append((ai[:3, :3], ai[:3, 3]))
        if a.align == 'identity':
            R, t, share, rms, tol = np.eye(3), np.zeros(3), 1.0, 0.0, 0.0
        elif a.align == 'world':
            R, t, share, rms, tol = wi[:3, :3], wi[:3, 3], 1.0, 0.0, 0.0
        else:
            R, t, share, rms, tol = rigid_fit(sV, sF, oV, oF, starts)
            if share < a.min_fit:
                print(f'  ПРОПУСК {o.name}: новая сетка ложится на старую только на {100 * share:.0f}% '
                      f'(порог {100 * a.min_fit:.0f}%) — проверить пару или задать --align')
                continue
        nV = sV @ R.T + t
        _, _, attrs = load_mesh(p, o.comps[0][0], o.comps[0][1])
        painted = any(x.strip() for x in attrs)
        keep = None
        note = ''
        if painted:
            if len(sF) == len(oF):
                # the same faces in the same order: either the face-to-face adjacency is
                # identical (an edit that moved vertices only), or the faces lie where they were
                c_old, c_new = oV[oF].mean(1), nV[sF].mean(1)
                if (np.array_equal(adjacency(oV, oF), adjacency(sV, sF)) or
                        np.mean(np.linalg.norm(c_old - c_new, axis=1) < max(tol, 0.05)) > 0.9):
                    keep = attrs
            note = ('покраска сохранена' if keep else
                    f'ПОКРАСКА СНЯТА (граней {len(oF)} → {len(sF)}' +
                    (', порядок граней другой' if len(sF) == len(oF) else '') + ')')
        put_mesh(p, o, nV, sF, keep)
        changed += 1
        zmin_old = apply(item, oV)[:, 2].min(); zmin_new = apply(item, nV)[:, 2].min()
        fit = f'совпало {100 * share:.0f}% поверхности, rms {rms:.3f} мм' if a.align == 'auto' else f'--align {a.align}'
        print(f'  ЗАМЕНЁН {o.name} ← {ref}: граней {len(oF)} → {len(sF)}, объём '
              f'{oi["vol"]:.1f} → {si["vol"]:.1f} мм³; {fit}; низ на столе {zmin_old:.3f} → {zmin_new:.3f} мм'
              + (f'; {note}' if note else ''))
    added = 0
    if a.add is not None:
        used = {r.split('#')[0] for r, _ in pr.values()}
        want = [n for n in lib.files if n not in used and (not a.add or n in a.add or lib.files[n]['key'] in map(norm, a.add))]
        bed = (256.0, 256.0)
        try:
            import hardware
            bed = hardware.bed()
        except Exception:
            pass
        for n in want:
            V, F, _ = lib.mesh(n)
            lo, hi = V.min(0), V.max(0)
            V = V - [(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, lo[2]]
            name = lib.files[n]['key']
            p.add_object(name, V, F, a.filament, 1, (bed[0] / 2, bed[1] / 2, 0))
            added += 1
            print(f'  ДОБАВЛЕН {name} ← {n}: граней {len(F)}, филамент {a.filament}, '
                  f'в центр тарелки 1 — разложить вручную')
    p.save(a.o)
    print(f'\nзаменено {changed}, добавлено {added}; позы и настройки объектов не тронуты → {a.o}')


def project_keys(p):
    raw = p.files.get('Metadata/project_settings.config')
    try:
        return set(json.loads(raw))
    except Exception:
        return None


def cmd_set(a):
    p = Project(a.project)
    known = project_keys(p)
    kv = []
    for s in a.kv:
        if '=' not in s:
            sys.exit(f'ожидалось key=value, получено {s!r}')
        k, v = s.split('=', 1)
        if known is not None and k not in known and k != 'extruder' and not a.force:
            sys.exit(f'ключа {k!r} нет в настройках проекта — слайсер его молча проигнорирует; '
                     f'--force, если уверен')
        if k == 'extruder' and not v.isdigit():
            sys.exit(f'extruder — номер филамента, получено {v!r}')
        kv.append((k, v))
    for name in a.object:
        try:
            oid = p.object_id(name)
        except KeyError:
            sys.exit(f'нет объекта {name!r}')
        m = re.search(r'(<object id="%s">)(.*?)(</object>)' % oid, p.cfg, re.S)
        blk = m.group(2)
        head, sep, tail = blk.partition('<part')
        for k, v in kv:
            old = re.search(r'\n\s*<metadata key="%s" value="([^"]*)"/>' % re.escape(k), head)
            if old:
                head = head[:old.start()] + head[old.end():]
            if v != '':
                anchor = re.search(r'<metadata key="(?:extruder|name)" value="[^"]*"/>\n', head)
                line = f'    <metadata key="{k}" value="{v}"/>\n'
                if anchor:
                    head = head[:anchor.end()] + line + head[anchor.end():]
                else:
                    head = '\n' + line + head.lstrip('\n')
            print(f'  {name}: {k} {old.group(1) if old else "—"} → {v if v != "" else "— (снят)"}')
        p.cfg = p.cfg[:m.start(2)] + head + sep + tail + p.cfg[m.end(2):]
    p.save(a.o)
    print(f'→ {a.o}')


PALETTE = {'white': 'FFFFFF', 'black': '000000', 'grey': '808080', 'silver': 'C0C0C0',
           'red': 'D02020', 'orange': 'F08020', 'yellow': 'F5D000', 'gold': 'C8A040',
           'green': '20A040', 'lime': 'A0D040', 'blue': '2050C0', 'navy': '1A2A60',
           'cyan': '30B0E0', 'purple': '7040B0', 'pink': 'F090B0', 'brown': '704020',
           'beige': 'E8C8A0', 'skin': 'F0B890'}


def colour_name(hexs):
    h = hexs.lstrip('#')[:6]
    try:
        c = np.array([int(h[i:i + 2], 16) for i in (0, 2, 4)], float)
    except ValueError:
        return None
    return min(PALETTE, key=lambda n: np.sum((np.array([int(PALETTE[n][i:i + 2], 16)
                                                         for i in (0, 2, 4)]) - c) ** 2 * [3, 4, 2]))


def cmd_export(a):
    p, objs = read_project(a.project)
    names = {}
    try:
        cols = json.loads(p.files['Metadata/project_settings.config']).get('filament_colour', [])
        for i, c in enumerate(cols, 1):
            names[i] = colour_name(c) or f'filament{i}'
    except Exception:
        pass
    for s in a.names or []:
        for kv in s.split(','):
            k, v = kv.split('='); names[int(k)] = v
    out = pathlib.Path(a.o); out.mkdir(parents=True, exist_ok=True)
    groups = []
    for o in objs:
        items = [it for it, pr in o.items if pr == '1' or a.all]
        if not items:
            print(f'  ПРОПУСК {o.name}: не печатается (printable=0); --all, чтобы взять')
            continue
        if any(q['subtype'] != 'normal_part' for q in o.parts):
            print(f'  {o.name}: части-модификаторы и вычитаемые части в STL не идут — только normal_part')
        V, F = object_mesh(p, o)
        W = apply(items[0], V)
        inv = invariants(W, F)
        for g in groups:
            if g['fil'] == o.extruder and same_shape(g['inv'], inv) and len(g['F']) == len(F):
                if rigid_fit(W, F, g['V'], g['F'], n=2500)[2] > 0.9:
                    g['n'] += len(items); g['also'].append(o.name); break
        else:
            groups.append(dict(name=o.name, fil=o.extruder, V=W, F=F, inv=inv, n=len(items), also=[]))
    for g in groups:
        V = g['V']
        if not a.world:
            lo, hi = V.min(0), V.max(0)
            V = V - [(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, lo[2]]
        safe = re.sub(r'[\\/:*?"<>|]+', '-', g['name']).strip().replace(' ', '_')
        fname = f"{names.get(g['fil'], 'filament%d' % g['fil'])}_{safe}" + (f"_x{g['n']}" if g['n'] > 1 else '') + '.stl'
        write_stl(out / fname, V, g['F'])
        lo, hi = V.min(0), V.max(0)
        ext = hi - lo
        print(f"  {fname:<40} граней {len(g['F']):>7}  габарит {ext[0]:.2f} × {ext[1]:.2f} × {ext[2]:.2f} мм  "
              f"низ {lo[0]:.2f} {lo[1]:.2f} {lo[2]:.2f}" + (f"  (+ {', '.join(g['also'])})" if g['also'] else ''))
    print(f'\nфайлов {len(groups)} → {out}')
    print('филаменты: ' + ', '.join(f'{k}={v}' for k, v in sorted(names.items())))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    for nm in ('diff', 'sync'):
        q = sub.add_parser(nm)
        q.add_argument('project'); q.add_argument('parts')
        q.add_argument('--map', help='JSON {объект: файл.stl[#тело]} — пары, которые не находятся сами')
        q.add_argument('--pair', action='append', default=[], metavar='ОБЪЕКТ=ФАЙЛ',
                       help='одна пара вручную; файл.stl#k — k-е тело файла, с нуля')
    q = sub.choices['diff']
    q.add_argument('--write-map', metavar='JSON', help='сохранить найденные пары')
    q = sub.choices['sync']
    q.add_argument('-o', required=True)
    q.add_argument('--align', choices=('auto', 'identity', 'world'), default='auto',
                   help='identity: STL уже в координатах объекта; world: STL в позе на столе')
    q.add_argument('--min-fit', type=float, default=0.5,
                   help='доля поверхности, которая обязана совпасть со старой сеткой')
    q.add_argument('--force', action='store_true', help='заменить и совпавшие по форме')
    q.add_argument('--add', nargs='*', help='добавить файлы, которых нет в проекте (без имён — все)')
    q.add_argument('--filament', type=int, default=1, help='филамент новых объектов')
    q = sub.add_parser('set')
    q.add_argument('project'); q.add_argument('kv', nargs='+', metavar='key=value')
    q.add_argument('--object', action='append', required=True)
    q.add_argument('-o', required=True)
    q.add_argument('--force', action='store_true', help='записать ключ, которого нет в проекте')
    q = sub.add_parser('export')
    q.add_argument('project'); q.add_argument('-o', required=True)
    q.add_argument('--world', action='store_true', help='оставить координаты стола')
    q.add_argument('--all', action='store_true', help='и объекты с printable=0')
    q.add_argument('--names', action='append', metavar='1=white,2=black')
    a = ap.parse_args()
    {'diff': cmd_diff, 'sync': cmd_sync, 'set': cmd_set, 'export': cmd_export}[a.cmd](a)


if __name__ == '__main__':
    main()
