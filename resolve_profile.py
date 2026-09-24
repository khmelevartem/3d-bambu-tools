#!/usr/bin/env python3
"""Расплющивает профиль Bambu Studio: резолвит цепочку `inherits` до конца.

Bambu Studio CLI (--load-settings / --load-filaments) принимает только
"плоский" JSON, а профили в Resources/profiles/BBL/ построены на наследовании.

Использование:
    python3 resolve_profile.py "Bambu Lab A1 0.4 nozzle" out.json
    python3 resolve_profile.py "0.20mm Standard @BBL A1" out.json
    python3 resolve_profile.py "Bambu PLA Basic @BBL A1" out.json
"""
import json
import sys
from pathlib import Path

ROOT = Path("/Applications/BambuStudio.app/Contents/Resources/profiles/BBL")
SUBDIRS = ("machine", "process", "filament")
# `from` и `type` обязательны для CLI — их не выбрасываем.
SKIP = {"inherits", "instantiation", "setting_id", "name", "include"}


def index() -> dict[str, Path]:
    """name -> путь к json по всем подпапкам профилей."""
    out = {}
    for sub in SUBDIRS:
        for p in (ROOT / sub).rglob("*.json"):
            try:
                nm = json.loads(p.read_text()).get("name")
            except Exception:
                continue
            if nm:
                out.setdefault(nm, p)
    return out


def flatten(name: str, idx: dict[str, Path], seen=None) -> dict:
    """Собирает ключи от корня цепочки к листу: потомок перекрывает предка."""
    seen = seen or set()
    if name in seen:
        raise SystemExit(f"циклическое наследование на «{name}»")
    seen.add(name)
    if name not in idx:
        raise SystemExit(f"профиль не найден: «{name}»")
    data = json.loads(idx[name].read_text())
    parent = data.get("inherits")
    merged = flatten(parent, idx, seen) if parent else {}
    # Ключ `include` — вторая, независимая от `inherits` ветка сборки профиля.
    # У машин Bambu настоящий стартовый G-код (калибровка, прогрев, первая
    # линия) лежит не в профиле принтера, а в файлах-шаблонах, перечисленных
    # в `include`. Без них наследование доводит до fdm_machine_common
    # с заглушкой «M109 S205» и печатью всей детали при 205 °C.
    for inc in data.get("include", []):
        merged.update({k: v for k, v in json.loads(idx[inc].read_text()).items()
                       if k not in SKIP})
    merged.update({k: v for k, v in data.items() if k not in SKIP})
    merged["name"] = name
    merged["from"] = "system"   # без этого CLI падает: "from unsupported"
    return merged


if __name__ == "__main__":
    if len(sys.argv) < 3:
        idx = index()
        print("Укажите имя профиля и файл вывода. Доступно, например:")
        for n in sorted(idx):
            if "A1" in n and "A1 mini" not in n:
                print("   ", n)
        sys.exit(1)
    idx = index()
    res = flatten(sys.argv[1], idx)
    Path(sys.argv[2]).write_text(json.dumps(res, indent=1, ensure_ascii=False))
    print(f"{sys.argv[1]} -> {sys.argv[2]}  ({len(res)} ключей)")
