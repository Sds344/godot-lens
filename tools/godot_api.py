#!/usr/bin/env python3
"""godot_api.py — query the REAL Godot 4.5.1 API offline.

Why this exists: an LLM writing GDScript from memory hallucinates methods,
mixes Godot 3.x with 4.x, and guesses signatures. This tool gives it the
engine's own machine-generated API surface as ground truth, on demand, without
loading 11 MB into context.

Data source: `godot --headless --dump-extension-api-with-docs`, which dumps the
engine's complete reflection data including class/method/property/signal
descriptions. Regenerate with `tools/godot_api.sh dump`.

Usage:
  godot_api.py class CharacterBody2D     # full class: inheritance, members, docs
  godot_api.py method move_and_slide     # signature + docs, searched everywhere
  godot_api.py method CharacterBody2D.move_and_slide
  godot_api.py property velocity         # which classes expose this property
  godot_api.py signal body_entered
  godot_api.py search "screen shake"     # full-text search across all docs
  godot_api.py inherit CharacterBody2D   # just the inheritance chain
  godot_api.py enum MotionMode           # global/class enum values
  godot_api.py stats                     # what's in the dump
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from project_path import state_dir  # noqa: E402


def _default_api_path():
    """Locate the dump wherever the state directory resolved to.

    The kit directory may be read-only, in which case the dump lives under
    $GODOT_LENS_HOME or a cache dir instead. Searching several locations is
    cheap and avoids telling the user to regenerate a dump that already exists.
    """
    candidates = []
    env = os.environ.get("GODOT_API_JSON")
    if env:
        candidates.append(env)
    state = state_dir(create=False)
    if state:
        candidates.append(os.path.join(state, "api-dump", "extension_api.json"))
    candidates.append(os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..", ".tooling", "api-dump", "extension_api.json"))
    for c in candidates:
        if os.path.exists(c):
            return c
    return candidates[0] if candidates else "extension_api.json"


API_PATH = _default_api_path()


def load():
    if not os.path.exists(API_PATH):
        sys.exit(
            f"API dump not found at {API_PATH}\n"
            "Generate it with: tools/godot_api.sh dump"
        )
    with open(API_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def strip_bb(text):
    """Turn Godot's BBCode doc markup into readable plain text.

    Code spans are extracted into placeholders first. Godot's docs nest BBCode
    inside `[code skip-lint]...[/code]` (e.g. `[code skip-lint][b]bold[/b][/code]`),
    so stripping tags in a single pass corrupts the code sample. Placeholding
    keeps the span's interior verbatim.
    """
    if not text:
        return ""

    spans = []

    def _stash(m):
        spans.append(m.group(1))
        return f"\x00{len(spans) - 1}\x00"

    # Multi-language example blocks. Godot wraps examples as
    # [codeblocks][gdscript][codeblock]...[/codeblock][/gdscript][/codeblocks].
    # Capture the whole block and keep only the GDScript variant.
    def _stash_block(m):
        body = m.group(1)
        gd = re.search(r"\[gdscript\](.*?)\[/gdscript\]", body, flags=re.S)
        inner = gd.group(1) if gd else body
        inner = re.sub(r"\[/?codeblock\]", "", inner)
        spans.append("\n" + inner.strip() + "\n")
        return f"\x00{len(spans) - 1}\x00"

    text = re.sub(r"\[codeblocks\](.*?)\[/codeblocks\]", _stash_block, text, flags=re.S)
    # Godot's own XML has unclosed [codeblocks] tags in places (e.g. ConfigFile),
    # which the paired regex above cannot match. Fall back to unwrapping the
    # language/block tags individually so the example still comes through.
    text = re.sub(r"\[/?(?:codeblocks|gdscript|csharp|codeblock)\]", "", text)
    # Standalone codeblock (no language wrapper).
    text = re.sub(r"\[codeblock\](.*?)\[/codeblock\]", _stash, text, flags=re.S)
    # Inline code spans (any attributes), captured before other tags are touched.
    text = re.sub(r"\[code[^\]]*\](.*?)\[/code\]", _stash, text, flags=re.S)

    # Formatting-only tags: drop the tag, keep the content.
    text = re.sub(r"\[/?(?:b|i|u|s|br|center|codeblocks|codeblock)\]", "", text)
    text = re.sub(r"\[url=[^\]]*\]", "", text)
    text = re.sub(r"\[/?color(?:=[^\]]*)?\]", "", text)
    text = re.sub(r"\[/?font(?:=[^\]]*)?\]", "", text)
    text = re.sub(r"\[/?font_size(?:=[^\]]*)?\]", "", text)
    # Reference tags that should render as meaningful text.
    text = re.sub(r"\[method ([^\]]+)\]", r"\1()", text)
    text = re.sub(r"\[member ([^\]]+)\]", r"\1", text)
    text = re.sub(r"\[param ([^\]]+)\]", r"\1", text)
    text = re.sub(r"\[constant ([^\]]+)\]", r"\1", text)
    text = re.sub(r"\[signal ([^\]]+)\]", r"\1", text)
    text = re.sub(r"\[theme_item ([^\]]+)\]", r"\1", text)
    # Any remaining [Tag arg] keeps only the argument; bare [Tag] is dropped.
    text = re.sub(r"\[[a-z_]+ ([^\]]+)\]", r"\1", text)
    text = re.sub(r"\[/?[a-z_]+\]", "", text)
    text = re.sub(r"\[([^\]]+)\]", r"\1", text)

    # Restore code spans verbatim, wrapped in backticks.
    text = re.sub(r"\x00(\d+)\x00", lambda m: "`" + spans[int(m.group(1))] + "`", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def sig(m):
    """Render a readable method signature."""
    args = []
    for a in m.get("arguments", []):
        t = a.get("type", "Variant")
        if a.get("meta"):
            t = a["meta"]
        s = f"{a['name']}: {t}"
        if a.get("default_value") not in (None, ""):
            s += f" = {a['default_value']}"
        args.append(s)
    ret = m.get("return_value") or {}
    r = f" -> {ret.get('type', 'void')}" if ret.get("type") else ""
    flags = " ".join(
        k for k, v in (("static", m.get("is_static")), ("const", m.get("is_const")),
                       ("virtual", m.get("is_virtual")), ("vararg", m.get("is_vararg")))
        if v
    )
    prefix = f"[{flags}] " if flags else ""
    return f"{prefix}{m['name']}({', '.join(args)}){r}"


def find_class(db, name):
    for c in db["classes"]:
        if c["name"].lower() == name.lower():
            return c
    return None


def find_builtin(db, name):
    for c in db["builtin_classes"]:
        if c["name"].lower() == name.lower():
            return c
    return None


def inheritance(db, cls):
    chain, cur, seen = [], cls, set()
    while cur and cur["name"] not in seen:
        seen.add(cur["name"])
        chain.append(cur["name"])
        parent = cur.get("inherits")
        cur = find_class(db, parent) if parent else None
    return chain


def walk_types(db, cls):
    """Yield this class and every ancestor, nearest first.

    Godot's own dump only lists members *declared* on each class: `connect` is
    declared on Object, not on Timer. Any lookup that ignores the chain reports
    a false "does not exist", which is exactly the failure mode this tool
    exists to prevent.
    """
    seen = set()
    cur = cls
    while cur and cur["name"] not in seen:
        seen.add(cur["name"])
        yield cur
        parent = cur.get("inherits")
        cur = (find_class(db, parent) or find_builtin(db, parent)) if parent else None


def members(db, cls, kind):
    """All members of `kind` reachable on cls, as (declaring_class, member).

    Nearest declaration wins for overrides.
    """
    out, seen_names = [], set()
    for t in walk_types(db, cls):
        for m in t.get(kind, []):
            if m["name"] in seen_names:
                continue
            seen_names.add(m["name"])
            out.append((t, m))
    return out


def cmd_class(db, args):
    if not args:
        return "usage: godot_api.py class <ClassName>"
    name = args[0]
    c = find_class(db, name) or find_builtin(db, name)
    if not c:
        near = [x["name"] for x in db["classes"] + db["builtin_classes"]
                if name.lower() in x["name"].lower()][:8]
        msg = f"Class '{name}' not found in Godot 4.5.1."
        if near:
            msg += "\nDid you mean: " + ", ".join(near)
        return msg

    out = [f"# {c['name']}"]
    if c.get("inherits"):
        out.append("Inherits: " + " < ".join(inheritance(db, c)))
    if c.get("brief_description"):
        out.append("\n" + strip_bb(c["brief_description"]))
    if c.get("description"):
        d = strip_bb(c["description"])
        out.append("\n" + (d[:1200] + "..." if len(d) > 1200 else d))

    # Declared-here members come first; inherited ones are summarised so the
    # output stays readably small while still telling the agent they exist.
    own_methods = {m["name"] for m in c.get("methods", [])}
    own_props = {p["name"] for p in c.get("properties", [])}

    if c.get("signals"):
        out.append("\n## Signals")
        for s in c["signals"]:
            a = ", ".join(f"{x['name']}: {x.get('type','Variant')}"
                          for x in s.get("arguments", []))
            out.append(f"- {s['name']}({a})")
    if c.get("properties"):
        out.append("\n## Properties")
        for p in c["properties"]:
            out.append(f"- {p['name']}: {p.get('type','Variant')}")
    if c.get("methods"):
        out.append("\n## Methods")
        for m in c["methods"]:
            out.append(f"- {sig(m)}")

    for kind, label, extra in (("signals", "Signals", ""),
                               ("properties", "Properties", ""),
                               ("methods", "Methods", "")):
        inherited = [(t, m) for t, m in members(db, c, kind)
                     if t["name"] != c["name"]]
        if not inherited:
            continue
        # Include the declaring class: "get_node (Node)" tells the agent where
        # to look, which a bare name list does not.
        entries = [f"{m['name']} ({t['name']})" for t, m in inherited]
        shown = entries[:60]
        out.append(f"\n## Inherited {label} ({len(entries)})")
        out.append(", ".join(shown))
        if len(entries) > len(shown):
            out.append(f"... and {len(entries) - len(shown)} more "
                       f"(query one directly: godot_api.py method <Class>.<name>)")
    return "\n".join(out)


def cmd_method(db, args):
    if not args:
        return "usage: godot_api.py method <name> | <Class.name>"
    spec = args[0]
    if "." in spec:
        cname, mname = spec.rsplit(".", 1)
        c = find_class(db, cname) or find_builtin(db, cname)
        if not c:
            return f"Class '{cname}' not found in Godot 4.5.1."
        hits = [(t, m) for t, m in members(db, c, "methods") if m["name"] == mname]
        if not hits:
            avail = [m["name"] for _, m in members(db, c, "methods")]
            sim = [a for a in avail if mname.lower() in a.lower()][:8]
            r = f"Method '{mname}' not found on {cname} (checked the full inheritance chain)."
            if sim:
                r += f"\nSimilar on {cname}: {', '.join(sim)}"
            return r
    else:
        hits = []
        for c in db["classes"] + db["builtin_classes"]:
            cur = find_class(db, c["name"]) or find_builtin(db, c["name"])
            for t, m in members(db, cur, "methods") if cur else []:
                if m["name"] == spec:
                    hits.append((c, m))
        if not hits:
            sim = set()
            for c in db["classes"]:
                for _, m in members(db, c, "methods"):
                    if spec.lower() in m["name"].lower():
                        sim.add(f"{c['name']}.{m['name']}")
            r = f"Method '{spec}' not found anywhere in Godot 4.5.1."
            if sim:
                r += "\nSimilar: " + ", ".join(sorted(sim)[:10])
            return r

    out = []
    seen = set()
    for c, m in hits[:6]:
        key = (c["name"], m["name"])
        if key in seen:
            continue
        seen.add(key)
        out.append(f"# {c['name']}.{m['name']}")
        out.append(sig(m))
        if m.get("description"):
            out.append("\n" + strip_bb(m["description"])[:900])
        out.append("")
    return "\n".join(out)


def cmd_property(db, args):
    if not args:
        return "usage: godot_api.py property <name>"
    name = args[0]
    out = []
    for c in db["classes"] + db["builtin_classes"]:
        cur = find_class(db, c["name"]) or find_builtin(db, c["name"])
        if not cur:
            continue
        for t, p in members(db, cur, "properties"):
            if p["name"] == name:
                origin = "" if t["name"] == c["name"] else f"  (from {t['name']})"
                out.append(f"- {c['name']}.{name}: {p.get('type','Variant')}{origin}")
                if p.get("description"):
                    out.append("    " + strip_bb(p["description"])[:200])
                if len(out) > 40:
                    return "\n".join(out) + "\n... (truncated)"
    return "\n".join(out) if out else f"Property '{name}' not found in Godot 4.5.1."


def cmd_signal(db, args):
    if not args:
        return "usage: godot_api.py signal <name>"
    name = args[0]
    out = []
    for c in db["classes"]:
        for t, s in members(db, c, "signals"):
            if s["name"] == name:
                a = ", ".join(f"{x['name']}: {x.get('type','Variant')}"
                              for x in s.get("arguments", []))
                origin = "" if t["name"] == c["name"] else f"  (from {t['name']})"
                out.append(f"- {c['name']}.{name}({a}){origin}")
    return "\n".join(out) if out else f"Signal '{name}' not found in Godot 4.5.1."



def cmd_search(db, args):
    if not args:
        return "usage: godot_api.py search <text>"
    needle = " ".join(args).lower()
    out = []
    for c in db["classes"]:
        blob = " ".join([c.get("brief_description", ""), c.get("description", "")]).lower()
        if needle in blob:
            out.append(f"{c['name']}: {strip_bb(c.get('brief_description',''))[:110]}")
    for c in db["classes"]:
        for m in c.get("methods", []):
            if needle in (m.get("description") or "").lower():
                out.append(f"{c['name']}.{m['name']}(): {strip_bb(m.get('description',''))[:110]}")
    if not out:
        return f"No API docs matched '{needle}'."
    return "\n".join(out[:50])


def cmd_inherit(db, args):
    if not args:
        return "usage: godot_api.py inherit <ClassName>"
    c = find_class(db, args[0])
    if not c:
        return f"Class '{args[0]}' not found in Godot 4.5.1."
    return " < ".join(inheritance(db, c))


def cmd_enum(db, args):
    if not args:
        return "usage: godot_api.py enum <EnumName>"
    name = args[0]
    out = []
    for e in db.get("global_enums", []):
        if e["name"] == name:
            out.append(f"# {e['name']} (global)")
            for v in e["values"]:
                out.append(f"- {v['name']} = {v['value']}")
    for c in db["classes"]:
        for e in c.get("enums", []):
            if e["name"] == name:
                out.append(f"# {c['name']}.{e['name']}")
                for v in e["values"]:
                    out.append(f"- {v['name']} = {v['value']}")
    return "\n".join(out) if out else f"Enum '{name}' not found in Godot 4.5.1."


def cmd_stats(db):
    h = db["header"]
    return (
        f"{h['version_full_name']} (precision: {h['precision']})\n"
        f"classes:         {len(db['classes'])}\n"
        f"builtin_classes: {len(db['builtin_classes'])}\n"
        f"utility_fns:     {len(db['utility_functions'])}\n"
        f"global_enums:    {len(db['global_enums'])}\n"
        f"singletons:      {len(db['singletons'])} "
        f"({', '.join(s['name'] for s in db['singletons'][:12])}...)"
    )


COMMANDS = {
    "class": cmd_class, "method": cmd_method, "property": cmd_property,
    "signal": cmd_signal, "search": cmd_search, "inherit": cmd_inherit,
    "enum": cmd_enum,
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        return 0
    db = load()
    cmd, args = sys.argv[1], sys.argv[2:]
    if cmd == "stats":
        print(cmd_stats(db))
        return 0
    fn = COMMANDS.get(cmd)
    if not fn:
        print(f"unknown command '{cmd}'\n")
        print(__doc__)
        return 2
    print(fn(db, args))
    return 0


if __name__ == "__main__":
    sys.exit(main())
