"""Paint tools: building a painted project, reading it, editing and splitting it.

Everything rests on two fixtures: the ball with two zones (656 + 624 faces)
and the figurine with three. The numbers below are the tools' own output on
those fixtures, so a change in any of them is a change in behaviour.
"""
import json
import re
import zipfile

from harness import case, run, num, close, contains, work, FIX, TOOLS

PAINT = ("numpy", "scipy", "PyMaxflow")     # paint.py
SPLIT = ("numpy", "scipy")                  # paint_despeckle.py, paint_split.py
MAKE = ("trimesh", "numpy")                 # make_multicolor_3mf.py
VIEW = ("numpy",)                           # paintview.py


def ball_3mf(d, name="ball.3mf", extra=()):
    """A painted project without settings: filament 2 below, filament 3 above."""
    out = d / name
    run([TOOLS / "make_multicolor_3mf.py", FIX / "ball.stl", FIX / "ball_zones.npy",
         "-o", out, "--no-project", *extra], deps=MAKE)
    return out


def ball_project(d, name="ball_p.3mf", extra=()):
    """The same project WITH Metadata/project_settings.config. Needs Bambu profiles."""
    out = d / name
    run([TOOLS / "make_multicolor_3mf.py", FIX / "ball.stl", FIX / "ball_zones.npy",
         "-o", out, *extra], deps=MAKE)
    return out


def ball_npz(d, name="p.npz"):
    out = d / name
    run([TOOLS / "paint.py", "parse", ball_3mf(d), out], deps=PAINT)
    return out


def fig_npz(d, name="fp.npz"):
    mf = d / "fig.3mf"
    run([TOOLS / "make_multicolor_3mf.py", FIX / "figurine.stl", FIX / "figurine_zones.npy",
         "-o", mf, "--no-project"], deps=MAKE)
    out = d / name
    run([TOOLS / "paint.py", "parse", mf, out], deps=PAINT)
    return out


def config(path):
    return json.loads(zipfile.ZipFile(path).read("Metadata/project_settings.config"))


def rewrite(src, dst, edit):
    """Copy a 3MF, passing every entry through `edit(name, bytes) -> bytes`."""
    zin = zipfile.ZipFile(src)
    with zipfile.ZipFile(dst, "w") as z:
        for it in zin.infolist():
            z.writestr(it.filename, edit(it.filename, zin.read(it.filename)))
    return dst


# --------------------------------------------------------------- make_multicolor_3mf.py
@case("make_multicolor:красит шар в два филамента", tools=("make_multicolor_3mf.py",))
def _():
    d = work("make_multicolor_ball")
    out = run([TOOLS / "make_multicolor_3mf.py", FIX / "ball.stl", FIX / "ball_zones.npy",
               "-o", d / "ball.3mf", "--no-project"], deps=MAKE)
    contains(out, "1,280 граней, зоны по граням",
             "филамент 2 (#8E9089)", "paint_color='8'", "<- extruder объекта",
             "филамент 3 (#7C4B27)", "paint_color='0C'")
    close(num(out, r"филамент 2 \(#8E9089\):\s+([\d.]+)%"), 51.2, 0.05, "доля филамента 2")
    close(num(out, r"филамент 3 \(#7C4B27\):\s+([\d.]+)%"), 48.8, 0.05, "доля филамента 3")
    assert (d / "ball.3mf").stat().st_size > 1000, "проект пустой"
    names = zipfile.ZipFile(d / "ball.3mf").namelist()
    assert "3D/Objects/object_1.model" in names, names


@case("make_multicolor:номера зон считает с нуля, --one-based не сдвигает",
      tools=("make_multicolor_3mf.py",))
def _():
    d = work("make_multicolor_one_based")
    out = run([TOOLS / "make_multicolor_3mf.py", FIX / "ball.stl", FIX / "ball_zones.npy",
               "-o", d / "b1.3mf", "--no-project", "--one-based"], deps=MAKE)
    contains(out, "филамент 1 (#F4EE2A):  51.2%", "paint_color='4'",
             "филамент 2 (#8E9089):  48.8%")
    assert "филамент 3" not in out, "с --one-based третий слот не нужен\n" + out


@case("make_multicolor:--colors ставит цвета слотов", tools=("make_multicolor_3mf.py",))
def _():
    d = work("make_multicolor_colors")
    out = run([TOOLS / "make_multicolor_3mf.py", FIX / "ball.stl", FIX / "ball_zones.npy",
               "-o", d / "c.3mf", "--no-project",
               "--colors", "#111111,#F4EE2A,#00FF00"], deps=MAKE)
    contains(out, "филамент 1 (#111111)", "филамент 2 (#F4EE2A)", "филамент 3 (#00FF00)")


@case("make_multicolor:--no-project не кладёт настроек, без него кладёт",
      needs=("bambu",), tools=("make_multicolor_3mf.py",))
def _():
    d = work("make_multicolor_project")
    bare = ball_3mf(d, "bare.3mf")
    assert "Metadata/project_settings.config" not in zipfile.ZipFile(bare).namelist()
    cfg = config(ball_project(d))
    assert cfg["filament_colour"] == ["#F4EE2A", "#8E9089", "#7C4B27"], cfg["filament_colour"]


# ------------------------------------------------------------------------------ paint.py
@case("paint:parse читает сетку и покраску шара", tools=("paint.py",))
def _():
    d = work("paint_parse")
    out = run([TOOLS / "paint.py", "parse", ball_3mf(d), d / "p.npz"], deps=PAINT)
    contains(out, "3D/Objects/object_1.model")
    close(num(out, r"вершин (\d+)"), 642, 0, "вершин")
    close(num(out, r"граней (\d+)"), 1280, 0, "граней")
    close(num(out, r"филамент 2: (\d+) граней"), 656, 0, "граней филамента 2")
    close(num(out, r"филамент 3: (\d+) граней"), 624, 0, "граней филамента 3")
    close(num(out, r"филамент 2: \d+ граней, ([\d.]+) мм2"), 1444.3, 0.1, "площадь филамента 2")
    close(num(out, r"филамент 3: \d+ граней, ([\d.]+) мм2"), 1369.6, 0.1, "площадь филамента 3")
    assert (d / "p.npz").exists()


@case("paint:stats меряет границу цвета по рёбрам", tools=("paint.py",))
def _():
    d = work("paint_stats")
    out = run([TOOLS / "paint.py", "stats", ball_npz(d)], deps=PAINT)
    close(num(out, r"граница цвета ([\d.]+) мм"), 105.7, 0.1, "длина границы")
    close(num(out, r"граница цвета [\d.]+ мм по (\d+) рёбрам"), 48, 0, "рёбер на границе")
    close(num(out, r"поверхность (\d+) мм2"), 2814, 1, "площадь поверхности")
    close(num(out, r"филамент 2:\s+\d+ граней,\s+([\d.]+) мм2"), 1444.3, 0.1, "площадь 2")


@case("paint:stats на ровной покраске не находит крапа", tools=("paint.py",))
def _():
    d = work("paint_stats_clean")
    out = run([TOOLS / "paint.py", "stats", ball_npz(d)], deps=PAINT)
    contains(out, "одноцветных кусков 2",
             "мельче 0.5 мм2: 0 (всего 0.00 мм2)",
             "одиночных выбивающихся граней: 0 (0.00 мм2)")


@case("paint:smooth не двигает и без того ровную границу", tools=("paint.py",))
def _():
    d = work("paint_smooth")
    out = run([TOOLS / "paint.py", "smooth", ball_npz(d), d / "p2.npz",
               "--band", "4", "--lam", "1.0"], deps=PAINT)
    close(num(out, r"старт: граница ([\d.]+) мм"), 105.7, 0.1, "граница до")
    close(num(out, r"перекрашено целых (\d+)"), 0, 0, "перекрашено граней")
    contains(out, "филамент 2:   1444.3 ->   1444.3 мм2")


@case("paint:write кладёт покраску обратно без потерь", tools=("paint.py",))
def _():
    d = work("paint_write")
    src, npz = ball_3mf(d), d / "p.npz"
    run([TOOLS / "paint.py", "parse", src, npz], deps=PAINT)
    out = run([TOOLS / "paint.py", "write", src, npz, d / "back.3mf"], deps=PAINT)
    contains(out, "без изменений 1280, перекрашено 0")
    again = run([TOOLS / "paint.py", "parse", d / "back.3mf", d / "p3.npz"], deps=PAINT)
    close(num(again, r"филамент 2: (\d+) граней"), 656, 0, "граней филамента 2 после записи")
    close(num(again, r"филамент 3: (\d+) граней"), 624, 0, "граней филамента 3 после записи")


@case("paint:explode на одноцветных гранях ничего не дробит", tools=("paint.py",))
def _():
    d = work("paint_explode")
    out = run([TOOLS / "paint.py", "explode", ball_3mf(d), d / "e.npz"], deps=PAINT)
    contains(out, "дроблёных граней 0 -> подтреугольников 1280, вершин 642")
    close(num(out, r"поверхность ([\d.]+) мм2"), 2814.0, 0.5, "площадь после разбора")
    close(num(out, r"открытых рёбер (\d+)"), 0, 0, "открытых рёбер")


@case("paint:filament на файле без настроек объясняет, а не падает", tools=("paint.py",))
def _():
    d = work("paint_filament_bare")
    out = run([TOOLS / "paint.py", "filament", ball_3mf(d), d / "plus.3mf", "#C12E1F"],
              deps=PAINT, expect=1)
    assert "Traceback (most recent call last)" not in out, "трейсбек вместо сообщения\n" + out
    contains(out, "нет Metadata/project_settings.config",
             "--no-project", "retune_project.py")
    assert not (d / "plus.3mf").exists(), "недоделанный файл всё-таки написан"


@case("paint:filament доводит число филаментов до N+1", needs=("bambu",), tools=("paint.py",))
def _():
    d = work("paint_filament_grow")
    src = ball_project(d)
    out = run([TOOLS / "paint.py", "filament", src, d / "four.3mf", "#C12E1F"], deps=PAINT)
    contains(out, "филаментов 3 -> 4", "#C12E1F")
    was, now = config(src), config(d / "four.3mf")
    assert now["filament_colour"] == ["#F4EE2A", "#8E9089", "#7C4B27", "#C12E1F"], \
        now["filament_colour"]
    n = len(was["filament_colour"])
    # the new slot is copied off the second one, colour excepted
    assert now["filament_settings_id"][-1] == was["filament_settings_id"][1], \
        now["filament_settings_id"]
    # every per-filament list — one entry per slot — has to gain an entry
    bad = [(k, len(now.get(k, []))) for k, v in was.items()
           if isinstance(v, list) and len(v) == n and len(now.get(k, [])) != n + 1]
    assert not bad, f"списки не доросли до {n + 1}: {bad}"


@case("paint:filament не выдумывает матрицу промывки", needs=("bambu",), tools=("paint.py",))
def _():
    d = work("paint_filament_nomatrix")
    src = ball_project(d)
    assert "flush_volumes_matrix" not in config(src), "фикстура уже с матрицей"
    out = run([TOOLS / "paint.py", "filament", src, d / "four.3mf", "#C12E1F"], deps=PAINT)
    contains(out, "матрицы промывки в проекте нет")
    assert "flush_volumes_matrix" not in config(d / "four.3mf"), \
        "матрица появилась там, где её не было"


@case("paint:filament растит матрицу промывки до (n+1)²", needs=("bambu",), tools=("paint.py",))
def _():
    d = work("paint_filament_matrix")
    src = ball_project(d)
    cfg = config(src)
    n = len(cfg["filament_colour"])
    cfg["flush_volumes_matrix"] = [str(0 if i == j else 140)
                                   for i in range(n) for j in range(n)]
    js = json.dumps(cfg, indent=4, ensure_ascii=True).replace("\n", "\r\n").encode("utf-8")
    withm = rewrite(src, d / "with_matrix.3mf",
                    lambda name, b: js if name.endswith("project_settings.config") else b)
    out = run([TOOLS / "paint.py", "filament", withm, d / "four.3mf", "#C12E1F"], deps=PAINT)
    assert "матрицы промывки в проекте нет" not in out, "матрицу потеряли\n" + out
    m = config(d / "four.3mf")["flush_volumes_matrix"]
    assert len(m) == (n + 1) ** 2, f"матрица {len(m)}, ожидалось {(n + 1) ** 2}"
    assert m[0] == "0" and m[-1] == "0", f"диагональ не нулевая: {m[0]}, {m[-1]}"


# -------------------------------------------------------------------- paint_despeckle.py
@case("paint_despeckle:на чистой покраске чистить нечего",
      tools=("paint_despeckle.py",))
def _():
    d = work("paint_despeckle_clean")
    out = run([TOOLS / "paint_despeckle.py", ball_npz(d), d / "pd.npz",
               "--scale", "1.0", "--min-width", "0.6", "--min-area", "1.0"], deps=SPLIT)
    contains(out, "проход 1: чистить нечего", "филаменты после: {2: 656, 3: 624}")


@case("paint_despeckle:мелкий кусок отдаёт соседу", tools=("paint_despeckle.py",))
def _():
    d = work("paint_despeckle_small")
    out = run([TOOLS / "paint_despeckle.py", fig_npz(d), d / "pd.npz",
               "--scale", "1.0", "--min-width", "0.6", "--min-area", "600"], deps=SPLIT)
    contains(out, "проход 1: кусков 2 из 5", "проход 2: чистить нечего",
             "филаменты после: {2: 768, 3: 400}")


@case("paint_despeckle:--keep не трогает названный филамент", tools=("paint_despeckle.py",))
def _():
    d = work("paint_despeckle_keep")
    out = run([TOOLS / "paint_despeckle.py", fig_npz(d), d / "pd.npz",
               "--scale", "1.0", "--min-width", "0.6", "--min-area", "600",
               "--keep", "4"], deps=SPLIT)
    contains(out, "проход 1: кусков 1 из 5", "филаменты после: {2: 408, 3: 400, 4: 360}")


# -------------------------------------------------------------------- paint_normalize.py
@case("paint_normalize:дописывает код граням без paint_color",
      tools=("paint_normalize.py",))
def _():
    d = work("paint_normalize_fill")
    bare = rewrite(ball_3mf(d), d / "bare.3mf",
                   lambda name, b: re.sub(rb' paint_color="8"', b"", b)
                   if name.endswith(".model") else b)
    out = run([TOOLS / "paint_normalize.py", bare, d / "norm.3mf"])
    contains(out, "экструдер объекта: филамент 2, код '8'",
             "1,280 граней, дописано 656",
             "[ok] филамент каждой грани", "[ok] координаты вершин",
             "[ok] прочие записи архива",
             "граней по филаментам: {2: 656, 3: 624}")
    assert (d / "norm.3mf").stat().st_size > 1000


@case("paint_normalize:на полном файле ничего не дописывает",
      tools=("paint_normalize.py",))
def _():
    d = work("paint_normalize_noop")
    out = run([TOOLS / "paint_normalize.py", ball_3mf(d), d / "norm.3mf"])
    contains(out, "1,280 граней, дописано 0",
             "[ok] число граней — 1,280 и 1,280",
             "граней по филаментам: {2: 656, 3: 624}")


# ------------------------------------------------------------------------- paintview.py
@case("paintview:render пишет непустой PNG", tools=("paintview.py",))
def _():
    d = work("paintview_render")
    png = d / "view.png"
    run([TOOLS / "paintview.py", "render", ball_npz(d), png,
         "--eye", "0,-150,15", "--target", "0,0,15",
         "--fov", "30", "--size", "400x400"], deps=VIEW)
    assert png.exists(), "PNG не создан"
    head = png.read_bytes()
    assert head[:8] == b"\x89PNG\r\n\x1a\n", f"не PNG: {head[:8]!r}"
    assert len(head) > 2000, f"PNG подозрительно мал: {len(head)} байт"


@case("paintview:pick различает верх и низ шара", tools=("paintview.py",))
def _():
    d = work("paintview_pick")
    out = run([TOOLS / "paintview.py", "pick", ball_npz(d),
               "--eye", "0,-150,15", "--target", "0,0,15", "--fov", "30",
               "--size", "400x400", "--px", "200,150", "200,250", "5,5"], deps=VIEW)
    top, bottom, miss = out.strip().splitlines()[-3:]
    assert "бел" in top, f"верх шара не филамент 3: {top}"
    assert "тело" in bottom, f"низ шара не филамент 2: {bottom}"
    assert "мимо модели" in miss, f"угол кадра попал в модель: {miss}"
    close(num(top, r"xyz \[\s*[-\d.]+\s+[-\d.]+\s+([\d.]+)\]"), 24.26, 0.2, "z верхней точки")


@case("paintview:grid печатает таблицу промеров", tools=("paintview.py",))
def _():
    d = work("paintview_grid")
    out = run([TOOLS / "paintview.py", "grid", ball_npz(d),
               "--eye", "0,-150,15", "--target", "0,0,15", "--fov", "30",
               "--size", "400x400", "--box", "150,250,3", "--rows", "150,250,3"], deps=VIEW)
    rows = [l for l in out.strip().splitlines() if "|" in l]
    assert len(rows) == 3, f"строк {len(rows)}, ожидалось 3\n{out}"
    assert rows[0].count("бел") == 3, f"верхний ряд не весь белый: {rows[0]}"
    assert rows[2].count("тело") == 3, f"нижний ряд не весь телесный: {rows[2]}"


# ------------------------------------------------------------------------ paint_split.py
@case("paint_split:plan считает куски и длину шва", tools=("paint_split.py",))
def _():
    d = work("paint_split_plan")
    out = run([TOOLS / "paint_split.py", "plan", ball_npz(d), "--min-area", "20"], deps=SPLIT)
    contains(out, "вершин 642, граней 1280, поверхность 2814 мм2",
             "габарит 30.0 × 30.0 × 30.0 мм", "скорлуп сетки 1",
             "как есть: шов 106 мм по 48 рёбрам, кусков 2")
    close(num(out, r"шов (\d+) мм по 48 рёбрам"), 106, 1, "длина шва")
    assert "после укрупнения до 20.0 мм2: шов 106 мм по 48 рёбрам, кусков 2" in out, \
        "укрупнение до 20 мм2 не должно ничего менять на двух крупных кусках\n" + out


@case("paint_split:plan узнаёт отдельные скорлупы", tools=("paint_split.py",))
def _():
    d = work("paint_split_shells")
    out = run([TOOLS / "paint_split.py", "plan", fig_npz(d), "--min-area", "20"], deps=SPLIT)
    contains(out, "скорлуп сетки 3", "кусков 5",
             "филамент 3: отдельная скорлупа 604 мм2 — это уже готовая деталь")
    close(num(out, r"шов (\d+) мм по 48 рёбрам"), 113, 1, "длина шва фигурки")


@case("paint_split:cut режет на замкнутые детали, объём сходится",
      tools=("paint_split.py",))
def _():
    d = work("paint_split_cut")
    out = run([TOOLS / "paint_split.py", "cut", ball_npz(d), d / "parts",
               "--min-area", "20"], deps=SPLIT)
    contains(out, "filament2:", "filament3:", "замкнута", "центр петли снаружи: 0")
    close(num(out, r"сумма деталей ([\d.]+) см3"), 14.02, 0.02, "сумма объёмов деталей")
    close(num(out, r"против исходных ([\d.]+) см3"), 14.02, 0.02, "исходный объём")
    close(num(out, r"расхождение ([\d.]+) %"), 0.0, 0.05, "расхождение объёмов")
    close(num(out, r"filament2: \d+ мм2, граней (\d+)"), 704, 0, "граней детали 2")
    for n in ("filament2.stl", "filament3.stl"):
        p = d / "parts" / n
        assert p.exists(), f"нет {n}"
        assert p.stat().st_size > 10000, f"{n} подозрительно мал: {p.stat().st_size}"


@case("paint_split:joints отвергает шов, который идёт по цвету", tools=("paint_split.py",))
def _():
    d = work("paint_split_joints")
    out = run([TOOLS / "paint_split.py", "joints", ball_npz(d),
               "--emit", d / "j.json"], deps=SPLIT)
    contains(out, "швов 1", "по цвету", "задание на 0 стыков записано")
    close(num(out, r"швов 1.*?\n.*?\n\s+0\s+3\s+2\s+([\d.]+)мм"), 105.7, 0.2, "длина шва")
    job = json.loads((d / "j.json").read_text())
    assert job, "файл задания пуст"
