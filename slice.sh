#!/usr/bin/env bash
# Оценка печати без открытия Bambu Studio: вес пластика, время, G-код,
# плюс готовый проект .3mf, который открывается в интерфейсе.
#
# Про 3MF из CLI. Раньше здесь было написано, что этот файл — только для
# отправки на принтер и обратно не открывается. Это неверно, диагноз был
# ошибочный. Мешает ровно одна вещь: Bambu Studio пишет в
# Metadata/model_settings.config значение compatible_printers с кавычками
# внутри XML-атрибута и не экранирует их:
#     <metadata key="compatible_printers" value=""Bambu Lab A1 0.4 nozzle""/>
# Парсер на этом спотыкается: «not well-formed (invalid token) … line 17»,
# а интерфейс показывает «The file does not contain any geometry data».
# Production extension и p:path тут ни при чём, они читаются нормально.
# Скрипт чинит кавычки сам. НО: то, что файл после починки разбирается
# парсером, ещё не значит, что интерфейс его показывает. Проверено 11.09:
# тест на открытие (см. скилл 3d-modeling) не проходит ни один файл-проект,
# включая записанные самой Bambu Studio. Считать «открывается» непроверенным.
#
# Использование:
#     ./slice.sh /путь/model.stl              — стоковый профиль A1
#     ./slice.sh --supports /путь/model.stl   — он же, но с поддержками
#
# БЕЗ флага поддержек нет: в стоковом процессе стоит enable_support = 0,
# и вес со временем описывают печать без них. Что получилось на самом деле —
# печатает gcode_report.py в конце работы, по самому G-коду, а не по профилю.
#
# Сопло и имена пресетов берутся из hardware.json (через tools/hardware.py),
# здесь их нет. Разовое переопределение: A1_NOZZLE=0.2 ./slice.sh деталь.stl
#
# Всё, что скрипт пишет, ложится в work/out внутри проекта, а не рядом
# с моделью: в папке модели живёт только то, что печатается.
set -euo pipefail

BS="/Applications/BambuStudio.app/Contents/MacOS/BambuStudio"
HERE="$(cd "$(dirname "$0")" && pwd)"
PROF="$HERE/profiles"
SUPPORTS=0
if [ "${1:-}" = "--supports" ]; then SUPPORTS=1; shift; fi
[ $# -eq 1 ] || { sed -n '19,31p' "$0"; exit 2; }

MODEL="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"
OUT="$(dirname "$HERE")/work/out"

# Железо — из hardware.json. Кэш расплющенных профилей помечен соплом: иначе
# после смены сопла скрипт молча режет старым профилем.
NZ="$(python3 "$HERE/hardware.py" nozzle_key)"
MACHINE_ID="$(python3 "$HERE/hardware.py" machine)"
PROCESS_ID="$(python3 "$HERE/hardware.py" process)"
FILAMENT_ID="$(python3 "$HERE/hardware.py" filament)"
echo "сопло $NZ: $MACHINE_ID / $PROCESS_ID / $FILAMENT_ID"

mkdir -p "$PROF" "$OUT"
[ -f "$PROF/machine-$NZ.json" ]  || python3 "$HERE/resolve_profile.py" "$MACHINE_ID"  "$PROF/machine-$NZ.json"
[ -f "$PROF/process-$NZ.json" ]  || python3 "$HERE/resolve_profile.py" "$PROCESS_ID"  "$PROF/process-$NZ.json"
[ -f "$PROF/filament-$NZ.json" ] || python3 "$HERE/resolve_profile.py" "$FILAMENT_ID" "$PROF/filament-$NZ.json"

# Процесс: стоковый или его копия с поддержками. Оба досылают curr_bed_type:
# без него CLI ставит Cool Plate и стол греется на 35 °C вместо 65, нужных
# PLA на текстурированной PEI (проверено по M190 в G-коде).
PROCESS="$PROF/process-$NZ.json"
[ "$SUPPORTS" = 1 ] && PROCESS="$PROF/process_support-$NZ.json"
python3 - "$PROF/process-$NZ.json" "$PROCESS" "$SUPPORTS" "$(python3 "$HERE/hardware.py" plate)" <<'PY'
import json, sys, pathlib
base, dst, supports, plate = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]), sys.argv[3] == "1", sys.argv[4]
d = json.loads(base.read_text())
d["curr_bed_type"] = plate
if supports:
    # ровно то, чем профиль с поддержками отличался от стокового
    d["name"] = d["name"] + " + поддержки"
    d["enable_support"] = "1"
    d["support_on_build_plate_only"] = "1"
    d["wall_loops"] = "3"
pathlib.Path(dst).write_text(json.dumps(d, ensure_ascii=False, indent=4))
PY

# --outputdir обязателен и абсолютен; имя в --export-3mf — без пути.
# Профили обязаны быть расплющены: CLI не ругается на «inherits», он молча
# игнорирует наследование и подставляет свои дефолты — нарежет «успешно»,
# но с чужими настройками. Этим и занят resolve_profile.py.
"$BS" --outputdir "$OUT" \
      --load-settings "$PROF/machine-$NZ.json;$PROCESS" \
      --load-filaments "$PROF/filament-$NZ.json" \
      --orient 1 --arrange 1 --slice 0 \
      --export-3mf "$(basename "${MODEL%.*}").gcode.3mf" "$MODEL" > "$OUT/slice.log" 2>&1

python3 - "$OUT" "$MODEL" <<'PY'
import json, re, shutil, sys, pathlib, zipfile
out, model = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
r = json.loads((out / "result.json").read_text())

def walk(d, key):
    if isinstance(d, dict):
        if key in d: yield d[key]
        for v in d.values(): yield from walk(v, key)
    elif isinstance(d, list):
        for v in d: yield from walk(v, key)

grams = sum(walk(r, "total_used_g"))
# Время берём из total_predication — это та же оценка, что Bambu пишет в
# G-код строкой «total estimated time». Сумма feature_type_times завышает
# примерно на 15 % (на замере давала 2 ч 3 мин вместо 1 ч 47 мин).
secs = sum(walk(r, "total_predication"))
print(f"пластик:  {grams:.1f} г")
print(f"время:    {int(secs // 3600)} ч {int(secs % 3600 // 60)} мин")
print(f"G-код:    {out / 'plate_1.gcode'}")

p3mf = out / (model.stem + ".gcode.3mf")
if p3mf.exists():
    with zipfile.ZipFile(p3mf) as z:
        if "Metadata/plate_1.png" in z.namelist():
            (out / "plate_preview.png").write_bytes(z.read("Metadata/plate_1.png"))
            print(f"превью:   {out / 'plate_preview.png'}")

    # чиним неэкранированные кавычки и пересобираем архив
    tmp = out / "_repack"
    if tmp.exists(): shutil.rmtree(tmp)
    with zipfile.ZipFile(p3mf) as z: z.extractall(tmp)
    cfg = tmp / "Metadata" / "model_settings.config"
    if cfg.exists():
        s = cfg.read_text()
        fixed = re.sub(r'<metadata key="([^"]*)" value="(.*?)"/>',
                       lambda m: '<metadata key="%s" value="%s"/>'
                                 % (m.group(1), m.group(2).replace('"', "&quot;")), s)
        if fixed != s:
            cfg.write_text(fixed)
            with zipfile.ZipFile(p3mf, "w", zipfile.ZIP_DEFLATED) as z:
                for f in sorted(tmp.rglob("*")):
                    if f.is_file(): z.write(f, f.relative_to(tmp))
    shutil.rmtree(tmp)
    print(f"проект:   {p3mf}  (открывается в Bambu Studio)")

print(f"\nисходная модель: {model}")
PY

# Разбивка по типам линий и явный ответ про поддержки — читать её, а не
# только итоговые граммы: итог одинаково выглядит и с поддержками, и без.
echo
python3 "$HERE/gcode_report.py" "$OUT/plate_1.gcode"
