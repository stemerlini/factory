"""Geometric factor of a two-disc telescope (Sullivan 1971, Nucl. Instr. Meth. 95, 5)."""

import json
import math
import os

import yaml

from factory import metrics

stack = yaml.safe_load(open(json.loads(os.environ["FACTORY_INPUTS"])["stack"]))
d1, d2 = stack["layers"][0], stack["layers"][1]
r1, r2 = d1["radius_mm"] / 10, d2["radius_mm"] / 10  # cm
length = d2["gap_mm"] / 10  # cm
s = r1**2 + r2**2 + length**2
g = 0.5 * math.pi**2 * (s - math.sqrt(s**2 - 4 * r1**2 * r2**2))
half_angle = math.degrees(math.atan((r1 + r2) / length))

print(f"R1 = {r1} cm, R2 = {r2} cm, L = {length} cm")
print(f"G = {g:.4f} cm2 sr, full-acceptance half angle = {half_angle:.1f} deg")
metrics.write("G_geom", g, "cm2 sr")
metrics.write("half_angle", half_angle, "deg")
