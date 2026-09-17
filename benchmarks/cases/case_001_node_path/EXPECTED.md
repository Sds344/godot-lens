# case_001_node_path

## Fault class
Runtime-only. A script asks for a node path that does not exist.

## Symptom
The project passes **static** validation (`godot --check-only` is clean: the
code is syntactically and type-wise valid). It fails only when the code runs.

## Why this case exists
This is the canonical "static analysis cannot see it" bug. An agent that only
reads `.gd`/`.tscn` text has no way to know the node is missing; it must either
read the runtime scene tree or boot the game.

## Expected fix
Either point `get_node` at an existing node, or add the missing node to
`Main.tscn`. Both are acceptable.
