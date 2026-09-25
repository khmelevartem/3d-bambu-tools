#!/usr/bin/env python3
"""Edit per-filament paint inside a finished 3MF. The geometry never changes.

Paint lives in the paint_color attribute on <triangle>. This reads it,
straightens the colour borders with a graph cut and writes it back, touching
neither vertices nor face indices.

    uv run --with numpy --with scipy --with PyMaxflow python tools/paint.py \
        parse  model.3mf work/p.npz
    uv run ... python tools/paint.py stats  work/p.npz
    uv run ... python tools/paint.py smooth work/p.npz work/p2.npz --band 4 --lam 1.0
    uv run ... python tools/paint.py write  model.3mf work/p2.npz ready.3mf
    uv run ... python tools/paint.py filament model.3mf with_fifth.3mf '#C12E1F'
    uv run ... python tools/paint.py explode model.3mf work/e.npz --object Cube

The paint codes and the traps of the format are in references/paint-format.md
of the 3mf-paint skill.
"""
import argparse, json, os, re, sys, zipfile
import numpy as np

CODE2FIL = {'': 1, '4': 1, '8': 2, '0C': 3, '1C': 4, '2C': 5, '3C': 6, '4C': 7}
FIL2CODE = {v: k for k, v in CODE2FIL.items() if k}
SPLIT = -1                      # triangle painted with the brush in several colours
TRI = re.compile(r'<triangle v1="(\d+)" v2="(\d+)" v3="(\d+)"'
                 r'(?:\s+paint_color="([0-9A-Fa-f]+)")?\s*/>')
VERT = re.compile(r'<vertex x="([^"]*)" y="([^"]*)" z="([^"]*)"\s*/>')


def model_entry(z):
    """Имя записи внутри 3MF, где лежит сетка: она бывает и в 3dmodel.model,
    и в 3D/Objects/*.model."""
    cands = [n for n in z.namelist() if n.endswith('.model')]
    for n in sorted(cands, key=lambda n: -z.getinfo(n).file_size):
        if b'<triangle' in z.read(n)[:200000] or z.getinfo(n).file_size > 100000:
            return n
    raise SystemExit(f'не нашёл сетку среди {cands}')


# ------------------------------------------------ project objects
def objects(z):
    """Объекты проекта: (имя, филамент объекта, запись с сеткой).

    В 3MF от Bambu Studio каждый объект лежит отдельным файлом
    3D/Objects/object_N.model, а 3D/3dmodel.model только ссылается на них
    через <component p:path=…>. Имя и экструдер объекта — в
    Metadata/model_settings.config."""
    try:
        top = z.read('3D/3dmodel.model').decode('utf-8')
    except KeyError:
        return [('', 1, model_entry(z))]
    meta = {}
    try:
        cfg = z.read('Metadata/model_settings.config').decode('utf-8')
        for blk in re.findall(r'<object id="(\d+)">(.*?)</object>', cfg, re.S):
            # the object's own metadata comes before the first <part>; otherwise
            # on a multi-part object the last part's name wins
            head = blk[1].split('<part')[0]
            kv = dict(re.findall(r'<metadata key="(\w+)" value="([^"]*)"/>', head))
            meta[blk[0]] = (kv.get('name', ''), int(kv.get('extruder', 1)))
    except KeyError:
        pass
    out = []
    for oid, body in re.findall(r'<object id="(\d+)"[^>]*>(.*?)</object>', top, re.S):
        m = re.search(r'p:path="([^"]+)"', body)
        ent = m.group(1).lstrip('/') if m else '3D/3dmodel.model'
        nm, ext = meta.get(oid, ('', 1))
        out.append((nm, ext, ent))
    return out or [('', 1, model_entry(z))]


def pick_object(z, want):
    """Выбрать объект по имени или по номеру; None — единственный/самый большой."""
    obs = objects(z)
    if want is None:
        if len(obs) == 1:
            return obs[0]
        names = ', '.join(f'{i}:{n or "без имени"}' for i, (n, _, _) in enumerate(obs))
        raise SystemExit(f'в проекте {len(obs)} объектов, нужен --object ({names})')
    if want.isdigit() and int(want) < len(obs):
        return obs[int(want)]
    hit = [o for o in obs if want.lower() in o[0].lower()]
    if len(hit) != 1:
        names = ', '.join(n or '?' for n, _, _ in obs)
        raise SystemExit(f'--object {want!r} не опознан, в проекте: {names}')
    return hit[0]


# ------------------------------------------------ brush: split faces
def paint_bits(code):
    """Код paint_color -> поток бит.

    Так пишет TriangleSelector::serialize: биты пакуются в ниблы младшим
    вперёд, а ниблы в строку — задом наперёд. Поэтому строка читается
    справа налево. Проверка на файле: у всех дроблёных граней поток
    расходуется ровно до последнего бита."""
    out = []
    for ch in reversed(code):
        v = int(ch, 16)
        out += [(v >> i) & 1 for i in range(4)]
    return out


def paint_tree(code):
    """Дроблёная грань -> [(состояние, барицентрические координаты 3x3), …].

    Состояние 0 — «цвета нет», то есть экструдер объекта; N — филамент N.
    Разбиение то же, что в TriangleSelector::perform_split: 2 бита — сколько
    сторон поделено, 2 бита — особая сторона, дальше поддеревья детей.

    Порядок детей в потоке — обратный порядку разбиения; установлен опытом,
    а не из документации: перебор всех 24 расстановок на грани куба y=+12.8,
    где покраска заведомо полосами, дал у обратного порядка границу цвета
    798 пикселей против 1118 у ближайшего и 10154 у худшего (work/face_search).
    Прямой порядок даёт узнаваемую фрактальную кашу вместо полос.

    Сетка получается с T-стыками: соседние грани дробятся на разную глубину,
    и общее ребро с одной стороны поделено, с другой нет. Для цвета это
    безразлично, но замкнутой такая сетка не бывает."""
    b = paint_bits(code)
    pos = [0]

    def take(n):
        i = pos[0]
        if i + n > len(b):
            raise ValueError(f'поток кода {code} кончился раньше времени')
        pos[0] += n
        return sum(b[i + k] << k for k in range(n))

    res = []

    def rec(p):
        sides = take(2)
        if sides == 0:
            st = take(2)
            if st == 3:
                st = 3 + take(4)
            res.append((st, p))
            return
        i = take(2)
        j, k = (i + 1) % 3, (i + 2) % 3
        mid = [(p[t] + p[(t + 1) % 3]) / 2 for t in range(3)]   # mid[t] on side t..t+1
        if sides == 1:                       # side i is split
            ch = [[p[i], mid[i], p[k]], [mid[i], p[j], p[k]]]
        elif sides == 2:                     # sides i and k, meeting at p[i]
            ch = [[p[i], mid[i], mid[k]], [mid[i], p[j], mid[k]], [p[j], p[k], mid[k]]]
        else:                                # all three are split
            ch = [[p[i], mid[i], mid[k]], [mid[i], p[j], mid[j]],
                  [mid[j], p[k], mid[k]], [mid[i], mid[j], mid[k]]]
        for c in reversed(ch):               # children lie in the stream back to front
            rec(np.array(c))

    rec(np.eye(3))
    if pos[0] < len(b) - 3:
        raise ValueError(f'код {code}: прочитано {pos[0]} бит из {len(b)}')
    return res


def adjacency(V, F):
    """Смежность граней по общему ребру + длины рёбер и площади."""
    n = len(F)
    E = np.concatenate([F[:, [0, 1]], F[:, [1, 2]], F[:, [2, 0]]])
    E.sort(axis=1)
    key = E[:, 0].astype(np.int64) * (1 << 32) + E[:, 1]
    order = np.argsort(key, kind='stable')
    ks, fid = key[order], np.tile(np.arange(n), 3)[order]
    idx = np.flatnonzero(ks[:-1] == ks[1:])
    ea, eb = fid[idx], fid[idx + 1]
    ve = E[order][idx]
    elen = np.linalg.norm(V[ve[:, 0]] - V[ve[:, 1]], axis=1)
    P = V[F]
    area = 0.5 * np.linalg.norm(np.cross(P[:, 1] - P[:, 0], P[:, 2] - P[:, 0]), axis=1)
    return ea, eb, elen, area


# ---------------------------------------------------------------- explode
def cmd_explode(a):
    """Разложить кисть на подтреугольники: сетка, где каждая грань одноцветна."""
    z = zipfile.ZipFile(a.src)
    name, ext, ent = pick_object(z, a.object)
    raw = z.read(ent).decode('utf-8')
    V0 = np.array(VERT.findall(raw), dtype=np.float64)
    tri, lab, split = [], [], 0
    for m in TRI.finditer(raw):
        idx = [int(m.group(i)) for i in (1, 2, 3)]
        code = (m.group(4) or '').upper()
        if code in CODE2FIL:
            tri.append(V0[idx])
            lab.append(ext if code == '' else CODE2FIL[code])
            continue
        split += 1
        for st, bc in paint_tree(code):
            tri.append(bc @ V0[idx])
            lab.append(ext if st == 0 else st)
    P = np.array(tri, dtype=np.float64)
    lab = np.array(lab, dtype=np.int8)

    # vertex welding: a subtriangle is computed from its own parent, so one and
    # the same point comes out differing by ~1e-13 across a shared edge
    q = np.round(P.reshape(-1, 3) / a.weld).astype(np.int64)
    _, first, inv = np.unique(q, axis=0, return_index=True, return_inverse=True)
    V = P.reshape(-1, 3)[first]
    F = inv.reshape(-1, 3).astype(np.int64)
    good = (F[:, 0] != F[:, 1]) & (F[:, 1] != F[:, 2]) & (F[:, 2] != F[:, 0])
    F, lab = F[good], lab[good]

    ea, eb, elen, area = adjacency(V, F)
    try:
        fcol = np.array(json.loads(z.read('Metadata/project_settings.config'))
                        .get('filament_colour', []), dtype=object)
    except Exception:
        fcol = np.array([], dtype=object)
    np.savez_compressed(a.out, V=V, F=F, lab=lab, ea=ea, eb=eb, elen=elen, area=area,
                        entry=np.array(ent), fcol=fcol)
    open_e = 3 * len(F) - 2 * len(ea)
    print(f'{name or ent}: дроблёных граней {split} -> подтреугольников {len(F)}, '
          f'вершин {len(V)}')
    print(f'  поверхность {area.sum():.1f} мм2, вырожденных выброшено {int((~good).sum())}, '
          f'открытых рёбер {open_e}')
    for f in sorted(set(lab.tolist())):
        print(f'  филамент {f}: {int((lab==f).sum()):6d} граней, {area[lab==f].sum():8.1f} мм2')


# ---------------------------------------------------------------- parse
def cmd_parse(a):
    z = zipfile.ZipFile(a.src)
    ent = model_entry(z)
    raw = z.read(ent).decode('utf-8')
    V = np.array(VERT.findall(raw), dtype=np.float64)
    t1, t2, t3, lab = [], [], [], []
    for m in TRI.finditer(raw):
        t1.append(m.group(1)); t2.append(m.group(2)); t3.append(m.group(3))
        lab.append(CODE2FIL.get((m.group(4) or '').upper(), SPLIT))
    F = np.array([t1, t2, t3], dtype=np.int64).T
    lab = np.array(lab, dtype=np.int8)

    # face adjacency across shared edges, plus edge lengths and areas
    n = len(F)
    E = np.concatenate([F[:, [0, 1]], F[:, [1, 2]], F[:, [2, 0]]])
    E.sort(axis=1)
    key = E[:, 0].astype(np.int64) * (1 << 32) + E[:, 1]
    order = np.argsort(key, kind='stable')
    ks, fid = key[order], np.tile(np.arange(n), 3)[order]
    idx = np.flatnonzero(ks[:-1] == ks[1:])
    ea, eb = fid[idx], fid[idx + 1]
    ve = E[order][idx]
    elen = np.linalg.norm(V[ve[:, 0]] - V[ve[:, 1]], axis=1)
    P = V[F]
    area = 0.5 * np.linalg.norm(np.cross(P[:, 1] - P[:, 0], P[:, 2] - P[:, 0]), axis=1)

    # The project's real filament colours, so a render shows the part as it will
    # leave the printer. Without them paintview uses its notional palette, and
    # on a foreign project the colours come out permuted.
    try:
        fcol = np.array(json.loads(z.read('Metadata/project_settings.config'))
                        .get('filament_colour', []), dtype=object)
    except Exception:
        fcol = np.array([], dtype=object)

    np.savez_compressed(a.out, V=V, F=F, lab=lab, ea=ea, eb=eb, elen=elen, area=area,
                        entry=np.array(ent), fcol=fcol)
    print(f'{ent}: вершин {len(V)}, граней {n}, пар соседей {len(ea)} '
          f'(у замкнутой сетки было бы {3*n//2})')
    for f in sorted(set(lab.tolist())):
        nm = 'дроблёные' if f == SPLIT else f'филамент {f}'
        print(f'  {nm}: {int((lab==f).sum())} граней, {area[lab==f].sum():.1f} мм2')


# ---------------------------------------------------------------- common
class Mesh:
    def __init__(self, path):
        d = np.load(path, allow_pickle=True)
        self.V, self.F = d['V'], d['F']
        self.lab = d['lab'].astype(np.int32)
        self.ea, self.eb, self.elen, self.area = d['ea'], d['eb'], d['elen'], d['area']
        self.n = len(self.F)
        self.C = self.V[self.F].mean(1)
        P = self.V[self.F]
        nr = np.cross(P[:, 1] - P[:, 0], P[:, 2] - P[:, 0])
        ln = np.linalg.norm(nr, axis=1); ln[ln == 0] = 1
        self.nrm = nr / ln[:, None]

    def boundary(self, lab):
        return float(self.elen[lab[self.ea] != lab[self.eb]].sum())

    def csr(self):
        src = np.concatenate([self.ea, self.eb]); dst = np.concatenate([self.eb, self.ea])
        o = np.argsort(src, kind='stable')
        return np.searchsorted(src[o], np.arange(self.n + 1)), dst[o]


def hop_distance(m, seed_mask, maxhop):
    """расстояние в гранях от seed_mask, обход по общим рёбрам"""
    ptr, dst = m.csr()
    dist = np.full(m.n, 1 << 30, np.int32)
    front = np.flatnonzero(seed_mask)
    dist[front] = 0
    for h in range(1, maxhop + 1):
        cnt = ptr[front + 1] - ptr[front]
        if not cnt.sum(): break
        nxt = dst[np.repeat(ptr[front], cnt) +
                  (np.arange(cnt.sum()) - np.repeat(np.cumsum(cnt) - cnt, cnt))]
        nxt = np.unique(nxt[dist[nxt] > h])
        if not len(nxt): break
        dist[nxt] = h; front = nxt
    return dist


def components(m, lab):
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    same = lab[m.ea] == lab[m.eb]
    g = coo_matrix((np.ones(same.sum()), (m.ea[same], m.eb[same])), shape=(m.n, m.n))
    return connected_components(g, directed=False)


# ---------------------------------------------------------------- stats
def cmd_stats(a):
    m = Mesh(a.npz)
    lab = m.lab
    uni = lab > 0
    print(f'поверхность {m.area.sum():.0f} мм2, граней {m.n}, '
          f'среднее ребро {m.elen.mean():.3f} мм')
    print(f'граница цвета {m.boundary(lab):.1f} мм по {int((lab[m.ea]!=lab[m.eb]).sum())} рёбрам')
    for f in sorted(set(lab.tolist())):
        nm = 'дроблёные' if f == SPLIT else f'филамент {f}'
        print(f'  {nm}: {int((lab==f).sum()):7d} граней, {m.area[lab==f].sum():8.1f} мм2')
    nc, comp = components(m, lab)
    ca = np.bincount(comp, weights=m.area)
    small = (ca > 0) & (ca < a.min_area)
    print(f'одноцветных кусков {int((ca>0).sum())}, '
          f'мельче {a.min_area} мм2: {int(small.sum())} (всего {ca[small].sum():.2f} мм2)')
    # isolated outliers: all three neighbours intact, one colour, and not its own
    ptr, dst = m.csr()
    cnt = np.diff(ptr)
    idx = np.flatnonzero(cnt == 3)
    l3 = lab[dst[ptr[idx][:, None] + np.arange(3)]]
    out = idx[(l3[:, 0] == l3[:, 1]) & (l3[:, 1] == l3[:, 2]) & (l3[:, 0] > 0)
              & (l3[:, 0] != lab[idx]) & (lab[idx] > 0)]
    print(f'одиночных выбивающихся граней: {len(out)} ({m.area[out].sum():.2f} мм2)')


# ---------------------------------------------------------------- smooth
def cmd_smooth(a):
    import maxflow
    m = Mesh(a.npz)
    orig = m.lab.copy()
    uni = orig > 0
    ea, eb, elen, area = m.ea, m.eb, m.elen, m.area

    # Price of running the border along an edge: edge length, discounted by the
    # crease - the sharper the dihedral angle, the cheaper, so the border sticks.
    cosd = np.clip((m.nrm[ea] * m.nrm[eb]).sum(1), -1, 1)
    w = elen * np.maximum(a.crease_floor, ((1 + cosd) / 2) ** a.crease_pow)
    # Price of repainting a face. Zero for split faces when they are released.
    Dw = a.lam * area * (uni if a.splits == 'free' else np.ones(m.n, bool))

    bnd = np.zeros(m.n, bool)
    diff = orig[ea] != orig[eb]
    bnd[ea[diff]] = True; bnd[eb[diff]] = True
    dist = hop_distance(m, bnd, max(a.band, a.core))
    frozen = dist > a.band
    if a.core:
        # The core of a single-colour patch is pinned: otherwise shortening the
        # border eats narrow details, which cost more by perimeter than by area.
        frozen |= (dist >= a.core) & uni
    if a.splits == 'keep':
        frozen |= ~uni
    print(f'коридор {a.band}, вес {a.lam}, ядро с {a.core}, дроблёные — '
          f'{"закреплены" if a.splits=="keep" else "отпущены"}: '
          f'подвижных {int((~frozen).sum())} из {m.n}')

    lab = np.where(uni, orig, 1).astype(np.int32)
    labels = [int(f) for f in np.unique(orig) if f > 0]
    INF = 1e9
    print(f'старт: граница {m.boundary(lab):.1f} мм')
    for rnd in range(a.rounds):
        changed = 0
        for i in range(len(labels)):
            for j in range(i + 1, len(labels)):
                A, B = labels[i], labels[j]
                free = ~uni if a.splits == 'free' else np.zeros(m.n, bool)
                sel = np.flatnonzero((lab == A) | (lab == B) | free)
                if not len(sel): continue
                idx = np.full(m.n, -1, np.int64); idx[sel] = np.arange(len(sel))
                ok = (idx[ea] >= 0) & (idx[eb] >= 0)
                g = maxflow.Graph[float](); nodes = g.add_nodes(len(sel))
                su = uni[sel]
                capA = np.where(su & (orig[sel] != A), Dw[sel], 0.0)
                capB = np.where(su & (orig[sel] != B), Dw[sel], 0.0)
                fz = frozen[sel] & su
                capA[fz & (orig[sel] != A)] = INF
                capB[fz & (orig[sel] != B)] = INF
                third = ~su & (lab[sel] != A) & (lab[sel] != B)   # already given to a third colour
                capA[third] = INF; capB[third] = INF
                g.add_grid_tedges(nodes, capB, capA)
                g.add_edges(idx[ea[ok]], idx[eb[ok]], w[ok], w[ok])
                g.maxflow()
                new = np.where(g.get_grid_segments(nodes), B, A).astype(np.int32)
                new[third] = lab[sel][third]
                changed += int((new != lab[sel]).sum()); lab[sel] = new
        print(f'  проход {rnd+1}: сдвинуто {changed:6d}, граница {m.boundary(lab):.1f} мм')
        if not changed: break

    for _ in range(20):                       # absorb the remaining speckle
        nc, comp = components(m, lab)
        ca = np.bincount(comp, weights=area)
        bad = np.flatnonzero((ca > 0) & (ca < a.min_area))
        if not len(bad): break
        isbad = np.isin(comp, bad)
        cross = (lab[ea] != lab[eb]) & (isbad[ea] ^ isbad[eb])
        s = np.where(isbad[ea[cross]], ea[cross], eb[cross])
        t = np.where(isbad[ea[cross]], eb[cross], ea[cross])
        best = {}
        for cs, ct, L in zip(comp[s], lab[t], elen[cross]):
            best[(cs, ct)] = best.get((cs, ct), 0.0) + L
        win = {}
        for (cs, ct), L in best.items():
            if L > win.get(cs, (0.0, 0))[0]: win[cs] = (L, ct)
        if not win: break
        for cs, (_, ct) in win.items(): lab[comp == cs] = ct

    ch = (lab != orig) & uni
    print(f'\nперекрашено целых {int(ch.sum())} ({area[ch].sum():.1f} мм2, '
          f'{100*area[ch].sum()/area.sum():.3f}% площади)')
    if a.splits == 'free':
        print(f'дроблёных сведено к одному цвету: {int((~uni).sum())}')
    for f in labels:
        print(f'  филамент {f}: {area[uni&(orig==f)].sum():8.1f} -> {area[lab==f].sum():8.1f} мм2')
    d = dict(np.load(a.npz, allow_pickle=True)); d['lab'] = lab.astype(np.int8)
    np.savez_compressed(a.out, **d)


# ---------------------------------------------------------------- write
def cmd_write(a):
    lab = np.load(a.npz, allow_pickle=True)['lab'].astype(int)
    zin = zipfile.ZipFile(a.src)
    ent = model_entry(zin)
    raw = zin.read(ent).decode('utf-8')
    st = dict(i=0, same=0, recol=0, collapsed=0, kept=0)

    def sub(mm):
        i = st['i']; st['i'] = i + 1
        old = (mm.group(4) or '').upper()
        new = int(lab[i])
        split = old not in CODE2FIL
        if split and not a.collapse_splits:
            st['kept'] += 1; return mm.group(0)          # hand brushwork - verbatim
        if not split and CODE2FIL[old] == new:
            st['same'] += 1; return mm.group(0)
        st['collapsed' if split else 'recol'] += 1
        return (f'<triangle v1="{mm.group(1)}" v2="{mm.group(2)}" v3="{mm.group(3)}"'
                f' paint_color="{FIL2CODE[new]}"/>')

    out = TRI.sub(sub, raw)
    assert st['i'] == len(lab), f'треугольников в файле {st["i"]}, меток {len(lab)}'
    print(f'без изменений {st["same"]}, перекрашено {st["recol"]}, '
          f'дроблёных сведено {st["collapsed"]}, сохранено дословно {st["kept"]}')
    tmp = a.dst + '.tmp'
    with zipfile.ZipFile(tmp, 'w') as z:
        for it in zin.infolist():
            data = out.encode('utf-8') if it.filename == ent else zin.read(it.filename)
            zi = zipfile.ZipInfo(it.filename, date_time=it.date_time)
            zi.compress_type = it.compress_type; zi.external_attr = it.external_attr
            z.writestr(zi, data)
    zin.close(); os.replace(tmp, a.dst)
    print(f'{a.dst}  {os.path.getsize(a.dst)/1e6:.2f} МБ')


# ---------------------------------------------------------------- filament
# Filament settings are not one value per filament: with N filaments there are
# lists of length N (one entry), 2N (normal and high-flow nozzle) and 4N.
# ALL of them must be extended - leave one at the old length and Bambu Studio
# reads past the end of a vector. The CLI stays silent and returns 0, while the
# GUI reports an invalid configuration and then no geometry data.
DENY_PREFIX = ('machine_max_', 'extruder_', 'printer_extruder')
DENY = {'printable_area', 'bed_exclude_area', 'print_compatible_printers', 'nozzle_diameter',
        'thumbnails', 'bed_custom_texture', 'bed_custom_model', 'wipe_tower_x', 'wipe_tower_y',
        'flush_volumes_matrix',                      # N x N, rebuilt separately
        'filament_nozzle_map', 'filament_volume_map'}  # fixed 9 slots


def cmd_filament(a):
    zin = zipfile.ZipFile(a.src)
    cfg = json.loads(zin.read('Metadata/project_settings.config'))
    n = len(cfg['filament_colour'])
    SRC = min(1, n - 1)                              # which filament to copy from
    grown = []
    for k, v in cfg.items():
        if not isinstance(v, list) or not v: continue
        if k in DENY or k.startswith(DENY_PREFIX) or len(v) % n: continue
        b = len(v) // n
        if k == 'filament_self_index':               # holds its own number
            v.extend([str(n + 1)] * b)
        else:
            v.extend(v[SRC * b:(SRC + 1) * b])
        grown.append(b)
    cfg['filament_colour'][n] = a.colour
    if 'filament_multi_colour' in cfg: cfg['filament_multi_colour'][n] = a.colour
    from collections import Counter
    print('расширено ключей по записей-на-филамент:', dict(Counter(grown)))

    M = [int(x) for x in cfg['flush_volumes_matrix']]
    new = [[M[i * n + j] for j in range(n)] for i in range(n)]
    for i in range(n): new[i].append(new[i][SRC])
    new.append([new[SRC][j] for j in range(n)] + [0])
    cfg['flush_volumes_matrix'] = [str(x) for row in new for x in row]

    left = [(k, len(v)) for k, v in cfg.items() if isinstance(v, list) and v
            and k not in DENY and not k.startswith(DENY_PREFIX)
            and len(v) % n == 0 and len(v) % (n + 1)]
    assert not left, f'остались списки старой длины: {left}'
    assert len(cfg['flush_volumes_matrix']) == (n + 1) ** 2

    ms = zin.read('Metadata/model_settings.config').decode('utf-8')
    for key, add in (('filament_maps', '1'), ('filament_volume_maps', '0')):
        ms = re.sub(rf'(key="{key}" value=")([^"]*)(")',
                    lambda mm: mm.group(1) + mm.group(2) + ' ' + add + mm.group(3), ms)
    js = json.dumps(cfg, indent=4, ensure_ascii=True).replace('\n', '\r\n')  # as Bambu Studio does
    repl = {'Metadata/project_settings.config': js.encode('utf-8'),
            'Metadata/model_settings.config': ms.encode('utf-8')}
    with zipfile.ZipFile(a.dst, 'w') as z:
        for it in zin.infolist():
            zi = zipfile.ZipInfo(it.filename, date_time=it.date_time)
            zi.compress_type = it.compress_type; zi.external_attr = it.external_attr
            z.writestr(zi, repl.get(it.filename, zin.read(it.filename)))
    print(f'филаментов {n} -> {n+1}, цвета: {cfg["filament_colour"]}')
    print(f'{a.dst}  {os.path.getsize(a.dst)/1e6:.2f} МБ')


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='cmd', required=True)

    q = sub.add_parser('parse', help='3MF -> npz с сеткой, покраской и смежностью')
    q.add_argument('src'); q.add_argument('out'); q.set_defaults(fn=cmd_parse)

    q = sub.add_parser('explode', help='разложить кисть на одноцветные подтреугольники')
    q.add_argument('src'); q.add_argument('out')
    q.add_argument('--object', help='имя или номер объекта в проекте')
    q.add_argument('--weld', type=float, default=1e-5, help='сетка сварки вершин, мм')
    q.set_defaults(fn=cmd_explode)

    q = sub.add_parser('stats', help='длина границы, крап, площади по филаментам')
    q.add_argument('npz'); q.add_argument('--min-area', type=float, default=0.5)
    q.set_defaults(fn=cmd_stats)

    q = sub.add_parser('smooth', help='выровнять границы цвета графорезом')
    q.add_argument('npz'); q.add_argument('out')
    q.add_argument('--band', type=int, default=4, help='на сколько граней границе можно сдвинуться')
    q.add_argument('--lam', type=float, default=1.0, help='вес привязки к исходной покраске')
    q.add_argument('--core', type=int, default=3, help='с какой грани закреплять сердцевину (0 — не закреплять)')
    q.add_argument('--splits', choices=('keep', 'free'), default='keep',
                   help='дроблёные треугольники: keep — не трогать, free — отдать на решение графорезу')
    q.add_argument('--crease-pow', type=float, default=4.0)
    q.add_argument('--crease-floor', type=float, default=0.05)
    q.add_argument('--min-area', type=float, default=0.3)
    q.add_argument('--rounds', type=int, default=6)
    q.set_defaults(fn=cmd_smooth)

    q = sub.add_parser('write', help='записать покраску обратно в 3MF')
    q.add_argument('src'); q.add_argument('npz'); q.add_argument('dst')
    q.add_argument('--collapse-splits', action='store_true',
                   help='свести дроблёные треугольники к одному цвету')
    q.set_defaults(fn=cmd_write)

    q = sub.add_parser('filament', help='добавить в проект ещё один филамент')
    q.add_argument('src'); q.add_argument('dst'); q.add_argument('colour')
    q.set_defaults(fn=cmd_filament)

    a = p.parse_args()
    a.fn(a)


if __name__ == '__main__':
    main()
