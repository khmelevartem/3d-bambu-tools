#!/usr/bin/env python3
"""Собирает проект Bambu Studio (.3mf) с уже нанесённой покраской по треугольникам.

На входе — меш и массив номеров филамента (по вершинам или по граням),
на выходе — .3mf, который открывается в Bambu Studio сразу раскрашенным,
как будто зоны нарисовали кистью Color Painting. Руками ничего красить не надо.

Запуск:
    uv run --with trimesh --with numpy python3 tools/make_multicolor_3mf.py \
        model.stl zones.npy -o model_color.3mf

Как это устроено
----------------
Покраска живёт в атрибуте `paint_color` на `<triangle>` внутри
`3D/Objects/object_1.model`. Это не 3MF-стандарт, а расширение Bambu Studio
(унаследованное от PrusaSlicer, там тот же механизм зовётся
`slic3rpe:mmu_segmentation`). Стандартные свойства треугольников по
core-спецификации (`pid`/`p1`/`p2`/`p3`) Bambu Studio при загрузке
ИГНОРИРУЕТ — проверено, см. bbs_3mf.cpp:_handle_object_start_triangle.

Строка `paint_color` — это сериализованное дерево TriangleSelector: слайсер
умеет дробить исходный треугольник на подтреугольники, чтобы граница цвета
прошла внутри грани. Нам дробление не нужно — красим грань целиком, поэтому
строка получается короткой, в один-два символа. Вывод кодировки:

  TriangleSelector::serialize() (TriangleSelector.cpp:1997) кладёт в поток
  битов для НЕразбитого листа сначала два нуля («ноль разрезанных сторон»),
  затем состояние. Если состояние n < 3 — два бита n. Если n >= 3 —
  префикс 0b11 и потом тетрада (n - 3).

  FacetsAnnotation::get_triangle_as_string() (Model.cpp:4610) читает поток
  тетрадами, каждую собирает младшим битом вперёд и вставляет символ
  В НАЧАЛО строки — то есть тетрады в строке идут в обратном порядке.

  Состояние — это EnforcerBlockerType (Model.hpp:716), где Extruder1 = 1,
  Extruder2 = 2, Extruder3 = 3 и т. д. Что номер филамента = состоянию,
  видно в GLGizmoMmuSegmentation.hpp:100:
      get_left_button_state_type() { return EnforcerBlockerType(idx + 1); }

  Отсюда для «вся грань покрашена филаментом N»:
      N=1 -> биты 0,0,1,0 -> тетрада 4        -> "4"
      N=2 -> биты 0,0,0,1 -> тетрада 8        -> "8"
      N=3 -> 0,0,1,1 и 0,0,0,0 -> тетрады C,0 -> "0C"
      N=4 -> 0,0,1,1 и 1,0,0,0 -> тетрады C,1 -> "1C"
      N>=3 в общем виде -> hex(N-3) + "C"  (до N=17)

Грабли
------
* Непокрашенные грани печатаются филаментом, назначенным объекту
  (`<metadata key="extruder">` в model_settings.config) — это «состояние 0».
  Соблазн не красить самую большую зону вовсе: печать не меняется, а файл
  короче. Так делать НЕ НАДО, и проверено, что Bambu Studio так не делает:
  в файлах с MakerWorld `paint_color` стоит на каждой грани без исключения.
  Соглашение «нет атрибута = экструдер объекта» знает слайсер, но не знает
  посторонний читатель: аддон Blender ThreeMF_io отдавал такие грани первому
  попавшемуся материалу, и фон фигурки красился чужим цветом (20.09.2026).
  Экономия при этом — 1,7 % размера .3mf: XML лежит в zip и жмётся.
* Число филаментов в project_settings.config должно быть не меньше
  максимального номера: bbs_3mf.cpp:2382 сбрасывает extruder объекта в 1,
  если он больше длины filament_settings_id.
* Файл считается «родным» проектом Bambu только если в 3dmodel.model есть
  <metadata name="Application">BambuStudio-...</metadata> (bbs_3mf.cpp:4237).
  Иначе слайсер идёт по ветке «чужой 3mf» и трактует часть данных иначе.
* Кавычки внутри value="..." в model_settings.config надо экранировать.
  Сам Bambu Studio этого не делает и пишет нечитаемый XML — см. --fix-quotes.
"""
import argparse
import json
import os
import sys
import zipfile

import numpy as np
import trimesh

import hardware                       # имена пресетов — из hardware.json

BED = 256.0                       # стол A1, мм
MAX_FILAMENT = 17                 # дальше кодировка требует ещё одну тетраду

# Профили филаментов по умолчанию — под AMS lite этой мастерской.
DEFAULT_FILAMENTS = [
    # (имя профиля, код филамента, цвет в интерфейсе)
    ("Bambu PLA Basic @BBL A1", "GFA00", "#F4EE2A"),   # жёлтый
    ("Bambu PLA Basic @BBL A1", "GFA00", "#8E9089"),   # серый
    ("Bambu PLA Basic @BBL A1", "GFA00", "#7C4B27"),   # коричневый
    ("Bambu PLA Matte @BBL A1", "GFA01", "#E88A28"),   # мандариновый
]


def paint_code(filament: int) -> str:
    """Строка paint_color для «грань целиком покрашена филаментом N» (N с единицы).

    Вывод кодировки — в докстринге модуля. Проверено экспериментом:
    Bambu Studio 02.08.03.66 читает такой 3mf и при реэкспорте отдаёт те же строки.
    """
    if not 1 <= filament <= MAX_FILAMENT:
        raise ValueError(f"номер филамента вне диапазона 1..{MAX_FILAMENT}: {filament}")
    if filament < 3:
        # Две младшие тетрады: [0,0] «не разрезан» + два бита состояния.
        return "48"[filament - 1]
    # Префикс 0b1100 = 'C' идёт первой тетрадой в потоке, но в строке
    # тетрады перевёрнуты, поэтому 'C' оказывается последней.
    return "%X" % (filament - 3) + "C"


def faces_from_vertices(mesh: trimesh.Trimesh, per_vertex: np.ndarray) -> np.ndarray:
    """Зона грани = зона, за которую «проголосовали» минимум две её вершины.

    Если все три вершины разные (такое бывает только на стыке трёх зон),
    берём вершину с наименьшим номером зоны — лишь бы детерминированно.
    """
    tri = per_vertex[mesh.faces]                      # (F, 3)
    a, b, c = tri[:, 0], tri[:, 1], tri[:, 2]
    out = np.where(a == b, a, np.where(a == c, a, np.where(b == c, b, tri.min(axis=1))))
    return out.astype(np.int32)


def xml_escape(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;")
             .replace(">", "&gt;").replace('"', "&quot;"))


def build_object_model(mesh: trimesh.Trimesh, face_fil: np.ndarray) -> bytes:
    """3D/Objects/object_1.model — геометрия плюс paint_color на гранях.

    Красим ВСЕ грани, включая `base`, хотя его цвет задаёт и extruder объекта.
    Раньше грани `base` оставались без атрибута: слайсер читает такую грань как
    «состояние 0 = экструдер объекта», но посторонний читатель обязан знать это
    соглашение, и не всякий знает — аддон Blender отдавал их случайному цвету.
    Оригиналы с MakerWorld несут код на каждой грани; цена явного кода — 1,7 %
    размера .3mf, потому что файл всё равно зажат в zip.
    Собираем через список кусков, а не через ElementTree: на 2 млн граней
    дерево в памяти не помещается, а склейка строк отрабатывает за секунды.
    """
    v = np.asarray(mesh.vertices, dtype=np.float64)
    f = np.asarray(mesh.faces, dtype=np.int64)

    out = [
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<model unit="millimeter" xml:lang="en-US"'
        ' xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02"'
        ' xmlns:BambuStudio="http://schemas.bambulab.com/package/2021"'
        ' xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06"'
        ' requiredextensions="p">\n'
        ' <metadata name="BambuStudio:3mfVersion">1</metadata>\n'
        ' <resources>\n'
        '  <object id="1" p:UUID="00010000-81cb-4c03-9d28-80fed5dfa1dc" type="model">\n'
        '   <mesh>\n'
        '    <vertices>\n'
    ]
    out.append("".join(
        '     <vertex x="%.6f" y="%.6f" z="%.6f"/>\n' % (x, y, z) for x, y, z in v))
    out.append('    </vertices>\n    <triangles>\n')

    codes = {int(n): paint_code(int(n)) for n in np.unique(face_fil)}
    chunk = []
    for (v1, v2, v3), n in zip(f, face_fil):
        chunk.append('     <triangle v1="%d" v2="%d" v3="%d" paint_color="%s"/>\n'
                     % (v1, v2, v3, codes[int(n)]))
    out.append("".join(chunk))
    out.append('    </triangles>\n   </mesh>\n  </object>\n </resources>\n</model>\n')
    return "".join(out).encode()


def build_single_model(mesh, face_fil, offset, app_version: str) -> bytes:
    """3D/3dmodel.model со ВСЕЙ геометрией внутри — без расширения production.

    Зачем: Bambu Studio 02.08.03.66 на macOS не открывает в интерфейсе 3MF,
    у которых геометрия вынесена в отдельный `3D/Objects/*.model` и подключена
    через `p:path` (расширение production). Молча — ни модели, ни автобэкапа.
    Проверено на файлах, которые она же сама и записала своим `--export-3mf`:
    они тоже не открываются. CLI (`--info`, `--slice`) те же файлы читает.
    Односоставный файл открывается.
    """
    v = np.asarray(mesh.vertices, dtype=np.float64)
    f = np.asarray(mesh.faces, dtype=np.int64)
    dx, dy, dz = offset
    out = [
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<model unit="millimeter" xml:lang="en-US"'
        ' xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02"'
        ' xmlns:BambuStudio="http://schemas.bambulab.com/package/2021">\n'
        f' <metadata name="Application">BambuStudio-{app_version}</metadata>\n'
        ' <metadata name="BambuStudio:3mfVersion">1</metadata>\n'
        ' <resources>\n'
        '  <object id="1" type="model">\n   <mesh>\n    <vertices>\n'
    ]
    out.append("".join(
        '     <vertex x="%.6f" y="%.6f" z="%.6f"/>\n' % (x, y, z) for x, y, z in v))
    out.append('    </vertices>\n    <triangles>\n')
    codes = {int(n): paint_code(int(n)) for n in np.unique(face_fil)}
    chunk = []
    for (v1, v2, v3), n in zip(f, face_fil):
        chunk.append('     <triangle v1="%d" v2="%d" v3="%d" paint_color="%s"/>\n'
                     % (v1, v2, v3, codes[int(n)]))
    out.append("".join(chunk))
    out.append('    </triangles>\n   </mesh>\n  </object>\n </resources>\n')
    out.append(' <build>\n'
               f'  <item objectid="1" transform="1 0 0 0 1 0 0 0 1 '
               f'{dx:.5f} {dy:.5f} {dz:.5f}" printable="1"/>\n'
               ' </build>\n</model>\n')
    return "".join(out).encode()


def build_root_model(offset, app_version: str) -> bytes:
    """3D/3dmodel.model — обёртка: объект-компонент плюс место на столе."""
    dx, dy, dz = offset
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<model unit="millimeter" xml:lang="en-US"'
        ' xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02"'
        ' xmlns:BambuStudio="http://schemas.bambulab.com/package/2021"'
        ' xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06"'
        ' requiredextensions="p">\n'
        # Без этой строки слайсер считает файл чужим (bbs_3mf.cpp:4237)
        f' <metadata name="Application">BambuStudio-{app_version}</metadata>\n'
        ' <metadata name="BambuStudio:3mfVersion">1</metadata>\n'
        ' <resources>\n'
        '  <object id="2" p:UUID="00000001-61cb-4c03-9d28-80fed5dfa1dc" type="model">\n'
        '   <components>\n'
        '    <component p:path="/3D/Objects/object_1.model" objectid="1"'
        ' p:UUID="00010000-b206-40ff-9872-83e8017abed1"'
        ' transform="1 0 0 0 1 0 0 0 1 0 0 0"/>\n'
        '   </components>\n'
        '  </object>\n'
        ' </resources>\n'
        ' <build p:UUID="2c7c17d8-22b5-4d84-8835-1976022ea369">\n'
        f'  <item objectid="2" p:UUID="00000002-b1ec-4553-aec9-835e5b724bb4"'
        f' transform="1 0 0 0 1 0 0 0 1 {dx:.5f} {dy:.5f} {dz:.5f}" printable="1"/>\n'
        ' </build>\n'
        '</model>\n'
    ).encode()


def build_model_settings(name: str, base: int, face_count: int, oid: int = 2) -> bytes:
    """Metadata/model_settings.config — имя объекта, его филамент и одна часть.

    Настройки процесса сюда не пишем: они переопределяют профиль, а нам
    нужен профиль как есть. Заодно не воспроизводим баг Bambu Studio
    с неэкранированным compatible_printers.
    """
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<config>\n'
        f'  <object id="{oid}">\n'
        f'    <metadata key="name" value="{xml_escape(name)}"/>\n'
        # extruder объекта = филамент для НЕпокрашенных граней
        f'    <metadata key="extruder" value="{base}"/>\n'
        f'    <metadata face_count="{face_count}"/>\n'
        '    <part id="1" subtype="normal_part">\n'
        f'      <metadata key="name" value="{xml_escape(name)}"/>\n'
        '      <metadata key="matrix" value="1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1"/>\n'
        f'      <mesh_stat face_count="{face_count}" edges_fixed="0"'
        ' degenerate_facets="0" facets_removed="0" facets_reversed="0"'
        ' backwards_edges="0"/>\n'
        '    </part>\n'
        '  </object>\n'
        '  <plate>\n'
        '    <metadata key="plater_id" value="1"/>\n'
        '    <metadata key="plater_name" value=""/>\n'
        '    <metadata key="locked" value="false"/>\n'
        '    <model_instance>\n'
        f'      <metadata key="object_id" value="{oid}"/>\n'
        '      <metadata key="instance_id" value="0"/>\n'
        '    </model_instance>\n'
        '  </plate>\n'
        '  <assemble>\n'
        '  </assemble>\n'
        '</config>\n'
    ).encode()


def load_profiles():
    """Подтягивает resolve_profile.py из соседнего файла — он резолвит `inherits`."""
    import importlib.util
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resolve_profile.py")
    spec = importlib.util.spec_from_file_location("resolve_profile", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def build_project_settings(filaments, printer: str, process: str) -> bytes:
    """Metadata/project_settings.config — печатник, процесс и филаменты AMS.

    Кладём ПОЛНЫЙ расплющенный профиль, а не пару опознавательных ключей.
    Так надо: CLI читает часть значений без проверки на null, например
    BambuStudio.cpp:2095
        old_printable_height = (int)(config.opt_float("printable_height"));
    и на урезанном конфиге валится в SIGSEGV ещё до нарезки. С полным
    профилем `--export-3mf` и `--slice` отрабатывают (проверено).

    Значения филамента в профиле — списки на один слот; под N слотов их
    надо размножить, иначе слайсер возьмёт настройки только первого.
    """
    rp = load_profiles()
    idx = rp.index()
    cfg = rp.flatten(printer, idx)
    cfg.update(rp.flatten(process, idx))

    per_filament = [rp.flatten(nm, idx) for nm, _id, _col in filaments]
    # Ключи, которые в профиле филамента означают не «на слот», а что-то своё.
    not_per_slot = {"type", "from", "name", "filament_id", "compatible_printers",
                    "compatible_printers_condition", "filament_ingredients_safe",
                    "filament_emission_safe", "filament_contact_safe"}
    for key, val in per_filament[0].items():
        if key in not_per_slot:
            continue
        if isinstance(val, list) and len(val) == 1:
            cfg[key] = [p.get(key, val)[0] if isinstance(p.get(key, val), list)
                        else p.get(key) for p in per_filament]
        else:
            cfg[key] = val

    n = len(filaments)
    cfg.update({
        "from": "project",
        "name": "project_settings",
        "version": "02.08.03.66",
        "printer_settings_id": printer,
        "print_settings_id": process,
        "filament_settings_id": [f[0] for f in filaments],
        "filament_ids": [f[1] for f in filaments],
        "filament_colour": [f[2] for f in filaments],
        # filament_map — это «филамент -> экструдер», а не «-> слот AMS».
        # У A1 экструдер один, поэтому везде единицы.
        "filament_map": ["1"] * n,
        "filament_map_mode": "Auto For Flush",
    })
    # Расплющенный профиль — это дамп ПРЕСЕТА, и в нём остаются ключи,
    # которых в конфиге проекта быть не должно: "type", "include",
    # "description", "is_custom_defined". У Bambu Studio есть проверка
    # (строка в бинарнике: found invalid config type %1% from config %2%),
    # поэтому их убираем. Но имейте в виду: убрать их — необходимо, а не
    # достаточно. Файл без этих ключей интерфейс всё равно не открывает,
    # почему так — в скилле 3d-modeling, references/bambu-cli.md.
    # Сравнивать свой набор ключей полезно с тем, что пишет
    # сама Bambu Studio: прогнать через --export-3mf и взять set(мой)-set(её).
    for key in ("type", "include", "description", "is_custom_defined",
                "compatible_printers", "compatible_printers_condition",
                "instantiation", "setting_id", "inherits"):
        cfg.pop(key, None)
    return json.dumps(cfg, indent=4, ensure_ascii=False).encode()


CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\n'
    ' <Default Extension="rels"'
    ' ContentType="application/vnd.openxmlformats-package.relationships+xml"/>\n'
    ' <Default Extension="model"'
    ' ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>\n'
    ' <Default Extension="png" ContentType="image/png"/>\n'
    ' <Default Extension="gcode" ContentType="text/x.gcode"/>\n'
    '</Types>\n'
)
ROOT_RELS = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
    ' <Relationship Target="/3D/3dmodel.model" Id="rel-1"'
    ' Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>\n'
    '</Relationships>\n'
)
MODEL_RELS = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
    ' <Relationship Target="/3D/Objects/object_1.model" Id="rel-1"'
    ' Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>\n'
    '</Relationships>\n'
)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="STL + массив зон -> раскрашенный проект Bambu Studio",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Номер филамента в .npy — с нуля (0 = первый слот AMS), "
               "если не задан --one-based.")
    ap.add_argument("mesh", help="STL/OBJ/PLY/GLB — геометрия")
    ap.add_argument("zones", help=".npy с номером филамента: по вершинам или по граням")
    ap.add_argument("-o", "--out", required=True, help="куда писать .3mf")
    ap.add_argument("--one-based", action="store_true",
                    help="в .npy номера уже с единицы")
    ap.add_argument("--colors", help="цвета слотов через запятую, напр. #F4EE2A,#8E9089")
    ap.add_argument("--filaments",
                    help="имена профилей филамента через ';' — по одному на слот")
    ap.add_argument("--printer", default=hardware.profile()["machine"])
    ap.add_argument("--process", default=hardware.profile()["process"])
    ap.add_argument("--inline", action="store_true",
                    help="вся геометрия в одном 3D/3dmodel.model, без расширения "
                         "production. Именно такой файл открывается в интерфейсе "
                         "Bambu Studio; файл с вынесенной геометрией — нет.")
    ap.add_argument("--no-project", action="store_true",
                    help="не класть project_settings.config: получается «голая» "
                         "модель с покраской, без пресетов. Такой файл нечему "
                         "забраковать на этапе проверки конфига — Bambu Studio "
                         "открывает его со своими текущими настройками.")
    ap.add_argument("--no-center", action="store_true",
                    help="не двигать модель в центр стола")
    a = ap.parse_args()

    mesh = trimesh.load(a.mesh, force="mesh")
    if not isinstance(mesh, trimesh.Trimesh):
        sys.exit(f"не меш: {a.mesh}")
    zones = np.load(a.zones)
    if zones.ndim != 1:
        sys.exit(f"ожидался одномерный массив, получено {zones.shape}")

    if len(zones) == len(mesh.vertices):
        face_fil = faces_from_vertices(mesh, zones)
        src = "по вершинам"
    elif len(zones) == len(mesh.faces):
        face_fil = zones.astype(np.int32)
        src = "по граням"
    else:
        sys.exit(f"длина массива {len(zones)} не совпадает ни с числом вершин "
                 f"({len(mesh.vertices)}), ни с числом граней ({len(mesh.faces)})")
    if not a.one_based:
        face_fil = face_fil + 1

    slots = int(face_fil.max())
    if slots > MAX_FILAMENT:
        sys.exit(f"филаментов {slots}, кодировка рассчитана на {MAX_FILAMENT}")

    filaments = list(DEFAULT_FILAMENTS)
    if a.filaments:
        given = [s.strip() for s in a.filaments.split(";")]
        filaments = [(nm, "GFA01" if "Matte" in nm else "GFA00",
                      filaments[i][2] if i < len(filaments) else "#FFFFFF")
                     for i, nm in enumerate(given)]
    if a.colors:
        cols = [c.strip() for c in a.colors.split(",")]
        filaments = [(filaments[i][0] if i < len(filaments) else DEFAULT_FILAMENTS[0][0],
                      filaments[i][1] if i < len(filaments) else "GFA00", c)
                     for i, c in enumerate(cols)]
    if len(filaments) < slots:
        sys.exit(f"зон {slots}, а слотов описано {len(filaments)} — "
                 f"добавьте --colors/--filaments")
    filaments = filaments[:max(slots, 1)]

    # Самый частый филамент назначаем объекту как extruder — но красим и его
    # грани тоже. Пропуск атрибута у фоновых граней экономил 1,7 % .3mf и
    # стоил того, что вне слайсера цвет фона читался неверно.
    counts = np.bincount(face_fil, minlength=slots + 1)
    base = int(counts.argmax())

    print(f"{a.mesh}: {len(mesh.faces):,} граней, зоны {src}")
    for n in range(1, slots + 1):
        mark = "  <- extruder объекта" if n == base else ""
        print(f"  филамент {n} ({filaments[n-1][2]}): {counts[n]/len(face_fil)*100:5.1f}%"
              f"  paint_color={paint_code(n)!r}{mark}")

    # На стол: центр габарита в центр стола, низ на z = 0.
    lo, hi = mesh.bounds
    if a.no_center:
        offset = (0.0, 0.0, 0.0)
    else:
        mesh.apply_translation(-(lo + hi) / 2 * np.array([1, 1, 0]) - np.array([0, 0, lo[2]]))
        offset = (BED / 2, BED / 2, 0.0)

    os.makedirs(os.path.dirname(os.path.abspath(a.out)) or ".", exist_ok=True)
    name = os.path.basename(a.mesh)
    # ZIP_DEFLATED обязателен: несжатый XML на 2 млн граней — под 200 МБ.
    with zipfile.ZipFile(a.out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        z.writestr("[Content_Types].xml", CONTENT_TYPES)
        z.writestr("_rels/.rels", ROOT_RELS)
        oid = 1 if a.inline else 2
        if a.inline:
            z.writestr("3D/3dmodel.model",
                       build_single_model(mesh, face_fil, offset, "02.08.03.66"))
        else:
            z.writestr("3D/_rels/3dmodel.model.rels", MODEL_RELS)
            z.writestr("3D/3dmodel.model", build_root_model(offset, "02.08.03.66"))
            z.writestr("3D/Objects/object_1.model",
                       build_object_model(mesh, face_fil))
        z.writestr("Metadata/model_settings.config",
                   build_model_settings(name, base, len(mesh.faces), oid))
        if not a.no_project:
            z.writestr("Metadata/project_settings.config",
                       build_project_settings(filaments, a.printer, a.process))

    print(f"готово: {a.out}  ({os.path.getsize(a.out)/1e6:.1f} МБ)")
    print("открыть:  open -a BambuStudio " + a.out)


if __name__ == "__main__":
    main()
