# Fixture provenance

These files are **frozen engine output**, captured so the presentation layer can be
built and tested without running Godot.

## Why freezing output is the right move

The renderers were originally tangled into the collectors, so changing an output
format meant booting the engine. That is slow, and it makes exactly the cases that
need the most care — a project that cannot boot, a project with no git history, a
payload with no runtime section — the hardest to test, because you have to
manufacture a broken project to see them.

A frozen payload inverts that. The renderers become testable in milliseconds, in
CI, with no engine installed, and the awkward inputs become ordinary files. The
cost is that a fixture can drift from what the collector really emits, so the
provenance below is the thing that keeps it honest: each file records what
produced it, on which engine, from which project revision.

**These are test inputs, not claims about any project.** Nothing should read
`inspect-ok.json` as "the project is healthy" — it is a synthetic arrangement of
real observations, labelled as such.

## Files

| File | Represents | Derived from |
|---|---|---|
| `inspect-ok.json` | a healthy project: game booted, no findings | real capture, 2026-09-17 |
| `inspect-broken.json` | runtime errors plus a scene-layer finding | real capture, 2026-09-17 |
| `inspect-noboot.json` | **the game was never started** | real capture + the real preflight message |

## Source of each element

Captured on 2026-09-17 from `Renpy2Godot/godot_project` (a Ren'Py-to-Godot
conversion), Godot `4.5.1.stable.official.f62fdbde1`, via this project's own
collectors:

- **scene trees** — `tools/godot/scene_tree_dump.gd`, static and `--runtime`
  views. The `static 0 -> runtime 5` contrast is real: `Main.tscn` declares a
  single childless `Control` and builds its UI in `_ready()`.
- **observed displays** — `tools/godot/trace_dump.gd` output in
  `experiments/renpy-fidelity-001/trace.json`, paired by `fidelity.group_by_frame`.
  The pairing is recomputed here rather than copied so the fixture cannot encode a
  pairing rule that the code no longer uses. An earlier revision of this fixture
  did exactly that and attributed the first line of dialogue to the "Continue"
  button, because a button sits below the dialogue and won the "lowest node is the
  body text" rule.
- **findings** — `tools/godot_validate.sh --json` record shape.
- **fingerprint** — `study.fingerprint()`. The `+9 uncommitted` in the recorded
  snapshot is retained deliberately: it is the honest state of the working tree
  when the capture was taken, and a fixture that claims a clean tree would be
  demonstrating a reproducibility it did not have.
- **the no-boot message** — verbatim from the real
  `tools/main_scene_check.sh` preflight, so the wording tested is the wording
  shipped.

## Keeping these honest

`benchmarks/render_selftest.py` asserts the properties the renderers must never
violate, including that a payload with `booted: false` is rendered as *unknown*
rather than *clean*. If an element here drifts from what the collectors emit, the
renderers keep passing against a shape that no longer exists — so when a
collector's output shape changes, the fixture changes with it, and the
`PROTOCOL.md` version is what records that the shape moved.
