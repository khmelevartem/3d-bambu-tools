#!/usr/bin/env python3
"""Optimise one file before printing: settings, colour, and the fact from G-code.

figcheck.py answers what is wrong with the geometry - the bottom, overhangs,
stair steps. This one answers what is wrong with the PROJECT: whether the right
settings arrived, what multicolour will cost, and what actually came out.

    python3 tools/figopt.py audit  project.3mf              settings against the machine
    python3 tools/figopt.py colors project.3mf              colour changes by layer, and their price
    python3 tools/figopt.py gcode  work/out/plate_1.gcode   the fact, after slicing
    python3 tools/figopt.py tilt   project.3mf              does tilting change the change count

`audit` and `colors` run before slicing and do not need Bambu Studio.
The machine comes from hardware.json; A1_NOZZLE overrides the nozzle.
"""
import json, math, os, re, sys, zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hardware

BLOCK, WARN, HINT, OK = "БЛОКЕР", "ВАЖНО", "СОВЕТ", "ок"


def cfg_of(path):
    with zipfile.ZipFile(path) as z:
        return json.loads(z.read("Metadata/project_settings.config"))


def one(v):
    """A value may be a list per filament — take the first."""
    return v[0] if isinstance(v, list) and v else v


def audit(path):
    c = cfg_of(path)
    hw = hardware.load()
    prof = hardware.profile()
    nfil = len(c.get("filament_colour") or [])
    out = []

    def say(level, what, why):
        out.append((level, what, why))

    # --- compatibility with the machine: this decides whether the file opens at all
    pid = c.get("printer_settings_id", "")
    if pid != prof["machine"]:
        say(BLOCK, f"принтер в проекте: {pid}",
            f"у нас {prof['machine']}. Интерфейс откажется печатать: «The selected "
            f"printer is incompatible with the print file configuration». Меняется "
            f"только вручную в интерфейсе — CLI не равен переключению принтера")
    nz = one(c.get("nozzle_diameter"))
    if nz is not None and abs(float(nz) - hardware.nozzle()) > 1e-9:
        say(BLOCK, f"сопло в проекте {nz}", f"установлено {hardware.nozzle()}")
    if c.get("curr_bed_type") != hw["printer"]["plate"]:
        say(WARN, f"стол в проекте: {c.get('curr_bed_type')}",
            f"у нас {hw['printer']['plate']}; другой тип — другая температура стола")

    # --- multicolour: the main cost item
    if nfil > 1:
        say(WARN, f"{nfil} филамента: башня очистки "
                  f"{'включена' if str(c.get('enable_prime_tower')) == '1' else 'выключена'}",
            "решать нарезкой обоих вариантов, а не правилом. Замер 19.09.2026 на этом "
            "файле: с башней стало ХУЖЕ — +39.2 г и +2 ч 07 мин, а промывка не "
            "сократилась (110.9 -> 117.6 г, время промывки 668 -> 676 мин). На A1 "
            "основной расход уходит не в башню, а в отходы при смене филамента, "
            "и башня ложится сверху. Считать: figopt.py gcode на обеих нарезках")
        if nfil > 4:
            say(WARN, f"филаментов {nfil}, а слотов у AMS lite 4",
                "раскладку по слотам смотреть в интерфейсе; лимит цветов на A1 "
                "документацией не подтверждён")
        for k in ("flush_into_infill", "flush_into_objects"):
            if str(c.get(k)) == "1":
                say(HINT, f"{k} = 1", "промывка прячется в деталь — экономит пластик, "
                                      "но подмешивает цвет внутрь стенок")

    # --- what is visible on the surface of a figurine
    if str(c.get("wall_generator")) != "arachne":
        say(HINT, f"генератор стенок {c.get('wall_generator')}",
            "на органике arachne честно лучше: меньше мостов и щелевого заполнения")
    if c.get("seam_position") == "back":
        say(HINT, "шов «Сзади»",
            "выстраивает швы в одну прямую линию по центру спины. На фигурке "
            "«Выровненная» обычно уводит их в складки сама")
    if str(c.get("seam_slope_type")) == "none":
        say(HINT, "клиновидный шов выключен",
            "размазывает шов по 10 мм периметра, столбика точек не остаётся. "
            "Включать вместе с override_filament_scarf_seam_setting = 1, иначе "
            "настройка филамента перебьёт настройку процесса")
    if str(c.get("adaptive_layer_height")) == "1":
        say(HINT, "переменная высота слоя включена",
            "осознанное решение человека, само по себе не ошибка. Проверить два "
            "места: "
            "органические поддержки с ней несовместимы, а через CLI она дробит "
            "слои ниже min_layer_height экструдера")

    # --- supports
    if str(c.get("enable_support")) == "1":
        st = c.get("support_style")
        if st != "tree_organic":
            say(HINT, f"стиль поддержек {st}",
                "у органических (tree_organic, в интерфейсе «Органический») меньше "
                "точек контакта и они снимаются руками без следов")
        if str(c.get("support_on_build_plate_only")) == "1":
            say(WARN, "«Поддержка только от стола» включена",
                "у фигурки большая часть свесов висит над самой моделью и останется "
                "без поддержки. Проверить лучом: uv run tools/figcheck.py")
        ztd = c.get("support_top_z_distance")
        lh = float(c.get("layer_height", 0) or 0)
        if ztd is not None and lh and float(ztd) < 2 * lh - 1e-9:
            say(HINT, f"зазор поддержки сверху {ztd} при слое {lh}",
                "PLA по PLA при однослойном зазоре приваривается; брать два слоя")

    # --- adhesion
    if c.get("brim_type") in (None, "no_brim", "none"):
        say(HINT, "каймы нет",
            "у органики первый слой распадается на островки, связанные только выше; "
            "brim_type = outer_only, 5 мм")

    # --- will the values reach the GUI at all
    dss = c.get("different_settings_to_system") or []
    if dss and not str(dss[0]).strip():
        say(BLOCK, "список different_settings_to_system пуст",
            "интерфейс берёт системный пресет и накладывает только перечисленные "
            "здесь ключи. Всё остальное в файле декоративно — CLI этого не видит "
            "и режет по плоскому конфигу")

    # --- dimensions
    lh = c.get("layer_height")
    m = re.match(r"([\d.]+)mm", str(c.get("print_settings_id", "")))
    if m and lh and abs(float(m.group(1)) - float(lh)) > 1e-9:
        say(HINT, f"слой {lh} при профиле «{c.get('print_settings_id')}»",
            "имя профиля и значение разошлись — автор правил слой руками")

    print(f"{os.path.basename(path)}: {nfil} филамент(ов), слой {lh}, "
          f"первый {c.get('initial_layer_print_height')}")
    print(f"  профиль: {c.get('print_settings_id')} / {pid}\n")
    order = {BLOCK: 0, WARN: 1, HINT: 2}
    for level, what, why in sorted(out, key=lambda r: order[r[0]]):
        print(f"[{level}] {what}")
        for line in _wrap(why, 74):
            print(f"         {line}")
    if not out:
        print("замечаний нет")
    return sum(1 for r in out if r[0] == BLOCK)


def _wrap(s, n):
    words, line, out = s.split(), "", []
    for w in words:
        if len(line) + len(w) + 1 > n:
            out.append(line); line = w
        else:
            line = f"{line} {w}".strip()
    if line:
        out.append(line)
    return out


# ---------------------------------------------------------------- colour

def _mesh_on_plate(path):
    """Vertices, faces and filament per face, in bed millimetres.

        A mesh in a 3MF lies in its own units; the matrix in
        <build><item transform=...>, through the <components> chain, converts it to
        millimetres. Without it both the bounding box and — more importantly — the
        z axis are wrong, because a figure is often stored lying down and it is that
        matrix which stands it on its feet.
"""
    import numpy as np
    import paint
    z = zipfile.ZipFile(path)
    ent = paint.model_entry(z)
    raw = z.read(ent).decode("utf-8")
    V = np.array(paint.VERT.findall(raw), dtype=np.float64)
    t1, t2, t3, lab = [], [], [], []
    for m in paint.TRI.finditer(raw):
        t1.append(m.group(1)); t2.append(m.group(2)); t3.append(m.group(3))
        lab.append(paint.CODE2FIL.get((m.group(4) or "").upper(), paint.SPLIT))
    F = np.array([t1, t2, t3], dtype=np.int64).T
    lab = np.array(lab, dtype=np.int8)

    root = z.read("3D/3dmodel.model").decode("utf-8")
    M, T = np.eye(3), np.zeros(3)
    it = re.search(r'<item[^>]*transform="([^"]+)"', root)
    if it:
        a = [float(x) for x in it.group(1).split()]
        if len(a) >= 9:
            M = np.array(a[:9]).reshape(3, 3).T
            if len(a) >= 12:
                T = np.array(a[9:12])
    return V @ M.T + T, F, lab, float(np.linalg.norm(M[:, 0]))


def _tilt(W, rx, ry):
    """Tilt about the part's centre, then back onto the bed (z-min = 0)."""
    import numpy as np
    c = (W.max(0) + W.min(0)) / 2
    P = W - c
    for ang, ax in ((rx, 0), (ry, 1)):
        if not ang:
            continue
        t = math.radians(ang)
        ca, sa = math.cos(t), math.sin(t)
        R = np.eye(3)
        if ax == 0:
            R[1, 1], R[1, 2], R[2, 1], R[2, 2] = ca, -sa, sa, ca
        else:
            R[0, 0], R[0, 2], R[2, 0], R[2, 2] = ca, sa, -sa, ca
        P = P @ R.T
    P = P + c
    P[:, 2] -= P[:, 2].min()
    return P


def _changes(W, F, lab, nfil, LH, H1):
    """How many filament changes this pose gives: by layers, without slicing."""
    import numpy as np
    zt = W[F][:, :, 2]
    lo, hi = zt.min(1), zt.max(1)
    nlay = int(math.ceil((float(W[:, 2].max()) - H1) / LH)) + 1

    def layer_of(v):
        return np.clip(np.floor((v - H1) / LH).astype(int) + 1, 0, nlay - 1)

    l0, l1 = layer_of(lo), layer_of(hi)
    present = np.zeros((nlay, nfil + 1), bool)
    for f in range(1, nfil + 1):
        m = lab == f
        if not m.any():
            continue
        acc = np.zeros(nlay + 1, np.int64)
        np.add.at(acc, l0[m], 1)
        np.add.at(acc, l1[m] + 1, -1)
        present[:, f] = np.cumsum(acc[:-1]) > 0
    cnt = present[:, 1:].sum(1)
    return int(np.maximum(cnt - 1, 0).sum()), present, cnt, nlay


def tilt_sweep(path, lo=-45, hi=45, step=5):
    """Whether a tilt changes the number of filament changes. From the mesh; no slicing needed."""
    import numpy as np
    c = cfg_of(path)
    nfil = len(c.get("filament_colour") or [])
    if nfil < 2:
        print("филамент один — наклон на смены не влияет")
        return 0
    W0, F, lab, _ = _mesh_on_plate(path)
    LH = float(c["layer_height"])
    H1 = float(c.get("initial_layer_print_height", LH))
    base, _, _, nlay0 = _changes(W0, F, lab, nfil, LH, H1)
    print(f"{os.path.basename(path)}: без наклона {base} смен, слоёв {nlay0}\n")
    print("смен на слой — честная мерка: полное число смен падает уже от того,")
    print("что наклонённая деталь ниже, и слоёв в ней меньше.\n")
    print(f"{'ось':>4} {'угол':>6} {'слоёв':>7} {'смен':>7} {'к базе':>9} "
          f"{'смен/слой':>10} {'к базе':>9}")
    best = (0, 0, base)
    for axis, name in ((0, "X"), (1, "Y")):
        for a in range(lo, hi + 1, step):
            if a == 0:
                continue
            W = _tilt(W0, a if axis == 0 else 0, a if axis == 1 else 0)
            ch, _, _, nl = _changes(W, F, lab, nfil, LH, H1)
            print(f"{name:>4} {a:>5}° {nl:>7} {ch:>7} {100*(ch-base)/base:>+8.0f}% "
                  f"{ch/nl:>10.2f} {100*(ch/nl-base/nlay0)/(base/nlay0):>+8.0f}%")
            if ch < best[2]:
                best = (a if axis == 0 else 0, a if axis == 1 else 0, ch)
    print()
    print(f"теоретический минимум при {nfil} филаментах — {nfil-1} смен "
          f"(если бы каждый цвет занимал свой пояс по высоте)")
    if best[2] < base:
        print(f"лучший наклон: rx={best[0]}° ry={best[1]}° — {best[2]} смен "
              f"вместо {base} ({100*(best[2]-base)/base:+.0f} %)")
        print("проверить нарезкой и посмотреть, что стало с опорой: figcheck.py --rx/--ry")
    else:
        print("наклон смен не сокращает: любая поза, кроме исходной, только добавляет.")
        print("Это обычный исход — наклон растягивает каждую цветную зону по высоте,")
        print("и слоёв, где встречается несколько цветов, становится больше.")
    return 0


def colors(path):
    import numpy as np
    c = cfg_of(path)
    col = c.get("filament_colour") or []
    nfil = len(col)
    if nfil < 2:
        print("филамент один — считать нечего")
        return 0
    W, F, lab, scale = _mesh_on_plate(path)
    LH = float(c["layer_height"])
    H1 = float(c.get("initial_layer_print_height", LH))
    zt = W[F][:, :, 2]
    lo, hi = zt.min(1), zt.max(1)
    zmax = float(W[:, 2].max())
    nlay = int(math.ceil((zmax - H1) / LH)) + 1

    def layer_of(v):
        return np.clip(np.floor((v - H1) / LH).astype(int) + 1, 0, nlay - 1)

    l0, l1 = layer_of(lo), layer_of(hi)
    present = np.zeros((nlay, nfil + 1), bool)
    for f in range(1, nfil + 1):
        m = lab == f
        if not m.any():
            continue
        acc = np.zeros(nlay + 1, np.int64)
        np.add.at(acc, l0[m], 1)
        np.add.at(acc, l1[m] + 1, -1)
        present[:, f] = np.cumsum(acc[:-1]) > 0
    cnt = present[:, 1:].sum(1)
    changes = int(np.maximum(cnt - 1, 0).sum())

    fm = c.get("flush_volumes_matrix")
    avg = 0.0
    if fm:
        m = np.array([float(x) for x in fm], dtype=float).reshape(nfil, nfil)
        avg = float(m[m > 0].mean()) * float(one(c.get("flush_multiplier")) or 1)
    dens = hardware.density_default()

    print(f"{os.path.basename(path)}: {zmax:.1f} мм, слой {LH}, слоёв {nlay}, "
          f"филаментов {nfil}" + (f", масштаб из <build> ×{scale:.4g}" if abs(scale - 1) > 1e-6 else ""))
    print(f"\nсмен филамента не меньше {changes} "
          f"(слоёв с одним цветом {int((cnt <= 1).sum())}, с несколькими {int((cnt > 1).sum())})")
    if avg:
        g = changes * avg * dens / 1000
        print(f"промывка по матрице проекта: ~{changes * avg / 1000:.0f} см³ = ~{g:.0f} г "
              f"(средняя смена {avg:.0f} мм³)")
        print(f"   башня очистки: "
              f"{'включена' if str(c.get('enable_prime_tower')) == '1' else 'выключена'} — "
              f"на A1 она промывку не впитывает, а добавляет свой расход; мерить обеими нарезками")
    print(f"\n{'фил':>4} {'цвет':<9} {'граней':>9} {'слоёв':>6} {'доля слоёв':>11}  диапазон z, мм")
    rows = []
    for f in range(1, nfil + 1):
        m = present[:, f]
        if not m.any():
            continue
        zs = np.flatnonzero(m)
        zlo = max(H1 + (zs[0] - 1) * LH, 0.0)
        zhi = H1 + zs[-1] * LH
        rows.append((f, col[f - 1], int((lab == f).sum()), int(m.sum()), zlo, zhi))
        print(f"{f:>4} {col[f-1]:<9} {rows[-1][2]:>9} {rows[-1][3]:>6} "
              f"{100*rows[-1][3]/nlay:>10.0f}%  {zlo:6.1f} … {zhi:6.1f}")

    # the costliest filament: little area, many layers - it is paid for in changes
    tot = sum(r[2] for r in rows)
    worst = sorted(rows, key=lambda r: (r[3] / nlay) / max(r[2] / tot, 1e-9), reverse=True)[:2]
    print("\nчто дороже всего обходится:")
    for f, cc, faces, lays, zlo, zhi in worst:
        print(f"  филамент {f} ({cc}): {100*faces/tot:.1f}% площади, но живёт на "
              f"{lays} слоях ({100*lays/nlay:.0f}%) — за него платится "
              f"почти на каждом слое диапазона {zlo:.0f}…{zhi:.0f} мм")
    print("  убрать его, свести в узкий диапазон по высоте или закрасить "
          "соседним цветом — самый прямой способ сократить печать")

    # what dropping each filament would save: changes recomputed without it
    print("\nесли отказаться от одного цвета (слить его с соседним):")
    base = changes
    for f in range(1, nfil + 1):
        if not present[:, f].any():
            continue
        keep = [g for g in range(1, nfil + 1) if g != f]
        c2 = present[:, keep].sum(1)
        ch2 = int(np.maximum(c2 - 1, 0).sum())
        dg = (base - ch2) * avg * dens / 1000 if avg else 0
        print(f"  без филамента {f} ({col[f-1]}): смен {base} -> {ch2} "
              f"(-{100*(base-ch2)/max(base,1):.0f} %)" +
              (f", промывки меньше на ~{dg:.0f} г" if avg else ""))
    return 0


# ---------------------------------------------------------------- the fact

def gcode(path):
    import gcode_report
    head = {}
    with open(path, errors="ignore") as fh:
        for i, line in enumerate(fh):
            if i > 400:
                break
            m = re.match(r";\s*(total filament weight \[g\]|enable_prime_tower|"
                         r"filament_colour|layer_height)\s*:?=?\s*(.+)", line)
            if m:
                head.setdefault(m.group(1), m.group(2).strip())
    tools = 0
    with open(path, errors="ignore") as fh:
        for line in fh:
            if re.match(r"^T\d+\s*$", line):
                tools += 1
    grams, dens, diam, eta = gcode_report.parse(path)
    printed = sum(grams.values())
    total = sum(float(x) for x in head.get("total filament weight [g]", "0").split(","))
    purge = total - printed
    print(f"{os.path.basename(path)}")
    print(f"  смен филамента (команд T): {tools}")
    print(f"  пластика всего:  {total:6.1f} г")
    print(f"  из них в деталь: {printed:6.1f} г")
    print(f"  в промывку:      {purge:6.1f} г" +
          (f"  ({100*purge/total:.0f} % расхода)" if total else ""))
    print(f"  башня очистки: {'включена' if head.get('enable_prime_tower') == '1' else 'ВЫКЛЮЧЕНА'}")
    if eta:
        print(f"  {eta}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(2)
    cmd, path = sys.argv[1], sys.argv[2]
    if cmd == "audit":
        sys.exit(1 if audit(path) else 0)
    if cmd == "colors":
        sys.exit(colors(path))
    if cmd == "gcode":
        sys.exit(gcode(path))
    if cmd == "tilt":
        sys.exit(tilt_sweep(path))
    print(f"неизвестная команда {cmd}")
    sys.exit(2)
