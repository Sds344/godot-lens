# Convey the converter's own severity instead of flattening every translator warning to `info`

`godot_diagnose.py` classifies any runtime `push_warning` whose text contains
`renpy2godot:` as a single rule:

```python
if "renpy2godot:" in w:
    code = "CONVERTER_FIDELITY_GAP"
    sev = "info"
```

Every such warning becomes `info`, regardless of what was actually lost. That
means the two situations below are indistinguishable in the output:

```
WARNING: renpy2godot: unsupported node dialogue_attributes   # a line of dialogue was deleted
WARNING: renpy2godot: unsupported node with                 # a transition is a plain cut
```

Both render as:

```
[INFO] CONVERTER_FIDELITY_GAP
  impact: A construct was not fully translated; behaviour may differ from the Ren'Py original.
```

## Why this matters

Observed on a real conversion. A script containing

```renpy
e happy "Hello there!"
```

produced a Godot project that `godot-lens` reported as completely healthy —
`0 error, 0 warning`, all three validation layers CLEAN — while the game was
**missing that line of dialogue**. The only trace anywhere in the tooling was
this one `info`-level note, whose wording ("behaviour may differ") reads like
polish loss.

An agent asked to verify the project therefore concludes it is fine. That is the
failure mode this project exists to prevent: the run was healthy and the output
was wrong.

Ranking by consequence is exactly what this layer does for node rules — a
shape-less collider is an `error` while an unused variable would be `info` — but
that reasoning is not applied to the converter's own output.

## Concrete proposal

The converter embeds a machine-readable severity in its diagnostic records
(`error` / `warning` for the construct itself), and the runtime warning is the
only channel currently crossing into the engine. Two options:

1. **Structured channel (preferred).** Have the runtime emit the fidelity gap as
   JSON (or have `godot_context.py` read the converter's `diagnostics.json` when
   present) so `godot_diagnose.py` can rank it with real information rather than
   pattern-matching a string prefix.
2. **Cheap interim.** Keep the prefix rule but split it: map the construct name
   to a severity table, and default unknown constructs to `warning` rather than
   `info`. Silently dropping content should never be reported at the lowest
   level.

## Scope note

This is **not** a request to make godot-lens judge translation correctness. It
cannot: it has no access to the source, and `ARCHITECTURE.md` already states
that limitation correctly ("An observability layer cannot judge intent").

The ask is narrower: do not discard severity information that the toolchain
already has, and do not render content loss as the least important severity.

## Reproduction

A Godot project generated from a Ren'Py script containing a dialogue line with
attributes (`e happy "text"`), then:

```bash
python3 tools/godot_diagnose.py
```

Expected: a finding that conveys content was lost.
Actual: `[INFO] CONVERTER_FIDELITY_GAP`, indistinguishable from a missing
transition, with the summary reading `0 error, 0 warning`.
