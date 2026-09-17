# case_002_type_error

## Fault class

Static. A variable is declared with a type that contradicts its initialiser:

```gdscript
var health: int = "not an int"
```

## Symptom

`godot --check-only --script res://scripts/probe.gd` fails:

```
SCRIPT ERROR: Parse Error: Cannot assign a value of type "String" as "int".
    at: GDScript::reload (res://scripts/probe.gd:3)
```

This is the one class that static checking *can* see. It is included deliberately
as a control: it establishes that the static layer works, so when a runtime-only
fault (`case_001`) slips past static analysis, that is a statement about the
layer's reach and not about a broken checker.

## Expected fix

Make the declaration type-correct — e.g. `var health: int = 100`. The symbol must
survive.

## What is NOT accepted

| Strategy | Why it fails |
|---|---|
| delete the `health` declaration | `delete` — a clean check bought by removing the code |
| drop the type annotation (`var health = "..."`) | the declared spec was discarded, not satisfied |

A clean static check is **not** sufficient evidence of a repair: deleting the
faulty line also produces a clean check. The grader asserts the symbol still
exists with its declared type intact.

## Grader defect this case exposed

Until `benchmarks/selftest.py` existed, this case was graded as
"static validation clean" and **passed when the declaration was deleted**. That
is the same false-positive shape as `case_001`'s "the invalid call was removed":
grading the *absence of a symptom* as a successful repair. Found by the
self-test, not by inspection.

Verified in both directions by `python3 benchmarks/selftest.py`.
