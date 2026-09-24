#!/usr/bin/env python3
"""Правка Metadata/project_settings.config внутри 3MF: меняет ключи и при
надобности выкидывает филамент из проекта целиком.

Всё остальное в архиве переносится дословно — сетка, покраска, превью,
раскладка по пластинам. Так проверяется РОВНО одна правка за раз.

    python3 tools/patch3mf.py in.3mf out.3mf key=value [key=value ...]
    python3 tools/patch3mf.py in.3mf out.3mf --drop-filament 2   # номер с 1
"""
import json, sys, zipfile

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

cfg = json.loads(data['Metadata/project_settings.config'])
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

data['Metadata/project_settings.config'] = json.dumps(cfg, indent=4, ensure_ascii=True,
                                                      sort_keys=True).encode('utf-8')
with zipfile.ZipFile(dst, 'w', zipfile.ZIP_DEFLATED) as zo:
    for it in items:
        zo.writestr(it, data[it.filename])
print('записано', dst)
