#!/usr/bin/env python3
"""Проверка STL на печатаемость FDM. Без зависимостей (только stdlib).

ПРЕДУПРЕЖДЕНИЕ: вершины склеиваются по точному совпадению координат.
На крупных импортированных сетках (сотни тысяч граней, координаты
во float32) это даёт ЛОЖНЫЕ срабатывания по герметичности и роду
поверхности. Для таких мешей верить `BambuStudio --info файл.stl`:
manifold = yes и отсутствие строки open_edges означают, что дыр нет.
На проверенном таком меше скрипт показывал 95 «негерметичных» рёбер
и род 63, тогда как --info говорил manifold = yes, number_of_parts = 1,
а trimesh — watertight. Модели из OpenSCAD/build123d считает верно. Для чужих сеток есть
tools/meshdoctor.py: он различает дырки, non-manifold рёбра и вывернутые
грани и не сваривает вершины там, где связность задана индексами файла.

Свесы считаются от порога 45° — это общее правило FDM. Сама Bambu ставит
поддержки раньше: в профиле A1 support_threshold_angle = 30.
"""
import sys, struct, math, re
from collections import defaultdict
import hardware                       # сопло, слой, стол — из hardware.json

NOZZLE, LAYER = hardware.nozzle(), hardware.layer_height()
MAX_OVERHANG = 45.0                   # общее правило FDM, от сопла не зависит
# Плотность своих катушек — в hardware.json; по умолчанию PLA Basic 1.26.
DENSITY = hardware.density_default()
BED = hardware.bed()

def load(p):
    d = open(p,'rb').read()
    if d[:5].lower() != b'solid' or b'facet' not in d[:512]:      # бинарный STL
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
        if L>1e-12:                                   # угол свеса
            nz = cr[2]/L
            ang = math.degrees(math.acos(max(-1,min(1,-nz))))   # 0 = смотрит строго вниз
            on_bed = all(abs(v[2]-zmin) < 1e-4 for v in vs)   # грань лежит на столе - это не свес
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

    # Число тел: грани сливаются через рёбра, у которых ровно две грани.
    # Без него род считать нельзя. Формула (2-хи)/2 верна ТОЛЬКО для одного
    # тела; на детали из нескольких она врёт и даёт отрицательные значения.
    # Проверено 19.09.2026 на телах с известной топологией: две сферы давали
    # -1 вместо 0, сфера с тором 0 вместо 1, три тора 1 вместо 3, дорожка
    # железной дороги -6. Правильно: род = (2*тел - хи) / 2.
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
    # Это вес СПЛОШНОЙ детали. Реальная печать с заполнением 15 % и двумя
    # периметрами весит вчетверо меньше: на замере 240 г здесь против 60.6 г
    # по нарезке. Настоящую цифру даёт tools/slice.sh.
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
