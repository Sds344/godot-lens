"""arms.py — the three experimental arms.

The arms differ in exactly one dimension: which **evidence** the agent can obtain.
Everything else — task text, project, model, round limit, permitted tools-is
identical, because a difference in any of those would be confounded with the
treatment.

The mistake this file is written to avoid is a strawman control. It is tempting to
give the control arm no feedback, which guarantees the treatment wins and proves
nothing. So:

    A  blind     the game can be run, but nothing is explained
    B  native    the FULL Godot toolchain: run it, read its errors, run it again
    C  lens      B plus godot-lens

**B is the comparison that matters.** C must beat B for the tooling to have
demonstrated anything.

`--allowedTools` is set identically for all arms, and `godot-lens` is reached
through Bash (it is a shell command, not a distinct Claude Code tool), so the arm
difference cannot leak through the permission configuration.
"""
from __future__ import annotations

# Tools every arm may use. `Read`/`Edit`/`Write`/`Bash` is the minimum for a
# code-editing task; adding web tools would let an agent look up Godot behaviour
# instead of observing the project, which is a different experiment.
ALLOWED_TOOLS = ["Read", "Edit", "Write", "Bash", "Glob", "Grep", "TodoWrite"]

# Arm A gets no shell. Its defining property is that it receives NO feedback, and
# the first run enforced that with a 4-turn cap instead — which measured the cap,
# not the arm: all six cells reported `turns=5` and none finished, so the arm's 0%
# was an artifact of being cut off mid-exploration.
#
# Removing Bash enforces the property structurally: the agent can read and edit as
# much as it needs, but cannot run the project, so it cannot observe the result.
# That is what "one pass, no verification" should mean.
A_TOOLS = ["Read", "Edit", "Write", "Glob", "Grep", "TodoWrite"]

# Godot needs writable config/data homes or it aborts with a bare signal 11, which
# looks like an engine bug. Every arm is told this because it is an environment
# fact, not a diagnostic capability — withholding it would make the control fail
# for reasons unrelated to observation.
_ENV_PREAMBLE = """\
The project is a Godot 4 project in `godot_project/`. The engine is on PATH as
`godot`, and it must be run from inside `godot_project/`.

Godot needs writable config directories. These are already set in the environment
for this shell (`XDG_CONFIG_HOME`, `XDG_DATA_HOME`, `XDG_CACHE_HOME`).
"""

_HEADLESS_HINT = """\
Useful commands:
  cd godot_project && godot --headless --quit-after 60      # run the game, see errors
  cd godot_project && godot --headless --check-only --script res://scripts/x.gd
  cd godot_project && godot --headless --import             # import assets if needed
"""

# Given to A. The one-shot floor: the agent is told it gets a single pass and will
# not be able to verify, so it must produce its best fix from the sources alone.
#
# It can still read files — a floor that could not read the code would measure
# nothing about the fault — but it is not given a loop. This differs from B in two
# ways at once (feedback availability AND iteration budget), so A establishes a
# floor and must never be used to argue for the value of feedback. See README.md.
_A_ONESHOT = _ENV_PREAMBLE + """
You have a SINGLE pass. Produce the complete fix now. You will not get to run the
project to check your work, and you will not get a chance to revise it, so reason
carefully from the sources before you edit. Make the change and stop.
"""

# Given to B and C. The full native loop, stated as an expectation so the control
# is a competent developer rather than a careless one.
_BC_NATIVE = _ENV_PREAMBLE + """
Work iteratively: make a change, run the project, read the output, and repeat
until the problem is genuinely fixed. You have up to 5 rounds of edit-and-verify.
Godot's own output is the authority on whether the project is broken.

""" + _HEADLESS_HINT

# Given only to C. Explains what the tools answer, not what they will say — the
# agent must still decide to use them and interpret the result.
_C_LENS = _BC_NATIVE + """
A tool is available that reports the engine's actual state, including conditions
Godot does not report itself.

  godot-lens inspect          situation report: parsed and running scene trees
  godot-lens inspect --agent  the same, compressed
  godot-lens diagnose         interprets symptoms, with impact and suggested action
  godot-lens validate         runs static, scene and runtime checks
  godot-lens api              asks the engine which classes, methods and
                              properties actually exist in this version

Example:
  GODOT_PROJECT=$(pwd)/godot_project godot-lens inspect --agent

Set GODOT_PROJECT to the `godot_project` directory (it is also inherited from the
environment). These tools report facts about the running project; they do not
change it.
"""

_SYSTEM = ("You are working inside a Godot project. Make the smallest change that "
           "genuinely fixes the stated problem. Do not weaken, delete or bypass "
           "code to make an error message disappear — the behaviour must still "
           "work. When you believe the task is complete, say so explicitly.")


def prompt(arm, task):
    """The full prompt for one (arm, task) pair."""
    if arm == "A":
        body = _A_ONESHOT
    elif arm == "B":
        body = _BC_NATIVE
    elif arm == "C":
        body = _C_LENS
    else:
        raise ValueError(f"unknown arm {arm!r}; expected A, B or C")
    return (f"{body}\n"
            "---\n"
            f"TASK: {task}\n")


def system_prompt(_arm):
    """Identical across arms by construction; the argument exists so a future
    change that makes it arm-dependent has to be written deliberately."""
    return _SYSTEM


# Turn budgets per arm. A is capped so it cannot run a verify-and-revise loop;
# B and C get enough turns to iterate freely. The cap is on harness turns, not on
# the 5 repair rounds the protocol names — one turn may batch several tool calls.
MAX_TURNS = {"A": 20, "B": 30, "C": 30}


def tools_for(arm):
    return A_TOOLS if arm == "A" else ALLOWED_TOOLS

ARMS = {
    "A": {"name": "one-shot", "lens": False,
          "description": "single pass, no verification, no iteration"},
    "B": {"name": "native", "lens": False,
          "description": "A + full native loop, stated as an expectation"},
    "C": {"name": "lens", "lens": True,
          "description": "B + godot-lens"},
}


def render_table():
    lines = ["arm  name    godot-lens  evidence",
             "---  ------  ----------  " + "-" * 46]
    for key, spec in ARMS.items():
        lines.append(f"{key}    {spec['name']:<6}  "
                     f"{'yes' if spec['lens'] else 'no ':>10}  {spec['description']}")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 2 and sys.argv[1] == "--prompt":
        print(prompt(sys.argv[2], "<task text>"))
    else:
        print(render_table())
