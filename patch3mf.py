#!/usr/bin/env python3
"""Patch Metadata/project_settings.config inside a 3MF: change keys and, if
needed, drop a filament from the project entirely.

Everything else in the archive is carried over verbatim — mesh, paint,
preview, plate layout. That is how EXACTLY one change gets tested at a time.

    python3 tools/patch3mf.py in.3mf out.3mf key=value [key=value ...]
    python3 tools/patch3mf.py in.3mf out.3mf --drop-filament 2   # 1-based
"""
import json, sys, zipfile

CFG = 'Metadata/project_settings.config'

if {'-h', '--help'} & set(sys.argv[1:]):
    print(__doc__); sys.exit(0)
if len(sys.argv) < 4:
    sys.exit(__doc__)

src, dst = sys.argv[1], sys.argv[2]
args = sys.argv[3:]
drop = None
kv = {}
i = 0
while i < len(args):
    if args[i] == '--drop-filament':
        drop = int(args[i+1]); i += 2
    else:
        k, v = args[i].split('=', 1); kv[k] = v; i += 1

with zipfile.ZipFile(src) as z:
    items = z.infolist()
    data = {it.filename: z.read(it.filename) for it in items}

if CFG not in data:
    sys.exit(f'{src}: нет {CFG}: файл собран с --no-project или экспортирован\n'
             'без настроек. Они появляются, когда файл открыт и сохранён\n'
             'в Bambu Studio; перенести их туда потом — retune_project.py')
cfg = json.loads(data[CFG])
n = len(cfg['filament_colour'])
if drop is not None:
    d = drop - 1
    for k, v in list(cfg.items()):
        if isinstance(v, list) and len(v) == n:
            cfg[k] = v[:d] + v[d+1:]
    m = cfg.get('flush_volumes_matrix')
    if m is not None and len(m) == n*n:
        cfg['flush_volumes_matrix'] = [m[r*n+c] for r in range(n) for c in range(n)
                                       if r != d and c != d]
    for k in ('filament_self_index', 'filament_map'):
        if k in cfg and isinstance(cfg[k], list):
            cfg[k] = [str(j+1) for j in range(len(cfg[k]))] if k == 'filament_self_index' \
                     else ['1'] * len(cfg[k])
    print(f'филамент {drop} выкинут: было {n}, стало {len(cfg["filament_colour"])}')
for k, v in kv.items():
    old = cfg.get(k)
    cfg[k] = json.loads(v) if v[:1] in '[{' else v
    print(f'  {k}: {old!r} -> {cfg[k]!r}')

data[CFG] = json.dumps(cfg, indent=4, ensure_ascii=True,
                                                      sort_keys=True).encode('utf-8')
with zipfile.ZipFile(dst, 'w', zipfile.ZIP_DEFLATED) as zo:
    for it in items:
        zo.writestr(it, data[it.filename])
print('записано', dst)
