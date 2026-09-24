#!/usr/bin/env python3
"""Назначить объектам проекта филамент, которым они печатаются.

Какой филамент возьмёт объект, решает ключ `extruder` в
`Metadata/model_settings.config` — номер слота, а не покраска по треугольникам.
После импорта STL Bambu Studio ставит всем единицу, и разложенные по пластинам
одноцветные детали печатаются одним цветом.

По умолчанию номер берётся из имени: `filament4.stl_2` → филамент 4. Так
называет детали `paint_split.py`, и после «Разделить на объекты» имя
наследуется. Что не разобралось по имени — задаётся руками.

    uv run --quiet python tools/set_extruder.py проект.3mf готово.3mf
    uv run … python tools/set_extruder.py проект.3mf готово.3mf --set 18=3 --set 22=5

Остальное содержимое 3MF переписывается байт в байт: сетки, покраска,
настройки печати и раскладка по пластинам не трогаются.
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
        if not old:                      # объекта без ключа не бывает, но пусть
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
