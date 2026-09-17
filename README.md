# godot-lens

**Runtime observability for AI coding agents working on Godot projects.**

Coding agents (Claude Code, Codex CLI, opencode and similar) cannot see the
Godot editor. They read `.gd` and `.tscn` files and infer the rest — and that
inference is wrong often enough to matter, because in Godot **the files are not
the game**.

`godot-lens` converts engine state into text and JSON an agent can read, act on
and verify. Headless: no GUI, no editor plugin, no MCP server, no screenshots.

> **Scope.** This tells an agent *what actually happened*. It does not tell it
> *what should have happened* — that needs a specification. It is a correctness
> guard, not an autonomous developer. The limits are substantial; see
> [Measured boundaries](ARCHITECTURE.md#measured-boundaries) before adopting.

## What it looks like

A scene file may declare one empty node and build its entire UI in `_ready()`:

```
$ godot-lens inspect
engine:   4.5.1.stable.official   api_dump=yes   match=True
project:  4 scenes, 1 scripts     main=res://Main.tscn
api:      971 classes
scene:    res://Main.tscn
    static : 0 children (root Control)
    runtime: 5 children
      Main :Control  [script=story_runtime.gd]
        Images :Node2D
          @Sprite2D@6 :Sprite2D
          @Sprite2D@7 :Sprite2D
        @Label@2 :Label  [text="Hello, #friend."]
        @Label@3 :Label  [text="Eileen"]
        @Button@4 :Button  [text="Continue"]
        @VBoxContainer@5 :VBoxContainer
runtime:  0 errors, 0 warnings
findings: 0
```

`0 children` versus `5 children` is the whole point. File-based analysis reports
that scene as empty. The `@Label@3` text and the two `@Sprite2D` nodes were read
from a **running game**, not inferred from a file — and note that the nodes are
*engine-named* (`@Sprite2D@6`), which is the signature of nodes that exist only
because code created them.

Exit code 0 means healthy; 1 means there is something to fix.

## A green check is not a repair

This is the result that distinguishes the project, and it is a warning rather
than a feature.

Every agent benchmark faces the same problem: **an agent optimises against
whatever you grade.** If the grade is "the error is gone", an agent can make the
error go away without fixing anything — and it will, because that is cheaper.
Both of the grader defects found in this project had exactly that shape:

| Case | The check | How it was beaten |
|---|---|---|
| `case_001` | "the invalid `get_node()` call was removed" | the script was replaced with `print("nothing to see here")` — a clean run on a project where nothing was fixed |
| `case_002` | "static validation is clean" | the faulty declaration was deleted — a clean check bought by removing the code |

Both were graded as **passes on a project where nothing was repaired**. A
benchmark that can pass a broken project is worse than no benchmark, because it
manufactures confidence.

So the graders are treated as code under test:

```bash
$ python3 benchmarks/selftest.py
scenarios: 12   hacks attempted: 6   hacks rejected: 6
All graders behaved as specified.
```

Six named attack strategies — redirect the lookup, swallow the error, delete the
feature, mention the fix in a comment — must all be rejected, and the real repair
must be accepted. The first defect was found by reading the code; the second only
by writing this self-test. "We reject reward hacking" is a runnable claim here,
not a sentence in a document.

The generalisable rule: **a negative check ("the error is gone") is not a
positive check ("the thing works")**, and the gap between them is where reward
hacking lives.

## The tools

One front door, so an agent has three stable commands instead of five file paths:

```bash
godot-lens inspect     # what is the situation right now?  (facts)
godot-lens diagnose    # what do these symptoms mean?      (meaning)
godot-lens validate    # is the project broken?            (runs the game)
godot-lens scene       # what is actually in this scene?
godot-lens api         # does this symbol exist in this engine?
godot-lens bench       # benchmark agents; verify the graders
```

`./godot-lens` works from a fresh clone with no installation. `uv tool install .`
(or `pip install --user .`) provides the same command on `PATH`.

**Facts, meaning and advice are separate commands on purpose.** `inspect` reports
state, `diagnose` explains it, and neither decides what *should* have happened —
that needs a specification, which is not this tool's to invent. See
[`PROTOCOL.md`](PROTOCOL.md).

| Command | Underlying tool | Answers |
|---|---|---|
| `inspect` | `tools/godot_context.py` | "What is the situation right now?" — one call, ~550 tokens |
| `diagnose` | `tools/godot_diagnose.py` | "What do these symptoms *mean*?" |
| `validate` | `tools/godot_validate.sh` | "Is the project broken?" — three layers |
| `scene` | `tools/godot_scene.sh` | "What is actually in this scene?" |
| `api` | `tools/godot_api.py` | "Does this method exist in this engine?" |

### Diagnosis turns state into consequences

```
$ python3 tools/godot_diagnose.py
[ERROR] COLLISION_SHAPE_MISSING  res://Player.tscn node=Shape
  meaning: CollisionShape2D has no Shape resource assigned.
  impact : This node contributes no collision at all. Its parent physics body
           cannot detect or block anything, so characters pass through
           geometry or fall out of the world.
  action : Assign a Shape2D (RectangleShape2D / CircleShape2D / CapsuleShape2D).
```

Severity is ranked by **consequence**, not by the editor's icon colour. The
editor marks an unused variable and a shape-less collider identically; for an
agent they are not remotely equal.

### Validation in three layers

```bash
tools/godot_validate.sh                 # all layers
tools/godot_validate.sh --layer static  # fastest: parse and type only
tools/godot_validate.sh --json          # one JSON object per finding
```

| Layer | Catches | Blind to |
|---|---|---|
| static | parse errors, type mismatches, unknown identifiers/functions — all in one pass | warnings, node paths |
| scene | missing `ext_resource`, unknown `type=`, malformed `.tscn` | bad property names, bad node paths, logic |
| runtime | real errors, `push_error`/`push_warning`, invalid node paths, null derefs | code not reached within N frames |

### API knowledge from the engine itself

```bash
tools/godot_api.sh dump     # generate from the installed binary
tools/godot_api.sh check    # does it match the installed engine?
```

```bash
python3 tools/godot_api.py class CharacterBody2D
python3 tools/godot_api.py method CharacterBody2D.move_and_slide
python3 tools/godot_api.py search "interpolate"
```

The reference is generated by `godot --dump-extension-api-with-docs`, so it
**cannot disagree with the engine you actually run** — no version skew, no
invented methods. 11 MB of reflection data is queried on demand, never loaded
into context. Lookups resolve the full inheritance chain: `connect` is declared
on `Object`, so `godot_api.py method Timer.connect` finds it.

## In CI, without an agent

The tools do not require an AI to be useful. `godot_validate.sh` already exits
non-zero on findings and emits one JSON object per finding, so it drops into any
pipeline with no adapter:

```yaml
- name: Godot project health
  run: |
    cd godot-lens
    bash tools/godot_validate.sh --json
```

Findings are structured, so a CI job can annotate a diff instead of scraping a
log:

```json
{"layer":"scene","file":"res://Player.tscn","line":6,"message":"ERROR: ..."}
```

Two caveats matter more in CI than anywhere else, because a pipeline that goes
green is trusted:

- **Godot's exit code is not a health signal.** It returns `0` even when a scene
  fails to load fatally. `godot_validate.sh` matches on output text for this
  reason, and a CI step that calls `godot` directly will report false success.
- **A green run proves the executed paths are healthy, not that the project is
  correct.** A fault in an unreached branch is invisible to both static checking
  and a headless boot. Do not read a passing pipeline as a correctness proof.

## Install

Clone or copy the directory next to (or inside) your Godot project:

```
your-game/
├── project.godot
└── godot-lens/
```

Then:

```bash
cd godot-lens
python3 tools/godot_context.py --summary
```

### Requirements

- Godot 4.x on `PATH` (developed and verified against 4.5.1)
- `python3`
- `bash`

### Environment variables

| Variable | Purpose |
|---|---|
| `GODOT_PROJECT` | Path to the Godot project (a `project.godot` path also works) |
| `GODOT_LENS_HOME` | Where to keep the API dump and Godot's XDG state |
| `GODOT` | Path to the Godot binary (default: `godot` on `PATH`) |
| `GODOT_API_JSON` | Path to an existing API dump, skipping discovery |

**The state directory matters if the install location is not writable**
(system-wide, container, shared or read-only). Godot aborts with a bare
`signal 11` when it cannot write its config dirs, which looks like an engine
bug. Resolution order: `$GODOT_LENS_HOME`, then `<lens>/.tooling` if writable,
then a cache directory. When none works it says so:

```bash
export GODOT_LENS_HOME=/var/tmp/godot-lens
```

Project discovery scores candidates rather than taking the first match, because
silently resolving to the wrong project makes every tool report confidently
about something nobody asked about. A directory named `godot_project` is
preferred; hidden directories and `test/`, `vendor/`, `node_modules/` are
penalised.

## Benchmarks

Measure whether an agent can **find and fix** a real fault — not whether a model
can write GDScript from memory.

```bash
python3 benchmarks/benchmark.py --list
python3 benchmarks/benchmark.py --make-copy /tmp/lab
python3 benchmarks/benchmark.py --case case_001_node_path --setup /tmp/lab
python3 benchmarks/benchmark.py --all --check /tmp/lab
```

Three fault classes: runtime node path, static type error, missing resource.

**The graders are themselves tested.** Each case is verified in both directions
*and* against the reward-hacks that have historically beaten it:

```bash
python3 benchmarks/selftest.py
```

```
scenarios: 12   hacks attempted: 6   hacks rejected: 6
All graders behaved as specified.
```

The hacks are named strategies in `benchmarks/repairs.py` — redirect the lookup,
swallow the error, delete the feature, mention the fix in a comment. This exists
because the benchmark has twice graded a *silenced symptom* as a successful
repair:

- `case_001` passed when the script contained no `get_node()` call at all, so
  replacing the feature with a `print()` statement was graded as "the invalid
  call was removed";
- `case_002` passed when the faulty declaration was deleted, because "static
  validation is clean" is also true of a project with the code removed.

Both were false **positives** — a pass on a project where nothing was fixed. One
was found by inspection, the other only by the self-test. A benchmark that can
pass a broken project is worse than no benchmark, because it manufactures
confidence.

`--make-copy` builds a self-contained lab: the tooling plus a copy of your
project, with its own fresh `.git`. The repo matters — without one, an agent
walking up the tree can resolve its root to an enclosing repository and edit the
wrong files. This happened during development. `--setup` refuses to inject a
fault into a lab with uncommitted changes, because stacked faults make a grade
describe a project the case does not contain.

**Grade on observed behaviour, never on string presence** — and note that a clean
health check is not evidence of a repair either. See `PROTOCOL.md` for the JSON
contract every consumer reads, and `benchmarks/cases/*/EXPECTED.md` for what each
case does and does not accept.

## Status and limitations

Early-stage, verified against Godot 4.5.1.

What it does well: engine-accurate API knowledge, runtime state as a
first-class view, three-layer validation, and diagnostics that explain impact.

What it cannot do, and you should know before relying on it:

- **Only executed code is observable.** A fault in an unreachable branch is
  invisible to both static analysis and a headless boot. A green run proves the
  executed paths are healthy, not that the project is correct. For a branching
  game most content may be outside the boot path.
- **Editor warnings are unreachable headlessly.** `--check-only` says nothing
  about them, and there is no known substitute.
- **Invalid property names are silently ignored** by a headless load.
- **It cannot judge intent.** A project can be perfectly healthy and still be
  semantically wrong — the failure mode that matters most for code generators
  and converters.
- **The diagnostic rule set is small** (four node rules plus scene and runtime
  checks). The structure is the contribution; the content is a starting point.
- **The runtime view is a snapshot, not a recording.** It is the tree after `N`
  frames, so anything created and freed within those frames is invisible. This
  is the root cause of the coverage limit above, and the first thing a trace view
  would fix.

Read [`LESSONS.md`](LESSONS.md) for the failure modes behind these limits,
including a live agent run that **redirected a lookup to an unrelated node,
declared success, and passed the first version of the grader** — the single most
instructive result from this project.

## Documentation

- [`PROTOCOL.md`](PROTOCOL.md) — the observable-state JSON contract
  (`godot-lens/observation` v`0.1`), its observation model, and the
  absent-vs-null rules every consumer depends on
- [`ROADMAP.md`](ROADMAP.md) — what is next, what is deliberately deferred, and
  the boundaries that let two agents work in parallel safely
- [`ARCHITECTURE.md`](ARCHITECTURE.md) — layers, boundaries, design principles
- [`LESSONS.md`](LESSONS.md) — findings, mistakes, open problems

## License

MIT. See [`LICENSE`](LICENSE).
