"""Записать изменённые координаты вершин обратно в 3MF, сохранив покраску.

Единственный способ поправить геометрию чужого проекта, не потеряв цвет:
`paint_color` привязан к НОМЕРУ треугольника, поэтому двигать вершины можно,
а менять их число и порядок — нельзя. Ремонт сеткой (`meshfix.py --put`)
нумерацию ломает и требует переноса покраски; здесь она остаётся на месте.

    uv run --with numpy python3 tools/writeverts.py исходный.3mf правка.npz новый.3mf

В npz три массива:
    V      (N,3) float — новые координаты ВСЕХ вершин, в порядке файла
    moved  (N,)  bool  — какие из них переписывать
    entry  str         — путь внутри архива, обычно 3D/3dmodel.model
                         или 3D/Objects/object_1.model

Строки нетронутых вершин переносятся дословно, порядок записей и способ сжатия
в архиве сохраняются — диф остаётся только там, где геометрия правда поменялась.

Проверено 19.09.2026 на `тройная развилка жд.3mf` (4926 вершин, 9852 грани):
с moved=False XML выходит байт в байт исходным; с moved=True на всех вершинах
координаты совпадают точно (0 мм), сдвиг на 1 мм воспроизводится с точностью
2.8e-14 мм; порядок граней и состав архива не меняются ни в одном случае.

Разбор `<vertex .../>` идёт регуляркой по тому формату, каким пишет Bambu
Studio. Если вершин найдено не столько, сколько в npz, скрипт падает на
assert — молча покалеченного файла не будет.
"""
import re, shutil, sys, zipfile
import numpy as np

src, npz, dst = sys.argv[1], sys.argv[2], sys.argv[3]
d = np.load(npz, allow_pickle=True)
V, moved = d['V'], d['moved']
ent = str(d['entry'])

z = zipfile.ZipFile(src)
raw = z.read(ent).decode('utf-8')
VERT = re.compile(r'<vertex x="([^"]*)" y="([^"]*)" z="([^"]*)"\s*/>')

out, pos, i, changed = [], 0, 0, 0
for m in VERT.finditer(raw):
    if moved[i]:
        out.append(raw[pos:m.start()])
        x, y, zz = V[i]
        out.append(f'<vertex x="{x:.9g}" y="{y:.9g}" z="{zz:.9g}"/>')
        pos = m.end(); changed += 1
    i += 1
out.append(raw[pos:])
assert i == len(V), (i, len(V))
new = ''.join(out).encode('utf-8')

with zipfile.ZipFile(dst, 'w') as w:
    for it in z.infolist():
        data = new if it.filename == ent else z.read(it.filename)
        zi = zipfile.ZipInfo(it.filename, date_time=it.date_time)
        zi.compress_type = it.compress_type
        zi.external_attr = it.external_attr
        w.writestr(zi, data)
print(f'{ent}: вершин всего {len(V)}, переписано {changed}, дословно {len(V)-changed}')
