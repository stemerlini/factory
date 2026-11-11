"""Mass and power budgets: per-block estimates plus a system margin."""

import json
import os

import yaml

from factory import metrics

budgets = yaml.safe_load(open(json.loads(os.environ["FACTORY_INPUTS"])["budgets"]))
margin = budgets.get("margin", 0.0)
mass = power = 0.0
for name, b in budgets["blocks"].items():
    mass += b.get("mass_g", 0)
    power += b.get("power_w", 0)
    print(f"{name:12} {b.get('mass_g', 0):8.1f} g {b.get('power_w', 0):6.2f} W")
print(f"{'estimate':12} {mass:8.1f} g {power:6.2f} W")
mass, power = mass * (1 + margin), power * (1 + margin)
print(f"{'with margin':12} {mass:8.1f} g {power:6.2f} W  ({margin:.0%})")
metrics.write("mass", mass, "g")
metrics.write("power", power, "W")
