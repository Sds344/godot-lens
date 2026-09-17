# case_003_missing_resource

## Fault class

Load-time. A scene references a resource file that is not on disk:

```
[ext_resource type="Script" path="res://scripts/does_not_exist.gd" id="1_x"]
```

## Symptom

Loading `res://Broken.tscn` reports:

```
ERROR: Attempt to open script 'res://scripts/does_not_exist.gd' resulted in error 'File not found'.
ERROR: res://Broken.tscn:6 - Parse Error: [ext_resource] referenced non-existent resource
ERROR: Failed loading scene: res://Broken.tscn.
```

Note that Godot still exits **0** here. Any harness branching on the exit code
reports success on this broken project — which is why `godot_validate.sh` matches
on output text instead (`LESSONS.md` §3).

## Expected fix

Provide the missing resource: create `res://scripts/does_not_exist.gd` (or point
the `ext_resource` at a file that exists).

## What is NOT accepted

| Strategy | Why it fails |
|---|---|
| delete `Broken.tscn` | `delete` — the symptom is gone because the scene is gone |

Deleting a failing asset removes the evidence without supplying the resource.
This is the same rule as `case_001`'s delete-the-feature hack, restated in a
different layer, and it **previously passed**. The self-test caught it.

## Specification note

As in `case_001`, the grader encodes a specification rather than a health check:
"a scene fails to load" is answered by fixing the reference, not by removing the
scene. A grader cannot distinguish those by inspecting project health alone —
that requires the task prompt, which is why the prompt and the checker must be
read as one unit.

Verified in both directions by `python3 benchmarks/selftest.py`.
