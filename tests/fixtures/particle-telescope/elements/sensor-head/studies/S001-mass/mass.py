"""Sensor head mass: aluminium tube + end caps + collimator + silicon."""

import json
import math
import os

import yaml

from factory import metrics

p = json.loads(os.environ["FACTORY_PARAMS"])
stack = yaml.safe_load(open(json.loads(os.environ["FACTORY_INPUTS"])["stack"]))
rho_al, rho_si = 2.70e-3, 2.33e-3  # g/mm3
r_out, t, length = p["housing_od_mm"] / 2, p["housing_wall_mm"], p["housing_length_mm"]
tube = math.pi * (r_out**2 - (r_out - t) ** 2) * length * rho_al
caps = 2 * math.pi * r_out**2 * t * rho_al
silicon = sum(math.pi * d["radius_mm"] ** 2 * d["thickness_um"] / 1000 * rho_si
              for d in stack["layers"])
total = tube + caps + p["collimator_mass_g"] + silicon
print(f"tube {tube:.1f} g, caps {caps:.1f} g, collimator {p['collimator_mass_g']} g, "
      f"silicon {silicon:.2f} g -> {total:.1f} g")
metrics.write("mass", total, "g")
