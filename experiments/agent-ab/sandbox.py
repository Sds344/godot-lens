"""sandbox.py — make a lab hermetic before you trust any number that comes out of it.

This module exists because of a specific, observed failure.

The v1 A/B matrix was run with every lab in `/tmp/ab-eval/<cell>/` and no
filesystem containment.  The agent's shell could read anything.  Six of thirty
cells found their way out of the lab, and one of them recorded this thought:

    JACKPOT: `/mnt/wslg/distro/home/adminstrator/projects/Renpy2Godot/
    godot_project/data/story.json` — the reference (uncorrupted) story.json.
    Also, there's the agent-ab experiment directory with a task harness, which
    likely includes the task definition and the grader.  Let me look at
    `experiments/agent-ab/` — mutations, graders.  This will tell me exactly
    what the expected repair is.

Three independent doors were open, and none of them were the one we had already
closed:

  1. `lab/.tooling/api-dump` was a **symlink into the kit**.  Resolving it put
     the whole repository one `..` away: `cases.py`, `mutations.py`,
     `benchmarks/cases/*/EXPECTED.md` — the answer key for every case.
  2. `/tmp/ab-eval` **retained every cell's lab**, so an agent in repeat 2 could
     read repeat 1 — and, because the arms share the lab root, another arm.
  3. The agent ran `Grep` with `path="/"`.  The whole filesystem answered,
     including the WSL alias `/mnt/wslg/distro/...`, which exposed the pristine
     parent project and the sibling `Renpy2Godot` reference fixture.

Door 3 is not closable by tidying up paths.  A shell with unrestricted read is
an oracle for any answer that exists on the disk, so the only fix is to change
what exists on the disk from the agent's point of view.  That is what this
module does: it runs one cell inside a mount namespace where the lab is the
world.

Design constraints, learned the hard way:

* **Keep the network.**  The agent under test is an API client; a hermetic but
  offline sandbox measures nothing.
* **Keep the lab at its real path.**  `GODOT_PROJECT`, the transcript's absolute
  paths and the fingerprint all assume it.
* **Hide `/home`.**  It holds the kit, the sibling repositories, Claude Code's
  own history, and the pytest temp dirs that contained the uncorrupted
  reference data.  Only two read-only islands are re-mounted inside: the Node
  runtime that carries the agent CLI, and Godot itself.
* **Pass credentials as environment variables, never as a mounted file.**  The
  settings file is read by the parent process and only its `env` block crosses
  the boundary, so no token is written to a disk the agent can read.

`check()` is the part that matters.  A sandbox that has not been tested is a
comment, not a control: it probes for each of the doors above from inside the
namespace and returns the ones still open.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess

# Read-only islands that must exist inside the namespace, because the agent CLI
# and Godot live under `$HOME` while the rest of `$HOME` must stay invisible.
def _islands() -> list[str]:
    out = []
    node = shutil.which("node")
    if node:
        # .../.nvm/versions/node/<ver>/bin/node -> keep the whole version dir,
        # because the CLI resolves its own package tree relative to the binary.
        out.append(os.path.dirname(os.path.dirname(os.path.realpath(node))))
    godot = shutil.which("godot")
    if godot:
        real = os.path.realpath(godot)
        out.append(os.path.dirname(real))              # the binary itself
        out.append(os.path.dirname(godot))             # the launcher symlink
        opt = os.path.join(os.path.expanduser("~"), ".local", "opt")
        if os.path.isdir(opt):
            out.append(opt)
    # Deduplicate, keeping order, and drop anything that is not a real dir.
    seen, uniq = set(), []
    for p in out:
        if p and p not in seen and os.path.exists(p):
            seen.add(p)
            uniq.append(p)
    return uniq


# Directories the parent process needs to hand to the sandboxed command.
_SYSTEM_RO = ("/usr", "/lib", "/lib64", "/bin", "/sbin", "/etc")

# The probe tests for **content witnesses**, not for directories.
#
# The first version of this check used `os.path.exists` on the kit root and
# reported a leak on a sandbox that was in fact airtight.  `bwrap` creates the
# missing parents of every bind destination, so mounting a lab at
# `<kit>/.tooling/lab` conjures an empty `/home/.../godot-lens` directory out of
# nothing.  A path that exists is not a path that answers.
#
# So each door names a file that only exists in the real thing: the case
# definitions, an expected-repair file, the sibling's reference `story.json`,
# and Claude Code's own settings.  If the witness is readable, the door is open.
def _doors(lab: str) -> dict:
    kit = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    home = os.path.expanduser("~")
    lab = os.path.abspath(lab)
    witnesses = [
        ("answer key: the experiment's fault definitions",
         os.path.join(kit, "experiments", "agent-ab", "cases.py")),
        ("answer key: an expected-repair file",
         os.path.join(kit, "benchmarks", "cases", "case_001_node_path", "EXPECTED.md")),
        ("answer key: the fault injectors",
         os.path.join(kit, "benchmarks", "mutations.py")),
        ("reference data: the sibling Renpy2Godot project",
         os.path.join(os.path.dirname(kit), "Renpy2Godot", "godot_project", "data", "story.json")),
        ("the whole home directory, via the WSL alias",
         "/mnt/wslg/distro/home/adminstrator/projects"),
        ("the real home directory", os.path.join(home, ".claude", "settings.json")),
        ("the agent CLI's own session history",
         os.path.join(home, ".claude", "history.jsonl")),
    ]
    return {
        "witnesses": witnesses,
        "lab": lab,
        "lab_root": os.path.dirname(lab),
    }


PROBE = r"""
import json, os, sys
spec = json.loads(__SPEC__)
bad = []

# 1. No answer-bearing file that lives outside the lab may be readable.
for label, path in spec["witnesses"]:
    if os.path.exists(path):
        bad.append("%s -> %s" % (label, path))

# 2. The lab's own siblings must be invisible: a previous repeat, or the other
#    arm, is the same document read twice.
root = spec["lab_root"]
if os.path.isdir(root):
    mine = os.path.basename(spec["lab"])
    for entry in sorted(os.listdir(root)):
        if entry != mine:
            bad.append("other cells' labs -> %s" % os.path.join(root, entry))

# 3. Sanity: the lab itself must still work, or "hermetic" is a lie.
if not os.path.isfile(os.path.join(spec["lab"], "godot_project", "project.godot")):
    bad.append("harness error: the lab's own project is not visible")

print("HERMETIC" if not bad else "LEAK")
for b in bad:
    print("  leak: " + b)
"""


def available() -> bool:
    return shutil.which("bwrap") is not None


def auth_env() -> dict:
    """The `env` block of Claude Code's settings file, and nothing else.

    The token is passed through the environment so that no readable copy of it
    lands on a disk inside the namespace.
    """
    for path in (os.path.expanduser("~/.claude/settings.json"),
                 os.path.expanduser("~/.claude/settings.local.json")):
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            continue
        env = data.get("env")
        if isinstance(env, dict):
            return {str(k): str(v) for k, v in env.items()}
    return {}


def _bin_dirs() -> list[str]:
    """Directories that must be on PATH inside the namespace.

    Node carries the agent CLI; Godot lives under `$HOME/.local`.  Both are
    read-only islands, so listing them here is safe.
    """
    dirs = []
    for name in ("node", "godot"):
        found = shutil.which(name)
        if found:
            dirs.append(os.path.dirname(os.path.realpath(found)))
            dirs.append(os.path.dirname(found))
    seen, uniq = set(), []
    for d in dirs:
        if d and d not in seen and os.path.isdir(d):
            seen.add(d)
            uniq.append(d)
    return uniq


# Infrastructure variables that must cross the boundary.
#
# `--clearenv` is there to stop the parent's environment naming the kit, the
# sibling repositories, and the answer key.  It is not there to cut the network:
# this machine reaches the model API through an egress proxy at
# `https_proxy=http://127.0.0.1:<port>`, and the first hermetic run died in
# `api_retry` with `"error": "unknown"` — no HTTP status, meaning the connection
# never left — because the proxy variables had been scrubbed along with
# everything else.
#
# These names carry infrastructure, not identity or answers, so they are passed
# through explicitly.  The distinction is the point: a variable is safe to
# forward when leaking it tells the agent nothing about the task.
_INFRA_ENV = (
    "http_proxy", "https_proxy", "no_proxy",
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
    "NODE_USE_ENV_PROXY",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "NODE_EXTRA_CA_CERTS",
    "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",
)


def infra_env() -> dict:
    """Proxy and TLS settings the sandboxed process needs to reach the network."""
    return {k: os.environ[k] for k in _INFRA_ENV if os.environ.get(k)}


def base_env(lab: str) -> dict:
    """A minimal environment for one cell.

    Deliberately built from scratch rather than inherited: the parent's
    environment names the kit and the sibling projects, and `env` is the
    cheapest leak of all.
    """
    state = os.path.join(lab, ".tooling")
    home = os.path.join(state, "home")
    tmp = os.path.join(state, "tmp")
    for d in (home, tmp):
        os.makedirs(d, exist_ok=True)
    path: list[str] = []
    for candidate in [lab] + _bin_dirs() + ["/usr/local/bin", "/usr/bin", "/bin"]:
        if candidate and candidate not in path:
            path.append(candidate)
    env = {
        "HOME": home,
        "TMPDIR": tmp,
        "PATH": ":".join(path),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TERM": "dumb",
        "GODOT_PROJECT": os.path.join(lab, "godot_project"),
        "GODOT_LENS_HOME": state,
        "XDG_CONFIG_HOME": os.path.join(state, "godot_home", "config"),
        "XDG_DATA_HOME": os.path.join(state, "godot_home", "data"),
        "XDG_CACHE_HOME": os.path.join(state, "godot_home", "cache"),
        "XDG_RUNTIME_DIR": tmp,
        "GODOT_SILENCE_ROOT_WARNING": "1",
        # Godot wants a video driver even when headless; without one it aborts.
        "GODOT_AUDIO_DRIVER": "Dummy",
    }
    env.update(auth_env())
    env.update(infra_env())
    return env


def wrap(cmd: list[str], lab: str, env: dict) -> list[str]:
    """Return `cmd` wrapped so that it can only see `lab`.

    Network namespaces are left alone: the agent is an API client.  Everything
    else is unshared.
    """
    lab = os.path.abspath(lab)
    argv = [
        "bwrap",
        "--unshare-pid", "--unshare-ipc", "--unshare-uts", "--unshare-cgroup",
        "--die-with-parent",
        # `--setenv` adds to the inherited environment rather than replacing it,
        # so without this the parent's PATH, PWD and DSH_* variables cross the
        # boundary and name the kit. `env` is the cheapest leak there is.
        "--clearenv",
        # A fresh root that contains nothing but the system libraries.
        "--tmpfs", "/",
    ]
    for d in _SYSTEM_RO:
        if os.path.exists(d):
            argv += ["--ro-bind", d, d]
    for d in _islands():
        argv += ["--ro-bind", d, d]
    argv += [
        "--proc", "/proc",
        "--dev", "/dev",
        "--tmpfs", "/tmp",
        "--bind", lab, lab,          # after the tmpfs, so the lab survives it
        "--chdir", lab,
    ]
    for key in ("HOME", "TMPDIR"):
        os.makedirs(env[key], exist_ok=True)
    for key, value in sorted(env.items()):
        argv += ["--setenv", key, value]
    return argv + ["--"] + cmd


def check(lab: str, timeout: int = 60) -> list[str]:
    """Run the probe inside the namespace and return the doors still open.

    An empty list means hermetic.  Anything else is a defect in the harness,
    not in the agent under test, and should stop the run.
    """
    if not available():
        return ["bwrap is not installed: no containment at all"]
    lab = os.path.abspath(lab)
    spec = _doors(lab)
    script = PROBE.replace("__SPEC__", repr(json.dumps(spec)))
    probe = os.path.join(lab, ".tooling", "hermeticity_probe.py")
    os.makedirs(os.path.dirname(probe), exist_ok=True)
    with open(probe, "w", encoding="utf-8") as fh:
        fh.write(script)
    env = base_env(lab)
    try:
        p = subprocess.run(
            wrap(["python3", probe], lab, env),
            capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return [f"probe could not run: {exc}"]
    finally:
        try:
            os.remove(probe)
        except OSError:
            pass
    if p.returncode != 0:
        return [f"probe exited {p.returncode}: {(p.stderr or '')[-400:]}"]
    if "HERMETIC" in p.stdout:
        return []
    return [ln.strip() for ln in p.stdout.splitlines() if ln.strip().startswith("leak:")]


if __name__ == "__main__":
    import sys
    # The self-check must run where the real labs run, and the result must
    # survive the call that produced it, so it lives in the kit's own scratch
    # space.  A lab placed under `/tmp` disappears between shell invocations in
    # this harness, which makes "it passed a moment ago" unfalsifiable.
    here = os.path.dirname(os.path.abspath(__file__))
    kit = os.path.dirname(os.path.dirname(here))
    default = os.path.join(kit, ".tooling", "sandbox-selfcheck", "cell")
    target = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else default)
    os.makedirs(os.path.join(target, "godot_project"), exist_ok=True)
    with open(os.path.join(target, "godot_project", "project.godot"), "w") as fh:
        fh.write("; probe\n")
    problems = check(target)
    print(f"lab:   {target}")
    print(f"bwrap: {'yes' if available() else 'no'}")
    if problems:
        print("NOT HERMETIC")
        for prob in problems:
            print("  " + prob)
        sys.exit(1)
    print("HERMETIC - every known door is closed")
