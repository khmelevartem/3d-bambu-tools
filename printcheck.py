#!/usr/bin/env python3
"""Check an STL for FDM printability. No dependencies, stdlib only.

WARNING: vertices are merged by exact coordinate match. On large imported
meshes (hundreds of thousands of faces, float32 coordinates) that produces
FALSE positives for watertightness and for the genus of the surface. For
such meshes trust `BambuStudio --info file.stl` instead: manifold = yes
with no open_edges line means there are no holes. On one such mesh this
script reported 95 "leaking" edges and genus 63, while --info said
manifold = yes, number_of_parts = 1, and trimesh called it watertight.
Meshes from OpenSCAD/build123d are measured correctly. For foreign meshes
use meshdoctor.py: it tells holes, non-manifold edges and reversed faces
apart, and does not weld vertices where connectivity is given by the
file's own indices.

Overhangs are counted from a 45 degree threshold, the general FDM rule.
Bambu itself starts supporting earlier: the A1 profile has
support_threshold_angle = 30.
"""
import sys, struct, math, re
from collections import defaultdict
import hardware                       # nozzle, layer, bed — from hardware.json

NOZZLE, LAYER = hardware.nozzle(), hardware.layer_height()
MAX_OVERHANG = 45.0                   # general FDM rule, independent of the nozzle
# Spool densities live in hardware.json; the default is PLA Basic, 1.26.
DENSITY = hardware.density_default()
BED = hardware.bed()

def load(p):
    d = open(p,'rb').read()
    if d[:5].lower() != b'solid' or b'facet' not in d[:512]:      # binary STL
        n = struct.unpack('<I', d[80:84])[0]
        return [ (struct.unpack('<3f', d[84+i*50:96+i*50]),
                  [struct.unpack('<3f', d[96+i*50+j*12:108+i*50+j*12]) for j in range(3)])
                 for i in range(n) ]
    t = d.decode('utf-8','replace'); out=[]
    for m in re.finditer(r'facet\s+normal\s+(\S+)\s+(\S+)\s+(\S+)(.*?)endfacet', t, re.S):
        out.append((tuple(map(float,m.group(1,2,3))),
                    [tuple(map(float,v)) for v in re.findall(r'vertex\s+(\S+)\s+(\S+)\s+(\S+)', m.group(4))]))
    return out

def main(path):
    tris = load(path); print(f"{path}: {len(tris)} треугольников")
    zmin = min(v[2] for _,vs in tris for v in vs)
    q = lambda v: tuple(round(c,4) for c in v)
    edges=defaultdict(int); dr=defaultdict(int); efaces={}; vol=0.0; area=0.0
    xs=ys=zs=None; over=0.0; overmax=0.0
    for fi,(n,vs) in enumerate(tris):
        if len(vs)!=3: continue
        a,b,c = vs
        u=[b[i]-a[i] for i in range(3)]; w=[c[i]-a[i] for i in range(3)]
        cr=[u[1]*w[2]-u[2]*w[1], u[2]*w[0]-u[0]*w[2], u[0]*w[1]-u[1]*w[0]]
        L=math.hypot(*cr); A=L/2; area+=A
        vol += (a[0]*(b[1]*c[2]-b[2]*c[1]) - a[1]*(b[0]*c[2]-b[2]*c[0]) + a[2]*(b[0]*c[1]-b[1]*c[0]))/6
        vq=[q(v) for v in vs]
        for i in range(3):
            e=frozenset((vq[i],vq[(i+1)%3]))
            edges[e]+=1; dr[(vq[i],vq[(i+1)%3])]+=1
            efaces.setdefault(e,[]).append(fi)
        if L>1e-12:                                   # overhang angle
            nz = cr[2]/L
            ang = math.degrees(math.acos(max(-1,min(1,-nz))))   # 0 = pointing straight down
            on_bed = all(abs(v[2]-zmin) < 1e-4 for v in vs)   # a face on the bed is not an overhang
            if ang < (90-MAX_OVERHANG) - 1e-6 and A>1e-9 and not on_bed:
                over += A; overmax=max(overmax, 90-ang)
        for v in vs:
            xs=(min(xs[0],v[0]),max(xs[1],v[0])) if xs else (v[0],v[0])
            ys=(min(ys[0],v[1]),max(ys[1],v[1])) if ys else (v[1],v[1])
            zs=(min(zs[0],v[2]),max(zs[1],v[2])) if zs else (v[2],v[2])
    openE=[e for e,c in edges.items() if c!=2]
    badW=[e for e,c in dr.items() if c!=1]
    size=(xs[1]-xs[0], ys[1]-ys[0], zs[1]-zs[0])
    V,E,F=len({v for _,vs in tris for v in map(q,vs)}), len(edges), len(tris)

    # Shell count: faces are unioned across edges that have exactly two
    # faces. Without it the genus cannot be computed. The formula (2-chi)/2
    # holds ONLY for a single shell; on a part made of several it lies and
    # returns negative values. Verified 2026-09-19 on bodies of known
    # topology: two spheres gave -1 instead of 0, a sphere plus a torus
    # 0 instead of 1, three tori 1 instead of 3, a piece of train track -6.
    # Correct: genus = (2*shells - chi) / 2.
    par=list(range(F))
    def find(x):
        r=x
        while par[r]!=r: r=par[r]
        while par[x]!=r: par[x],x=r,par[x]
        return r
    for e,fl in efaces.items():
        if len(fl)!=2: continue
        a,b=find(fl[0]),find(fl[1])
        if a!=b: par[b]=a
    shells=len({find(i) for i in range(F)}) if F else 0

    chi=V-E+F
    if openE or badW:
        genus="не определён: сетка не замкнута либо обход рассогласован"
    elif (2*shells-chi)%2:
        genus=("не определён: хи нечётно. У замкнутой поверхности хи = 2-2g всегда чётно,\n              значит рёбра целы, но поверхность не простая — например, два тела\n              сходятся в одной вершине либо сидят вырожденные грани")
    else:
        genus=str((2*shells-chi)//2)

    ok=lambda b: "OK " if b else "!! "
    print(f" {ok(not openE)}герметичность: рёбер с count!=2: {len(openE)}")
    print(f" {ok(not badW)}согласованность обхода: плохих направленных рёбер: {len(badW)}")
    # This is the weight of a SOLID part. A real print at 15 % infill with
    # two perimeters weighs about a quarter of it: 240 g here against 60.6 g
    # from the slicer in one measurement. slice.sh gives the real number.
    print(f" {ok(vol>0)}объём: {vol:.2f} мм³  ({vol/1000:.2f} см³, "
          f"~{vol*DENSITY/1000:.0f} г сплошняком — реальный вес даёт slice.sh)")
    print(f"    площадь поверхности: {area:.1f} мм²")
    print(f"    отдельных тел: {shells}")
    print(f"    эйлерова хар-ка V-E+F = {chi}  -> род (сквозных отверстий) = {genus}")
    print(f" {ok(all(size[i]<=BED[i] for i in range(3)))}габарит: {size[0]:.2f} x {size[1]:.2f} x {size[2]:.2f} мм (стол {BED[0]}x{BED[1]}x{BED[2]})")
    print(f"    Z от {zs[0]:.2f} до {zs[1]:.2f}" + ("  !! деталь не лежит на z=0" if abs(zs[0])>1e-6 else ""))
    pct = 100*over/area if area else 0
    print(f" {ok(pct<1)}свесы круче {MAX_OVERHANG}°: {pct:.1f}% площади, максимум {overmax:.1f}° от горизонтали")
    print(f"    слоёв по {LAYER} мм: {math.ceil(size[2]/LAYER)}")

if __name__ == "__main__":
    for p in sys.argv[1:]: main(p); print()
