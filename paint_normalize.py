#!/usr/bin/env python3
"""Write paint_color onto faces that have none, using the object's extruder code.

A face without the attribute is "state 0": the slicer prints it with the
filament assigned to the object in `<metadata key="extruder">`. Only the
slicer knows that convention. The Blender addon `ThreeMF_io` gave such faces
to whichever material came first, and a figure's background got the wrong
colour. MakerWorld originals carry a code on every face; this does the same.

    python3 tools/paint_normalize.py in.3mf out.3mf

Geometry, settings and every other archive entry are copied verbatim; only
the `<triangle .../>` lines inside `*.model` are touched. After writing, the
script compares input and output triangle by triangle itself and exits
non-zero if anything diverged: the check is not optional.

The print changes by a fraction of a percent — Bambu runs state 0 and an
explicit extruder 1 through different segmentation branches, so colour
borders shift by fractions of a line width. Measured on Dutch: 172.02 ->
172.57 g, filament changes 543 -> 542. Details: 3mf-paint/paint-format.md.
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

# Extruder of the top-level object. Should one also sit on a part, the script
# refuses to run rather than painting the background at random.
OBJECT_RE = re.compile(rb"<object\s[^>]*>(.*?)</object>", re.S)
EXTRUDER_RE = re.compile(rb'<metadata\s+key="extruder"\s+value="(\d+)"\s*/>')
PART_RE = re.compile(rb"<part\s.*?</part>", re.S)


def paint_code(filament: int) -> bytes:
    """paint_color code for "whole face painted with filament N", N from 1.

    The encoding is spelled out in the header of make_multicolor_3mf.py.
    """
    if not 1 <= filament <= MAX_FILAMENT:
        raise ValueError(f"номер филамента вне диапазона 1..{MAX_FILAMENT}: {filament}")
    if filament < 3:
        return b"48"[filament - 1:filament]
    return (b"%X" % (filament - 3)) + b"C"


def code_to_filament(code: bytes, base: int):
    """Filament number from a code; None means a face split up by the brush."""
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
    """The filament a face without paint_color is printed with."""
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
