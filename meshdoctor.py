#!/usr/bin/env python3
"""Диагноз сетки: что именно сломано и чинится ли это автоматом.

Зачем отдельно от printcheck.py: тот склеивает вершины по round(c,4) и на
импортированных сетках во float32 выдаёт ложные дыры (о чём написано в его
собственной шапке). Здесь вершины склеиваются по допуску через сдвинутые
сетки хеширования, и результат сверяется с точной склейкой: расхождение
между ними — это и есть «шов рассыпался в числах», а не настоящая дыра.

Дефекты разделяются по классам, потому что чинятся они по-разному:

  открытое ребро (1 грань)   дырка          -> holes_fill закрывает
  non-manifold ребро (>=3)   Т-образный шов -> нужен разрез/удаление, автомат опасен
  несогласованный обход      вывернутая грань -> recalc_face_normals
  вырожденная грань          нулевая площадь  -> удаление
  дубль грани                              -> удаление

Форматы: STL (бинарный и ASCII), 3MF (проектный Bambu и голый core), OFF, OBJ.
GLB/PLY -> сперва прогнать через Blender (см. meshfix.py --convert).
"""
import sys, os, struct, zipfile, re, math, json
import xml.etree.ElementTree as ET
import numpy as np
import hardware                       # диаметр сопла — из hardware.json


# ---------- чтение ----------

def read_stl(path):
    buf = open(path, 'rb').read()
    ascii_hdr = buf[:5].lower() == b'solid' and b'facet' in buf[:512]
    if not ascii_hdr:
        n = struct.unpack('<I', buf[80:84])[0]
        if len(buf) < 84 + n * 50:
            raise ValueError(f"бинарный STL оборван: заявлено {n} треугольников")
        raw = np.frombuffer(buf, dtype=np.uint8, count=n * 50, offset=84).reshape(n, 50)
        v = raw[:, 12:48].copy().view('<f4').reshape(n, 3, 3).astype(np.float64)
        return [("stl", v.reshape(-1, 3), np.arange(n * 3).reshape(n, 3), False)]
    txt = buf.decode('utf-8', 'replace')
    nums = np.array(re.findall(r'vertex\s+(\S+)\s+(\S+)\s+(\S+)', txt), dtype=np.float64)
    n = len(nums) // 3
    return [("stl", nums[:n * 3], np.arange(n * 3).reshape(n, 3), False)]


SCALE_WARN = {}          # label -> строка про масштаб из <build>, см. _build_scales


def _uniform_scale(t):
    """Равномерный масштаб из матрицы 3MF (9 чисел поворота+масштаба). None — если неравномерный."""
    a = [float(x) for x in t.split()]
    if len(a) < 9:
        return None
    cols = [math.sqrt(a[0] ** 2 + a[1] ** 2 + a[2] ** 2),
            math.sqrt(a[3] ** 2 + a[4] ** 2 + a[5] ** 2),
            math.sqrt(a[6] ** 2 + a[7] ** 2 + a[8] ** 2)]
    if max(cols) - min(cols) > 1e-6 * max(cols):
        return None
    return cols[0]


def _build_scales(z, models):
    """{(файл, id объекта): масштаб} из <build><item> с проходом через <components>.

    Сетка внутри 3MF лежит в своих единицах, в миллиметры её переводит матрица
    из <build>. `BambuStudio --info` её НЕ применяет и печатает сырой габарит —
    проверено 19.09.2026 на «dutch on chair hi3d.3mf»: и он, и meshdoctor дают
    19.28×25.40×19.79 при масштабе 3.3465, то есть настоящая фигурка 64×85×66 мм.
    Мы её тоже не применяем (иначе разойдёмся с --info), а называем: без этого
    вердикт «мельче сопла» занижает дефект во столько же раз.
    """
    comps, builds = {}, []
    for name in models:
        try:
            root = ET.fromstring(z.read(name))
        except Exception:
            continue
        ns = {'c': root.tag.split('}')[0].strip('{')} if '}' in root.tag else {}
        P = '{http://schemas.microsoft.com/3dmanufacturing/production/2015/06}path'
        def find(el, tag):
            return el.findall(f'c:{tag}', ns) if ns else el.findall(tag)
        for res in find(root, 'resources'):
            for obj in find(res, 'object'):
                for cs in find(obj, 'components'):
                    for c in list(cs):
                        k = _uniform_scale(c.get('transform') or '1 0 0 0 1 0 0 0 1')
                        tgt = (c.get(P) or '').lstrip('/') or name
                        comps.setdefault((name, obj.get('id')), []).append(
                            (tgt, c.get('objectid'), 1.0 if k is None else k))
        for b in find(root, 'build'):
            for it in find(b, 'item'):
                k = _uniform_scale(it.get('transform') or '1 0 0 0 1 0 0 0 1')
                tgt = (it.get(P) or '').lstrip('/') or name
                builds.append((tgt, it.get('objectid'), 1.0 if k is None else k))

    out, seen = {}, set()
    stack = list(builds)
    while stack:
        f, oid, k = stack.pop()
        if (f, oid) in seen:
            continue
        seen.add((f, oid))
        out[(f, oid)] = k
        for f2, oid2, k2 in comps.get((f, oid), []):
            stack.append((f2, oid2, k * k2))
    return out


def read_3mf(path):
    """Каждый <object> с сеткой — отдельно. Индексы уже в файле, склейка не нужна."""
    out = []
    with zipfile.ZipFile(path) as z:
        models = [n for n in z.namelist() if n.lower().endswith('.model')]
        scales = _build_scales(z, models)
        for name in sorted(models):
            root = ET.fromstring(z.read(name))
            ns = {'c': root.tag.split('}')[0].strip('{')} if '}' in root.tag else {}
            def find(el, tag):
                return el.findall(f'c:{tag}', ns) if ns else el.findall(tag)
            for res in find(root, 'resources'):
                for obj in find(res, 'object'):
                    for mesh in find(obj, 'mesh'):
                        vs, ts = find(mesh, 'vertices'), find(mesh, 'triangles')
                        if not vs or not ts:
                            continue
                        V = np.array([[float(v.get('x')), float(v.get('y')), float(v.get('z'))]
                                      for v in list(vs[0])], dtype=np.float64)
                        F = np.array([[int(t.get('v1')), int(t.get('v2')), int(t.get('v3'))]
                                      for t in list(ts[0])], dtype=np.int64)
                        if len(F):
                            label = f"{os.path.basename(name)}:object id={obj.get('id')}"
                            if obj.get('name'):
                                label += f" «{obj.get('name')}»"
                            k = scales.get((name, obj.get('id')))
                            if k is not None and abs(k - 1) > 1e-6:
                                SCALE_WARN[label] = (
                                    f"!! сетка в своих единицах: <build> масштабирует её в {k:.4g} раза. "
                                    f"Всё ниже — сырые единицы файла (так же считает BambuStudio --info); "
                                    f"настоящие миллиметры = ×{k:.4g}, и дефект «мельче сопла» тоже")
                            out.append((label, V, F, True))
    if not out:
        raise ValueError("в 3MF нет ни одной сетки (возможно, это .gcode.3mf — результат нарезки)")
    return out


def read_off(path):
    toks = []
    for line in open(path):
        line = line.split('#')[0].strip()
        if line:
            toks.append(line)
    assert toks[0].upper().startswith('OFF')
    head = toks[0][3:].split() or toks[1].split()
    start = 1 if toks[0][3:].split() else 2
    nv, nf = int(head[0]), int(head[1])
    V = np.array([list(map(float, toks[start + i].split()[:3])) for i in range(nv)])
    F = []
    for i in range(nf):
        p = list(map(int, toks[start + nv + i].split()[:1 + int(toks[start + nv + i].split()[0])]))
        for k in range(1, p[0] - 1):           # веер по n-угольнику
            F.append([p[1], p[1 + k], p[2 + k]])
    return [("off", V, np.array(F, dtype=np.int64), True)]


def read_obj(path):
    V, F = [], []
    for line in open(path):
        if line.startswith('v '):
            V.append(list(map(float, line.split()[1:4])))
        elif line.startswith('f '):
            idx = [int(p.split('/')[0]) for p in line.split()[1:]]
            idx = [i - 1 if i > 0 else len(V) + i for i in idx]
            for k in range(1, len(idx) - 1):
                F.append([idx[0], idx[k], idx[k + 1]])
    return [("obj", np.array(V), np.array(F, dtype=np.int64), True)]


def read_any(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == '.stl':  return read_stl(path)
    if ext == '.3mf':  return read_3mf(path)
    if ext == '.off':  return read_off(path)
    if ext == '.obj':  return read_obj(path)
    raise ValueError(f"формат {ext} не читается напрямую: сперва meshfix.py --convert через Blender")


# ---------- склейка вершин ----------

def weld(V, F, tol):
    """Слить вершины ближе tol. Восемь сдвинутых сеток: две точки в пределах
    tol по каждой оси гарантированно попадают в одну ячейку хотя бы в одном
    сдвиге, поэтому шов не рассыпается на границе округления."""
    n = len(V)
    parent = np.arange(n)

    def find(x):
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:
            parent[x], x = root, parent[x]
        return root

    for combo in range(8):
        shift = np.array([(combo >> k) & 1 for k in range(3)]) * (tol / 2)
        key = np.floor((V + shift) / tol).astype(np.int64)
        _, inv = np.unique(key, axis=0, return_inverse=True)
        order = np.argsort(inv, kind='stable')
        inv_s = inv[order]
        starts = np.flatnonzero(np.r_[True, inv_s[1:] != inv_s[:-1]])
        for s, e in zip(starts, np.r_[starts[1:], len(order)]):
            if e - s > 1:
                rep = find(order[s])
                for j in order[s + 1:e]:
                    rj = find(j)
                    if rj != rep:
                        parent[rj] = rep
    roots = np.array([find(i) for i in range(n)])
    uniq, remap = np.unique(roots, return_inverse=True)
    return V[uniq], remap[F]


# ---------- анализ ----------

def analyse(V, F, tol):
    r = {"faces_in": len(F), "verts_in": len(V)}

    # вырожденные: совпали индексы либо нулевая площадь
    a, b, c = V[F[:, 0]], V[F[:, 1]], V[F[:, 2]]
    cross = np.cross(b - a, c - a)
    area2 = np.linalg.norm(cross, axis=1)
    same_idx = (F[:, 0] == F[:, 1]) | (F[:, 1] == F[:, 2]) | (F[:, 0] == F[:, 2])
    degen = same_idx | (area2 <= 1e-14)
    r["degenerate"] = int(degen.sum())

    # Вырожденную грань надо СХЛОПНУТЬ, а не выбросить. Выброс рвёт дыру:
    # у соседних граней пропадает общее ребро. На дорожке жд удаление 12
    # вырожденных граней открывало 12 рёбер, которых в файле нет, — ровно
    # тот ложный диагноз, которым грешит printcheck.py. Blender при импорте
    # и Bambu Studio при загрузке схлопывают, поэтому у них сетка замкнута
    # (проверено 18.09.2026: Blender 84476 граней и 0 открытых рёбер,
    # BambuStudio --info manifold = yes).
    if degen.any():
        nv = len(V)
        parent_v = np.arange(nv)

        def fv(x):
            root = x
            while parent_v[root] != root:
                root = parent_v[root]
            while parent_v[x] != root:
                parent_v[x], x = root, parent_v[x]
            return root
        for tri in F[degen]:
            a0 = fv(tri[0])
            for t in tri[1:]:
                rt = fv(t)
                if rt != a0:
                    parent_v[rt] = a0
        roots = np.array([fv(i) for i in range(nv)])
        keep_v, remap = np.unique(roots, return_inverse=True)
        V = V[keep_v]          # вершины переиндексовать вместе с гранями
        F = remap[F]
        keep = ~((F[:, 0] == F[:, 1]) | (F[:, 1] == F[:, 2]) | (F[:, 0] == F[:, 2]))
        F = F[keep]
        a, b, c = V[F[:, 0]], V[F[:, 1]], V[F[:, 2]]
        cross = np.cross(b - a, c - a)
        area2 = np.linalg.norm(cross, axis=1)
    if len(F) == 0:
        r["empty"] = True
        return r

    # дубли граней (с точностью до вращения и направления)
    key = np.sort(F, axis=1)
    _, first, counts = np.unique(key, axis=0, return_index=True, return_counts=True)
    r["duplicate_faces"] = int((counts - 1).sum())

    # рёбра
    E = np.vstack([F[:, [0, 1]], F[:, [1, 2]], F[:, [2, 0]]])
    Es = np.sort(E, axis=1)
    uniq_e, inv_e, cnt_e = np.unique(Es, axis=0, return_inverse=True, return_counts=True)
    r["edges"] = len(uniq_e)
    r["open_edges"] = int((cnt_e == 1).sum())          # дырка
    r["nonmanifold_edges"] = int((cnt_e >= 3).sum())   # Т-образный шов
    nm_e = uniq_e[cnt_e >= 3]
    r["nm_edge_max"] = (round(float(np.linalg.norm(V[nm_e[:, 0]] - V[nm_e[:, 1]], axis=1).max()), 4)
                        if len(nm_e) else 0.0)

    # Обход считать ТОЛЬКО по нормальным рёбрам с двумя гранями. Если мерить
    # по всем, каждое non-manifold ребро попадает сюда же и число раздувается
    # ровно вдвое против числа non-manifold — на переключателе жд было
    # «non-manifold 3, обход 6», и вторая цифра ничего своего не сообщала.
    order_e = np.argsort(inv_e, kind='stable')
    inv_es = inv_e[order_e]
    starts_e = np.flatnonzero(np.r_[True, inv_es[1:] != inv_es[:-1]])
    pair_mask = cnt_e[inv_es[starts_e]] == 2
    p0, p1 = order_e[starts_e[pair_mask]], order_e[starts_e[pair_mask] + 1]
    r["bad_winding_edges"] = int((E[p0] == E[p1]).all(axis=1).sum())

    # оболочки: связность граней через рёбра
    nf = len(F)
    parent = np.arange(nf)

    def find(x):
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:
            parent[x], x = root, parent[x]
        return root


    # Два счёта тел. Через все рёбра non-manifold шов склеивает касающиеся
    # тела в одно; через нормальные — тела считаются раздельно. Расхождение
    # и есть диагноз: «тройная развилка жд» — 1 тело через все рёбра и 40
    # через нормальные, то есть 40 кусков, слепленных касанием по 39 рёбрам,
    # а не объединённых булевой операцией.
    face_of = np.tile(np.arange(nf), 3)
    for only_pairs in (False, True):
        parent = np.arange(nf)
        for s, e in zip(starts_e, np.r_[starts_e[1:], len(order_e)]):
            if only_pairs and (e - s) != 2:
                continue
            rep = find(face_of[order_e[s]])
            for j in order_e[s + 1:e]:
                rj = find(face_of[j])
                if rj != rep:
                    parent[rj] = rep
        lbl = np.array([find(i) for i in range(nf)])
        n = len(np.unique(lbl))
        r["bodies_touching" if not only_pairs else "bodies_separate"] = n
        if only_pairs and n > 1:
            # Объём каждого тела отдельно: так видно мусорные островки,
            # которые остаются после воксельной пересборки и после булевых
            # операций. Печатать их не надо, а в габарит они входят.
            vol_f = np.einsum('ij,ij->i', a, cross) / 6.0
            uniq_l, inv_l = np.unique(lbl, return_inverse=True)
            vols = np.bincount(inv_l, weights=vol_f, minlength=len(uniq_l))
            cnts = np.bincount(inv_l, minlength=len(uniq_l))
            order_b = np.argsort(-np.abs(vols))
            # Показываем дюжину крупнейших, а СЧИТАЕМ мусор по всем. Раньше
            # счёт шёл по тому же срезу в 12 штук, и у Dutch с его 6811 телами
            # отчёт говорил «мусорных 11» вместо 6810 (замечено 19.09.2026).
            biggest = abs(vols[order_b[0]])
            r["junk_bodies"] = int((np.abs(vols) < biggest * 1e-3).sum())
            r["body_volumes"] = [(int(cnts[i]), round(float(vols[i]), 3)) for i in order_b[:12]]
    r["shells"] = r["bodies_touching"]

    # дырки как петли из открытых рёбер
    bnd = uniq_e[cnt_e == 1]
    if len(bnd):
        vs = np.unique(bnd)
        idx = {v: i for i, v in enumerate(vs)}
        p2 = np.arange(len(vs))

        def f2(x):
            root = x
            while p2[root] != root:
                root = p2[root]
            while p2[x] != root:
                p2[x], x = root, p2[x]
            return root
        for u, w in bnd:
            ru, rw = f2(idx[u]), f2(idx[w])
            if ru != rw:
                p2[rw] = ru
        groups = {}
        for u, w in bnd:
            groups.setdefault(f2(idx[u]), []).append(np.linalg.norm(V[u] - V[w]))
        r["holes"] = len(groups)
        r["hole_perimeters"] = sorted((round(float(sum(g)), 3) for g in groups.values()), reverse=True)
    else:
        r["holes"] = 0
        r["hole_perimeters"] = []

    # объём и габарит
    vol = np.einsum('ij,ij->i', a, cross).sum() / 6.0
    r["volume"] = float(vol)
    r["area"] = float(area2.sum() / 2)
    lo, hi = V[np.unique(F)].min(0), V[np.unique(F)].max(0)
    r["bbox"] = [round(x, 3) for x in (hi - lo)]
    r["faces"] = len(F)
    r["verts"] = len(np.unique(F))
    r["euler"] = chi = r["verts"] - r["edges"] + r["faces"]
    # Род считать от числа ТЕЛ: (2*тел - хи)/2. Формула (2-хи)/2 верна только
    # для одного тела, а на нескольких даёт отрицательные значения. Нечётное
    # хи означает, что поверхность не простая, и рода у неё нет.
    bodies = r["bodies_separate"]
    if r["open_edges"] or r["nonmanifold_edges"]:
        r["genus"] = "не определён: сетка не замкнута"
    elif (2 * bodies - chi) % 2:
        r["genus"] = "не определён: хи нечётно, поверхность не простая"
    else:
        r["genus"] = str((2 * bodies - chi) // 2)
    r["watertight"] = (r["open_edges"] == 0 and r["nonmanifold_edges"] == 0)
    r["consistent"] = (r["bad_winding_edges"] == 0)
    return r


NOZZLE = hardware.nozzle()   # из hardware.json: всё мельче сопла в печати не существует


def verdict(r):
    # Мерить дефект соплом, а не считать штуки. Дырка периметром 0.16 мм на
    # утке — это круг диаметром 0.05 мм: экструдер кладёт нитку 0.42 мм, такую
    # дырку он просто заметает. Гнать на ремонт из-за неё значит рисковать
    # покраской и топологией ради того, чего в пластике не будет.
    tiny = r.get("hole_perimeters") and all(p < NOZZLE * math.pi for p in r["hole_perimeters"])

    if r.get("empty"):
        return "ПУСТО", "после выброса вырожденных граней не осталось геометрии"
    njunk = r.get("junk_bodies", 0)
    if njunk and r["watertight"] and r["consistent"]:
        return "АВТОМАТ", (f"сетка замкнута, но рядом с деталью висит {njunk} мусорных тел "
                           "— meshfix.py --dropjunk")
    if r["watertight"] and r["consistent"] and r["volume"] > 0 and not r["duplicate_faces"]:
        extra = f" (вырожденных граней {r['degenerate']} — выбрасываются при нарезке)" if r["degenerate"] else ""
        return "ЧИСТО", "слайсер съест как есть" + extra
    # Всё ли, что сломано, мельче сопла? Тогда это шум сетки, а не дефект детали.
    nm_tiny = r.get("nm_edge_max", 0) < NOZZLE
    junk_only = r.get("junk_bodies", 0) == max(0, r.get("bodies_separate", 1) - 1)
    if (tiny or not r["holes"]) and nm_tiny and junk_only and not r["degenerate"] and not r["duplicate_faces"]:
        bits = []
        if r["holes"]:
            bits.append(f"{r['holes']} дыр периметром до {max(r['hole_perimeters'])} мм")
        if r["nonmanifold_edges"]:
            bits.append(f"{r['nonmanifold_edges']} non-manifold рёбер длиной до {r['nm_edge_max']} мм")
        if njunk:
            bits.append(f"{njunk} мусорных тел")
        if bits:
            return "МЕЛОЧЬ", ("; ".join(bits) + f" — всё мельче сопла {NOZZLE} мм. "
                              "Слайсер это заметает; ремонт рискует больше, чем чинит")
    if r["nonmanifold_edges"]:
        return "РУКАМИ", (f"{r['nonmanifold_edges']} non-manifold рёбер, самое длинное {r['nm_edge_max']} мм — "
                          f"поточечный ремонт их не берёт, лечит воксельная пересборка: "
                          f"meshfix.py --remesh {NOZZLE / 2:g}")
    if r["volume"] < 0:
        return "АВТОМАТ", "сетка вывернута наизнанку целиком — recalc_face_normals"
    bits = []
    if r["holes"]:            bits.append(f"{r['holes']} дыр")
    if r["bad_winding_edges"]: bits.append(f"{r['bad_winding_edges']} рёбер с рассогласованным обходом")
    if r["degenerate"]:       bits.append(f"{r['degenerate']} вырожденных граней")
    if r["duplicate_faces"]:  bits.append(f"{r['duplicate_faces']} дублей граней")
    return "АВТОМАТ", ", ".join(bits) or "мелочь"


def report(path, tol_rel=1e-6, as_json=False, quiet_clean=False):
    parts = read_any(path)
    results = []
    for label, V, F, indexed in parts:
        diag = float(np.linalg.norm(V.max(0) - V.min(0))) if len(V) else 1.0
        tol = max(diag * tol_rel, 1e-9)

        if indexed:
            # 3MF, OBJ, OFF: связность задана индексами автора — это и есть
            # истина. Склеивать вершины тут нельзя: у «dutch on chair» в списке
            # 14 вершин с одинаковыми координатами (шов развёртки), и склейка
            # сшивала два листа поверхности в 7 несуществующих non-manifold
            # рёбер. В файле их ноль, модель целая (проверено 18.09.2026).
            r = analyse(V, F, tol)
            r["seam_noise"] = 0
            uniq_v = np.unique(V, axis=0)
            r["split_vertices"] = len(V) - len(uniq_v)
        else:
            # STL: индексов нет вообще, каждый треугольник со своими копиями
            # вершин. Сперва склейка бит-в-бит; если она уже дала замкнутую
            # сетку — по допуску склеивать незачем, это самый дорогой шаг
            # (десяток секунд на 470 тыс. граней).
            uniq_v, inv_v = np.unique(V, axis=0, return_inverse=True)
            r_exact = analyse(uniq_v, inv_v[F], tol)
            if r_exact.get("empty") or (r_exact["open_edges"] == 0
                                        and r_exact["nonmanifold_edges"] == 0
                                        and r_exact["bad_winding_edges"] == 0):
                r, r["seam_noise"] = r_exact, 0
            else:
                r = analyse(*weld(V, F, tol), tol=tol)
                r["seam_noise"] = r_exact["open_edges"] - r["open_edges"]
            r["split_vertices"] = 0
        r["label"], r["file"], r["indexed"] = label, path, indexed
        r["tol"] = tol
        r["verdict"], r["why"] = verdict(r)
        results.append(r)

    if as_json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return results

    for r in results:
        if quiet_clean and r["verdict"] == "ЧИСТО":
            continue
        head = f"{os.path.basename(path)}"
        if r["label"] not in ("stl", "obj", "off"):
            head += f"  [{r['label']}]"
        print(f"\n{head}")
        if r["label"] in SCALE_WARN:
            print("  " + SCALE_WARN[r["label"]])
        bodies = (f"тел {r['bodies_touching']}" if r['bodies_touching'] == r['bodies_separate']
                  else f"тел {r['bodies_touching']}, но по нормальным рёбрам {r['bodies_separate']} "
                       f"— куски слеплены касанием, а не объединены")
        print(f"  {r['faces_in']} треугольников, габарит {r['bbox'][0]}×{r['bbox'][1]}×{r['bbox'][2]} мм, {bodies}")
        bv = r.get("body_volumes")
        if bv and len(bv) > 1:
            big = bv[0][1]
            print("     тела (граней / мм³): " + ", ".join(f"{c}/{v:g}" for c, v in bv[:6])
                  + (f" ... ещё {r['bodies_separate']-len(bv[:6])}" if r['bodies_separate'] > 6 else ""))
            if r.get("junk_bodies"):
                print(f"     мусорных островков мельче тысячной доли главного тела: "
                      f"{r['junk_bodies']} из {r['bodies_separate']} — выбросить перед печатью")
        if r.get("empty"):
            print("  !! пусто"); continue
        mark = lambda ok: "OK " if ok else "!! "
        # мелочь, которую слайсер выбрасывает сам: не поднимать ложную тревогу
        petty = "OK " if r["verdict"] == "ЧИСТО" else "!! "
        print(f"  {mark(r['open_edges']==0)}открытых рёбер (дырки): {r['open_edges']}"
              + (f" -> {r['holes']} петель, периметры {r['hole_perimeters'][:5]}" if r['holes'] else ""))
        print(f"  {mark(r['nonmanifold_edges']==0)}non-manifold рёбер (>=3 граней): {r['nonmanifold_edges']}")
        print(f"  {mark(r['bad_winding_edges']==0)}рёбер с рассогласованным обходом: {r['bad_winding_edges']}")
        print(f"  {petty if r['degenerate'] else 'OK '}вырожденных граней: {r['degenerate']}"
              + ("  (слайсер их игнорирует)" if r['degenerate'] and r['verdict'] == "ЧИСТО" else ""))
        print(f"  {petty if r['duplicate_faces'] else 'OK '}дублей граней: {r['duplicate_faces']}")
        print(f"  {mark(r['volume']>0)}объём {r['volume']:.1f} мм³ ({r['volume']/1000:.2f} см³)"
              + ("  (отрицательный: сетка вывернута)" if r['volume'] < 0 else ""))
        print(f"     эйлерова хар-ка {r['euler']}, тел {r['bodies_separate']}, "
              f"род (сквозных отверстий) {r['genus']}")
        if r["seam_noise"]:
            print(f"     склейка по допуску {r['tol']:.2e} мм убрала {r['seam_noise']} ложных открытых рёбер")
        if r.get("split_vertices"):
            print(f"     вершин-дублей по координатам: {r['split_vertices']} — это швы автора, "
                  "не дефект: связность берётся из индексов файла")
        print(f"  ВЕРДИКТ: {r['verdict']} — {r['why']}")
        if r["verdict"] in ("РУКАМИ", "АВТОМАТ"):
            worst = max([r.get("nm_edge_max", 0)] + (r.get("hole_perimeters") or [0]))
            if worst and worst < NOZZLE:
                print(f"     масштаб: самый крупный дефект {worst} мм при сопле {NOZZLE} мм. "
                      "Деталь, скорее всего, напечатается и так — чинить, если слайсер даст сбой")
    return results


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    flags = {a for a in sys.argv[1:] if a.startswith('--')}
    if not args:
        print(__doc__); sys.exit(1)
    bad = 0
    for p in args:
        try:
            rs = report(p, as_json='--json' in flags, quiet_clean='--only-broken' in flags)
            if any(r["verdict"] != "ЧИСТО" for r in rs):
                bad += 1
        except Exception as e:
            print(f"\n{os.path.basename(p)}\n  !! не прочитан: {e}")
            bad += 1
    sys.exit(1 if bad else 0)
