#!/usr/bin/env python3
"""annotate.py — turn validator findings into GitHub Actions annotations.

Why this is a separate file rather than an inline heredoc
--------------------------------------------------------
The first version of the CI workflow embedded this logic in a YAML `run:` block.
That is a bad place for it, for two reasons that both cost real time:

* Python heredocs inside YAML `run: |` blocks are indentation-sensitive in two
  languages at once. The block silently mis-parsed, and the pipeline would have
  annotated nothing while still exiting 0 — a check that reports success by doing
  nothing is exactly the failure mode this project exists to prevent.
* Logic in a workflow file cannot be run or tested outside CI. Here it can:
  `python3 benchmarks/annotate.py <file>` works locally on a captured log.

Usage:
  tools/godot_validate.sh --json | tee /tmp/validate.txt
  python3 benchmarks/annotate.py /tmp/validate.txt

Exit code: 0 always. Annotation is a reporting step — the validator's own exit
code is what decides pass or fail, and a reporting tool must not be able to turn a
failing build green or a green build red.
"""
from __future__ import annotations

import json
import os
import sys

# GitHub truncates annotation messages; keeping under this avoids a silently cut
# message that reads as if the finding ended mid-sentence.
MAX_MESSAGE = 900


def findings(path):
    """Parse the validator's JSONL output.

    The validator writes human-readable text and structured records to the same
    stream, so only lines that are a JSON object and are not the summary record
    count as findings. Anything unparsable is skipped rather than treated as a
    finding: a malformed record is a bug in the reporter, not a project defect,
    and inventing an annotation from it would be a false accusation.
    """
    if not path or not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not (line.startswith("{") and '"summary"' not in line):
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict):
                out.append(rec)
    return out


def annotate(records, out=None):
    """Emit GitHub workflow commands, one per finding."""
    out = out or sys.stdout
    for rec in records:
        layer = str(rec.get("layer", "?"))
        message = str(rec.get("message", "")).replace("\n", " ").strip()
        message = " ".join(message.split())
        if len(message) > MAX_MESSAGE:
            message = message[:MAX_MESSAGE] + " …(truncated)"
        # The layer is carried in the message because a single finding with no
        # layer context is hard to act on: "runtime" and "scene" failures need
        # different fixes.
        body = f"[{layer}] {message}" if message else f"[{layer}] (no message)"
        path = rec.get("file") or ""
        line = rec.get("line")
        if path and isinstance(line, int):
            print(f"::error file={path},line={line}::{body}", file=out)
        elif path:
            print(f"::error file={path}::{body}", file=out)
        else:
            print(f"::error::{body}", file=out)


def main(argv):
    path = argv[1] if len(argv) > 1 else None
    if path is None:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    records = findings(path)
    if not records:
        # Say so explicitly. Silence here is indistinguishable from "the
        # annotator failed to run", and a reader should not have to guess which.
        print(f"annotate: no findings parsed from {path}", file=sys.stderr)
        return 0
    annotate(records)
    print(f"annotate: {len(records)} finding(s) annotated", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
