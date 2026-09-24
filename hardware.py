#!/usr/bin/env python3
"""Чтение hardware.json — единственного места, где записано железо.

Ни один скрипт проекта не держит диаметр сопла, высоту слоя, ширину линии
и имена профилей у себя в константах: всё берётся отсюда. Поменял сопло —
правишь одно поле в hardware.json, и printcheck, meshdoctor, figcheck
и slice.sh начинают мерить по нему.

    import hardware
    hardware.nozzle()        # 0.4  — диаметр установленного сопла, мм
    hardware.line_width()    # 0.42 — ширина линии у его штатного профиля
    hardware.layer_height()  # 0.2
    hardware.profile()       # {'machine': ..., 'process': ..., 'filament': ...}

Разовое переопределение, без правки файла:  A1_NOZZLE=0.2 python3 …

Только stdlib: printcheck.py и meshdoctor.py запускаются системным python3.
"""
import json, os, pathlib

PATH = pathlib.Path(__file__).resolve().parent.parent / "hardware.json"


def load():
    return json.loads(PATH.read_text(encoding="utf-8"))


def nozzle():
    """Диаметр установленного сопла, мм. A1_NOZZLE перебивает файл."""
    env = os.environ.get("A1_NOZZLE")
    if env:
        return float(env)
    return float(load()["nozzle"]["installed"])


def profile(nz=None):
    """Блок профилей для сопла: имена пресетов и их числа."""
    d = load()
    key = _key(nz if nz is not None else nozzle())
    if key not in d["profiles"]:
        raise SystemExit(
            f"hardware.json: для сопла {key} профилей нет. Есть: "
            + ", ".join(k for k in d["profiles"] if not k.startswith("_")))
    return d["profiles"][key]


def _key(nz):
    return f"{float(nz):g}"


def line_width(nz=None):
    return float(profile(nz)["line_width"])


def layer_height(nz=None):
    return float(profile(nz)["layer_height"])


def bed():
    return tuple(load()["printer"]["bed"])


def plate():
    return load()["printer"]["plate"]


def density_default():
    return float(load()["density_default"])


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:                 # для shell: hardware.py machine
        k = sys.argv[1]
        d = load()
        if k == "nozzle_key":
            print(_key(nozzle()))
        elif k == "plate":
            print(plate())
        else:
            v = profile().get(k, d.get(k))
            if v is None:
                raise SystemExit(f"нет ключа {k}")
            print(v)
    else:
        print(f"сопло {nozzle()} мм, слой {layer_height()}, линия {line_width()}")
        print(f"профили: {profile()['machine']} / {profile()['process']} / {profile()['filament']}")
        print(f"стол {bed()}, пластина {plate()}")
