#!/usr/bin/env python3
"""Assign each project object the filament it is printed with.

Which filament an object takes is decided by the `extruder` key in
`Metadata/model_settings.config` — a slot number, not per-triangle paint.
After an STL import Bambu Studio sets every object to 1, and single-colour
parts laid out across plates all print in the same colour.

By default the number comes from the name: `filament4.stl_2` -> filament 4.
That is how `paint_split.py` names its parts, and the name survives
"Split to objects". Whatever the name does not resolve is set by hand.

    uv run --quiet python tools/set_extruder.py project.3mf done.3mf
    uv run … python tools/set_extruder.py project.3mf done.3mf --set 18=3 --set 22=5

The rest of the 3MF is rewritten byte for byte: meshes, paint, print
settings and the plate layout are left alone.
"""
import argparse, re, shutil, sys, zipfile

CFG = 'Metadata/model_settings.config'
OBJ = re.compile(r'<object id="(\d+)">(.*?)</object>', re.S)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('src'); ap.add_argument('dst')
    ap.add_argument('--set', action='append', default=[], metavar='ID=N',
                    help='явно: номер объекта = номер филамента')
    a = ap.parse_args()
    force = {}
    for s in a.set:
        k, v = s.split('=')
        force[int(k)] = int(v)

    z = zipfile.ZipFile(a.src)
    cfg = z.read(CFG).decode('utf-8')

    plan = []

    def fix(m):
        oid, body = int(m.group(1)), m.group(2)
        nm = re.search(r'key="name" value="([^"]*)"', body)
        nm = nm.group(1) if nm else ''
        if oid in force:
            f = force[oid]
        else:
            g = re.search(r'filament(\d+)', nm)
            if not g:
                plan.append((oid, nm, None))
                return m.group(0)
            f = int(g.group(1))
        old = re.search(r'key="extruder" value="([^"]*)"', body)
        plan.append((oid, nm, f))
        body = re.sub(r'(key="extruder" value=")[^"]*(")', rf'\g<1>{f}\g<2>', body,
                      count=1)
        if not old:                      # an object without the key should not exist
            body = f'\n    <metadata key="extruder" value="{f}"/>' + body
        return f'<object id="{oid}">{body}</object>'

    out = OBJ.sub(fix, cfg)

    with zipfile.ZipFile(a.dst, 'w', zipfile.ZIP_DEFLATED) as w:
        for it in z.infolist():
            data = out.encode('utf-8') if it.filename == CFG else z.read(it.filename)
            w.writestr(it, data)

    for oid, nm, f in sorted(plan):
        print(f'объект {oid:>3}  {nm:<20} → филамент {f if f else "НЕ РАЗОБРАЛ, оставлен как был"}')
    miss = [o for o, n, f in plan if f is None]
    print(f'\n{len(plan)-len(miss)} объектов из {len(plan)} → {a.dst}')
    if miss:
        print(f'без номера в имени: {miss} — задать ключом --set ID=N')


if __name__ == '__main__':
    main()
