"""0-D Hall thruster performance: v_ex = sqrt(2 e V eta_v / m_i)."""

import json
import math
import os

from factory import metrics

p = json.loads(os.environ["FACTORY_PARAMS"])
e, m_xe, g0 = 1.602176634e-19, 131.293 * 1.66053907e-27, 9.80665
v_ex = math.sqrt(2 * e * p["discharge_voltage"] * p["voltage_utilisation"] / m_xe)
thrust = p["mass_utilisation"] * p["mass_flow"] * v_ex
isp = thrust / (p["mass_flow"] * g0)

print(f"v_ex = {v_ex:.0f} m/s, thrust = {thrust * 1e3:.2f} mN, Isp = {isp:.0f} s")
metrics.write("Isp", isp, "s")
metrics.write("thrust", thrust * 1e3, "mN")
