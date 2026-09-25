"""Нарезка и работа с проектом: slice.sh, gcode_report, figcheck, figopt,
patch3mf, retune_project, resolve_profile, refcompare.

Нарезка — самое медленное в наборе, поэтому шарик режется ровно дважды
(без поддержек и с ними), а G-код обеих нарезок переиспользуется всеми
остальными кейсами. Третья нарезка — про кэш профилей, её не совместить
с прочими: у неё другой hardware.json.

slice.sh пишет в work/out в корне проекта — так он устроен, каталог задать
нельзя. Прогон набора затирает там plate_1.gcode, result.json, slice.log
и превью; work/ по правилам проекта расходная, но знать об этом надо.
"""
import json
import shutil
import zipfile

from harness import case, run, num, close, contains, work, FIX, TOOLS, ROOT

CFG = "Metadata/project_settings.config"
OUT = ROOT / "work" / "out"          # slice.sh пишет только сюда
_CACHE = {}                          # общие заготовки: нарезка и проекты


# --------------------------------------------------------------- заготовки

def sh(argv, env_pairs=(), **kw):
    """Запустить с подменой переменных окружения: ("BS=/nope", "A1_NOZZLE=0.2")."""
    env = dict(pair.split("=", 1) for pair in env_pairs)
    return run(argv, env=env, **kw)


def sliced(supports=False):
    """Нарезать шарик один раз за прогон. -> (вывод slice.sh, каталог с G-кодом)"""
    key = "sup" if supports else "plain"
    if key not in _CACHE:
        d = work(f"_gcode_{key}")
        argv = [TOOLS / "slice.sh"] + (["--supports"] if supports else []) + [FIX / "ball.stl"]
        out = sh(argv)
        for f in ("plate_1.gcode", "result.json"):
            shutil.copy(OUT / f, d / f)
        _CACHE[key] = (out, d)
    return _CACHE[key]


def project(with_settings=True):
    """Трёхцветный шарик: с блоком настроек (нужна Bambu Studio) и без него."""
    key = "proj" if with_settings else "bare"
    if key not in _CACHE:
        d = work(f"_{key}")
        p = d / ("p.3mf" if with_settings else "c.3mf")
        argv = [TOOLS / "make_multicolor_3mf.py", FIX / "ball.stl",
                FIX / "ball_zones.npy", "-o", p]
        if not with_settings:
            argv.append("--no-project")
        run(argv, deps=("trimesh", "numpy"))
        _CACHE[key] = p
    return _CACHE[key]


def repack(src, dst, **keys):
    """Копия 3MF с правкой настроек — чем угодно, включая списки.

    patch3mf.py кладёт только строки, а different_settings_to_system обязан
    быть списком: так его пишет интерфейс, и так его читает retune_project.
    """
    with zipfile.ZipFile(src) as zin:
        cfg = json.loads(zin.read(CFG))
        cfg.update(keys)
        with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
            for it in zin.infolist():
                data = json.dumps(cfg, indent=4, ensure_ascii=False).encode() \
                    if it.filename == CFG else zin.read(it.filename)
                zout.writestr(it, data)
    return dst


# ------------------------------------------------------------------ slice.sh

@case("slice:вес и время шарика", needs=("bambu",), tools=("slice.sh",))
def _():
    out, _ = sliced()
    contains(out, "сопло 0.4: Bambu Lab A1 0.4 nozzle / 0.20mm Standard @BBL A1",
             "Bambu PLA Basic @BBL A1", "G-код:", "проект:")
    close(num(out, r"пластик:\s+([\d.]+) г"), 5.5, 0.7, "вес шарика")
    close(num(out, r"время:\s+0 ч (\d+) мин"), 25, 7, "время печати, мин")


@case("slice:без флага поддержек их и нет", needs=("bambu",), tools=("slice.sh",))
def _():
    out, _ = sliced()
    contains(out, "ПОДДЕРЖЕК В ЭТОЙ НАРЕЗКЕ НЕТ")
    assert "ПОДДЕРЖКИ ЕСТЬ" not in out, "без флага в нарезке оказались поддержки"


@case("slice:--supports добавляет поддержки", needs=("bambu",), tools=("slice.sh",))
def _():
    out, _ = sliced(supports=True)
    contains(out, "ПОДДЕРЖКИ ЕСТЬ", "Support")
    close(num(out, r"ПОДДЕРЖКИ ЕСТЬ: ([\d.]+) г"), 0.17, 0.12, "вес поддержек")
    close(num(out, r"итого\s+([\d.]+)"), 6.4, 0.8, "вес с поддержками")


@case("slice:без Bambu Studio — отказ, а не тихая нарезка", needs=("bambu",),
      tools=("slice.sh",))
def _():
    out = sh([TOOLS / "slice.sh", FIX / "ball.stl"],
             env_pairs=("BS=/nonexistent",), expect=2)
    contains(out, "Bambu Studio не найден: /nonexistent", "переменной BS")
    assert "пластик:" not in out, "нарезал, хотя слайсера нет"


@case("slice:без аргументов не режет, а объясняется", needs=("bambu",),
      tools=("slice.sh",))
def _():
    out = sh([TOOLS / "slice.sh"], expect=2)
    contains(out, "enable_support = 0", "A1_NOZZLE=0.2 ./slice.sh part.stl")
    assert "пластик:" not in out, "нарезал без имени модели"
    # Строк «./slice.sh /path/model.stl» и «--supports» здесь нет и не
    # проверяется: справка печатается как sed -n '19,31p', а блок Usage лежит
    # на строках 16-18 — обе строки с вызовом срезаны. Дефект самого slice.sh.


@case("slice:кэш профилей помечен пресетом, а не только соплом", needs=("bambu",),
      tools=("slice.sh",))
def _():
    # Тот же диаметр сопла, другой филамент: тег кэша по соплу не сдвинется,
    # и без сверки имени пресета нарезка пошла бы старым профилем.
    d = work("slice_cache")
    hw = json.loads((TOOLS / "hardware.example.json").read_text())
    hw["profiles"]["0.4"]["filament"] = "Bambu PLA Matte @BBL A1"
    (d / "hw.json").write_text(json.dumps(hw, ensure_ascii=False))
    out = sh([TOOLS / "slice.sh", FIX / "ball.stl"],
             env_pairs=(f"A1_HARDWARE={d / 'hw.json'}",))
    contains(out, "Bambu PLA Matte @BBL A1")
    close(num(out, r"филамент 1\.75 мм, ([\d.]+) г/см³"), 1.32, 0.001,
          "плотность Matte вместо Basic")


# ------------------------------------------------------------ gcode_report.py

@case("gcode_report:разбивка по типам линий", needs=("bambu",),
      tools=("gcode_report.py",))
def _():
    _, d = sliced()
    out = run([TOOLS / "gcode_report.py", d / "plate_1.gcode"])
    contains(out, "тип линии", "Outer wall", "Inner wall", "Sparse infill",
             "Travel", "итого", "total estimated time")
    close(num(out, r"итого\s+([\d.]+)"), 5.42, 0.7, "вес по G-коду")
    close(num(out, r"филамент 1\.75 мм, ([\d.]+) г/см³"), 1.26, 0.001, "плотность Basic")
    close(num(out, r"Outer wall\s+([\d.]+)"), 0.99, 0.3, "внешний периметр")


@case("gcode_report:говорит прямо, что поддержек нет", needs=("bambu",),
      tools=("gcode_report.py",))
def _():
    _, d = sliced()
    out = run([TOOLS / "gcode_report.py", d / "plate_1.gcode"])
    contains(out, "ПОДДЕРЖЕК В ЭТОЙ НАРЕЗКЕ НЕТ")
    assert "\nSupport" not in out, "строка Support в нарезке без поддержек"


@case("gcode_report:находит поддержки в нарезке с ними", needs=("bambu",),
      tools=("gcode_report.py",))
def _():
    _, d = sliced(supports=True)
    out = run([TOOLS / "gcode_report.py", d / "plate_1.gcode"])
    contains(out, "Support", "Support interface", "ПОДДЕРЖКИ ЕСТЬ")
    close(num(out, r"ПОДДЕРЖКИ ЕСТЬ: ([\d.]+) г"), 0.17, 0.12, "вес поддержек")


# ---------------------------------------------------------------- figcheck.py

def figcheck(*args):
    """У figcheck свои зависимости в заголовке — его и запускает uv."""
    return run(["uv", "run", "--quiet", TOOLS / "figcheck.py", *args])


@case("figcheck:шар стоит в точку и просит срез дна", tools=("figcheck.py",))
def _():
    out = figcheck(FIX / "ball.stl")
    contains(out, "1280 граней", "габарит 30.0 x 30.0 x 30.0 мм", "замкнутость True",
             "ОПОРА НА СТОЛ", "нужен плоский срез дна")
    close(num(out, r"объём ([\d.]+) см³"), 14.0, 0.1, "объём шара")
    assert num(out, r"первый слой: ([\d.]+) мм²") < 5, "шар не может стоять пятном"
    # чем выше рез, тем больше опора
    close(num(out, r"\n\s+3\.00\s+(\d+) мм²"), 251, 15, "опора при резе 3 мм")


@case("figcheck:перебитый заголовок объясняется словами", tools=("figcheck.py",))
def _():
    """`--with` заменяет заголовок скрипта, а не дополняет его."""
    out = run(["uv", "run", "--quiet", "--with", "trimesh", "--with", "numpy",
               "python", TOOLS / "figcheck.py", FIX / "ball.stl"], expect=1)
    contains(out, "нет зависимостей", "запускать без --with")


@case("figcheck:свесы фигурки опираются на саму модель", tools=("figcheck.py",))
def _():
    out = figcheck(FIX / "figurine.stl")
    contains(out, "1168 граней", "СВЕСЫ И ПОДДЕРЖКИ", "на саму модель опираются",
             "«Поддержка только от стола» оставит эти площади без поддержки")
    assert num(out, r"на саму модель опираются \d+ мм² \((\d+)%\)", int) > 50, \
        "торчащая рука обязана опираться на саму модель, а не на стол"
    # у шара, наоборот, всё висящее садится на стол
    ball = figcheck(FIX / "ball.stl")
    assert num(ball, r"на саму модель опираются \d+ мм² \((\d+)%\)", int) == 0, \
        "у шара свесам не на что опереться, кроме стола"


@case("figcheck:тонкий слой сглаживает ступеньки", tools=("figcheck.py",))
def _():
    out = figcheck(FIX / "figurine.stl")
    contains(out, "СТУПЕНЬКИ НА ПОВЕРХНОСТИ", "порог поддержки")
    grub = num(out, r"\n\s+0\.20\s+([\d.]+)%")      # доля площади со ступенькой <0.1 мм
    fine = num(out, r"\n\s+0\.08\s+([\d.]+)%")
    assert fine > grub + 10, f"слой 0.08 обязан быть глаже 0.20: {fine}% против {grub}%"


@case("figcheck:--rx наклоняет до постановки на стол", tools=("figcheck.py",))
def _():
    flat = num(figcheck(FIX / "figurine.stl"), r"габарит [\d.]+ x ([\d.]+) x")
    tilt = num(figcheck(FIX / "figurine.stl", "--rx", "15"),
               r"габарит [\d.]+ x ([\d.]+) x")
    close(flat, 18.0, 0.1, "габарит по Y без наклона")
    assert tilt > flat + 5, f"наклон на 15° не изменил габарит: {tilt} против {flat}"


# ------------------------------------------------------------------ figopt.py

@case("figopt:audit принимает проект под наше железо", needs=("bambu",),
      tools=("figopt.py",))
def _():
    out = run([TOOLS / "figopt.py", "audit", project()])
    contains(out, "3 филамент(ов), слой 0.2",
             "0.20mm Standard @BBL A1 / Bambu Lab A1 0.4 nozzle")
    assert "БЛОКЕР" not in out, "на своём же железе аудит не должен блокировать"


@case("figopt:audit ловит чужой принтер", needs=("bambu",), tools=("figopt.py",))
def _():
    d = work("figopt_alien")
    run([TOOLS / "patch3mf.py", project(), d / "alien.3mf",
         "printer_settings_id=Bambu Lab P1S 0.4 nozzle"])
    out = run([TOOLS / "figopt.py", "audit", d / "alien.3mf"], expect=1)
    contains(out, "[БЛОКЕР] принтер в проекте: Bambu Lab P1S 0.4 nozzle",
             "у нас Bambu Lab A1 0.4 nozzle")


@case("figopt:colors считает смены филамента", needs=("bambu",), tools=("figopt.py",))
def _():
    out = run([TOOLS / "figopt.py", "colors", project()])
    contains(out, "слоёв 150, филаментов 3", "смен филамента не меньше",
             "что дороже всего обходится")
    # зоны шарика — нижняя и верхняя половины, граница одна
    assert 1 <= num(out, r"смен филамента не меньше (\d+)", int) <= 20, \
        "на двух зонах по высоте смен должны быть единицы"
    close(num(out, r"\n\s+2 #8E9089\s+(\d+)", int), 656, 1, "граней у филамента 2")


@case("figopt:gcode показывает факт после нарезки", needs=("bambu",),
      tools=("figopt.py",))
def _():
    _, d = sliced()
    out = run([TOOLS / "figopt.py", "gcode", d / "plate_1.gcode"])
    contains(out, "смен филамента (команд T)", "пластика всего", "в промывку",
             "башня очистки", "total estimated time")
    close(num(out, r"пластика всего:\s+([\d.]+) г"), 5.5, 0.7, "пластик по G-коду")


# ----------------------------------------------------------------- patch3mf.py

@case("patch3mf:меняет ключ в настройках", needs=("bambu",), tools=("patch3mf.py",))
def _():
    d = work("patch3mf_key")
    out = run([TOOLS / "patch3mf.py", project(), d / "o.3mf",
               "layer_height=0.12", "enable_support=1"])
    contains(out, "layer_height: '0.2' -> '0.12'", "enable_support: '0' -> '1'",
             "записано")
    # правка проверяется чужими глазами — чтением через figopt
    contains(run([TOOLS / "figopt.py", "audit", d / "o.3mf"]), "слой 0.12")


@case("patch3mf:выкидывает филамент из проекта", needs=("bambu",),
      tools=("patch3mf.py",))
def _():
    d = work("patch3mf_drop")
    out = run([TOOLS / "patch3mf.py", project(), d / "o.3mf", "--drop-filament", "3"])
    contains(out, "филамент 3 выкинут: было 3, стало 2")
    contains(run([TOOLS / "figopt.py", "audit", d / "o.3mf"]), "2 филамент(ов)")


@case("patch3mf:на файле без настроек — отказ, а не трейсбек", tools=("patch3mf.py",))
def _():
    d = work("patch3mf_bare")
    out = run([TOOLS / "patch3mf.py", project(with_settings=False), d / "o.3mf",
               "layer_height=0.12"], expect=1)
    contains(out, f"нет {CFG}", "собран с --no-project", "retune_project.py")
    assert "Traceback" not in out, "вместо сообщения вывалился трейсбек"
    assert not (d / "o.3mf").exists(), "испорченный файл всё-таки записан"


# ------------------------------------------------------------ retune_project.py

@case("retune_project:без --from даёт usage", tools=("retune_project.py",))
def _():
    d = work("retune_noref")
    out = run([TOOLS / "retune_project.py", project(with_settings=False),
               "-o", d / "o.3mf"], expect=2)
    contains(out, "usage: retune_project.py", "required: --from")
    assert "Traceback" not in out, "вместо usage вывалился трейсбек"


@case("retune_project:переносит настройки, не трогая авторские правки",
      needs=("bambu",), tools=("retune_project.py",))
def _():
    d = work("retune_port")
    # файл «человека»: интерфейс перечислил его правку в different_settings_to_system
    human = repack(project(), d / "human.3mf",
                   different_settings_to_system=["layer_height", "", ""],
                   layer_height="0.12")
    ref = d / "ref.3mf"
    run([TOOLS / "patch3mf.py", project(), ref,
         "layer_height=0.28", "enable_support=1", "sparse_infill_density=25%"])
    out = run([TOOLS / "retune_project.py", human, "-o", d / "o.3mf", "--from", ref])
    contains(out,
             "перенесено значений: 2",
             "список правок оставлен авторский: layer_height",
             "их значения не тронуты: layer_height=0.12",
             "перенесены без потерь: да")
    assert "layer_height" not in out.split("перенесено значений")[1].split("\n\n")[0], \
        "авторский слой перенесён, хотя человек правил его руками"
    cfg = json.loads(zipfile.ZipFile(d / "o.3mf").read(CFG))
    assert cfg["layer_height"] == "0.12", "слой человека затёрт эталонным"
    assert cfg["enable_support"] == "1", "настройка из эталона не доехала"


@case("retune_project:на файле без настроек — отказ, а не трейсбек", needs=("bambu",),
      tools=("retune_project.py",))
def _():
    d = work("retune_bare")
    out = run([TOOLS / "retune_project.py", project(with_settings=False),
               "-o", d / "o.3mf", "--from", project()], expect=1)
    contains(out, f"нет {CFG}", "его сперва должен открыть и сохранить человек")
    assert "Traceback" not in out, "вместо сообщения вывалился трейсбек"


# ----------------------------------------------------------- resolve_profile.py

@case("resolve_profile:процесс расплющен в плоский json", needs=("bambu",),
      tools=("resolve_profile.py",))
def _():
    d = work("resolve_process")
    out = run([TOOLS / "resolve_profile.py", "0.20mm Standard @BBL A1", d / "p.json"])
    contains(out, "0.20mm Standard @BBL A1 ->", "ключей")
    assert 100 < num(out, r"\((\d+) ключей\)", int) < 500, "разумного числа ключей нет"
    p = json.loads((d / "p.json").read_text())
    assert p["name"] == "0.20mm Standard @BBL A1", f"чужое имя в профиле: {p['name']}"
    assert p["layer_height"] == "0.2", f"слой не тот: {p['layer_height']}"
    for k in ("from", "type"):          # CLI без них профиль не примет
        assert k in p, f"CLI требует ключ {k}, а его нет"
    assert "inherits" not in p, "цепочка наследования не расплющена"


@case("resolve_profile:у принтера подтянут старт-G-код из include", needs=("bambu",),
      tools=("resolve_profile.py",))
def _():
    d = work("resolve_machine")
    run([TOOLS / "resolve_profile.py", "Bambu Lab A1 0.4 nozzle", d / "m.json"])
    m = json.loads((d / "m.json").read_text())
    g = m.get("machine_start_gcode", "")
    assert len(g) > 1000, (
        f"старт-G-код {len(g)} символов: ветка include не собрана, "
        "остался заглушечный M109 S205 — вся деталь печаталась бы при 205 °C")


@case("resolve_profile:несуществующий пресет — внятный отказ", needs=("bambu",),
      tools=("resolve_profile.py",))
def _():
    d = work("resolve_missing")
    out = run([TOOLS / "resolve_profile.py", "Нет такого пресета", d / "x.json"],
              expect=1)
    contains(out, "профиль не найден: «Нет такого пресета»")
    assert "Traceback" not in out, "вместо сообщения вывалился трейсбек"


# --------------------------------------------------------------- refcompare.py

REF = ("numpy", "scipy", "pillow")


def circle_ppm(path, side=400, r=150, tone=30):
    """«Фото» шарика: залитый круг известного радиуса на белом.

    Шар — единственная фигура, силуэт которой считается в уме, поэтому
    масштаб подгонки известен заранее: 2r пикселей на 30 мм.
    """
    c = side // 2
    rows = [bytes().join(bytes((tone, tone, tone)) if (x - c) ** 2 + (y - c) ** 2 <= r * r
                         else b"\xff\xff\xff" for x in range(side)) for y in range(side)]
    path.write_bytes(b"P6\n%d %d\n255\n" % (side, side) + b"".join(rows))
    return path


@case("refcompare:находит масштаб по силуэту", tools=("refcompare.py",))
def _():
    d = work("refcompare_fit")
    photo = circle_ppm(d / "photo.ppm")
    out = run([TOOLS / "refcompare.py", "fit", project(with_settings=False), photo,
               "--out", d / "cmp.npz"], deps=REF)
    contains(out, "сетка: вершин 642, граней 1280", "габарит [30. 30. 30.] мм",
             "подгонка:", "сохранено:")
    close(num(out, r"подгонка: ([\d.]+) px/мм"), 10.0, 0.4, "масштаб по силуэту")
    assert num(out, r"совпадение силуэтов ([\d.]+) %") > 95, \
        "круг против шара обязан сойтись почти идеально"
    assert "силуэты сошлись плохо" not in out


@case("refcompare:measure меряет ширины по сечениям", tools=("refcompare.py",))
def _():
    d = work("refcompare_measure")
    photo = circle_ppm(d / "photo.ppm")
    run([TOOLS / "refcompare.py", "fit", project(with_settings=False), photo,
         "--out", d / "cmp.npz"], deps=REF)
    out = run([TOOLS / "refcompare.py", "measure", d / "cmp.npz"], deps=REF)
    contains(out, "СИЛУЭТ ПО СЕЧЕНИЯМ", "верх: картинка строка", "ширина модели")
    assert num(out, r"наибольшее расхождение ширины: ([\d.]+) мм") < 2.0, \
        "шар против своего же круга не может разойтись на миллиметры"


@case("refcompare:sheet собирает лист сравнения", tools=("refcompare.py",))
def _():
    d = work("refcompare_sheet")
    photo = circle_ppm(d / "photo.ppm")
    run([TOOLS / "refcompare.py", "fit", project(with_settings=False), photo,
         "--out", d / "cmp.npz"], deps=REF)
    out = run([TOOLS / "refcompare.py", "sheet", d / "cmp.npz", d / "sheet.png"],
              deps=REF)
    contains(out, "лист:", "обрезка")
    assert (d / "sheet.png").stat().st_size > 10000, "лист сравнения пустой"


# ------------------- дефекты, найденные этим же набором и починенные

@case("figopt:на файле без настроек объясняется, а не падает", tools=("figopt.py",))
def _():
    out = run([TOOLS / "figopt.py", "audit", project(False)], deps=("numpy",), expect=1)
    assert "Traceback" not in out, f"трейсбек вместо объяснения:\n{out}"
    contains(out, CFG, "--no-project", "retune_project.py")


@case("retune_project:эталон без настроек — отказ, а не трейсбек",
      needs=("bambu",), tools=("retune_project.py",))
def _():
    d = work("retune_bare_ref")
    out = run([TOOLS / "retune_project.py", project(True), "-o", d / "r.3mf",
               "--from", project(False)], expect=1)
    assert "Traceback" not in out, f"трейсбек вместо отказа:\n{out}"
    contains(out, "эталон")


@case("make_multicolor:пишет тип пластины — иначе стол греется не под неё",
      needs=("bambu",), tools=("make_multicolor_3mf.py",))
def _():
    cfg = json.loads(zipfile.ZipFile(project(True)).read(CFG))
    assert cfg.get("curr_bed_type") == "Textured PEI Plate", \
        f"curr_bed_type в проекте: {cfg.get('curr_bed_type')!r}"
    # и это видно в G-коде: без ключа CLI берёт холодную пластину и греет до 35
    sliced()
    gcode = (OUT / "plate_1.gcode").read_text(errors="ignore")
    bed = num(gcode, r"\nM140 S(\d+)", int)
    close(bed, 65, 0, "температура стола")


@case("slice:подсказка показывает, как звать скрипт", tools=("slice.sh",))
def _():
    out = run([TOOLS / "slice.sh"], expect=2)
    contains(out, "./slice.sh /path/model.stl", "--supports")
