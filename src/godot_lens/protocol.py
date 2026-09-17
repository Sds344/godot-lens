"""The observable-state contract: one source of truth for schema and version.

This module exists because the schema identifier had begun to appear in more than
one place (the payload builder, the docs, the tests). Two copies of a version
string is one copy too many: they drift, and the drift is silent.

Keep this dependency-free. The payload builder and the CLI both import it, and
anything it imports becomes a dependency of every consumer.

On the version number: it is deliberately `0.x`. The shape is still being
discovered by observing real projects, so this states that a consumer pinning it
is taking a risk we have not yet earned the right to ask of it. See PROTOCOL.md
for the two rules that are treated as settled *despite* `0.x`.
"""
from __future__ import annotations

# The contract name. Constant; identifies *what* a document is.
SCHEMA = "godot-lens/observation"

# The schema version of the shape. Consumers should branch on this string.
VERSION = "0.1"


def identity():
    """The fields every payload carries to describe itself."""
    return {"schema": SCHEMA, "version": VERSION}


def describe():
    """One-line human description, used by `godot-lens version`."""
    return f"{SCHEMA} {VERSION}"


if __name__ == "__main__":
    # Callable as a module so a shell script or a subprocess can read the
    # authoritative values instead of copying them. `--json` for machines,
    # bare for shell word-splitting.
    import json
    import sys

    if "--json" in sys.argv[1:]:
        print(json.dumps(identity()))
    elif "--version" in sys.argv[1:]:
        print(VERSION)
    else:
        print(describe())
