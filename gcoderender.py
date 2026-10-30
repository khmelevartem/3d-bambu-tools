#!/usr/bin/env python3
"""What the nozzle actually lays down in a layer, against what was meant.

Rasterises chosen layers of a Bambu Studio G-code per filament — every
extrusion move at the width its E value gives, arcs interpolated — and, when
target parts are given, compares the print with their section at the same
height: coverage, overflow, and the elements the slicer dropped outright
(dots below the minimum, a stem of a thin letter).

    uv run --with numpy --with scipy --with networkx --with opencv-python-headless --with trimesh \\
        gcoderender.py plate.gcode --z 2.4,2.6 --target 3=text.stl --target 2=ornament.stl

    --z        layer heights to render, comma-separated (default: the top layer)
    --target   N=part.stl — filament N (numbered from 1, as in the GUI) must cover
               the section of this part; repeat per part
    --offset   dx,dy — G-code minus part coordinates, mm. Default: the footprint of
               the target filaments in the rendered layers is centred on the footprint
               of the sections; a dropped element at the very edge skews it
    --px       raster step, mm (default 0.015)
    --out      folder for the PNGs (default: next to the G-code)

**The render proves presence, not legibility.** A line that is in place here
can still merge with its neighbour on the plate.
"""
import argparse, io, pathlib, re, sys, zipfile
import numpy as np, cv2

SKIP_FEATURES = ("prime tower", "wipe tower")
LOST = 0.5          # an element covered less than this is reported as dropped


def read_gcode(path):
    p = pathlib.Path(path)
    if p.suffix == ".3mf" or p.name.endswith(".gcode.3mf"):
        with zipfile.ZipFile(p) as z:
            name = next(n for n in z.namelist() if n.endswith(".gcode"))
            return z.read(name).decode("utf-8", "replace").split("\n")
    return p.read_text("utf-8", "replace").split("\n")


def parse(lines):
    """-> {z: [(filament, x0, y0, x1, y1, e)]}, {z: layer height}, filament diameter."""
    z = None; lh = {}; tool = 0; x = y = 0.0; feat = ""; flush = False
    segs = {}; dia = 1.75
    for L in lines:
        if L.startswith("; Z_HEIGHT:"):
            z = round(float(L.split(":")[1]), 3)
        elif L.startswith("; LAYER_HEIGHT:") and z is not None:
            lh[z] = float(L.split(":")[1])
        elif L.startswith("; FEATURE:"):
            feat = L.split(":", 1)[1].strip().lower()
        elif L.startswith("; FLUSH_START"):
            flush = True
        elif L.startswith("; FLUSH_END"):
            flush = False
        elif L.startswith("; filament_diameter"):
            dia = float(re.split(r"[:=]", L, 1)[1].split(",")[0])
        elif L.startswith("M620 S") and re.match(r"^M620 S(\d+)A", L):
            tool = int(re.match(r"^M620 S(\d+)A", L).group(1))
        elif L[:3] in ("G0 ", "G1 ", "G2 ", "G3 "):
            d = {}
            for w in L.split(";")[0].split()[1:]:
                if w[0] in "XYEIJ" and len(w) > 1:
                    d[w[0]] = float(w[1:])
            nx, ny = d.get("X", x), d.get("Y", y); e = d.get("E", 0.0)
            keep = e > 0 and z is not None and not flush and not any(s in feat for s in SKIP_FEATURES)
            if keep and L[:2] in ("G2", "G3") and ("I" in d or "J" in d):
                cx, cy = x + d.get("I", 0.0), y + d.get("J", 0.0); r = np.hypot(x - cx, y - cy)
                a0 = np.arctan2(y - cy, x - cx); a1 = np.arctan2(ny - cy, nx - cx)
                if L[:2] == "G3" and a1 <= a0 + 1e-9:
                    a1 += 2 * np.pi
                if L[:2] == "G2" and a1 >= a0 - 1e-9:
                    a1 -= 2 * np.pi
                k = max(4, int(abs(a1 - a0) * r / 0.02)); aa = np.linspace(a0, a1, k + 1)
                P = np.c_[cx + r * np.cos(aa), cy + r * np.sin(aa)]
                for p0, p1 in zip(P[:-1], P[1:]):
                    segs.setdefault(z, []).append((tool, *p0, *p1, e / k))
            elif keep and (nx != x or ny != y):
                segs.setdefault(z, []).append((tool, x, y, nx, ny, e))
            x, y = nx, ny
    return segs, lh, dia


def section(stl, z):
    """Closed rings of a part cut at height z, in the part's own XY."""
    import trimesh
    s = trimesh.load_mesh(stl).section(plane_origin=[0, 0, z], plane_normal=[0, 0, 1])
    return [] if s is None else [d[:, :2] for d in s.discrete if len(d) >= 3]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("gcode")
    ap.add_argument("--z", default="")
    ap.add_argument("--target", action="append", default=[])
    ap.add_argument("--offset", default="")
    ap.add_argument("--px", type=float, default=0.015)
    ap.add_argument("--out", default="")
    a = ap.parse_args(argv)
    PX = a.px
    segs, lh, dia = parse(read_gcode(a.gcode))
    if not segs:
        sys.exit("в G-коде нет ни одного движения экструзии с меткой Z_HEIGHT")
    zs = sorted(segs)
    ZL = [round(float(v), 3) for v in a.z.split(",")] if a.z else [zs[-1]]
    for zz in ZL:
        if zz not in segs:
            near = min(zs, key=lambda v: abs(v - zz))
            sys.exit(f"слоя z={zz} в G-коде нет; ближайший {near}")
    print(f"слоёв {len(zs)}, от {zs[0]} до {zs[-1]} мм; рисую z = {', '.join(map(str, ZL))}")
    A = np.pi * (dia / 2) ** 2

    targets = []
    for t in a.target:
        n, f = t.split("=", 1)
        targets.append((int(n), f))
    rings = {}
    zc = min(ZL) - lh.get(min(ZL), 0.2) / 2               # the middle of the lowest rendered layer
    for n, f in targets:
        rr = section(f, zc)
        if rr:
            rings.setdefault(n, []).append(rr)
        else:
            print(f"деталь {f} не пересекает z={zc:.3f}: цели для филамента {n} в этом слое нет")
    moves = [q for zz in ZL for q in segs[zz]]
    # where the target parts sit in G-code coordinates
    if a.offset:
        dx, dy = map(float, a.offset.split(","))
    elif rings:
        # the footprint of the target filaments in the rendered layers against the footprint
        # of the sections: one missing element at the very edge shifts this by its size
        g = np.array([q[1:5] for q in moves if q[0] + 1 in rings])
        if not len(g):
            sys.exit("в этих слоях нет ни одного движения филаментов из --target; задай --offset")
        b = np.vstack([r for rr in rings.values() for part in rr for r in part])
        dx = (g[:, [0, 2]].min() + g[:, [0, 2]].max() - b[:, 0].min() - b[:, 0].max()) / 2
        dy = (g[:, [1, 3]].min() + g[:, [1, 3]].max() - b[:, 1].min() - b[:, 1].max()) / 2
        print(f"сдвиг G-код − детали: {dx:+.2f}, {dy:+.2f} мм (по габариту филаментов цели)")
    else:
        dx = dy = 0.0
    xs = np.r_[[q[1] for q in moves], [q[3] for q in moves]] - dx
    ys = np.r_[[q[2] for q in moves], [q[4] for q in moves]] - dy
    x0, y0, x1, y1 = xs.min() - 1, ys.min() - 1, xs.max() + 1, ys.max() + 1
    for rr in rings.values():
        b = np.vstack([r for part in rr for r in part])
        x0, y0 = min(x0, b[:, 0].min() - 1), min(y0, b[:, 1].min() - 1)
        x1, y1 = max(x1, b[:, 0].max() + 1), max(y1, b[:, 1].max() + 1)
    W, H = int((x1 - x0) / PX) + 1, int((y1 - y0) / PX) + 1
    if W * H > 120e6:
        sys.exit(f"растр {W}×{H} слишком велик — увеличь --px")
    pix = lambda X, Y: (int(round((X - dx - x0) / PX * 16)), int(round((y1 - (Y - dy)) / PX * 16)))

    canv = {}
    for tl, xa, ya, xb, yb, e in moves:
        L = np.hypot(xb - xa, yb - ya); h = lh.get(ZL[0], 0.2)
        w = e * A / (L * h) + h * (1 - np.pi / 4)        # rectangle with round ends
        c = canv.setdefault(tl + 1, np.zeros((H, W), np.uint8))
        cv2.line(c, pix(xa, ya), pix(xb, yb), 255, max(1, int(round(w / PX))), cv2.LINE_8, shift=4)

    out = pathlib.Path(a.out) if a.out else pathlib.Path(a.gcode).resolve().parent
    out.mkdir(parents=True, exist_ok=True)
    stem = pathlib.Path(a.gcode).name.split(".")[0]
    tag = "_".join(map(str, ZL))
    for n in sorted(set(canv) | set(rings)):
        pr = canv.get(n, np.zeros((H, W), np.uint8)) > 0
        line = f"филамент {n}: напечатано {pr.sum() * PX * PX:8.1f} мм²"
        img = np.full((H, W, 3), 255, np.uint8)
        if n in rings:
            tg = np.zeros((H, W), np.uint8)
            for rr in rings[n]:          # one part at a time; inside a part a nested ring is a hole
                one = np.zeros((H, W), np.uint8)
                cv2.fillPoly(one, [np.c_[(r[:, 0] - x0) / PX, (y1 - r[:, 1]) / PX].round().astype(np.int32)
                                   for r in rr], 1)
                tg |= one
            tg = tg > 0
            nc, lab, st, _ = cv2.connectedComponentsWithStats(tg.astype(np.uint8), connectivity=8)
            cov = np.bincount(lab[pr], minlength=nc) / np.maximum(st[:, 4], 1)
            lost = [i for i in range(1, nc) if cov[i] < LOST]
            line += (f"  цель {tg.sum() * PX * PX:8.1f} мм²  покрыто {np.mean(pr[tg]) * 100:5.1f} %"
                     f"  вылезло за контур {(pr & ~tg).sum() * PX * PX:6.1f} мм²")
            print(line)
            print(f"    элементов {nc - 1}, пропало (покрыто меньше {LOST:.0%}): {len(lost)}")
            for i in lost[:12]:
                cx = x0 + (st[i, 0] + st[i, 2] / 2) * PX; cy = y1 - (st[i, 1] + st[i, 3] / 2) * PX
                print(f"      x {cx:6.1f}  y {cy:6.1f}  размер {st[i, 2] * PX:.2f}×{st[i, 3] * PX:.2f} мм"
                      f"  покрыто {cov[i] * 100:3.0f} %")
            if len(lost) > 12:
                print(f"      … и ещё {len(lost) - 12}")
            img[tg & ~pr] = (40, 40, 220)        # meant, not printed: red
            img[tg & pr] = (90, 170, 90)         # meant and printed: green
            img[~tg & pr] = (220, 120, 40)       # printed past the outline: blue
        else:
            print(line)
            img[pr] = (60, 60, 60)
        f = out / f"{stem}_z{tag}_ф{n}.png"
        cv2.imencode(".png", img)[1].tofile(str(f))
        print(f"    картинка: {f}")
    print("Отрисовка показывает, что легло, но не доказывает читаемость: слипание видно только на печати.")


if __name__ == "__main__":
    main()
