"""godot-lens — observation infrastructure for game-development agents.

This package is the *interface* layer. It does not reimplement observation: the
collectors, the diagnostic rules and the graders are the parts whose correctness
was established by measurement, and rewriting them for tidiness would put that
correctness at risk for no functional gain. The package locates them, dispatches
to them, and gives them a stable name.

That is a deliberate trade-off, recorded here because it looks like duplication
and is not: `tools/*.sh` still own the Godot invocation and the output matching,
and `tools/*.py` still own the payload and the rules. This package owns the
**front door**.
"""
from __future__ import annotations

from . import protocol  # noqa: F401

__all__ = ["protocol", "paths", "cli"]
