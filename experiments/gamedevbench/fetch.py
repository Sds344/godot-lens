"""fetch.py — download chosen GameDevBench tasks, and keep the answer key apart.

The benchmark ships each task as its own zip under `tasks/`, and the reference
solution as a *separate* zip under `tasks_gt/`.  That separation is deliberate —
the authors say the zips exist "specifically to prevent data leakage" — so this
script preserves it on disk:

    .tooling/gdb-tasks/<task>/task/     <- what the agent may see (tests stripped later)
    .tooling/gdb-tasks/<task>/gt/       <- reference solution, never mounted into a lab
    .tooling/gdb-tasks/<task>/<x>.zip   <- the archives as downloaded

Nothing here is committed; the zips carry binary assets up to tens of megabytes.
`data/tasks.txt` records which tasks were chosen and why, so the selection is
reproducible even though the payload is not.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
DEST = os.path.join(REPO, ".tooling", "gdb-tasks")
RAW = "https://raw.githubusercontent.com/waynchi/gamedevbench/main"


def _download(url: str, dest: str, tries: int = 3) -> int:
    last = None
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=120) as r:
                blob = r.read()
            if not blob.startswith(b"PK"):
                raise ValueError(f"not a zip ({len(blob)} bytes): {blob[:40]!r}")
            with open(dest, "wb") as fh:
                fh.write(blob)
            return len(blob)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            last = exc
    raise RuntimeError(f"{url}: {last}")


def _unpack(zip_path: str, into: str) -> None:
    if os.path.isdir(into):
        shutil.rmtree(into)
    os.makedirs(into, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(into)


def fetch_one(task: str) -> tuple[str, str]:
    """Return (task, status). Downloads both zips and unpacks them."""
    root = os.path.join(DEST, task)
    os.makedirs(root, exist_ok=True)
    for kind, sub in (("task", "tasks"), ("gt", "tasks_gt")):
        zip_path = os.path.join(root, f"{kind}.zip")
        if not os.path.exists(zip_path):
            size = _download(f"{RAW}/{sub}/{task}.zip", zip_path)
        else:
            size = os.path.getsize(zip_path)
        _unpack(zip_path, os.path.join(root, kind))
        # The archive nests everything under one top directory; flatten it so
        # callers get <root>/task/project.godot rather than two levels of search.
        inner = os.path.join(root, kind, sub, task)
        if os.path.isdir(inner):
            tmp = os.path.join(root, kind + ".tmp")
            shutil.rmtree(tmp, ignore_errors=True)
            shutil.move(inner, tmp)
            shutil.rmtree(os.path.join(root, kind), ignore_errors=True)
            shutil.move(tmp, os.path.join(root, kind))
    has = os.path.isfile(os.path.join(root, "task", "project.godot"))
    return task, ("ok" if has else "MISSING project.godot after unpack")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("tasks", nargs="*", help="task ids, e.g. task_0152")
    ap.add_argument("--from-file", default=os.path.join(HERE, "data", "tasks.txt"),
                    help="read task ids from this file (one per line, # comments)")
    ap.add_argument("--jobs", type=int, default=8)
    args = ap.parse_args()

    tasks = list(args.tasks)
    if not tasks and os.path.exists(args.from_file):
        with open(args.from_file, encoding="utf-8") as fh:
            for line in fh:
                line = line.split("#")[0].strip()
                # data/tasks.txt is `<task> <passed> <category> <reason>`; only
                # the first field is the task id.
                if line:
                    tasks.append(line.split()[0])
    if not tasks:
        raise SystemExit("fetch: no tasks given and no data/tasks.txt to read")

    bad = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        for task, status in pool.map(fetch_one, tasks):
            print(f"  {task:<12} {status}")
            if status != "ok":
                bad.append(task)
    print(f"\nfetched {len(tasks) - len(bad)}/{len(tasks)} into {os.path.relpath(DEST, REPO)}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
