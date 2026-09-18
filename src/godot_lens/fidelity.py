"""Semantic-fidelity study: does the running game still say what the source said?

This is the layer that closes the gap the rest of the kit deliberately does not
touch. Everything else here reports **what the engine did**; none of it can say
whether that was *right*, because rightness needs a specification. This module
supplies one — by comparing the running game against the source-derived IR — and
that makes it a different kind of tool with a different kind of failure.

The three worlds
----------------
For a generated game the usual single "bug" splits into three facts that can
disagree independently:

    source  (Ren'Py)      what the author wrote
    IR      (story.json)  what the converter understood
    runtime (Godot)       what the player sees

An engine-healthy project can still be semantically wrong, and that failure is
invisible to every other layer in this kit: the scene tree is well-formed, the
validator is clean, and the game says the wrong thing. The concrete instance that
motivated this work — `define narrator = Character(None)` folded into the body of
a later label — produced a perfectly valid Godot project.

How agreement is decided
------------------------
The expected trace is the ordered sequence of things the IR says will be
displayed. The actual trace is the ordered sequence of display changes the
runtime produced. Agreement is an **ordered subsequence match**:

    every expected display must appear, in order, in the runtime timeline

A subsequence rather than an equality because the runtime legitimately observes
more than the IR models — a Continue button's text, an image node's position, the
same text re-observed after a repaint. Those are not divergences. What is a
divergence is an expected display that never appears, or appears out of order,
and that is exactly the class of bug the narrator case represents.

Choosing subsequence also has a cost, and it should be stated: it will not detect
a display that appears *extra* times, or content that is present but attached to
the wrong speaker when both texts also appear elsewhere in the right order. It
detects omission and reordering, which is what generated-game bugs actually look
like. Widening it needs a stronger specification, not a looser matcher.
"""
from __future__ import annotations

import json
import os

# Display kinds the IR says produce visible text, and what field carries it.
TEXT_KINDS = {
    "dialogue": "text",
    "narration": "text",
    "menu": "prompt",
}


def normalise(text):
    """Make two spellings of the same display compare equal.

    The converter records Ren'Py source text as Python/JSON escapes
    (`"...\\"hello\\"."`), while the engine renders the literal characters. A
    byte comparison would report a divergence on every line containing a quote,
    which would make the tool useless for exactly the projects that need it.

    Doubled/escaped quotes are collapsed and whitespace is squeezed. This is the
    only leniency in the comparison, and it is deliberate: it forgives encoding,
    never content.
    """
    if text is None:
        return ""
    s = str(text)
    s = s.replace('\\"', '"').replace("\\'", "'").replace("\\n", "\n")
    s = " ".join(s.split())
    return s


def expected_trace(story):
    """The ordered displays the IR promises, with source locations.

    Walks labels in a deterministic order and descends into every nested body —
    `if` branches, menu choices, menu clauses. Missing a nesting level here would
    silently shrink the expectation set, which would make the comparison weaker
    without ever failing, so every container the IR uses is handled explicitly.
    """
    out = []
    for label in story.get("labels", []):
        _walk_body(label.get("body", []), str(label.get("name", "")), out)
    return out


def _walk_body(body, label_name, out):
    for node in body:
        if not isinstance(node, dict):
            continue
        kind = str(node.get("kind", ""))
        field = TEXT_KINDS.get(kind)
        if field and node.get(field) is not None:
            text = normalise(node.get(field))
            if text:
                out.append({
                    "kind": "narration" if kind == "narration" else kind,
                    "text": text,
                    "speaker": normalise(node.get("speaker")),
                    "label": label_name,
                    "source": node.get("source", {}),
                })
        # Recurse into every container shape the IR defines. `branches` carries
        # a list of {condition, body}; `choices`/`clauses` carry nested bodies.
        for branch in node.get("branches", []) or []:
            if isinstance(branch, dict):
                _walk_body(branch.get("body", []) or [], label_name, out)
        _walk_body(node.get("else_body", []) or [], label_name, out)
        for key in ("choices", "clauses"):
            for choice in node.get(key, []) or []:
                if isinstance(choice, dict):
                    _walk_body(choice.get("body", []) or [], label_name, out)
        _walk_body(node.get("body", []) or [], label_name, out)


# Node classes that carry story text. Interactive controls are excluded on
# purpose: a "Continue" button sits below the dialogue in a typical layout, so
# including it made the button's label the "body text" of every frame. A button
# is an affordance, not narration, and treating it as content is how a checker
# starts reporting divergences that are not there.
STORY_TEXT_TYPES = {
    "Label", "RichTextLabel", "TextEdit", "CodeEdit", "LineEdit",
}


def actual_trace(trace):
    """The ordered displays the runtime produced.

    Only story text is considered. Which node is the "content" label and which is
    the "speaker" plate is derived from layout: for a text-over-text layout the
    node drawn lower on screen carries the body text.
    """
    text_events = [e for e in trace.get("events", [])
                   if e.get("kind") == "text"
                   and normalise(e.get("to"))
                   and e.get("type") in STORY_TEXT_TYPES]
    out = []
    for e in text_events:
        out.append({
            "text": normalise(e.get("to")),
            "path": e.get("path"),
            "position": e.get("position", ""),
            "type": e.get("type"),
            "frame": e.get("frame"),
        })
    return out


def _position_key(position):
    """Sort key for a Vector2 rendered as "(x, y)" — larger y is lower on screen."""
    try:
        inner = str(position).strip("()")
        _x, y = inner.split(",")
        return float(y)
    except (ValueError, AttributeError):
        return 0.0


def group_by_frame(actual):
    """Pair the body text with the speaker plate within each frame.

    The IR pairs a speaker with the text it accompanies; the runtime emits them
    as two independent label changes in the same frame. Rebuilding the pairing is
    what lets speaker attribution be checked at all.

    In a conventional text-over-text layout the body text is the node with the
    LARGER y (lower on screen) and the name plate sits above it. Getting this
    backwards silently swaps body and speaker for every frame, which showed up as
    the first line of dialogue being attributed to its own text — an error that
    looks like a project bug and is not one.

    Only story-text nodes participate; a Continue button below the dialogue would
    otherwise be chosen as the body text of every frame.
    """
    frames = {}
    for item in actual:
        frames.setdefault(item["frame"], []).append(item)
    grouped = []
    for frame in sorted(frames):
        items = sorted(frames[frame], key=lambda i: _position_key(i["position"]))
        body = items[-1] if items else None
        speakers = items[:-1]
        grouped.append({
            "frame": frame,
            "text": body["text"] if body else "",
            "speaker": speakers[-1]["text"] if speakers else "",
        })
    return grouped


def speaker_display(story, speaker_id):
    """Resolve a speaker id to the name the game shows.

    The IR stores the Ren'Py variable (`e`); the runtime resolves it through
    `config["character.e"]` to the author-facing name (`Eileen`). Comparing the
    raw ids would report a speaker mismatch on every correctly translated line,
    which is the kind of noise that makes a checker get ignored.
    """
    key = normalise(speaker_id)
    if not key:
        return ""
    config = story.get("config", {}) or {}
    return normalise(config.get(f"character.{key}", key))


def executed_expected(story, probe):
    """The expected displays restricted to source spans the runtime executed.

    This is what makes the trace comparison usable on a real project. The IR
    contains every branch, but only one branch runs, so an unfiltered expectation
    reports every unexecuted alternative as "missing" — turning a correct project
    into a wall of false divergences the first time this was run.

    Filtering by the runtime's own executed spans solves that without the checker
    having to model control flow: the runtime is the oracle for which branch was
    taken, and the IR is the oracle for what that branch should display.
    """
    taken = {(str(s.get("path", "")), int(s.get("line", -1)))
             for s in (probe.get("spans") or [])}
    out = []
    for label in story.get("labels", []):
        _walk_executed(label.get("body", []), str(label.get("name", "")), taken,
                       story, out)
    return out


def _walk_executed(body, label_name, taken, story, out):
    for node in body:
        if not isinstance(node, dict):
            continue
        kind = str(node.get("kind", ""))
        src = node.get("source")
        field = TEXT_KINDS.get(kind)
        if field and isinstance(src, dict):
            key = (str(src.get("path", "")), int(src.get("line", -1)))
            if key in taken and node.get(field) is not None:
                text = normalise(node.get(field))
                if text:
                    out.append({
                        "kind": kind,
                        "text": text,
                        "speaker": speaker_display(story, node.get("speaker")),
                        "label": label_name,
                        "source": src,
                    })
        # Recurse through every container shape, exactly as the span collector
        # does. Skipping a level here would silently shrink what gets checked.
        for branch in node.get("branches", []) or []:
            if isinstance(branch, dict):
                _walk_executed(branch.get("body", []) or [], label_name, taken,
                               story, out)
        _walk_executed(node.get("else_body", []) or [], label_name, taken, story, out)
        for key in ("choices", "clauses"):
            for choice in node.get(key, []) or []:
                if isinstance(choice, dict):
                    _walk_executed(choice.get("body", []) or [], label_name,
                                   taken, story, out)
        _walk_executed(node.get("body", []) or [], label_name, taken, story, out)


def compare(expected, actual):
    """Ordered subsequence match, reporting the first real divergence.

    Returns a report rather than a boolean: an agent needs to know *which* line
    failed and *where* it came from, not merely that something is wrong.
    """
    grouped = group_by_frame(actual)
    actual_texts = [g["text"] for g in grouped]

    matched = []
    missing = []
    ai = 0
    for exp in expected:
        found = None
        for j in range(ai, len(actual_texts)):
            if actual_texts[j] == exp["text"]:
                found = j
                break
        if found is None:
            # Distinguish "never displayed" from "displayed before an earlier
            # line" — the second is a reordering, and the two need different
            # fixes. Reporting both as "missing" would hide a real distinction.
            earlier = exp["text"] in actual_texts
            missing.append({
                "text": exp["text"],
                "kind": exp["kind"],
                "speaker": exp["speaker"],
                "label": exp["label"],
                "source": exp.get("source", {}),
                "reason": ("displayed out of order" if earlier
                           else "never displayed at runtime"),
                "already_seen": earlier,
            })
        else:
            ai = found + 1
            matched.append({"text": exp["text"], "frame": grouped[found]["frame"],
                            "runtime_speaker": grouped[found]["speaker"],
                            "expected_speaker": exp["speaker"],
                            "source": exp.get("source", {})})

    speaker_mismatches = [
        m for m in matched
        if _speaker_disagrees(m["expected_speaker"], m["runtime_speaker"])
    ]

    return {
        "agree": not missing and not speaker_mismatches,
        "expected_count": len(expected),
        "matched_count": len(matched),
        "missing": missing,
        "speaker_mismatches": speaker_mismatches,
        "runtime_displays": grouped,
    }


def _speaker_disagrees(expected, actual):
    """A narration line has no speaker, so '' is correct there, not a mismatch.

    Only a *non-empty* expectation that the runtime failed to show is a real
    disagreement. Treating "" vs "" as a mismatch would fail every narration line
    and drown the signal.
    """
    exp = normalise(expected)
    act = normalise(actual)
    if exp == "":
        return False
    return exp != act


def render(report, expected, limit=12):
    lines = []
    if report["agree"]:
        lines.append(f"AGREE — {report['matched_count']}/{report['expected_count']} "
                     "expected displays observed in order.")
        return "\n".join(lines)
    lines.append(f"DIVERGED — {report['matched_count']}/{report['expected_count']} "
                 "expected displays observed in order.")
    for m in report["missing"][:limit]:
        src = m.get("source") or {}
        where = ""
        if src:
            where = f"  ({src.get('path','?')}:{src.get('line','?')})"
        lines.append("")
        lines.append(f"[MISSING] {m['kind']} in label {m['label']}{where}")
        lines.append(f"  expected: {m['text'][:110]!r}")
        lines.append(f"  reason  : {m['reason']}")
    for m in report["speaker_mismatches"][:limit]:
        lines.append("")
        lines.append(f"[SPEAKER] {m['text'][:70]!r}")
        lines.append(f"  expected speaker: {m['expected_speaker']!r}  "
                     f"runtime showed: {m['runtime_speaker']!r}")
    extra = len(report["missing"]) - limit
    if extra > 0:
        lines.append(f"\n... and {extra} more missing display(s).")
    return "\n".join(lines)
