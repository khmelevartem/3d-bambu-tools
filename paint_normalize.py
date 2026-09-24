#!/usr/bin/env python3
"""Дописывает paint_color грани, у которой его нет, кодом экструдера объекта.

Грань без атрибута — «состояние 0»: слайсер печатает её филаментом, который
назначен объекту в `<metadata key="extruder">`. Соглашение знает только
слайсер. Аддон Blender `ThreeMF_io` отдавал такие грани первому попавшемуся
материалу, и фон фигурки красился чужим цветом. Оригиналы с MakerWorld несут
код на каждой грани без исключения — этот скрипт приводит файл к тому же виду.

    python3 tools/paint_normalize.py вход.3mf выход.3mf

Геометрия, настройки и все прочие записи архива копируются дословно; трогаются
только строки `<triangle .../>` внутри `*.model`. После записи скрипт сам
сверяет вход и выход потреугольно и валится с ненулевым кодом, если что-то
разошлось: проверка не опция.

Печать меняется на доли процента — Bambu ведёт состояние 0 и явный экструдер 1
разными ветками сегментации, границы цвета двигаются на доли ширины линии.
Замер на Датче: 172,02 -> 172,57 г, смен филамента 543 -> 542. Разбор —
в скилле 3mf-paint, references/paint-format.md.
"""

import re
import sys
import zipfile
from collections import Counter

TRI = b"<triangle "
VERT = b"<vertex "
HAS_PAINT = b"paint_color="
CODE_RE = re.compile(rb'paint_color="([0-9A-Fa-f]+)"')
MAX_FILAMENT = 17

# Экструдер объекта верхнего уровня. Если он окажется ещё и на part — скрипт
# откажется работать, а не покрасит фон наугад.
OBJECT_RE = re.compile(rb"<object\s[^>]*>(.*?)</object>", re.S)
EXTRUDER_RE = re.compile(rb'<metadata\s+key="extruder"\s+value="(\d+)"\s*/>')
PART_RE = re.compile(rb"<part\s.*?</part>", re.S)


def paint_code(filament: int) -> bytes:
    """Код paint_color для «грань целиком покрашена филаментом N», N с единицы.

    Вывод кодировки — в шапке tools/make_multicolor_3mf.py.
    """
    if not 1 <= filament <= MAX_FILAMENT:
        raise ValueError(f"номер филамента вне диапазона 1..{MAX_FILAMENT}: {filament}")
    if filament < 3:
        return b"48"[filament - 1:filament]
    return (b"%X" % (filament - 3)) + b"C"


def code_to_filament(code: bytes, base: int):
    """Номер филамента по коду; None — грань, дроблённая кистью на части."""
    if not code:
        return base
    if code == b"4":
        return 1
    if code == b"8":
        return 2
    if len(code) == 2 and code.endswith(b"C"):
        return int(code[:1], 16) + 3
    return None


def default_extruder(zin: zipfile.ZipFile) -> int:
    """Филамент, которым печатается грань без paint_color."""
    cfg = zin.read("Metadata/model_settings.config")
    objects = OBJECT_RE.findall(cfg)
    if len(objects) != 1:
        sys.exit(f"в model_settings.config {len(objects)} объектов — разбирать вручную")
    body = objects[0]
    for part in PART_RE.findall(body):
        if EXTRUDER_RE.search(part):
            sys.exit("у part свой extruder — скрипт на это не рассчитан")
    m = EXTRUDER_RE.search(PART_RE.sub(b"", body))
    if m is None:
        sys.exit('у объекта нет key="extruder" — непонятно, чем красить фон')
    return int(m.group(1))


def scan(path: str):
    """Покраска и геометрия файла в виде, пригодном для сравнения."""
    with zipfile.ZipFile(path) as z:
        base = default_extruder(z)
        names = [n for n in z.namelist() if n.endswith(".model")]
        labels, splits, verts = [], [], []
        for name in names:
            with z.open(name) as f:
                for line in f:
                    if TRI in line:
                        m = CODE_RE.search(line)
                        code = m.group(1).upper() if m else b""
                        fil = code_to_filament(code, base)
                        if fil is None:
                            splits.append(code)
                            labels.append(-len(splits))
                        else:
                            labels.append(fil)
                    elif VERT in line:
                        verts.append(line)
        others = {n: z.read(n) for n in z.namelist() if n not in names}
    return base, labels, splits, verts, others


def rewrite(src: str, dst: str) -> int:
    with zipfile.ZipFile(src) as zin:
        fil = default_extruder(zin)
        code = paint_code(fil)
        tail = b' paint_color="' + code + b'"/>\n'
        print(f"{src}\n  экструдер объекта: филамент {fil}, код {code.decode()!r}")

        with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zout:
            for zi in zin.infolist():
                out = zipfile.ZipInfo(zi.filename, date_time=zi.date_time)
                out.compress_type = zipfile.ZIP_DEFLATED
                out.external_attr = zi.external_attr
                if not zi.filename.endswith(".model"):
                    zout.writestr(out, zin.read(zi))
                    continue
                total = added = 0
                with zin.open(zi) as fin, zout.open(out, "w") as fout:
                    for line in fin:
                        if TRI in line:
                            total += 1
                            if HAS_PAINT not in line and line.endswith(b"/>\n"):
                                line = line[:-3] + tail
                                added += 1
                        fout.write(line)
                if total:
                    print(f"  {zi.filename}: {total:,} граней, дописано {added:,}")
    return fil


def verify(src: str, dst: str) -> bool:
    ba, la, sa, va, oa = scan(src)
    bb, lb, sb, vb, ob = scan(dst)
    ok = True

    def check(what: str, good: bool, detail: str = "") -> None:
        nonlocal ok
        ok &= good
        print(f"  [{'ok' if good else 'ПЛОХО'}] {what}{(' — ' + detail) if detail else ''}")

    print("сверка входа и выхода:")
    check("экструдер объекта", ba == bb, f"{ba} и {bb}")
    check("число граней", len(la) == len(lb), f"{len(la):,} и {len(lb):,}")
    check("число вершин", len(va) == len(vb), f"{len(va):,} и {len(vb):,}")
    check("координаты вершин", va == vb)
    check("дроблёные кистью грани", sa == sb, f"{len(sa):,} штук")
    if len(la) == len(lb):
        bad = [i for i, (x, y) in enumerate(zip(la, lb)) if x != y]
        check("филамент каждой грани", not bad,
              "" if not bad else f"разошлись {len(bad):,}, первая — грань {bad[0]}")
    check("прочие записи архива", oa == ob)
    print("  граней по филаментам:",
          dict(sorted(Counter(x for x in lb if x > 0).items())))
    return ok


def main() -> None:
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    src, dst = sys.argv[1], sys.argv[2]
    rewrite(src, dst)
    if not verify(src, dst):
        sys.exit("сверка не сошлась — файл не отдавать")
    print(f"готово: {dst}")


if __name__ == "__main__":
    main()
