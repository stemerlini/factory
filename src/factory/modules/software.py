"""Software elements: tests and benchmarks are the verification; their counts are metrics."""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET

from ..model import Metric
from .api import Command, ElementKind, FileKind, Module, Param, RunContext, Task


def pytest_command(ctx: RunContext) -> Command:
    report = ctx.outputs_dir / "junit.xml"
    return Command([ctx.params.get("python") or sys.executable, "-m", "pytest", "-q",
                    f"--junitxml={report}", ctx.params["path"]], cwd=ctx.element_dir)


def pytest_collect(ctx: RunContext) -> dict[str, Metric]:
    root = ET.parse(ctx.outputs_dir / "junit.xml").getroot()
    suite = root if root.tag == "testsuite" else root.find("testsuite")
    total = int(suite.get("tests", 0))
    bad = int(suite.get("failures", 0)) + int(suite.get("errors", 0))
    return {"tests": Metric(total), "tests_failed": Metric(bad),
            "tests_passed": Metric(total - bad - int(suite.get("skipped", 0)))}


module = Module(
    id="software",
    title="Software",
    description="Code elements: test suites and benchmarks as verification.",
    kinds=[ElementKind("software", "Software", "λ", ("software", "script"), "SW",
                       "Firmware, flight software, ground tools.")],
    tasks=[Task(id="pytest", title="pytest suite", command=pytest_command,
                collect=pytest_collect, outputs=("junit.xml",),
                params=(Param("path", str, "design", help="tests, relative to the element"),
                        Param("python", str, None, help="interpreter")))],
    file_kinds=[FileKind("Python", ("*.py",), "λ")],
)
