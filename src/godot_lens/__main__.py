"""Allow `python -m godot_lens`, which is the zero-install entry point.

`uv tool install` and `pip install` also provide a `godot-lens` console script;
this module is for the case where someone has cloned the repository and does not
want to install anything at all.
"""
import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
