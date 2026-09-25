#!/usr/bin/env python3
"""Build a Bambu Studio project (.3mf) with per-triangle paint already applied.

Input: a mesh plus an array of filament numbers (per vertex or per face).
Output: a .3mf that opens in Bambu Studio already coloured, as if the zones had
been drawn with the Color Painting brush.

    uv run --with trimesh --with numpy python3 tools/make_multicolor_3mf.py \
        model.stl zones.npy -o model_color.3mf

How it works
------------
Paint lives in the `paint_color` attribute on `<triangle>` inside
`3D/Objects/object_1.model`. This is not part of the 3MF standard but a Bambu
Studio extension, inherited from PrusaSlicer where the same mechanism is called
`slic3rpe:mmu_segmentation`. The standard core-specification triangle
properties (`pid`/`p1`/`p2`/`p3`) are IGNORED by Bambu Studio on load - see
bbs_3mf.cpp:_handle_object_start_triangle.

A `paint_color` string is a serialised TriangleSelector tree: the slicer can
subdivide a triangle so that a colour border runs inside a face. Painting a
whole face needs no subdivision, so the string is one or two characters. The
encoding follows from the slicer source:

  TriangleSelector::serialize() writes, for an UNSPLIT leaf, two zero bits
  ("zero sides cut") followed by the state. For a state n < 3 that is the two
  bits of n; for n >= 3 it is the prefix 0b11 followed by the nibble (n - 3).

  FacetsAnnotation::get_triangle_as_string() reads the stream in nibbles,
  assembles each least significant bit first, and inserts the character AT THE
  FRONT of the string - so nibbles appear in reverse order.

  The state is EnforcerBlockerType, where Extruder1 = 1, Extruder2 = 2 and so
  on; the filament number equals the state.

  Hence, for "the whole face is painted with filament N":
      N=1 -> bits 0,0,1,0 -> nibble 4        -> "4"
      N=2 -> bits 0,0,0,1 -> nibble 8        -> "8"
      N=3 -> 0,0,1,1 and 0,0,0,0 -> nibbles C,0 -> "0C"
      N=4 -> 0,0,1,1 and 1,0,0,0 -> nibbles C,1 -> "1C"
      N>=3 in general -> hex(N-3) + "C", up to N=17

Rules
-----
* **Write a code on every face, the background included.** An uncoloured face
  prints with the filament assigned to the object ("state 0"), so skipping the
  largest zone changes nothing for the slicer and shortens the file by under
  two percent. But that convention is known only to the slicer: outside it,
  such faces take an arbitrary material. MakerWorld originals carry a code on
  every face without exception.
* The filament count in project_settings.config must be at least the highest
  number used, or the object's extruder is reset to 1.
* A file counts as a native Bambu project only when 3dmodel.model carries an
  Application metadata entry naming Bambu Studio. Otherwise the slicer takes
  its foreign-3mf branch and interprets part of the data differently.
* Quotes inside value="..." in model_settings.config must be escaped. Bambu
  Studio itself does not do this and writes unreadable XML - see --fix-quotes.
"""
import argparse
import json
import os
import sys
import zipfile

import numpy as np
import trimesh

import hardware                       # preset names come from hardware.json

BED = 256.0                       # bed size, mm
MAX_FILAMENT = 17                 # beyond this the encoding needs another nibble

# Default filament profiles.
DEFAULT_FILAMENTS = [
    # (profile name, filament code, colour shown in the GUI)
    ("Bambu PLA Basic @BBL A1", "GFA00", "#F4EE2A"),   # yellow
    ("Bambu PLA Basic @BBL A1", "GFA00", "#8E9089"),   # grey
    ("Bambu PLA Basic @BBL A1", "GFA00", "#7C4B27"),   # brown
    ("Bambu PLA Matte @BBL A1", "GFA01", "#E88A28"),   # tangerine
]


def paint_code(filament: int) -> str:
    """Строка paint_color для «грань целиком покрашена филаментом N» (N с единицы).

    Вывод кодировки — в докстринге модуля. Проверено экспериментом:
    Bambu Studio 02.08.03.66 читает такой 3mf и при реэкспорте отдаёт те же строки.
    """
    if not 1 <= filament <= MAX_FILAMENT:
        raise ValueError(f"номер филамента вне диапазона 1..{MAX_FILAMENT}: {filament}")
    if filament < 3:
        # The two low nibbles: [0,0] "not split" plus two state bits.
        return "48"[filament - 1]
    # The prefix 0b1100 = 'C' is the first nibble in the stream, but nibbles
    # are reversed in the string, so 'C' ends up last.
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
        # Without this line the slicer treats the file as foreign
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
        # the object's extruder is the filament for UNPAINTED faces
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
    # Keys that in a filament profile mean something other than "per slot".
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
        # filament_map is "filament -> extruder", not "-> AMS slot".
        # This machine has one extruder, so it is ones everywhere.
        "filament_map": ["1"] * n,
        "filament_map_mode": "Auto For Flush",
    })
    # A flattened profile is a dump of a PRESET and still carries keys that
    # must not appear in a project config: "type", "include", "description",
    # "is_custom_defined". Bambu Studio validates config types, so they are
    # removed here. Note that removing them is necessary, not sufficient:
    # the GUI still will not open such a file. Why is in the 3d-modeling
    # skill, references/bambu-cli.md.
    # skill, references/bambu-cli.md. A useful check is against what Bambu
    # Studio itself writes for the same model - hence the --export-3mf
    # comparison: run a file through --export-3mf and diff the key sets.
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

    # The most frequent filament is assigned to the object as its extruder,
    # but its faces are painted too: omitting the attribute on background
    # faces makes the background read as the wrong colour outside the slicer.
    counts = np.bincount(face_fil, minlength=slots + 1)
    base = int(counts.argmax())

    print(f"{a.mesh}: {len(mesh.faces):,} граней, зоны {src}")
    for n in range(1, slots + 1):
        mark = "  <- extruder объекта" if n == base else ""
        print(f"  филамент {n} ({filaments[n-1][2]}): {counts[n]/len(face_fil)*100:5.1f}%"
              f"  paint_color={paint_code(n)!r}{mark}")

    # Onto the bed: bounding box centre at the bed centre, bottom at z = 0.
    lo, hi = mesh.bounds
    if a.no_center:
        offset = (0.0, 0.0, 0.0)
    else:
        mesh.apply_translation(-(lo + hi) / 2 * np.array([1, 1, 0]) - np.array([0, 0, lo[2]]))
        offset = (BED / 2, BED / 2, 0.0)

    os.makedirs(os.path.dirname(os.path.abspath(a.out)) or ".", exist_ok=True)
    name = os.path.basename(a.mesh)
    # ZIP_DEFLATED is mandatory: uncompressed XML for millions of faces is huge.
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
