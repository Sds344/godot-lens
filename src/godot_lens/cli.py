"""The `godot-lens` command: a stable front door over the collectors.

Design rules, in priority order:

1. **Exit codes pass through unchanged.** An agent or a CI gate branches on them,
   so a wrapper that swallows one is worse than no wrapper. Nothing here
   interprets a result.
2. **stdout stays machine-readable.** Collectors write JSON to stdout and their
   own diagnostics too; this layer adds nothing to stdout that a parser would
   have to skip.
3. **Deprecation notices go to stderr.** A notice on stdout corrupts a pipeline.
4. **Facts, interpretation and advice stay separate.** `inspect` reports state,
   `diagnose` explains it, and there is deliberately no command that decides what
   should have happened. See PROTOCOL.md.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

from . import fidelity, paths, protocol, render, study

# The distinct layers, named so the CLI surface cannot blur them by accident.
LAYER_FACTS = "observe"
LAYER_MEANING = "interpret"

# Commands that inspect a project, and therefore need one resolved up front.
# `api` and `version` only query the engine's own reflection data; `validate` and
# `setup` resolve the project themselves and accept an explicit --project.
# `render` reads a payload from a file, so it must keep working with NO project
# and NO engine present — that is the whole point of decoupling it.
_NEEDS_PROJECT = {"inspect", "diagnose", "scene", "bench"}

# Filled in by main() before dispatch. Pinning GODOT_PROJECT for every child is
# what guarantees that all collectors in one run inspect the same project.
_ENV = None


def _read_payload(path):
    """Read a protocol payload from a file, or from stdin when path is '-'.

    All three renderers consume this, so the three audiences are guaranteed to be
    describing byte-identical input. It also makes the renderers reachable with
    no engine at all, which is what lets them be built and tested against frozen
    fixtures rather than needing a live Godot run to check a layout change.
    """
    try:
        if path in (None, "-"):
            raw = sys.stdin.read()
        else:
            with open(path, encoding="utf-8") as fh:
                raw = fh.read()
    except OSError as e:
        sys.exit(f"godot-lens: cannot read payload: {e}")
    raw = raw.strip()
    if not raw:
        sys.exit("godot-lens: no payload on stdin. Pipe one in, or pass --in FILE.")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as e:
        sys.exit(f"godot-lens: payload is not valid JSON: {e}")
    if not isinstance(payload, dict):
        sys.exit("godot-lens: payload must be a JSON object.")
    return payload


def _run(cmd, env=None):
    """Run a collector, passing its stdout through untouched.

    Returns the child's exit code. A missing collector is reported as an
    environment problem (exit 2) rather than being allowed to look like a
    project finding.
    """
    try:
        p = subprocess.run(cmd, env=env)
    except FileNotFoundError:
        print(f"godot-lens: cannot run {cmd[0]!r} — not found.", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
    return p.returncode


def _preflight(env=None):
    """Resolve the project once, before any collector starts.

    Without this, a missing project surfaces as a confusing failure from inside a
    subprocess. Resolving here also means every collector in one command run
    agrees on which project is under test — the disagreement that once made every
    tool report confidently about a leftover scratch project.

    Returns an environment with GODOT_PROJECT pinned, so children cannot resolve
    a different project than the one just reported.
    """
    proj = paths.project()
    out = dict(os.environ if env is None else env)
    out["GODOT_PROJECT"] = proj
    print(f"project: {proj}", file=sys.stderr)
    return out


def _py_tool(name, args):
    return [sys.executable, paths.tool_path(name), *args]


def _sh_tool(name, args):
    return ["bash", paths.tool_path(name), *args]


# --- commands -----------------------------------------------------------------

def _run_capture(cmd, env=None):
    """Run a collector and return (exit_code, stdout+stderr).

    Used where this process must transform a collector's output rather than pass
    it through. Kept separate from `_run` so the pass-through path stays a pure
    pass-through: a wrapper that always buffers would break streaming and could
    swallow a large payload.
    """
    try:
        p = subprocess.run(cmd, env=env, capture_output=True, text=True,
                           timeout=900)
    except FileNotFoundError:
        return 2, f"cannot run {cmd[0]!r} — not found."
    except subprocess.TimeoutExpired:
        return 124, "timed out"
    return p.returncode, p.stdout + p.stderr


def cmd_inspect(args):
    """What is the situation right now? (facts only)"""
    # The collector's own default is the full JSON payload; this CLI's default is
    # the digest, because a human or an agent typing `inspect` wants orientation.
    # `--json` asks for the raw schema; `--agent` asks for the compact digest that
    # is explicitly budgeted for a context window. `--summary` stays an accepted
    # alias for the digest so existing documentation keeps working.
    argv = []
    want_payload = args.json or args.json_out or args.agent
    if not want_payload:
        argv.append("--summary")
    if args.fast:
        argv.append("--fast")
    if args.main_only:
        argv.append("--main-only")
    if args.verbose:
        argv.append("--verbose")

    if not want_payload:
        return _run(_py_tool("godot_context.py", argv), env=_ENV)

    # Renderers consume the payload rather than re-deriving anything, so all
    # three audiences describe byte-identical input.
    code, out = _run_capture(_py_tool("godot_context.py", argv), env=_ENV)
    payload = _parse_payload(out)
    if payload is None:
        # Report rather than raise: a broken project must still produce usable
        # output, because that is exactly when the reader most needs to see it.
        sys.stderr.write(out)
        sys.exit("godot-lens: the collector produced no JSON payload.")

    if args.json_out:
        try:
            with open(args.json_out, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2, ensure_ascii=False)
                fh.write("\n")
        except OSError as e:
            sys.exit(f"godot-lens: cannot write payload: {e}")

    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    elif args.agent:
        print(render.agent_digest(payload))
    return 0 if payload.get("ok") else 1


def cmd_ci(args):
    """A one-line verdict for pipeline logs, from the same payload."""
    payload = _read_payload(args.input)
    print(render.ci_summary(payload))
    return 0 if payload.get("ok") else 1


def cmd_render(args):
    """Render a payload for a human: a single self-contained HTML file.

    Requires no project and no engine — the payload is the only input — which is
    what makes the presentation layer developable and testable on its own.
    """
    payload = _read_payload(args.input)
    diagnostics = None
    if args.diagnose:
        code, out = _run_capture(_py_tool("godot_diagnose.py", ["--json"]),
                                 env=_ENV)
        try:
            diagnostics = json.loads(out)
        except json.JSONDecodeError:
            # A diagnosis that cannot be produced must not take the report down
            # with it: the observation is still worth showing.
            print("godot-lens: diagnose produced no JSON; report without it.",
                  file=sys.stderr)
            diagnostics = None

    page = render.html_report(payload, diagnostics=diagnostics,
                              source_path=args.input)
    if args.out:
        try:
            with open(args.out, "w", encoding="utf-8") as fh:
                fh.write(page)
        except OSError as e:
            sys.exit(f"godot-lens: cannot write report: {e}")
        print(f"wrote {args.out}  ({len(page)} bytes)")
    else:
        print(page)
    return 0


def _parse_payload(text):
    """Pull the JSON object out of collector output.

    The collectors print engine noise on the same stream, so the payload is
    located by its first brace rather than assumed to be the whole of stdout.
    """
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def cmd_diagnose(args):
    """What do these symptoms mean? (interpretation)"""
    argv = []
    if args.json:
        argv.append("--json")
    if args.rules:
        argv.append("--rules")
    return _run(_py_tool("godot_diagnose.py", argv), env=_ENV)


def cmd_validate(args):
    """Is the project broken? (executes the game)"""
    argv = []
    if args.layer:
        argv += ["--layer", args.layer]
    if args.frames is not None:
        argv += ["--frames", str(args.frames)]
    if args.project:
        argv += ["--project", args.project]
    if args.json:
        argv.append("--json")
    if args.verbose:
        argv.append("--verbose")
    return _run(_sh_tool("godot_validate.sh", argv))


def cmd_scene(args):
    """What is actually in this scene?"""
    argv = list(args.scene)
    if args.runtime:
        argv.append("--runtime")
    if args.all:
        argv.append("--all")
    return _run(_sh_tool("godot_scene.sh", argv), env=_ENV)


def cmd_api(args):
    """Does this symbol exist in this engine?"""
    if not args.query:
        print(cmd_api.__doc__.strip(), file=sys.stderr)
        print("\n  godot-lens api class CharacterBody2D\n"
              "  godot-lens api method move_and_slide\n"
              "  godot-lens api search \"screen shake\"", file=sys.stderr)
        return 2
    return _run(_py_tool("godot_api.py", args.query))


def cmd_bench(args):
    """Benchmark an agent, and verify the graders themselves."""
    bench = os.path.join(paths.benchmarks_dir(), "benchmark.py")
    if args.selftest:
        return _run([sys.executable,
                     os.path.join(paths.benchmarks_dir(), "selftest.py")]
                    + (["--keep"] if args.keep else []),
                    env=_ENV)
    if args.coverage:
        # Measures how many fault classes the tooling can see at all. Ordered
        # before any agent experiment: an A/B result is uninterpretable without
        # it, because "no improvement" and "the injected fault was invisible" look
        # identical in the numbers.
        return _run([sys.executable,
                     os.path.join(paths.benchmarks_dir(), "coverage.py")],
                    env=_ENV)
    argv = []
    if args.list:
        argv.append("--list")
    if args.case:
        argv += ["--case", args.case]
    if args.all:
        argv.append("--all")
    if args.make_copy:
        argv += ["--make-copy", args.make_copy]
    if args.setup:
        argv += ["--setup", args.setup]
    if args.check:
        argv += ["--check", args.check]
    if args.allow_dirty:
        argv.append("--allow-dirty")
    if args.prompt:
        argv.append("--prompt")
    if not argv:
        print(cmd_bench.__doc__.strip(), file=sys.stderr)
        print("\n  godot-lens bench --selftest          # verify the graders\n"
              "  godot-lens bench --list              # list task cases",
              file=sys.stderr)
        return 2
    return _run([sys.executable, bench] + argv)


def cmd_setup(args):
    """One-time preparation: check the engine, build the API reference."""
    argv = ["bash", os.path.join(paths.require_kit(), "setup.sh")]
    if args.project:
        argv += ["--project", args.project]
    return _run(argv)


def cmd_study(args):
    """Pin what was tested, then compare the running game against the IR."""
    kit = paths.require_kit()

    if args.action == "snapshot":
        snap = study.capture_snapshot(kit, args.name, paths.project())
        print(study.fingerprint_line(snap["fingerprint"]))
        print(f"snapshot: {study.snapshot_dir(kit, args.name)}")
        return 0

    if args.action == "fingerprint":
        fp = study.fingerprint(paths.project())
        if args.json:
            print(json.dumps(fp, indent=2, ensure_ascii=False))
        else:
            print(study.fingerprint_line(fp))
        return 0

    if args.action == "mutations":
        for name in sorted(study.MUTATIONS):
            print(f"{name:16s} {study.MUTATIONS[name]}")
        return 0

    if args.action == "run":
        project = paths.project()
        result = study.run_fidelity(
            kit, args.name, project, scene=args.scene,
            frames=args.frames, inputs=args.inputs, mutate=args.mutate)
        if args.json:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            print(study.render_result(result))
        # Non-zero on divergence, so a CI gate or an agent can branch on it.
        return 0 if result["agree"] else 1

    print("godot-lens study: choose an action", file=sys.stderr)
    return 2


def cmd_version(args):
    """What is installed, and does the API reference match the engine?"""
    kit = paths.kit_root()
    # flush=True throughout: the child below writes straight to the inherited
    # stdout, so buffered prints in this process would otherwise appear *after*
    # the subprocess's output and scramble the report.
    print(f"schema:  {protocol.describe()}", flush=True)
    print(f"kit:     {kit or '(not found — set GODOT_LENS_KIT)'}", flush=True)
    # Import lazily: godot_version() runs a subprocess, and `version` should not
    # be the command that fails when the engine is absent.
    version = paths.godot_version() if kit else None
    print(f"engine:  {version or '(not found — set GODOT or install Godot 4.x)'}",
          flush=True)
    if kit:
        return _run(_sh_tool("godot_api.sh", ["check"]))
    return 0


# --- parser -------------------------------------------------------------------

def build_parser():
    ap = argparse.ArgumentParser(
        prog="godot-lens",
        description="Observation infrastructure for game-development agents. "
                    "Reports what the engine actually did; never what it should "
                    "have done.")
    sub = ap.add_subparsers(dest="command", metavar="<command>")

    p = sub.add_parser("inspect", help="what is the situation right now? (facts)")
    p.add_argument("--json", action="store_true",
                   help="emit the raw observable-state schema instead of a digest")
    p.add_argument("--fast", action="store_true",
                   help="skip booting the game (no runtime observation)")
    p.add_argument("--main-only", action="store_true",
                   help="inspect only the main scene (faster, shallower)")
    p.add_argument("--agent", action="store_true",
                   help="compact digest budgeted for an agent's context window; "
                        "compresses observation and never invents interpretation")
    p.add_argument("--json-out", metavar="FILE",
                   help="write the raw payload to FILE (input for `render`/`ci`)")
    p.add_argument("--verbose", action="store_true",
                   help="print resolved project/engine/state paths to stderr")
    p.add_argument("--summary", action="store_true",
                   help=argparse.SUPPRESS)  # accepted alias for the default
    p.set_defaults(func=cmd_inspect)

    p = sub.add_parser("ci",
                       help="one-line verdict for pipeline logs, from a payload")
    p.add_argument("--in", dest="input", metavar="FILE",
                   help="payload file, or '-' for stdin (default: stdin)")
    p.set_defaults(func=cmd_ci)

    p = sub.add_parser(
        "render",
        help="render a payload as one self-contained HTML file (no engine needed)")
    p.add_argument("--in", dest="input", metavar="FILE",
                   help="payload file, or '-' for stdin (default: stdin)")
    p.add_argument("--out", metavar="FILE",
                   help="write here instead of stdout")
    p.add_argument("--diagnose", action="store_true",
                   help="include the rule-bound diagnosis section")
    p.set_defaults(func=cmd_render)

    p = sub.add_parser("diagnose", help="what do these symptoms mean? (meaning)")
    p.add_argument("--json", action="store_true")
    p.add_argument("--rules", action="store_true", help="list the rule catalog")
    p.set_defaults(func=cmd_diagnose)

    p = sub.add_parser("validate", help="is the project broken? (runs the game)")
    p.add_argument("--layer", choices=["all", "static", "scene", "runtime"])
    p.add_argument("--frames", type=int)
    p.add_argument("--project")
    p.add_argument("--json", action="store_true")
    p.add_argument("--verbose", action="store_true")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("scene", help="what is actually in this scene?")
    p.add_argument("scene", nargs="*", help="scene path(s), or none with --all")
    p.add_argument("--runtime", action="store_true",
                   help="what exists after _ready(), not what the .tscn declares")
    p.add_argument("--all", action="store_true")
    p.set_defaults(func=cmd_scene)

    p = sub.add_parser("api", help="does this symbol exist in this engine?")
    p.add_argument("query", nargs="*", help="e.g. class CharacterBody2D")
    p.set_defaults(func=cmd_api)

    p = sub.add_parser("bench", help="benchmark agents; verify the graders")
    p.add_argument("--selftest", action="store_true",
                   help="verify the graders reject reward hacking")
    p.add_argument("--coverage", action="store_true",
                   help="measure which fault classes the tooling can see at all")
    p.add_argument("--keep", action="store_true", help="keep the selftest lab")
    p.add_argument("--list", action="store_true")
    p.add_argument("--case")
    p.add_argument("--all", action="store_true")
    p.add_argument("--make-copy", metavar="DIR")
    p.add_argument("--setup", metavar="DIR")
    p.add_argument("--check", metavar="DIR")
    p.add_argument("--allow-dirty", action="store_true")
    p.add_argument("--prompt", action="store_true")
    p.set_defaults(func=cmd_bench)

    p = sub.add_parser("setup", help="one-time preparation")
    p.add_argument("--project")
    p.set_defaults(func=cmd_setup)

    p = sub.add_parser(
        "study",
        help="pin what was tested, and check that the running game still says "
             "what the source said")
    p.add_argument("action",
                   choices=["snapshot", "run", "fingerprint", "mutations"],
                   help="snapshot: record the project fingerprint; "
                        "run: compare runtime against the IR; "
                        "fingerprint: print it; mutations: list the defects the "
                        "comparator is verified against")
    p.add_argument("--name", default="renpy-fidelity-001",
                   help="study name (a directory under experiments/)")
    p.add_argument("--scene", default="res://Main.tscn")
    p.add_argument("--frames", type=int, default=200)
    p.add_argument("--inputs", type=int, default=30)
    p.add_argument("--mutate", choices=sorted(study.MUTATIONS),
                   help="deliberately corrupt the EXPECTED trace, to verify the "
                        "comparator can catch that class of defect")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_study)

    p = sub.add_parser("version", help="schema, kit, engine, and API match")
    p.set_defaults(func=cmd_version)

    return ap


def main(argv=None):
    global _ENV
    ap = build_parser()
    args = ap.parse_args(argv)
    command = getattr(args, "command", None)
    if not command:
        ap.print_help()
        return 0
    _ENV = os.environ.copy()
    if command in _NEEDS_PROJECT:
        _ENV = _preflight(_ENV)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
