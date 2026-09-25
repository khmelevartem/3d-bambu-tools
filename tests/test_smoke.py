"""Fixtures and the config every other case rests on."""
from harness import case, run, num, close, contains, FIX, TOOLS

@case("hardware:reads the example config", tools=("hardware.py",))
def _():
    out = run([TOOLS / "hardware.py"])
    contains(out, "сопло 0.4 мм", "Bambu Lab A1 0.4 nozzle")

@case("hardware:A1_NOZZLE overrides the file", tools=("hardware.py",))
def _():
    out = run([TOOLS / "hardware.py"], env={"A1_NOZZLE": "0.2"})
    contains(out, "сопло 0.2 мм")

@case("fixtures:the ball is the documented ball", tools=("fixtures.py",))
def _():
    out = run([TOOLS / "meshdoctor.py", FIX / "ball.stl"], deps=("numpy", "scipy"))
    close(num(out, r"объём ([\d.]+) мм³"), 14015.5, 0.1, "объём сферы")
