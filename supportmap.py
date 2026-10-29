#!/usr/bin/env python3
"""Where supports actually touch a model, judged by the sliced G-code, and how
much of that contact lands on the surface somebody will look at.

`gcode_report.py` says whether a print has supports at all; `figcheck.py`
predicts them by casting rays at the mesh. Neither says what a person choosing
the pose of a part needs: *which faces* will carry support scars after this
slice, and whether those faces show. This tool reads what the slicer really
laid down, finds the place on the model each support column ends against, and
adds the contact up face by face.

    uv run --with numpy --with scipy --with trimesh --with embreex \\
        python tools/supportmap.py <command> ...

Commands
--------
map     PROJECT.3mf [GCODE]  contact area per object and per zone, and the
        largest visible contact spots with coordinates. Without GCODE the
        project is sliced first with its own settings (`--supports` switches
        supports on in a copy). The pose is the one in the project: nothing is
        re-oriented or re-arranged.
sweep   PROJECT.3mf --object NAME --poses "rx,ry[,rz];..."  the same part in
        several tilts, each sliced on its own and ranked by visible contact,
        then time, then grams. Every pose is a full slice, so the list is short
        (`--max`, 6 by default).
neck    GCODE  the thinnest cross-sections of support trunks - where a tree
        support snaps while printing.

    supportmap.py map  part.3mf work/out/plate_1.gcode --joints auto --hide-fil 3
    supportmap.py map  part.3mf --supports --out work/sm
    supportmap.py sweep part.3mf --object Arm --poses "0,0;30,0;-30,0;0,45" --supports
    supportmap.py neck work/out/plate_1.gcode

How a contact is found. Every support extrusion is sampled along its path
(arcs G2/G3 included) into 0.1 mm cells. In each cell the support forms one or
more columns of consecutive layers. The top of a column is cast upward to the
model: when the surface lies within the top Z distance plus one and a half
layers, that is a contact under the model. The bottom of a column that does
not start on the bed is cast downward the same way: a support standing on the
model. Contact cells are grouped into spots; inside a spot the gaps between
interface lines are closed (`--fill`, half the interface spacing and a bit),
the strand width is added at the rim, and each cell is spread over the face it
touched by 1/|nz| - a sloped face has more surface over the same cell.

The G-code and the project share bed coordinates. When they do not (a plate
other than the first, a foreign file), the offset between the outer walls and
the mesh is measured from their bounding boxes and applied; the tool prints it.

What counts as hidden, in this order, everything else is visible:

* `--hide-object NAME`  a whole object that ends up inside the assembly;
* `--hide-mask NAME=faces.npy`  a per-face boolean array (or face indices) of
  that object, in the order of its faces in the project;
* `--hide-fil 2,3`  faces painted with, or belonging by extruder to, these
  filaments - a joint area painted in its own colour;
* `--joints auto`  exactly planar regions of at least `--plane-min` mm2: the
  cut faces of a part split from an organic model. Off by default, because on
  a mechanical part every face is a plane;
* niche  a face that sees out through less than `--open-min` of its hemisphere,
  rays blocked within `--niche` mm by the object itself: the inside of a
  socket or a pocket. Under a chin it can trigger too - read the spot list.

Face classes are bound to face indices, not to positions, so they hold for
every pose of the sweep.
"""
import argparse, json, math, os, pathlib, re, shutil, subprocess, sys

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import partedit                      # noqa: E402  reading and writing a 3MF
import paint                         # noqa: E402  triangle and paint regexes
import gcode_report                  # noqa: E402  grams by line type

BS = os.environ.get("BS") or "/Applications/BambuStudio.app/Contents/MacOS/BambuStudio"
SUPPORT = ("Support", "Support interface", "Support transition")
G = 0.1                              # mm, raster cell
REASONS = ("видимо", "объект", "маска", "покраска", "стык", "ниша")
HDR_KEYS = ("layer_height", "initial_layer_print_height", "support_top_z_distance",
            "support_bottom_z_distance", "support_interface_spacing", "support_type",
            "support_line_width", "enable_support")


def die(msg, code=2):
    print(msg)
    sys.exit(code)


# ------------------------------------------------------------------ project

class Obj:
    """One printed object: its faces in the object frame and on the bed."""

    def __init__(self, oid, name, ext, V, F, fil, R, t):
        self.oid, self.name, self.ext = oid, name, ext
        self.V, self.F, self.fil = V, F, fil
        self.R, self.t = R, t
        self.W = V @ R.T + t


def _trf(s):
    if not s:
        return np.eye(3), np.zeros(3)
    a = [float(x) for x in s.split()]
    return np.array(a[:9]).reshape(3, 3).T, np.array(a[9:12])


def _trf_str(R, t):
    return " ".join(f"{v:.9g}" for v in list(R.T.reshape(-1)) + list(t))


OBJ_RE = re.compile(r'<object id="(\d+)"[^>]*>(.*?)</object>', re.S)


def _mesh_in(text, oid):
    for m in OBJ_RE.finditer(text):
        if m.group(1) == str(oid) and "<mesh" in m.group(2):
            body = m.group(2)
            V = np.array(paint.VERT.findall(body), float).reshape(-1, 3)
            F, code = [], []
            for t in paint.TRI.finditer(body):
                F.append((int(t.group(1)), int(t.group(2)), int(t.group(3))))
                code.append(paint.code_of(t))
            return V, np.array(F, np.int64).reshape(-1, 3), code
    return None


def load_project(path, plate=None):
    """The printed objects of a 3MF (of one plate, when the project has plates)."""
    p = partedit.Project(path)
    meta, parts = {}, {}
    for oid, blk in re.findall(r'<object id="(\d+)">(.*?)</object>', p.cfg, re.S):
        kv = dict(re.findall(r'<metadata key="(\w+)" value="([^"]*)"/>', blk.split("<part")[0]))
        meta[oid] = (kv.get("name", ""), int(kv.get("extruder", 1) or 1))
        for pid, sub, body in re.findall(r'<part id="(\d+)" subtype="(\w+)"[^>]*>(.*?)</part>',
                                         blk, re.S):
            e = re.search(r'key="extruder" value="(\d+)"', body)
            parts[(oid, pid)] = (sub, int(e.group(1)) if e else None)
    on_plate = None
    if plate is not None:
        for blk in re.findall(r"<plate>(.*?)</plate>", p.cfg, re.S):
            pid = re.search(r'plater_id" value="(\d+)"', blk)
            if pid and int(pid.group(1)) == plate:
                on_plate = set(re.findall(r'object_id" value="(\d+)"', blk))
    items = {}
    for attrs in re.findall(r"<item ([^>]*?)/?>", p.top):
        oid = re.search(r'objectid="(\d+)"', attrs).group(1)
        if 'printable="0"' in attrs:
            continue                 # switched off in the GUI: not in the G-code either
        tr = re.search(r'transform="([^"]*)"', attrs)
        items.setdefault(oid, tr.group(1) if tr else "")
    texts, out = {}, []
    for m in OBJ_RE.finditer(p.top):
        oid, body = m.group(1), m.group(2)
        if oid not in items or (on_plate is not None and oid not in on_plate):
            continue
        name, ext = meta.get(oid, (f"объект {oid}", 1))
        Vs, Fs, fil, n0 = [], [], [], 0
        comps = re.findall(r"<component ([^>]*?)/>", body)
        srcs = []
        if comps:
            for c in comps:
                path = re.search(r'p:path="([^"]+)"', c)
                cid = re.search(r'objectid="(\d+)"', c).group(1)
                tr = re.search(r'transform="([^"]*)"', c)
                sub, pext = parts.get((oid, cid), ("normal_part", None))
                if sub != "normal_part":
                    continue
                ent = path.group(1).lstrip("/") if path else "3D/3dmodel.model"
                if ent not in texts:
                    texts[ent] = p.files[ent].decode("utf-8")
                srcs.append((texts[ent], cid, tr.group(1) if tr else "", pext))
        else:
            srcs.append((p.top, oid, "", None))
        for text, cid, tr, pext in srcs:
            got = _mesh_in(text, cid)
            if got is None:
                continue
            V, F, code = got
            Rc, tc = _trf(tr)
            Vs.append(V @ Rc.T + tc)
            Fs.append(F + n0)
            n0 += len(V)
            base = pext or ext
            fil += [base if c == "" else paint.CODE2FIL.get(c, -1) for c in code]
        if not Fs:
            continue
        R, t = _trf(items[oid])
        out.append(Obj(oid, name, ext, np.vstack(Vs), np.vstack(Fs),
                       np.array(fil, np.int64), R, t))
    if not out:
        die(f"в {path} не нашёл ни одного печатаемого объекта"
            + (f" на тарелке {plate}" if plate is not None else ""))
    return out


# ------------------------------------------------------------------ G-code

def parse_gcode(path, arc_step=0.2):
    """Extrusion segments with their feature, height and width, arcs flattened.

    -> (dict of arrays x0 y0 x1 y1 z w f, list of feature names, header dict)"""
    hdr, feats, fid = {}, [], {}
    X0, Y0, X1, Y1, Z, Wd, Fe = [], [], [], [], [], [], []
    absxyz, abse = True, False
    x = y = z = e = 0.0
    feat, w = None, 0.42
    hre = re.compile(r";\s*(\w+)\s*=\s*(.*)$")
    with open(path, errors="ignore") as fh:
        for line in fh:
            c = line[:1]
            if c == ";":
                if line.startswith("; FEATURE:"):
                    feat = line[10:].strip()
                    if feat not in fid:
                        fid[feat] = len(feats); feats.append(feat)
                elif line.startswith("; LINE_WIDTH:"):
                    try:
                        w = float(line[13:])
                    except ValueError:
                        pass
                elif "=" in line:
                    m = hre.match(line)
                    if m and m.group(1) in HDR_KEYS and m.group(1) not in hdr:
                        hdr[m.group(1)] = m.group(2).strip()
                continue
            if c not in ("G", "M"):
                continue
            tok = line.split(";", 1)[0].split()
            if not tok:
                continue
            cmd = tok[0]
            if cmd == "G90":
                absxyz = True; continue
            if cmd == "G91":
                absxyz = False; continue
            if cmd == "M82":
                abse = True; continue
            if cmd == "M83":
                abse = False; continue
            if cmd == "G92":
                for t in tok[1:]:
                    if t[0] == "E":
                        e = float(t[1:] or 0)
                continue
            if cmd not in ("G0", "G1", "G2", "G3"):
                continue
            a = {}
            for t in tok[1:]:
                k = t[0]
                if k in "XYZEIJ":
                    try:
                        a[k] = float(t[1:])
                    except ValueError:
                        pass
            if absxyz:
                nx, ny, nz = a.get("X", x), a.get("Y", y), a.get("Z", z)
            else:
                nx, ny, nz = x + a.get("X", 0), y + a.get("Y", 0), z + a.get("Z", 0)
            de = 0.0
            if "E" in a:
                if abse:
                    de, e = a["E"] - e, a["E"]
                else:
                    de = a["E"]
            if de > 0 and feat is not None and (nx != x or ny != y or cmd in ("G2", "G3")):
                f = fid[feat]
                if cmd in ("G2", "G3") and ("I" in a or "J" in a):
                    cx, cy = x + a.get("I", 0), y + a.get("J", 0)
                    r = math.hypot(x - cx, y - cy)
                    a0 = math.atan2(y - cy, x - cx)
                    a1 = math.atan2(ny - cy, nx - cx)
                    sw = a1 - a0
                    if cmd == "G2":
                        if sw >= -1e-9:
                            sw -= 2 * math.pi
                    elif sw <= 1e-9:
                        sw += 2 * math.pi
                    n = max(1, int(math.ceil(abs(sw) * r / arc_step)))
                    px, py = x, y
                    for k in range(1, n + 1):
                        ang = a0 + sw * k / n
                        qx, qy = (cx + r * math.cos(ang), cy + r * math.sin(ang)) if k < n else (nx, ny)
                        X0.append(px); Y0.append(py); X1.append(qx); Y1.append(qy)
                        Z.append(nz); Wd.append(w); Fe.append(f)
                        px, py = qx, qy
                else:
                    X0.append(x); Y0.append(y); X1.append(nx); Y1.append(ny)
                    Z.append(nz); Wd.append(w); Fe.append(f)
            x, y, z = nx, ny, nz
    S = {k: np.array(v, float) for k, v in
         (("x0", X0), ("y0", Y0), ("x1", X1), ("y1", Y1), ("z", Z), ("w", Wd))}
    S["f"] = np.array(Fe, np.int64)
    return S, feats, hdr


def _hnum(hdr, key, default):
    try:
        return float(str(hdr.get(key, default)).split(",")[0])
    except ValueError:
        return default


def _sample(S, mask, step):
    """Points every `step` mm along the selected segments: (N,3) and widths."""
    x0, y0, x1, y1 = S["x0"][mask], S["y0"][mask], S["x1"][mask], S["y1"][mask]
    L = np.hypot(x1 - x0, y1 - y0)
    n = np.maximum(1, np.ceil(L / step).astype(np.int64))
    seg = np.repeat(np.arange(len(L)), n)
    k = np.arange(len(seg)) - np.repeat(np.cumsum(n) - n, n)
    t = (k + 0.5) / n[seg]
    P = np.c_[x0[seg] + t * (x1 - x0)[seg], y0[seg] + t * (y1 - y0)[seg], S["z"][mask][seg]]
    return P, S["w"][mask][seg]


def _disk(r):
    r = max(0, int(round(r)))
    yy, xx = np.mgrid[-r:r + 1, -r:r + 1]
    return xx * xx + yy * yy <= r * r + 0.25


# ------------------------------------------------------------------ contact

def _fib(n):
    i = np.arange(n) + 0.5
    phi = np.arccos(1 - 2 * i / n)
    th = np.pi * (1 + 5 ** 0.5) * i
    return np.c_[np.cos(th) * np.sin(phi), np.sin(th) * np.sin(phi), np.cos(phi)]


def _openness(obj, faces, depth):
    """Share of the hemisphere each face sees out through, rays against its own object."""
    import trimesh
    m = trimesh.Trimesh(obj.W, obj.F, process=False)
    P = obj.W[obj.F[faces]]
    C = P.mean(1)
    N = np.cross(P[:, 1] - P[:, 0], P[:, 2] - P[:, 0])
    N /= np.maximum(np.linalg.norm(N, axis=1, keepdims=True), 1e-12)
    D = _fib(64)
    cos = N @ D.T                                    # faces x dirs
    fi, di = np.nonzero(cos > 0.15)
    org = C[fi] + N[fi] * 0.02
    blocked = np.zeros(len(fi), bool)
    if len(fi):
        loc, ir, _ = m.ray.intersects_location(org, D[di], multiple_hits=False)
        if len(ir):
            d = np.linalg.norm(loc - org[ir], axis=1)
            blocked[ir[d < depth]] = True
    tot = np.bincount(fi, minlength=len(faces))
    blk = np.bincount(fi, weights=blocked, minlength=len(faces))
    return np.where(tot > 0, 1 - blk / np.maximum(tot, 1), 1.0)


def _planar(obj, min_area):
    """Faces lying in exact planes of at least min_area mm2 - cut faces."""
    P = obj.V[obj.F]
    N = np.cross(P[:, 1] - P[:, 0], P[:, 2] - P[:, 0])
    A = 0.5 * np.linalg.norm(N, axis=1)
    N = N / np.maximum(2 * A[:, None], 1e-12)
    d = (N * P.mean(1)).sum(1)
    key = np.c_[np.round(N / 0.01), np.round(d / 0.02)].astype(np.int64)
    _, inv = np.unique(key, axis=0, return_inverse=True)
    inv = inv.reshape(-1)
    area = np.bincount(inv, weights=A)
    return area[inv] >= min_area


def classify(objs, touched, a):
    """Reason index per touched face, per object: 0 visible, then REASONS order."""
    masks = {}
    for spec in a.hide_mask or []:
        nm, f = spec.split("=", 1)
        masks[nm] = np.load(f)
    hide_fil = {int(x) for x in (a.hide_fil or "").split(",") if x.strip()}
    out = {}
    for k, o in enumerate(objs):
        faces = touched.get(k)
        if faces is None or not len(faces):
            continue
        r = np.zeros(len(faces), np.int64)
        free = lambda: r == 0                              # noqa: E731
        if o.name in (a.hide_object or []):
            r[:] = 1
        if o.name in masks:
            mk = masks[o.name]
            if mk.dtype == bool:
                if len(mk) != len(o.F):
                    die(f"маска {o.name}: {len(mk)} значений, а граней {len(o.F)}")
                hit = mk[faces]
            else:
                hit = np.isin(faces, mk)
            r[free() & hit] = 2
        if hide_fil:
            r[free() & np.isin(o.fil[faces], list(hide_fil))] = 3
        if a.joints == "auto":
            r[free() & _planar(o, a.plane_min)[faces]] = 4
        if a.niche > 0:
            sel = np.flatnonzero(free())
            if len(sel):
                op = _openness(o, faces[sel], a.niche)
                r[sel[op < a.open_min]] = 5
        out[k] = r
    return out


def contact_map(objs, gcode, a):
    """Contact spots and area per face. -> dict with everything the report needs."""
    import trimesh
    from scipy import ndimage
    from scipy.spatial import cKDTree
    S, feats, hdr = parse_gcode(gcode)
    lh = _hnum(hdr, "layer_height", 0.2)
    first = _hnum(hdr, "initial_layer_print_height", lh)
    top_gap = _hnum(hdr, "support_top_z_distance", 0.2)
    bot_gap = _hnum(hdr, "support_bottom_z_distance", top_gap)
    spacing = _hnum(hdr, "support_interface_spacing", 0.5)
    fsup = [i for i, f in enumerate(feats) if f in SUPPORT]
    res = dict(lh=lh, top_gap=top_gap, bot_gap=bot_gap, offset=(0.0, 0.0),
               support_m=0.0, spots=[], per_obj={}, feats=feats, hdr=hdr, fill=0.0)

    # bed coordinates of the G-code against the project
    Wall = np.vstack([o.W for o in objs])
    ow = [i for i, f in enumerate(feats) if f == "Outer wall"]
    if ow:
        m = np.isin(S["f"], ow)
        gx = np.r_[S["x0"][m], S["x1"][m]]; gy = np.r_[S["y0"][m], S["y1"][m]]
        up = Wall[Wall[:, 2] > first / 2]
        if len(up) and len(gx):
            mc = (up[:, :2].min(0) + up[:, :2].max(0)) / 2
            gc = np.array([(gx.min() + gx.max()) / 2, (gy.min() + gy.max()) / 2])
            off = mc - gc
            res["offset"] = tuple(off)
            if np.hypot(*off) > 0.3:
                for k in ("x0", "x1"):
                    S[k] = S[k] + off[0]
                for k in ("y0", "y1"):
                    S[k] = S[k] + off[1]
    smask = np.isin(S["f"], fsup)
    res["support_m"] = float(np.hypot(S["x1"][smask] - S["x0"][smask],
                                      S["y1"][smask] - S["y0"][smask]).sum() / 1000)
    res["neck"] = necks(S, feats, first) if smask.any() else []
    if not smask.any():
        return res
    P, Wd = _sample(S, smask, G / 2)
    # gaps between interface lines are spacing + width wide between centres
    fill = a.fill if a.fill is not None else spacing / 2 + float(np.median(Wd)) / 2 + 0.1
    res["fill"] = fill
    x0, y0 = P[:, 0].min() - 1, P[:, 1].min() - 1
    ix = np.floor((P[:, 0] - x0) / G).astype(np.int64)
    iy = np.floor((P[:, 1] - y0) / G).astype(np.int64)
    ny = int(iy.max()) + 2
    cell = ix * ny + iy
    zq = np.round(P[:, 2], 3)
    o = np.lexsort((zq, cell))
    c_s, z_s, w_s = cell[o], zq[o], Wd[o]
    keep = np.r_[True, (c_s[1:] != c_s[:-1]) | (z_s[1:] != z_s[:-1])]
    c_u, z_u, w_u = c_s[keep], z_s[keep], w_s[keep]
    brk = max(2.5 * lh, 0.45)
    new = np.r_[True, (c_u[1:] != c_u[:-1]) | (z_u[1:] - z_u[:-1] > brk)]
    st = np.flatnonzero(new)
    en = np.r_[st[1:] - 1, len(c_u) - 1]

    # one mesh of everything on the plate, faces traced back to objects
    offs = np.cumsum([0] + [len(o.W) for o in objs])
    foffs = np.cumsum([0] + [len(o.F) for o in objs])
    big = trimesh.Trimesh(Wall, np.vstack([o.F + offs[k] for k, o in enumerate(objs)]),
                          process=False)
    fn = big.face_normals

    def cast(cells, z0, sign, reach):
        cx = x0 + (cells // ny + 0.5) * G
        cy = y0 + (cells % ny + 0.5) * G
        org = np.c_[cx, cy, z0]
        if not len(org):
            return np.zeros(0, np.int64), np.zeros((0, 3)), np.zeros(0, np.int64)
        dirs = np.tile([0, 0, sign], (len(org), 1)).astype(float)
        loc, ir, it = big.ray.intersects_location(org, dirs, multiple_hits=False)
        d = np.abs(loc[:, 2] - org[ir, 2])
        ok = d <= reach
        return ir[ok], loc[ok], it[ok]

    up_reach = top_gap + 1.5 * lh + 0.05
    i1, l1, t1 = cast(c_u[en], z_u[en] + 1e-3, 1.0, up_reach)
    lifted = z_u[st] > first + 1e-3
    sb = np.flatnonzero(lifted)
    i2, l2, t2 = cast(c_u[st][sb], z_u[st][sb] - lh - 1e-3, -1.0, bot_gap + 1.5 * lh + 0.05)
    i2 = sb[i2]
    cc = np.r_[c_u[en][i1], c_u[st][i2]]
    loc = np.vstack([l1, l2]) if len(l2) else l1
    tri = np.r_[t1, t2]
    wid = np.r_[w_u[en][i1], w_u[st][i2]]
    side = np.r_[np.zeros(len(i1), int), np.ones(len(i2), int)]   # 0 under the model, 1 on it
    if not len(cc):
        return res
    objk = np.searchsorted(foffs, tri, side="right") - 1
    local = tri - foffs[objk]

    # spots: contact points connected in 3D, per object and per side
    area_face = [np.zeros(len(o.F)) for o in objs]
    spots = []
    VOX = max(0.7, 2 * fill)          # mm: wider than the pitch of interface lines
    for k in np.unique(objk):
        for sd in (0, 1):
            sel = np.flatnonzero((objk == k) & (side == sd))
            if not len(sel):
                continue
            q = np.floor((loc[sel] - loc[sel].min(0)) / VOX).astype(np.int64)
            grid = np.zeros(q.max(0) + 1, bool)
            grid[q[:, 0], q[:, 1], q[:, 2]] = True
            lab, n = ndimage.label(grid, structure=np.ones((3, 3, 3)))
            sid = lab[q[:, 0], q[:, 1], q[:, 2]]
            for s in range(1, n + 1):
                mem = sel[sid == s]
                gx, gy = cc[mem] // ny, cc[mem] % ny
                rw = (float(np.median(wid[mem])) / 2 - G / 2) / G
                rf = fill / G
                pad = int(rf + rw) + 3
                bx, by = gx.min() - pad, gy.min() - pad
                img = np.zeros((gx.max() - bx + pad + 1, gy.max() - by + pad + 1), bool)
                img[gx - bx, gy - by] = True
                if rf >= 1:
                    img = ndimage.binary_closing(img, structure=_disk(rf))
                if rw >= 0.5:
                    img = ndimage.binary_dilation(img, structure=_disk(rw))
                fx, fy = np.nonzero(img)
                _, nn = cKDTree(np.c_[gx - bx, gy - by]).query(np.c_[fx, fy])
                ft = tri[mem][nn]
                nz = np.abs(fn[ft, 2])
                wa = G * G / np.maximum(nz, 0.25)
                np.add.at(area_face[k], local[mem][nn], wa)
                c = (loc[mem] * 1.0).mean(0)
                o_ = objs[k]
                spots.append(dict(obj=int(k), side=sd, area=float(wa.sum()),
                                  faces=local[mem][nn], fa=wa, bed=c,
                                  local=np.linalg.solve(o_.R, c - o_.t)))
    touched = {k: np.flatnonzero(area_face[k] > 0) for k in range(len(objs))}
    why = classify(objs, touched, a)
    per = {}
    for k, o in enumerate(objs):
        f = touched[k]
        if not len(f):
            continue
        r = why[k]
        A = area_face[k][f]
        lut = np.zeros(len(o.F), np.int64)
        lut[f] = r
        by = {REASONS[i]: float(A[r == i].sum()) for i in range(len(REASONS))}
        fil = {}
        for fl in np.unique(o.fil[f]):
            mm = o.fil[f] == fl
            fil[int(fl)] = (float(A[mm].sum()), float(A[mm & (r == 0)].sum()))
        per[k] = dict(total=float(A.sum()), by=by, fil=fil,
                      on_model=sum(s["area"] for s in spots if s["obj"] == k and s["side"] == 1))
        for s in spots:
            if s["obj"] == k:
                rr = lut[s["faces"]]
                s["visible"] = float(s["fa"][rr == 0].sum())
                hid = np.bincount(rr, weights=s["fa"], minlength=len(REASONS))
                hid[0] = 0
                s["why"] = REASONS[int(hid.argmax())] if hid.sum() > 0 else ""
                s["fil"] = int(np.bincount(np.maximum(o.fil[s["faces"]], 0),
                                           weights=s["fa"]).argmax())
    res.update(spots=spots, per_obj=per)
    return res


def necks(S, feats, first, min_carry=2.0, top=5):
    """Thinnest support sections that still carry support above them.

    Each layer of support is rasterised, holes filled, and
    split into connected sections; sections are linked to the layer above by
    overlap. A section is a neck candidate when support goes on at least
    `min_carry` mm above it - a tip under the model is thin by design - and
    there is support right under it: a stub that starts in the air is not a
    trunk."""
    from scipy import ndimage
    fs = [i for i, f in enumerate(feats) if f in SUPPORT]     # a ring is closed only with all of them
    m = np.isin(S["f"], fs)
    if not m.any():
        return []
    P, Wd = _sample(S, m, G / 2)
    x0, y0 = P[:, 0].min() - 1, P[:, 1].min() - 1
    ix = np.floor((P[:, 0] - x0) / G).astype(np.int64)
    iy = np.floor((P[:, 1] - y0) / G).astype(np.int64)
    shape = (ix.max() + 3, iy.max() + 3)
    zq = np.round(P[:, 2], 3)
    o = np.argsort(-zq, kind="stable")
    zq, ix, iy, Wd = zq[o], ix[o], iy[o], Wd[o]
    cut = np.flatnonzero(np.r_[True, zq[1:] != zq[:-1], True])
    prev, ptop, cand, pend = None, None, [], []
    for a0, a1 in zip(cut[:-1], cut[1:]):
        z = zq[a0]
        img = np.zeros(shape, bool)
        img[ix[a0:a1], iy[a0:a1]] = True
        rw = (float(np.median(Wd[a0:a1])) / 2 - G / 2) / G
        if rw >= 0.5:
            img = ndimage.binary_dilation(img, structure=_disk(rw))
        img = ndimage.binary_fill_holes(img)
        lab, n = ndimage.label(img)
        tz = np.full(n + 1, z)
        if prev is not None:
            ov = (lab > 0) & (prev > 0)
            if ov.any():
                np.maximum.at(tz, lab[ov], ptop[prev[ov]])
            # a section of the layer above is a neck only with support under it
            held = set(np.unique(prev[ov]).tolist()) if ov.any() else set()
            cand += [c for lb, c in pend if lb in held]
        pend = []
        if z > first + 1e-3 and n:
            area = np.bincount(lab.ravel(), minlength=n + 1) * G * G
            cx = ndimage.mean(np.indices(shape)[0], lab, range(1, n + 1))
            cy = ndimage.mean(np.indices(shape)[1], lab, range(1, n + 1))
            for c in range(1, n + 1):
                if tz[c] - z >= min_carry:
                    pend.append((c, (2 * math.sqrt(area[c] / math.pi), z,
                                     x0 + (cx[c - 1] + 0.5) * G, y0 + (cy[c - 1] + 0.5) * G,
                                     tz[c] - z)))
        prev, ptop = lab, tz
    cand.sort()
    out = []
    for c in cand:
        if all(math.hypot(c[2] - q[2], c[3] - q[3]) > 2.0 for q in out):
            out.append(c)
        if len(out) >= top:
            break
    return out


# ------------------------------------------------------------------ slicing

def slice_project(proj, out, plate, supports):
    """Slice a project with its own settings; -> (G-code, grams, seconds)."""
    if not os.path.exists(BS):
        die(f"Bambu Studio не найден: {BS}\nпуть задаётся переменной BS перед командой")
    out = pathlib.Path(out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    for f in list(out.glob("plate_*.gcode")) + [out / "result.json"]:
        if f.exists():
            f.unlink()
    src = pathlib.Path(proj).resolve()
    if supports:
        p = partedit.Project(src)
        key = "Metadata/project_settings.config"
        if key not in p.files:
            die(f"в {src} нет {key}: поддержки включить негде")
        c = json.loads(p.files[key])
        c["enable_support"] = "1"
        p.files[key] = json.dumps(c, indent=4, ensure_ascii=False).encode()
        src = out / "input.3mf"
        p.save(src)
    with open(out / "slice.log", "w") as log:
        subprocess.run([BS, "--outputdir", str(out), "--slice", str(plate), str(src)],
                       stdout=log, stderr=subprocess.STDOUT, timeout=3600)
    g = out / f"plate_{plate}.gcode"
    if not g.exists():
        die(f"нарезка не дала {g.name}; журнал: {out / 'slice.log'}")
    grams = secs = None
    rj = out / "result.json"
    if rj.exists():
        r = json.loads(rj.read_text())
        for pl in r.get("sliced_plates", []):
            grams = (grams or 0) + sum(f.get("total_used_g", 0) for f in pl.get("filaments", []))
            secs = (secs or 0) + pl.get("total_predication", 0)
    return g, grams, secs


# ------------------------------------------------------------------ report

def _fmt_t(s):
    return "—" if s is None else f"{int(s // 3600)} ч {int(s % 3600 // 60):02d} мин"


def report(objs, res, top):
    off = res["offset"]
    print(f"слой {res['lh']:.3g} мм, зазор сверху {res['top_gap']:.3g}, снизу {res['bot_gap']:.3g}; "
          f"тип поддержки {res['hdr'].get('support_type', '?')}; путь поддержки {res['support_m']:.2f} м")
    sh = math.hypot(*off)
    if sh > 0.3:
        print(f"G-код сдвинут относительно проекта на ({off[0]:+.2f}, {off[1]:+.2f}) мм — "
              "совмещено по габариту внешних стенок")
    else:
        print(f"совмещение с проектом: расхождение габаритов {sh:.2f} мм")
    if res["support_m"] == 0:
        print("ПОДДЕРЖЕК В ЭТОЙ НАРЕЗКЕ НЕТ — касаний нет")
        return
    tv = tt = 0.0
    for k, o in enumerate(objs):
        p = res["per_obj"].get(k)
        if not p:
            continue
        by = p["by"]
        tv += by["видимо"]; tt += p["total"]
        hid = ", ".join(f"{r} {by[r]:.1f}" for r in REASONS[1:])
        print(f"объект «{o.name}»: касание {p['total']:.1f} мм² (поддержка стоит на модели "
              f"{p['on_model']:.1f}), видимо {by['видимо']:.1f} мм²; скрыто: {hid}")
        print("   по филаментам: " + ", ".join(
            f"{f if f > 0 else 'дроблёные'}: {a:.1f} (видимо {v:.1f})"
            for f, (a, v) in sorted(p["fil"].items())))
    print(f"ИТОГО: касание {tt:.1f} мм², видимо {tv:.1f} мм²")
    sp = sorted(res["spots"], key=lambda s: (-s.get("visible", 0), -s["area"]))
    if sp:
        print(f"\nместа касания, сначала видимые (всего {len(sp)}, показано {min(top, len(sp))}):")
        print(f"   {'объект':14} {'видимо':>7} {'всего':>7}  {'стол x, y, z':22} "
              f"{'в осях объекта':22} откуда / почему скрыто")
        for s in sp[:top]:
            b, l = s["bed"], s["local"]
            src = "под моделью" if s["side"] == 0 else "стоит на модели"
            print(f"   {objs[s['obj']].name[:14]:14} {s.get('visible', 0):7.1f} {s['area']:7.1f}  "
                  f"{b[0]:6.1f} {b[1]:6.1f} {b[2]:6.1f}    {l[0]:6.1f} {l[1]:6.1f} {l[2]:6.1f}    "
                  f"{src}, ф{s.get('fil', '?')}{', ' + s['why'] if s.get('why') else ''}")
    print_necks(res.get("neck", []))


def print_necks(nk):
    if not nk:
        return
    print("\nсамые тонкие сечения поддержки, над которыми она продолжается (шейки стволов):")
    for d, z, x, y, carry in nk:
        print(f"   Ø {d:4.1f} мм на высоте {z:5.2f}, стол x {x:6.1f} y {y:6.1f}; выше неё ещё {carry:4.1f} мм поддержки")


def summary(objs, res):
    tv = sum(p["by"]["видимо"] for p in res["per_obj"].values())
    tt = sum(p["total"] for p in res["per_obj"].values())
    nk = min((n[0] for n in res.get("neck", [])), default=None)
    return tv, tt, nk


def to_json(objs, res, path):
    d = dict(offset=list(res["offset"]), support_m=res["support_m"],
             objects={objs[k].name: dict(total=p["total"], **p["by"],
                                         filaments={str(f): v for f, v in p["fil"].items()})
                      for k, p in res["per_obj"].items()},
             spots=[dict(object=objs[s["obj"]].name, area=s["area"], visible=s.get("visible", 0),
                         side="under" if s["side"] == 0 else "on", bed=list(map(float, s["bed"])),
                         local=list(map(float, s["local"])), hidden_by=s.get("why", ""))
                    for s in res["spots"]],
             necks=[dict(d=n[0], z=n[1], x=n[2], y=n[3], carried=n[4]) for n in res.get("neck", [])])
    pathlib.Path(path).write_text(json.dumps(d, ensure_ascii=False, indent=1))


# ------------------------------------------------------------------ commands

def default_out():
    return HERE.parent / "work" / "out" / "supportmap"


def cmd_map(a):
    plate = a.plate
    if a.gcode:
        g = pathlib.Path(a.gcode)
        if plate is None:
            m = re.search(r"plate_(\d+)\.gcode$", g.name)
            plate = int(m.group(1)) if m else 1
    else:
        plate = plate or 1
        g, grams, secs = slice_project(a.project, a.out or default_out(), plate, a.supports)
        print(f"нарезано: {g}; пластик {grams or 0:.1f} г, время {_fmt_t(secs)}")
    objs = load_project(a.project, plate)
    res = contact_map(objs, g, a)
    report(objs, res, a.top)
    if a.json:
        to_json(objs, res, a.json)
        print(f"\nJSON: {a.json}")


def _rot(rx, ry, rz):
    """Rotation about the bed axes: X first, then Y, then Z."""
    cx, sx = math.cos(math.radians(rx)), math.sin(math.radians(rx))
    cy, sy = math.cos(math.radians(ry)), math.sin(math.radians(ry))
    cz, sz = math.cos(math.radians(rz)), math.sin(math.radians(rz))
    Rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    Ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    Rz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    return Rz @ Ry @ Rx


def cmd_sweep(a):
    src = pathlib.Path(a.project)
    allo = load_project(src)
    hit = [o for o in allo if o.name == a.object]
    if len(hit) != 1:
        die(f"--object {a.object!r}: в проекте {len(hit)} таких; объекты: "
            + ", ".join(o.name for o in allo))
    ob = hit[0]
    poses = []
    for tok in re.split(r"[;\s]+", a.poses.strip()):
        if tok:
            v = [float(x) for x in tok.split(",")]
            poses.append(tuple(v + [0.0] * (3 - len(v)))[:3])
    if len(poses) > a.max:
        die(f"поз {len(poses)}, а предел --max {a.max}: каждая поза — отдельная нарезка")
    p0 = partedit.Project(src)
    plate = 1
    for blk in re.findall(r"<plate>(.*?)</plate>", p0.cfg, re.S):
        if f'object_id" value="{ob.oid}"' in blk:
            plate = int(re.search(r'plater_id" value="(\d+)"', blk).group(1))
    out = pathlib.Path(a.out or default_out() / "sweep").resolve()
    c = (ob.W.min(0) + ob.W.max(0)) / 2
    rows = []
    for i, (rx, ry, rz) in enumerate(poses):
        Rp = _rot(rx, ry, rz)
        Wn = (ob.W - c) @ Rp.T + c
        R = Rp @ ob.R
        t = Rp @ (ob.t - c) + c
        t[2] -= Wn[:, 2].min()
        p = partedit.Project(src)
        for o in allo:
            if o.oid != ob.oid:
                p.drop_object(o.name)
        p.top = re.sub(r'(<item objectid="%s"[^>]*?transform=")[^"]*(")' % ob.oid,
                       lambda m: m.group(1) + _trf_str(R, t) + m.group(2), p.top)
        d = out / f"pose_{i + 1}"
        d.mkdir(parents=True, exist_ok=True)
        proj = d / "pose.3mf"
        p.save(proj)
        g, grams, secs = slice_project(proj, d, plate, a.supports)
        objs = load_project(proj, plate)
        res = contact_map(objs, g, a)
        tv, tt, nk = summary(objs, res)
        sup_g = sum(gcode_report.parse(g)[0].get(k, 0.0) for k in SUPPORT)
        rows.append(dict(pose=(rx, ry, rz), vis=tv, tot=tt, g=grams, s=secs, sup=sup_g,
                         neck=nk, path=proj, off=math.hypot(*res["offset"])))
        print(f"поза {i + 1} ({rx:g}, {ry:g}, {rz:g}): видимо {tv:.1f} мм² из {tt:.1f}, "
              f"пластик {grams or 0:.1f} г, время {_fmt_t(secs)}")
        if a.verbose:
            report(objs, res, a.top)
    rows.sort(key=lambda r: (round(r["vis"]), r["s"] or 0, r["g"] or 0))
    print(f"\nрейтинг «{ob.name}»: по видимому касанию, затем по времени и весу")
    print(f"   {'место':5} {'rx,ry,rz':>16} {'видимо':>7} {'всего':>7} {'поддержка':>9} "
          f"{'пластик':>8} {'время':>11} {'шейка Ø':>8}")
    for k, r in enumerate(rows, 1):
        nk = f"{r['neck']:.1f}" if r["neck"] is not None else "—"
        print(f"   {k:5} {'%g,%g,%g' % r['pose']:>16} {r['vis']:7.1f} {r['tot']:7.1f} "
              f"{r['sup']:7.2f} г {r['g'] or 0:6.1f} г {_fmt_t(r['s']):>11} {nk:>8}")
    print(f"\nпроект лучшей позы: {rows[0]['path']}")


def cmd_neck(a):
    S, feats, hdr = parse_gcode(a.gcode)
    first = _hnum(hdr, "initial_layer_print_height", _hnum(hdr, "layer_height", 0.2))
    nk = necks(S, feats, first, a.min_carry, a.top)
    if not nk:
        print("поддержки без продолжения выше нет — шеек нет")
        return
    print_necks(nk)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(q):
        q.add_argument("--supports", action="store_true",
                       help="включить поддержки в копии проекта перед нарезкой")
        q.add_argument("--out", help="куда нарезать (по умолчанию ../work/out/supportmap)")
        q.add_argument("--hide-object", action="append", help="объект целиком скрыт в сборке")
        q.add_argument("--hide-mask", action="append", metavar="NAME=faces.npy",
                       help="маска граней объекта: bool на грань или номера граней")
        q.add_argument("--hide-fil", help="филаменты скрытых зон через запятую: 2,3")
        q.add_argument("--joints", choices=("off", "auto"), default="off",
                       help="auto: плоские области ≥ --plane-min мм² считать стыком")
        q.add_argument("--plane-min", type=float, default=25.0)
        q.add_argument("--niche", type=float, default=5.0,
                       help="мм: луч, упёршийся в свой объект ближе, закрыт (0 — не считать ниши)")
        q.add_argument("--open-min", type=float, default=0.25,
                       help="доля открытой полусферы, ниже которой грань — внутри ниши")
        q.add_argument("--fill", type=float, default=None,
                       help="мм: закрыть щели между линиями интерфейса (по умолчанию шаг/2 + ширина/2 + 0.1)")
        q.add_argument("--top", type=int, default=10, help="сколько мест касания показать")

    q = sub.add_parser("map", help="касание поддержек по факту нарезки")
    q.add_argument("project")
    q.add_argument("gcode", nargs="?")
    q.add_argument("--plate", type=int)
    q.add_argument("--json")
    common(q)
    q = sub.add_parser("sweep", help="перебор поз одной детали с нарезкой каждой")
    q.add_argument("project")
    q.add_argument("--object", required=True)
    q.add_argument("--poses", default="0,0;30,0;-30,0;0,30;0,-30;180,0",
                   help='"rx,ry[,rz];..." в градусах, повороты вокруг осей стола')
    q.add_argument("--max", type=int, default=6)
    q.add_argument("--verbose", action="store_true", help="полный отчёт по каждой позе")
    common(q)
    q = sub.add_parser("neck", help="самые тонкие сечения стволов поддержки")
    q.add_argument("gcode")
    q.add_argument("--min-carry", type=float, default=2.0)
    q.add_argument("--top", type=int, default=5)
    a = ap.parse_args()
    {"map": cmd_map, "sweep": cmd_sweep, "neck": cmd_neck}[a.cmd](a)


if __name__ == "__main__":
    main()
