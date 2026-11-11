"""For scripts run as studies: record metrics that requirements are checked against.

    from factory import metrics
    metrics.write("Isp", 1630, "s")
"""

from __future__ import annotations

import json
import os
from pathlib import Path


def write(name: str, value: float, unit: str = "") -> None:
    path = Path(os.environ["FACTORY_RUN_DIR"]) / "metrics.json"
    data = json.loads(path.read_text()) if path.exists() else {}
    data[name] = {"value": float(value), "unit": unit}
    path.write_text(json.dumps(data, indent=2))
