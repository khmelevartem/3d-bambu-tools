#!/usr/bin/env python3
"""Regression harness: registers cases, runs tools as subprocesses, reports.

Stdlib only, so it starts on the system python3. Tools that need packages are
launched through `uv run --with ...`, exactly as a person launches them.

A case is a function registered with @case. It asserts with plain `assert`
and with the helpers below; anything it raises is a failure, with the tool's
own output attached. `needs=` marks what the case cannot run without, and a
missing prerequisite is reported SKIP, not FAIL — the suite must be runnable
on a machine with no slicer and no Blender.

Fixed hardware: every case runs with A1_HARDWARE pointing at
hardware.example.json, so expected numbers do not depend on whose machine
this is.
"""
import os, pathlib, re, shutil, subprocess, sys, time

TESTS = pathlib.Path(__file__).resolve().parent
TOOLS = TESTS.parent
ROOT = TOOLS.parent
FIX = TESTS / ".fixtures"
WORK = TESTS / ".work"
HARDWARE = TOOLS / "hardware.example.json"

BAMBU = os.environ.get("BS") or "/Applications/BambuStudio.app/Contents/MacOS/BambuStudio"
BLENDER = os.environ.get("BLENDER") or "/Applications/Blender.app/Contents/MacOS/Blender"

CASES = []


def case(name, needs=(), tools=()):
    """Register a case. needs: 'bambu', 'blender'. tools: what it covers."""
    def deco(fn):
        CASES.append({"name": name, "fn": fn, "needs": tuple(needs),
                      "tools": tuple(tools) or (name.split(":")[0],)})
        return fn
    return deco


def have(what):
    return {"bambu": lambda: os.path.exists(BAMBU),
            "blender": lambda: os.path.exists(BLENDER),
            "openscad": lambda: shutil.which("openscad") is not None}[what]()


class ToolError(AssertionError):
    pass


def run(argv, deps=(), expect=0, timeout=900, env=None):
    """Run a tool and return its combined output. expect=None accepts any code.

    deps are pip names; with them the tool goes through uv, without them
    through the system python3 — matching how each tool is documented."""
    argv = [str(a) for a in argv]
    if argv[0].endswith(".py"):
        pre = ["uv", "run", "--quiet"] + [x for d in deps for x in ("--with", d)] + ["python"] \
            if deps else [sys.executable]
        argv = pre + argv
    e = dict(os.environ)
    e.update(A1_HARDWARE=str(HARDWARE), BS=BAMBU, BLENDER=BLENDER)
    e.update(env or {})                      # слово кейса — последнее
    p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, cwd=ROOT, env=e)
    out = p.stdout + p.stderr
    if expect is not None and p.returncode != expect:
        raise ToolError(f"код {p.returncode}, ожидался {expect}\n{tail(out)}")
    if "Traceback (most recent call last)" in out and expect == 0:
        raise ToolError(f"трейсбек при нулевом коде\n{tail(out)}")
    return out


def tail(s, n=18):
    lines = s.rstrip().splitlines()
    return "\n".join("    " + l for l in lines[-n:])


def num(out, pattern, cast=float):
    """First capture group of `pattern` in the output, as a number."""
    m = re.search(pattern, out)
    if not m:
        raise ToolError(f"в выводе нет {pattern!r}\n{tail(out)}")
    return cast(m.group(1).replace(",", "."))


def close(got, want, tol, what):
    if abs(got - want) > tol:
        raise ToolError(f"{what}: получено {got}, ожидалось {want} +-{tol}")


def contains(out, *needles):
    for n in needles:
        if n not in out:
            raise ToolError(f"в выводе нет {n!r}\n{tail(out)}")


def work(name):
    """A clean directory for one case."""
    d = WORK / name.replace(":", "_").replace("/", "_")
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    return d


def fixtures():
    """Build the fixtures once per run if they are not there."""
    if (FIX / "ball.stl").exists():
        return
    subprocess.run(["uv", "run", "--quiet", "--with", "numpy", "--with", "trimesh",
                    "python", str(TESTS / "fixtures.py"), str(FIX)], check=True, cwd=ROOT)


def main(argv):
    only = None
    for a in argv[1:]:
        if a.startswith("--only="):
            only = a.split("=", 1)[1]
    fixtures()
    WORK.mkdir(exist_ok=True)
    sel = [c for c in CASES if not only or only in c["name"]]
    fails, skips, ok, t0 = [], [], 0, time.time()
    for c in sel:
        miss = [n for n in c["needs"] if not have(n)]
        if miss:
            skips.append((c["name"], ", ".join(miss)))
            print(f"  ПРОПУСК {c['name']}  (нет: {', '.join(miss)})")
            continue
        t = time.time()
        try:
            c["fn"]()
        except Exception as ex:
            fails.append((c["name"], ex))
            print(f"  ПРОВАЛ  {c['name']}\n{ex}")
        else:
            ok += 1
            print(f"  ок      {c['name']}  ({time.time()-t:.1f} c)")
    covered = {t.removesuffix(".py").removesuffix(".sh") for c in sel for t in c["tools"]}
    print(f"\n{ok} прошло, {len(fails)} провалено, {len(skips)} пропущено "
          f"за {time.time()-t0:.0f} c; инструментов затронуто {len(covered)}")

    # Покрытие проверяется только на полном прогоне: --only режет выборку.
    if not only:
        every = {p.stem for p in TOOLS.glob("*.py")} | {"slice"}
        every -= {t.stem for t in TESTS.glob("*.py")}         # обвязка не инструмент
        naked = sorted(every - covered)
        if naked:
            print(f"БЕЗ ЕДИНОГО ТЕСТА: {', '.join(naked)}")
            fails.append(("покрытие", f"инструменты без тестов: {', '.join(naked)}"))
    if fails:
        print("\nПРОВАЛЫ:")
        for n, ex in fails:
            print(f"  {n}: {str(ex).splitlines()[0]}")
    return 1 if fails else 0
