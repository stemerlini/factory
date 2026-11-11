"""The core nouns. Nothing here knows about any physics or engineering domain."""

from __future__ import annotations

import operator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

OPS = {
    ">=": operator.ge,
    ">": operator.gt,
    "<=": operator.le,
    "<": operator.lt,
    "==": operator.eq,
}
METHODS = ("analysis", "test", "inspection", "demonstration")

# The universal loop every element goes through, in order. Elements may skip gates.
GATES = (
    ("describe", "Describe"),
    ("research", "Research"),
    ("architect", "Architect"),
    ("concept", "Concept"),
    ("analyse", "Analyse"),
    ("lock", "Lock"),
    ("design", "Design"),
    ("deliver", "Deliver"),
)
GATE_IDS = tuple(g for g, _ in GATES)
# Gates a study can serve: analyses prove the architecture, design checks prove the design,
# deliver studies produce outputs (BOM, Gerbers, STEP).
STUDY_GATES = ("analyse", "design", "deliver")


@dataclass
class Element:
    """A node of the product breakdown: the system, a subsystem, a component.

    ``path`` is "" for the root and "fee", "fee/adc"… below it; the folder of a child is
    ``<parent dir>/elements/<name>``.
    """

    path: str
    dir: Path
    prefix: str  # requirement id prefix: SYS, EL, ME, SW…
    title: str
    kind: str = "generic"
    description: str = ""
    skip: tuple[str, ...] = ()
    references: tuple[str, ...] = ()  # Citadel keys
    children: list[Element] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.path.rsplit("/", 1)[-1] if self.path else ""

    def key(self, local_id: str) -> str:
        """Project-wide key of something local to this element: ``fee/S001``."""
        return f"{self.path}/{local_id}" if self.path else local_id

    def walk(self):
        yield self
        for c in self.children:
            yield from c.walk()


@dataclass(frozen=True)
class Criterion:
    """A pass/fail rule on a named metric, e.g. Isp >= 1500 s."""

    metric: str
    op: str
    value: float
    unit: str = ""

    def check(self, value: float) -> bool:
        return OPS[self.op](value, self.value)

    def __str__(self) -> str:
        return f"{self.metric} {self.op} {self.value:g} {self.unit}".rstrip()

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Criterion:
        if d["op"] not in OPS:
            raise ValueError(f"unknown operator {d['op']!r} (use one of {', '.join(OPS)})")
        return cls(str(d["metric"]), d["op"], float(d["value"]), str(d.get("unit", "")))


@dataclass
class Requirement:
    id: str
    title: str
    element: str = ""  # element path
    text: str = ""
    method: str = "analysis"
    status: str = "draft"  # draft | approved | deleted
    criterion: Criterion | None = None
    parent: str | None = None  # requirement id in the parent element (flow-down)
    allocated: tuple[str, ...] = ()  # block ids in this element's architecture
    path: Path | None = None


@dataclass
class Study:
    """A configured job: one module task, its parameters and inputs."""

    id: str  # local to the element: S001
    title: str
    task: str  # "<module>.<task>", e.g. "geant4.run" or "script"
    dir: Path
    element: str = ""
    gate: str = "analyse"
    params: dict[str, Any] = field(default_factory=dict)
    inputs: dict[str, str] = field(default_factory=dict)  # name -> path or "@S001:output"
    verifies: list[str] = field(default_factory=list)
    description: str = ""

    @property
    def key(self) -> str:
        return f"{self.element}/{self.id}" if self.element else self.id

    @property
    def module(self) -> str:
        return self.task.split(".", 1)[0]

    def upstream(self) -> list[str]:
        """Keys of the studies whose outputs this study consumes.

        ``@S002:x`` is S002 of this element; ``@fee/S002:x`` names another element;
        ``@/S002:x`` is the root element's S002.
        """
        keys = set()
        for v in self.inputs.values():
            if v.startswith("@"):
                keys.add(self.resolve_key(v[1:].split(":", 1)[0]))
        return sorted(keys)

    def resolve_key(self, ref: str) -> str:
        if ref.startswith("/"):
            return ref[1:]
        if "/" in ref:
            return ref
        return f"{self.element}/{ref}" if self.element else ref


@dataclass
class Metric:
    value: float
    unit: str = ""


@dataclass
class Run:
    id: str
    study: str  # study key
    status: str  # queued | running | done | failed | cancelled
    fingerprint: str
    started: str
    finished: str | None = None
    commit: str | None = None
    dirty: bool = False
    toolchain: dict[str, str] = field(default_factory=dict)
    params: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Metric] = field(default_factory=dict)
    outputs: dict[str, str] = field(default_factory=dict)  # name -> sha256
    host: str = ""
    returncode: int | None = None
    pid: int | None = None
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d["metrics"] = {k: {"value": m.value, "unit": m.unit} for k, m in self.metrics.items()}
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Run:
        d = dict(d)
        d["metrics"] = {k: Metric(**m) for k, m in d.get("metrics", {}).items()}
        return cls(**d)
