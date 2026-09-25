#!/usr/bin/env python3
"""Reading hardware.json — the single place where the machine is recorded.

No script in this project keeps the nozzle diameter, layer height, line
width or preset names as its own constants: it all comes from here. Change
the nozzle, edit one field in hardware.json, and printcheck, meshdoctor,
figcheck and slice.sh all start measuring by it.

    import hardware
    hardware.nozzle()        # 0.4  — installed nozzle diameter, mm
    hardware.line_width()    # 0.42 — line width of its stock profile
    hardware.layer_height()  # 0.2
    hardware.profile()       # {'machine': ..., 'process': ..., 'filament': ...}

One-off override, without editing the file:  A1_NOZZLE=0.2 python3 …
Another config entirely — a test run, a second machine:  A1_HARDWARE=/path/to.json

Stdlib only: printcheck.py and meshdoctor.py run on the system python3.
"""
import json, os, pathlib

PATH = pathlib.Path(os.environ["A1_HARDWARE"]) if os.environ.get("A1_HARDWARE") \
    else pathlib.Path(__file__).resolve().parent.parent / "hardware.json"


def load():
    return json.loads(PATH.read_text(encoding="utf-8"))


def nozzle():
    """Installed nozzle diameter, mm. A1_NOZZLE overrides the file."""
    env = os.environ.get("A1_NOZZLE")
    if env:
        return float(env)
    return float(load()["nozzle"]["installed"])


def profile(nz=None):
    """The profile block for a nozzle: preset names and their numbers."""
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
    if len(sys.argv) > 1:                 # for shell: hardware.py machine
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
