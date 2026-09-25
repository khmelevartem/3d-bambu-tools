"""Инструменты правки объёма и сборки деталей.

solid_cut, pivot_joint, balljoint, graft, partedit, place3mf, set_extruder.

Что здесь проверяется по существу: деталь после реза складывается обратно
в исходное тело, штифт на миллиметр короче суммы глубин ниш, шар и гнездо
находятся голосованием по нормалям с правильным диаметром, а правка 3MF
частями и перестановка на столе не теряют ни одной покрашенной грани.
"""
import json, struct

from harness import case, run, num, close, contains, work, FIX, TOOLS

# --------------------------------------------------------------- вспомогательное

_READY = {}


def painted(name):
    """3MF с покраской из фикстуры <name>.stl. Собирается один раз за прогон."""
    if name not in _READY:
        d = work(f"общее_{name}")
        out = d / f"{name}.3mf"
        run([TOOLS / "make_multicolor_3mf.py", FIX / f"{name}.stl",
             FIX / f"{name}_zones.npy", "-o", out, "--no-project"],
            deps=("trimesh", "numpy"))
        _READY[name] = out
    return _READY[name]


def paint_of(threemf, d, tag="p"):
    """Отчёт paint.py parse: грани и филаменты покраски."""
    return run([TOOLS / "paint.py", "parse", threemf, d / f"{tag}.npz"],
               deps=("numpy", "scipy"))


def spec(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    return path


def scaled_ball(dst, k, z):
    """Копия ball.stl в масштабе k с центром (0,0,z) — сфера радиусом 15k.

    Нужна как резак: сферическая выемка в теле и есть гнездо шарнира,
    а собрать её без Blender нечем. Пишется прямо в двоичный STL, потому
    что сам тест идёт на системном python3, без numpy."""
    src = (FIX / "ball.stl").read_bytes()
    n = struct.unpack("<I", src[80:84])[0]
    out = [b"\0" * 80, struct.pack("<I", n)]
    for i in range(n):
        f = struct.unpack("<12f", src[84 + 50 * i: 84 + 50 * i + 48])
        v = []
        for j in range(3, 12, 3):
            v += [f[j] * k, f[j + 1] * k, (f[j + 2] - 15.0) * k + z]
        out.append(struct.pack("<12fH", *f[:3], *v, 0))
    dst.write_bytes(b"".join(out))
    return dst


def socketed_ball(d):
    """Шар радиусом 15 со сферическим гнездом Ø6 у макушки, центр (0,0,28)."""
    cutter = scaled_ball(d / "cutter_ball.stl", 0.2, 28.0)
    out = d / "socketed.stl"
    run([TOOLS / "solid_cut.py", "cut",
         spec(d / "socket.json",
              {"src": str(FIX / "ball.stl"),
               "parts": [{"name": "socketed", "out": str(out),
                          "ops": [{"kind": "mesh", "path": str(cutter),
                                   "op": "DIFFERENCE"}]}]})])
    return out


BJ = ("numpy", "scipy", "trimesh", "rtree")


# ============================================================ solid_cut.py

@case("solid_cut:рез плоскостью делит шар пополам", needs=("blender",),
      tools=("solid_cut.py",))
def _():
    d = work("solid_cut_половины")
    out = run([TOOLS / "solid_cut.py", "cut",
               spec(d / "s.json",
                    {"src": str(FIX / "ball.stl"),
                     "parts": [
                         {"name": "низ", "out": str(d / "низ.stl"),
                          "ops": [{"kind": "plane", "n": [0, 0, 1], "d": 15,
                                   "op": "INTERSECT"}]},
                         {"name": "верх", "out": str(d / "верх.stl"),
                          "ops": [{"kind": "plane", "n": [0, 0, -1], "d": -15,
                                   "op": "INTERSECT"}]}]})])
    close(num(out, r"низ\s+([\d.]+) мм³"), 7007.75, 0.5, "объём нижней половины")
    close(num(out, r"верх\s+([\d.]+) мм³"), 7007.75, 0.5, "объём верхней половины")
    close(num(out, r"сумма деталей ([\d.]+) мм³"), 14015.5, 0.5, "сумма половин")
    contains(out, "открытых рёбер 0", "non-manifold 0")
    assert (d / "низ.stl").exists() and (d / "верх.stl").exists()


@case("solid_cut:половины не пересекаются и складываются в исходное",
      needs=("blender",), tools=("solid_cut.py",))
def _():
    d = work("solid_cut_сверка")
    run([TOOLS / "solid_cut.py", "cut",
         spec(d / "s.json",
              {"src": str(FIX / "ball.stl"),
               "parts": [
                   {"name": "низ", "out": str(d / "низ.stl"),
                    "ops": [{"kind": "plane", "n": [0, 0, 1], "d": 15,
                             "op": "INTERSECT"}]},
                   {"name": "верх", "out": str(d / "верх.stl"),
                    "ops": [{"kind": "plane", "n": [0, 0, -1], "d": -15,
                             "op": "INTERSECT"}]}]})])
    out = run([TOOLS / "solid_cut.py", "check", FIX / "ball.stl",
               d / "низ.stl", d / "верх.stl"])
    close(num(out, r"исходное тело: ([\d.]+) мм³"), 14015.5, 0.5, "исходный объём")
    close(num(out, r"недостача (-?[\d.]+) "), 0.0, 0.5, "недостача")
    close(num(out, r"×\s+\S+\s+(-?[\d.]+) мм³"), 0.0, 1e-3, "пересечение пары")
    close(num(out, r"объединение деталей ([\d.]+) мм³"), 14015.5, 0.5, "объединение")
    close(num(out, r"выступает за исходное тело: (-?[\d.]+)"), 0.0, 1e-3,
          "выступ за исходное")
    contains(out, "ИТОГ: все пары чистые")


@case("solid_cut:наложение деталей поймано", needs=("blender",),
      tools=("solid_cut.py",))
def _():
    # Половины с перехлёстом в 6 мм: проверка обязана это увидеть, иначе
    # «детали не лезут друг в друга» ничего не стоит.
    d = work("solid_cut_перехлёст")
    run([TOOLS / "solid_cut.py", "cut",
         spec(d / "s.json",
              {"src": str(FIX / "ball.stl"),
               "parts": [
                   {"name": "низ", "out": str(d / "низ.stl"),
                    "ops": [{"kind": "plane", "n": [0, 0, 1], "d": 18,
                             "op": "INTERSECT"}]},
                   {"name": "верх", "out": str(d / "верх.stl"),
                    "ops": [{"kind": "plane", "n": [0, 0, -1], "d": -12,
                             "op": "INTERSECT"}]}]})])
    out = run([TOOLS / "solid_cut.py", "check", FIX / "ball.stl",
               d / "низ.stl", d / "верх.stl"])
    contains(out, "ДЕТАЛИ ЛЕЗУТ ДРУГ В ДРУГА", "ИТОГ: 1 пар пересекаются")
    close(num(out, r"×\s+\S+\s+([\d.]+) мм³"), 4160.2, 1.0, "объём перехлёста")
    # объединение при этом всё равно равно исходному телу
    close(num(out, r"объединение деталей ([\d.]+) мм³"), 14015.5, 0.5, "объединение")


@case("solid_cut:цилиндрическая ниша убирает свой объём", needs=("blender",),
      tools=("solid_cut.py",))
def _():
    d = work("solid_cut_ниша")
    out = run([TOOLS / "solid_cut.py", "cut",
               spec(d / "s.json",
                    {"src": str(FIX / "ball.stl"),
                     "parts": [{"name": "низ", "out": str(d / "низ.stl"), "ops": [
                         {"kind": "plane", "n": [0, 0, 1], "d": 15, "op": "INTERSECT"},
                         {"kind": "cyl", "axis": [0, 0, 1], "at": [0, 0, 15],
                          "t0": -5.0, "t1": 0.5, "r": 3.0, "op": "DIFFERENCE"}]}]})])
    # π·3²·5 = 141.4 мм³, гранёный резец забирает чуть меньше
    close(num(out, r"низ\s+([\d.]+) мм³"), 7007.75 - 141.4, 1.0, "объём с нишей")
    contains(out, "открытых рёбер 0")


@case("solid_cut:граница цвета ложится на плоскость", tools=("solid_cut.py",))
def _():
    d = work("solid_cut_граница")
    run([TOOLS / "paint.py", "explode", painted("ball"), d / "e.npz"],
        deps=("numpy", "scipy"))
    out = run([TOOLS / "solid_cut.py", "fit", d / "e.npz"], deps=("numpy", "scipy"))
    contains(out, "граница филаментов 2|3", "режется плоскостью")
    # зоны покраски шара разделены по z = 15, значит и плоскость там же
    close(num(out, r"n·x =\s+([\d.]+)"), 15.0, 0.5, "смещение плоскости реза")


@case("solid_cut:вставка под зону и резак кармана", tools=("solid_cut.py",))
def _():
    d = work("solid_cut_вставка")
    run([TOOLS / "paint.py", "explode", painted("ball"), d / "e.npz"],
        deps=("numpy", "scipy"))
    out = run([TOOLS / "solid_cut.py", "inlay", d / "e.npz", "--filament", "3",
               "--assembly", "0,0,1", "--out", d / "z"],
              deps=("numpy", "scipy", "shapely", "mapbox_earcut", "scikit-image"))
    contains(out, "филамент 3, кусков 1")
    # площадь куска должна совпасть с площадью филамента 3 у paint.py
    close(num(out, r"кусок\s+([\d.]+) мм²"), 1369.6, 1.0, "площадь зоны вставки")
    # зона — нижняя полусфера, вдоль нормали сборки это ровно радиус
    close(num(out, r"размах вдоль нормали ([\d.]+) мм"), 15.0, 0.1, "размах зоны")
    contains(out, "зазор кармана 0.20 вбок и 0.05 по глубине")
    assert (d / "z_1.stl").exists(), "не записана вставка"
    assert (d / "z_1_pocket.stl").exists(), "не записан резак кармана"


@case("solid_cut:без подкоманды печатает справку", tools=("solid_cut.py",))
def _():
    out = run([TOOLS / "solid_cut.py"], expect=1)
    contains(out, "Cut a body with proper bodies")


# =========================================================== pivot_joint.py

@case("pivot_joint:рез поперёк оси даёт две половины", needs=("blender",),
      tools=("pivot_joint.py",))
def _():
    d = work("pivot_рез")
    out = run([TOOLS / "pivot_joint.py",
               spec(d / "j.json",
                    {"out": str(d / "parts"),
                     "parts": {"ball": str(FIX / "ball.stl")},
                     "cuts": [{"part": "ball", "p": [0, 0, 15], "d": [0, 0, 1],
                               "keep": "низ", "away": "верх"}]})])
    close(num(out, r"загружено «ball»: граней \d+, объём ([\d.]+) см3"),
          14.02, 0.02, "объём исходного шара")
    a = num(out, r"«низ»: граней \d+, объём ([\d.]+) см3")
    b = num(out, r"«верх»: граней \d+, объём ([\d.]+) см3")
    close(a + b, 14.02, 0.03, "сумма половин")
    contains(out, "открытых рёбер 0")
    assert (d / "parts" / "низ.stl").exists(), "не записана нижняя половина"
    assert (d / "parts" / "верх.stl").exists(), "не записана верхняя половина"


@case("pivot_joint:штифт на миллиметр короче суммы глубин", needs=("blender",),
      tools=("pivot_joint.py",))
def _():
    d = work("pivot_штифт")
    out = run([TOOLS / "pivot_joint.py",
               spec(d / "j.json",
                    {"out": str(d / "parts"),
                     "parts": {"ball": str(FIX / "ball.stl")},
                     "cuts": [{"part": "ball", "p": [0, 0, 15], "d": [0, 0, 1],
                               "keep": "низ", "away": "верх"}],
                     "joints": [{"parts": ["низ", "верх"], "alts": ["низ", "верх"],
                                 "p": [0, 0, 15], "d": [0, 0, 1], "size": 4.0,
                                 "depth": [5.0, 5.0], "shape": "rect",
                                 "fit": [0.15, 0.3], "n": 2}]})])
    # 5.0 + 5.0 - 1.0 = 9.0; ниши шире штифта на fit с каждой стороны
    contains(out, "штифт 4.0×9.0", "ниши 4.15 и 4.30 на 5.0 и 5.0 мм")
    # shape: rect — сечение квадратное, тег ниши □, а не Ø
    contains(out, "□4.15 в «низ»", "□4.3 в «верх»", "□4.0×9.0")
    assert "Ø" not in out, "квадратная ниша отмечена круглым тегом"
    close(num(out, r"дно\s+(\d+) %"), 100, 1, "дно ниши")
    close(num(out, r"стенка\s+(\d+) %"), 100, 1, "стенка ниши")
    contains(out, "(ок)")
    for f in ("низ.stl", "верх.stl", "штифты.stl"):
        assert (d / "parts" / f).exists(), f"не записан {f}"
    # половины с нишами складываются в исходное тело минус объём ниш
    a = num(out, r"низ\.stl: граней \d+, объём ([\d.]+) см3")
    b = num(out, r"верх\.stl: граней \d+, объём ([\d.]+) см3")
    ниши = (num(out, r"□4\.15 в «низ» на \[-1\.0, 5\.0\]: убрано (\d+) мм3")
            + num(out, r"□4\.3 в «верх» на \[-5\.0, 1\.0\]: убрано (\d+) мм3"))
    close(a + b + ниши / 1000.0, 14.02, 0.03, "половины + ниши против исходного")


@case("pivot_joint:круглый штифт и разные глубины ниш", needs=("blender",),
      tools=("pivot_joint.py",))
def _():
    d = work("pivot_круглый")
    out = run([TOOLS / "pivot_joint.py",
               spec(d / "j.json",
                    {"out": str(d / "parts"),
                     "parts": {"ball": str(FIX / "ball.stl")},
                     "cuts": [{"part": "ball", "p": [0, 0, 15], "d": [0, 0, 1],
                               "keep": "низ", "away": "верх"}],
                     "joints": [{"parts": ["низ", "верх"], "alts": ["низ", "верх"],
                                 "p": [0, 0, 15], "d": [0, 0, 1], "size": 5.0,
                                 "depth": [6.0, 4.0], "shape": "round",
                                 "fit": [0.15, 0.3], "n": 3}]})])
    # 6.0 + 4.0 - 1.0 = 9.0 — правило то же и при несимметричных глубинах
    contains(out, "штифт 5.0×9.0", "ниши 5.15 и 5.30 на 6.0 и 4.0 мм",
             "Ø5.15 в «низ»", "Ø5.0×9.0", "штифтов 3")
    assert "□" not in out, "круглая ниша отмечена квадратным тегом"
    assert (d / "parts" / "штифты.stl").exists()


@case("pivot_joint:ниша мимо тела отменяется", needs=("blender",),
      tools=("pivot_joint.py",))
def _():
    # Ось стыка уведена за край шара: обе ниши промахиваются, и стык
    # обязан быть отменён целиком, а не сделан наполовину.
    d = work("pivot_мимо")
    out = run([TOOLS / "pivot_joint.py",
               spec(d / "j.json",
                    {"out": str(d / "parts"),
                     "parts": {"ball": str(FIX / "ball.stl")},
                     "cuts": [{"part": "ball", "p": [0, 0, 15], "d": [0, 0, 1],
                               "keep": "низ", "away": "верх"}],
                     "joints": [{"parts": ["низ", "верх"], "alts": ["низ", "верх"],
                                 "p": [40, 0, 15], "d": [0, 0, 1], "size": 4.0,
                                 "depth": [5.0, 5.0], "shape": "rect"}]})])
    contains(out, "ОТМЕНЁН")
    assert "штифты.stl" not in out, "штифт выписан под отменённый стык"


@case("pivot_joint:без задания печатает справку", tools=("pivot_joint.py",))
def _():
    out = run([TOOLS / "pivot_joint.py"], expect=1)
    contains(out, "Pivot joints on cylinders between printed parts")


# ============================================================= balljoint.py

@case("balljoint:на чистой сфере шарниров не находит", tools=("balljoint.py",))
def _():
    # Радиус шара 15 мм, а ищется 1.2..4.0 — правильный ответ «ничего нет»,
    # и это должен быть пустой отчёт, а не падение.
    out = run([TOOLS / "balljoint.py", "find", painted("ball")], deps=BJ)
    contains(out, "граней 1,280")
    assert "шар " not in out and "гнёзд" not in out, \
        f"найден несуществующий шарнир\n{out}"


@case("balljoint:шар находится голосованием по нормалям", tools=("balljoint.py",))
def _():
    out = run([TOOLS / "balljoint.py", "find", painted("ball"),
               "--rmin", "14", "--rmax", "16"], deps=BJ)
    contains(out, "шар")
    close(num(out, r"Ø([\d.]+)"), 29.88, 0.2, "диаметр шара")
    close(num(out, r"центр \(\s*-?[\d.]+,\s*-?[\d.]+,\s*([\d.]+)\)"), 15.0, 0.05,
          "высота центра шара")
    close(num(out, r"покрытие\s+(\d+)%"), 100, 1, "покрытие")
    assert num(out, r"невязка\s+([\d.]+) мкм") < 50, "невязка МНК велика"


@case("balljoint:гнездо в теле находится", needs=("blender",),
      tools=("balljoint.py",))
def _():
    d = work("balljoint_гнездо")
    out = run([TOOLS / "balljoint.py", "find", socketed_ball(d),
               "--rmin", "2.0", "--rmax", "4.0"], deps=BJ)
    contains(out, "гнёзд")
    close(num(out, r"Ø([\d.]+)"), 5.98, 0.1, "диаметр гнезда")
    close(num(out, r"центр \(\s*-?[\d.]+,\s*-?[\d.]+,\s*([\d.]+)\)"), 28.0, 0.05,
          "высота центра гнезда")
    contains(out, "ось ( 0.000, 0.000, 1.000)")


@case("balljoint:профиль гнезда лучами и резак по нему", needs=("blender",),
      tools=("balljoint.py",))
def _():
    d = work("balljoint_профиль")
    out = run([TOOLS / "balljoint.py", "profile", socketed_ball(d),
               "--socket=0,0,28", "-o", d / "prof.json"], deps=BJ)
    contains(out, "гнездо уточнено")
    close(num(out, r"дно: сфера R=([\d.]+)"), 3.0, 0.05, "радиус дна гнезда")
    # на экваторе гнезда луч обязан упереться в стенку радиусом 3
    close(num(out, r"\n\s+-0\.40\s+([\d.]+)"), 2.97, 0.05, "радиус на глубине -0.4")
    out = run([TOOLS / "balljoint.py", "cutter", d / "prof.json",
               "-o", d / "cutter.stl"], deps=BJ)
    contains(out, "герметичен True")
    close(num(out, r"макс r ([\d.]+)"), 2.99, 0.05, "максимальный радиус резака")
    assert (d / "cutter.stl").exists()


@case("balljoint:fit без --torso отказывается", tools=("balljoint.py",))
def _():
    # Сборку фигуры честной синтетикой не проверить: нужен чужой проект
    # с торсом и конечностями. Здесь только внятность отказа.
    out = run([TOOLS / "balljoint.py", "fit", painted("ball")], deps=BJ, expect=2)
    contains(out, "--torso")


# ================================================================= graft.py

@case("graft:сечение детали в точке", tools=("graft.py",))
def _():
    # Рука фигурки — цилиндр Ø5.2 мм вдоль X на высоте 52 мм.
    out = run([TOOLS / "graft.py", "section", painted("figurine"),
               "--at", "9,0,52", "--dir", "1,0,0", "--max", "6"],
              deps=("numpy", "scipy"))
    close(num(out, r"ширина ([\d.]+) ×"), 5.2, 0.05, "ширина сечения руки")
    close(num(out, r"толщина ([\d.]+) ед"), 5.2, 0.05, "толщина сечения руки")
    contains(out, "нормаль ленты")


@case("graft:лента дописана в конец, покраска цела", tools=("graft.py",))
def _():
    d = work("graft_лента")
    src = painted("figurine")
    было = paint_of(src, d, "до")
    out = run([TOOLS / "graft.py", "ribbon", src, d / "rib.npz",
               "--at", "9,0,52", "--dir", "1,0,0", "--up", "0,1,0",
               "--to", "0,0,62", "--to-dir=-1,0,0", "--max", "6"],
              deps=("numpy", "scipy"))
    close(num(out, r"профиль ([\d.]+) ×"), 5.2, 0.05, "профиль ленты")
    assert num(out, r"объём ([\d.]+) мм³") > 100, "лента вышла пустой"
    out = run([TOOLS / "graft.py", "put", src, d / "rib.npz", d / "new.3mf",
               "--filament", "2"], deps=("numpy", "scipy"))
    contains(out, "граней 1168 ->", "новые грани филамент 2 (код 8)")
    стало = paint_of(d / "new.3mf", d, "после")
    # старые индексы граней не сдвинулись: филаменты 3 и 4 не изменились,
    # а весь прирост ушёл во второй филамент — тот, которым красили ленту
    for f in (3, 4):
        close(num(стало, rf"филамент {f}: (\d+) граней"),
              num(было, rf"филамент {f}: (\d+) граней"), 0, f"грани филамента {f}")
    прирост = (num(стало, r"филамент 2: (\d+) граней")
               - num(было, r"филамент 2: (\d+) граней"))
    close(прирост, num(стало, r"граней (\d+),") - num(было, r"граней (\d+),"), 0,
          "прирост граней ушёл не только во второй филамент")


@case("graft:в пустой плоскости честно отказывается", tools=("graft.py",))
def _():
    out = run([TOOLS / "graft.py", "section", painted("figurine"),
               "--at", "9,0,52", "--dir", "1,0,0", "--max", "1"],
              deps=("numpy", "scipy"), expect=1)
    contains(out, "ничего мельче")


# ============================================================== partedit.py

@case("partedit:список объектов и частей", tools=("partedit.py",))
def _():
    out = run([TOOLS / "partedit.py", "list", painted("ball")], deps=("numpy",))
    contains(out, "объект 2: ball.stl (филамент 2)", "normal_part", "тарелка 1")


@case("partedit:негативная часть и сдвиг не теряют покраску",
      tools=("partedit.py",))
def _():
    d = work("partedit_части")
    src = painted("ball")
    было = paint_of(src, d, "до")
    out = run([TOOLS / "partedit.py", "apply",
               spec(d / "job.json",
                    {"src": str(src), "dst": str(d / "new.3mf"), "ops": [
                        {"op": "part", "object": "ball.stl", "name": "Канал",
                         "subtype": "negative_part",
                         "mesh": {"kind": "cylinder", "c": [0, 0, 15],
                                  "axis": [0, 0, 1], "r": 3.0,
                                  "t0": -20, "t1": 20}},
                        {"op": "shift", "name": "ball.stl", "d": [0, 0, -1.2]}]})],
              deps=("numpy",))
    # π·3²·40 = 1130.97 мм³
    close(num(out, r"объём ([\d.]+) мм³"), 1130.2, 2.0, "объём негативной части")
    contains(out, "ball.stl сдвинут на [0, 0, -1.2]")
    out = run([TOOLS / "partedit.py", "list", d / "new.3mf"], deps=("numpy",))
    contains(out, "negative_part   Канал")
    стало = paint_of(d / "new.3mf", d, "после")
    close(num(стало, r"граней (\d+),"), 1280, 0, "граней после правки частями")
    for f in (2, 3):
        close(num(стало, rf"филамент {f}: (\d+) граней"),
              num(было, rf"филамент {f}: (\d+) граней"), 0, f"грани филамента {f}")


@case("partedit:без подкоманды отказывается", tools=("partedit.py",))
def _():
    out = run([TOOLS / "partedit.py"], deps=("numpy",), expect=2)
    contains(out, "usage")


# ============================================================== place3mf.py

@case("place3mf:объект встаёт на середину стола", tools=("place3mf.py",))
def _():
    d = work("place3mf_середина")
    out = run([TOOLS / "place3mf.py", painted("ball"), d / "new.3mf",
               "--bed", "100", "100"], deps=("numpy",))
    contains(out, "габарит [30. 30. 30.] мм", "-> [50. 50. -0.]", "записано")
    close(num(out, r"3dmodel\.model: поправлено матриц (\d+)"), 2, 0,
          "поправлено матриц в 3dmodel.model")
    assert (d / "new.3mf").exists()


@case("place3mf:перестановка не теряет покраску", tools=("place3mf.py",))
def _():
    d = work("place3mf_покраска")
    src = painted("ball")
    было = paint_of(src, d, "до")
    run([TOOLS / "place3mf.py", src, d / "new.3mf", "--bed", "180", "180"],
        deps=("numpy",))
    стало = paint_of(d / "new.3mf", d, "после")
    close(num(стало, r"граней (\d+),"), 1280, 0, "граней после перестановки")
    for f in (2, 3):
        close(num(стало, rf"филамент {f}: (\d+) граней"),
              num(было, rf"филамент {f}: (\d+) граней"), 0, f"грани филамента {f}")


@case("place3mf:без аргументов отказывается", tools=("place3mf.py",))
def _():
    out = run([TOOLS / "place3mf.py"], deps=("numpy",), expect=2)
    contains(out, "usage")


# =========================================================== set_extruder.py

@case("set_extruder:филамент берётся из имени детали", tools=("set_extruder.py",))
def _():
    d = work("set_extruder_имя")
    # paint_split.py называет детали filamentN.stl — номер в имени и решает
    stl = d / "filament4.stl"
    stl.write_bytes((FIX / "ball.stl").read_bytes())
    src = d / "p.3mf"
    run([TOOLS / "make_multicolor_3mf.py", stl, FIX / "ball_zones.npy",
         "-o", src, "--no-project"], deps=("trimesh", "numpy"))
    было = paint_of(src, d, "до")
    out = run([TOOLS / "set_extruder.py", src, d / "new.3mf"])
    contains(out, "filament4.stl", "→ филамент 4", "1 объектов из 1")
    out = run([TOOLS / "partedit.py", "list", d / "new.3mf"], deps=("numpy",))
    contains(out, "(филамент 4)")
    стало = paint_of(d / "new.3mf", d, "после")
    for f in (2, 3):
        close(num(стало, rf"филамент {f}: (\d+) граней"),
              num(было, rf"филамент {f}: (\d+) граней"), 0, f"грани филамента {f}")


@case("set_extruder:имя без номера остаётся неразобранным",
      tools=("set_extruder.py",))
def _():
    d = work("set_extruder_безномера")
    out = run([TOOLS / "set_extruder.py", painted("ball"), d / "new.3mf"])
    contains(out, "НЕ РАЗОБРАЛ", "0 объектов из 1",
             "без номера в имени: [2] — задать ключом --set ID=N")


@case("set_extruder:--set назначает филамент вручную", tools=("set_extruder.py",))
def _():
    d = work("set_extruder_вручную")
    out = run([TOOLS / "set_extruder.py", painted("ball"), d / "new.3mf",
               "--set", "2=5"])
    contains(out, "→ филамент 5", "1 объектов из 1")
    out = run([TOOLS / "partedit.py", "list", d / "new.3mf"], deps=("numpy",))
    contains(out, "объект 2: ball.stl (филамент 5)")


@case("set_extruder:без аргументов отказывается", tools=("set_extruder.py",))
def _():
    out = run([TOOLS / "set_extruder.py"], expect=2)
    contains(out, "usage")


@case("solid_cut:OBJ приходит без поворота под Y-up", needs=("blender",),
      tools=("solid_cut.py",))
def _():
    # Импортёр OBJ у Blender по умолчанию считает, что вверху Y, а импортёр STL
    # — что Z. В одной сцене это разворачивает одну сетку относительно другой и
    # относительно плоскостей-резаков, которые заданы в мировых координатах.
    # Проверяем на несимметричном теле: рез по Z обязан остаться резом по Z.
    d = work("solid_cut_obj_оси")
    V, F = [], []
    import numpy as np
    box = np.array([[0, 0, 0], [8, 0, 0], [8, 4, 0], [0, 4, 0],
                    [0, 0, 20], [8, 0, 20], [8, 4, 20], [0, 4, 20]], float)
    faces = [[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7], [0, 1, 5], [0, 5, 4],
             [1, 2, 6], [1, 6, 5], [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7]]
    obj = d / "brick.obj"
    obj.write_text("".join(f"v {p[0]:.6f} {p[1]:.6f} {p[2]:.6f}\n" for p in box)
                   + "".join(f"f {a+1} {b+1} {c+1}\n" for a, b, c in faces))
    out = run([TOOLS / "solid_cut.py", "cut",
               spec(d / "s.json",
                    {"src": str(obj),
                     "parts": [{"name": "низ", "out": str(d / "низ.stl"),
                                "ops": [{"kind": "plane", "n": [0, 0, 1],
                                         "d": 5, "op": "INTERSECT"}]}]})])
    # брусок 8x4x20; рез по Z на высоте 5 оставляет 8*4*5 = 160 мм³.
    # Если бы OBJ развернулся, тот же рез отсёк бы по другой оси: 8*4*20=640
    # (плоскость прошла бы мимо) или 8*20*4 — любое, но не 160.
    close(num(out, r"низ\s+([\d.]+) мм³"), 160.0, 0.5, "объём отрезанного низа")
    contains(out, "открытых рёбер 0", "non-manifold 0")


@case("partedit:новый объект не занимает уже занятый id", tools=("partedit.py",))
def _():
    # Идентификаторы выдавались от 100 всегда. Проект, который уже правили,
    # держит id в сотнях — новый объект получал занятый номер, и слайсер
    # показывал на его месте чужую деталь, ничего не сообщая.
    d = work("partedit_ид")
    src = painted("ball")
    job = d / "j1.json"
    job.write_text(json.dumps(
        {"src": str(src), "dst": str(d / "one.3mf"),
         "ops": [{"op": "object", "name": "Первый", "extruder": 3, "plate": 1,
                  "pos": [128, 128, 0],
                  "mesh": {"kind": "box", "lo": [0, 0, 0], "hi": [4, 4, 4]}}]},
        ensure_ascii=False))
    run([TOOLS / "partedit.py", "apply", job], deps=("numpy",))
    job2 = d / "j2.json"
    job2.write_text(json.dumps(
        {"src": str(d / "one.3mf"), "dst": str(d / "two.3mf"),
         "ops": [{"op": "object", "name": "Второй", "extruder": 4, "plate": 1,
                  "pos": [160, 128, 0],
                  "mesh": {"kind": "box", "lo": [0, 0, 0], "hi": [6, 6, 6]}}]},
        ensure_ascii=False))
    run([TOOLS / "partedit.py", "apply", job2], deps=("numpy",))
    out = run([TOOLS / "partedit.py", "list", d / "two.3mf"], deps=("numpy",))
    contains(out, "Первый (филамент 3)", "Второй (филамент 4)")
    import re as _re, zipfile as _zip, collections as _c
    z = _zip.ZipFile(d / "two.3mf")
    for entry in ("3D/3dmodel.model", "Metadata/model_settings.config"):
        ids = _re.findall(r'<object id="(\d+)"', z.read(entry).decode())
        dup = [k for k, v in _c.Counter(ids).items() if v > 1]
        assert not dup, f"{entry}: повторённые id объектов {dup}"
