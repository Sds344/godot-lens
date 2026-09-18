"""Renderers: three audiences, one source of truth.

The three consumers of the observable-state schema are different in kind, not
just in taste, and conflating them is how a tool ends up serving none of them.

    agent   a compact digest inside a limited context window
    ci      a machine-parseable verdict, with annotations attached to a diff
    human   a single self-contained page, for review and for a screenshot

Design rules, in priority order
-------------------------------
1. **A renderer interprets nothing.** It may compress, order and format what the
   payload already says. It must not turn an observation into a judgement. The
   one exception is advice that a diagnostic rule already carries explicitly,
   which is labelled as such — a rule-bound action, not a suggestion.
2. **"Not observed" is never rendered as "healthy."** A project whose game never
   booted has *unknown* runtime behaviour. A digest that shows "0 errors" for it
   is actively misleading, and that case is a supported input here rather than an
   edge case, because it is exactly the state an agent is most likely to meet.
3. **Every renderer is a pure function of the payload.** No engine calls, no
   filesystem reads, no subprocesses. That is what makes them testable against
   frozen fixtures instead of requiring a live Godot run to check a layout change.

Why this module exists at all: the renderers were previously tangled into the
collectors, so changing an output format meant running the engine. Building them
against fixtures is both faster and the only way to test the un-runnable cases,
which are precisely the ones that need care.
"""
from __future__ import annotations

import html
import json

# Rough bytes-per-token for English/mixed prose and code. Deliberately crude: it
# exists to make the budget visible, not to be exact.
BYTES_PER_TOKEN = 4


def estimate_tokens(text):
    return max(1, len(text.encode("utf-8")) // BYTES_PER_TOKEN)


def _fingerprint_line(payload):
    fp = _as_dict(_as_dict(payload.get("project")).get("fingerprint"))
    if not fp:
        return None
    commit = fp.get("project_commit_short") or "(no git)"
    dirty = (f" +{fp.get('project_dirty_files')} uncommitted"
             if fp.get("project_dirty") else " clean")
    return f"{commit}{dirty}"


def _as_list(value):
    """Coerce to a list, because a partial or malformed payload is a supported input.

    A renderer that raises on an unexpected shape is worse than one that renders
    less: the caller loses the observation entirely, and the observation is
    exactly what is needed when something upstream is misbehaving.
    """
    return value if isinstance(value, list) else []


def _as_dict(value):
    return value if isinstance(value, dict) else {}


def runtime_status(payload):
    """What is actually known about runtime behaviour.

    Three states, not two. Collapsing "could not run" into "no errors" is the
    most damaging thing a digest can do, because it reads as a clean bill of
    health for a project nothing was learned about.
    """
    rt = _as_dict(payload.get("runtime"))
    if rt.get("booted") is False:
        return "unknown"
    if rt.get("error_count"):
        return "errors"
    return "clean"


# --- agent digest -------------------------------------------------------------

def agent_digest(payload, budget_tokens=700):
    """Compact, factual orientation for an agent. Never interprets.

    Structure separates observation from explanation so a reader can tell which
    is which: `observed` is what the engine did, `rule-bound advice` is text a
    named rule already carries. Nothing in between is invented here.
    """
    header = []
    engine = _as_dict(payload.get("engine"))
    project = _as_dict(payload.get("project"))
    header.append(f"project:   {project.get('main_scene') or '?'}"
                  f"   ({len(_as_list(project.get('scenes')))} scenes,"
                  f" {len(_as_list(project.get('scripts')))} scripts)")

    fp = _fingerprint_line(payload)
    header.append(f"engine:    {engine.get('installed') or '?'}"
                  f"   api_match={engine.get('api_matches_engine')}")
    if fp:
        # Answers "which version was measured", which a result is meaningless
        # without.
        header.append(f"version:   {fp}")
    if not project.get("import_cache_present", True):
        # A fact about the engine, not advice. An unimported project makes
        # ResourceLoader.exists() false for files that are on disk, so a texture
        # reading as null is a build-step artefact. `import_cache` is named
        # because that is the field the payload carries.
        header.append("WARNING:   import_cache=false — assets are not imported."
                      " Textures loaded by path read as null for files that exist"
                      " on disk.")

    status = runtime_status(payload)
    rt = _as_dict(payload.get("runtime"))
    # Each section is a self-contained block of lines: the header travels inside
    # the body. Keeping the header separate produced a duplicated "runtime:" line,
    # because the body already labelled itself.
    runtime_block = []
    if status == "unknown":
        runtime_block.append("runtime:   NOT OBSERVED — the game was not started.")
        for e in _as_list(rt.get("errors"))[:2]:
            runtime_block.append(f"           {str(e)[:150]}")
        runtime_block.append("           Nothing is known about runtime behaviour."
                             " This is")
        runtime_block.append("           not evidence that the project is healthy.")
    else:
        runtime_block.append(f"runtime:   {rt.get('error_count', 0)} errors,"
                             f" {rt.get('warning_count', 0)} warnings"
                             f"  over {rt.get('frames', '?')} frames")
        for e in _as_list(rt.get("errors"))[:3]:
            runtime_block.append(f"           {str(e)[:150]}")

    # Observed scene structure: static vs runtime is the whole point of the tool,
    # so both numbers always appear together.
    scene_block = []
    for scene in _as_list(payload.get("scenes")):
        scene = _as_dict(scene)
        st = _as_dict(scene.get("static"))
        rtv = _as_dict(scene.get("runtime"))
        name = scene.get("scene", "?")
        if "runtime" in scene and rtv.get("child_count") is not None:
            scene_block.append(f"  {name}: static {st.get('child_count', '?')}"
                               f" -> runtime {rtv.get('child_count', '?')}"
                               f" ({rtv.get('root_type', '?')})")
        else:
            scene_block.append(f"  {name}: static {st.get('child_count', '?')}"
                               " (runtime not observed)")
    if scene_block:
        scene_block.insert(0, "observed scenes:")

    displays = _as_list(rt.get("observed_displays"))
    display_block = []
    for d in displays[:8]:
        d = _as_dict(d)
        spk = d.get("speaker") or "(no speaker)"
        display_block.append(f"  f{d.get('frame')} {spk}: {str(d.get('text'))[:90]}")
    if display_block:
        display_block.insert(0, f"observed displays ({len(displays)}, in order):")

    findings = _as_list(_as_dict(payload.get("findings")).get("findings"))
    finding_block = []
    for f in findings[:8]:
        f = _as_dict(f)
        loc = f.get("file") or ""
        line = f.get("line")
        where = f"{loc}:{line}" if loc and line else loc
        finding_block.append(f"  [{f.get('layer')}] {where} "
                             f"{str(f.get('message'))[:120]}")
    if finding_block:
        finding_block.insert(0, f"findings ({len(findings)}):")

    def assemble(blocks):
        out = list(header)
        for block in blocks:
            if not block:
                continue
            out.append("")
            out.extend(block)
        out.append("")
        out.append(f"ok:        {payload.get('ok')}")
        return "\n".join(out)

    # In descending priority. The header, the runtime verdict and the findings are
    # never dropped: a digest that omits the error to save tokens is worse than no
    # digest. Whole blocks go, never a truncation mid-line, because a cut-off
    # payload reads as complete.
    blocks = [runtime_block, finding_block, scene_block, display_block]
    text = assemble(blocks)
    while estimate_tokens(text) > budget_tokens:
        for i in range(len(blocks) - 1, -1, -1):
            # Never drop the first two (runtime verdict, findings).
            if i >= 2 and blocks[i]:
                blocks[i] = []
                break
        else:
            break
        text = assemble(blocks)
    return text


# --- ci summary ---------------------------------------------------------------

def ci_summary(payload):
    """One-line verdict plus counts. For a log header, not for parsing.

    Parsing is what the JSONL findings stream is for; this exists so a human
    skimming CI output sees the same three-state status the agent digest uses.
    """
    status = runtime_status(payload)
    findings = _as_dict(payload.get("findings")).get("finding_count", 0)
    rt = _as_dict(payload.get("runtime"))
    if status == "unknown":
        verdict = "UNKNOWN — game not started"
    elif payload.get("ok"):
        verdict = "CLEAN"
    else:
        verdict = "FINDINGS"
    line = (f"godot-lens: {verdict} | findings={findings} | "
            f"runtime_errors={rt.get('error_count', 0)} | "
            f"schema={payload.get('schema')}/{payload.get('version')}")
    fp = _fingerprint_line(payload)
    if fp:
        line += f" | project={fp}"
    return line


# --- human report -------------------------------------------------------------

def _esc(v):
    return html.escape(str(v if v is not None else ""))


def _tree_html(node, depth=0):
    """Render a node tree as nested <details>, with an inline SVG spine.

    SVG rather than box-drawing characters because the report is read at
    unpredictable zoom levels, and a real graphic stays aligned when text
    reflows.
    """
    if not isinstance(node, dict):
        return ""
    name = _esc(node.get("name"))
    ntype = _esc(node.get("type"))
    kids = node.get("children") or []
    notes = []
    if node.get("generated_name"):
        notes.append('<span class="tag gen">generated</span>')
    if node.get("text"):
        notes.append(f'<span class="tag text">{_esc(str(node["text"])[:60])}</span>')
    if node.get("texture"):
        notes.append('<span class="tag tex">texture</span>')
    if node.get("shape") is None and node.get("type") == "CollisionShape2D":
        notes.append('<span class="tag bad">no shape</span>')
    if node.get("warnings"):
        notes.append(f'<span class="tag warn">{len(node["warnings"])} warning(s)</span>')
    note = " ".join(notes)

    if not kids:
        return (f'<div class="node leaf" style="--d:{depth}">'
                f'<svg class="spine" viewBox="0 0 12 12" aria-hidden="true">'
                f'<line x1="0" y1="0" x2="0" y2="6"/><line x1="0" y1="6" x2="8" y2="6"/>'
                f'</svg><code>{name}</code> <em>:{ntype}</em> {note}</div>')
    inner = "".join(_tree_html(c, depth + 1) for c in kids)
    return (f'<details class="node" style="--d:{depth}" open>'
            f'<summary><svg class="spine" viewBox="0 0 12 12" aria-hidden="true">'
            f'<line x1="0" y1="0" x2="0" y2="6"/><line x1="0" y1="6" x2="8" y2="6"/>'
            f'</svg><code>{name}</code> <em>:{ntype}</em> '
            f'<span class="count">{len(kids)}</span> {note}</summary>'
            f'<div class="kids">{inner}</div></details>')


def html_report(payload, diagnostics=None, source_path=None):
    """A single self-contained HTML file. No server, no build, no dependencies.

    Inline JSON plus vanilla JavaScript rather than a framework: the payload is a
    tree and a list, the page must open from a file:// URL, and a report that
    needs a toolchain to render is a report nobody regenerates.

    `diagnostics` is the optional L4 output. It is a separate argument, not a
    field of the payload, because the payload is observation and this is
    interpretation — merging them at render time would erase the distinction the
    protocol works to preserve.
    """
    status = runtime_status(payload)
    engine = _as_dict(payload.get("engine"))
    project = _as_dict(payload.get("project"))
    rt = _as_dict(payload.get("runtime"))
    fp = _as_dict(project.get("fingerprint"))

    status_text = {"clean": "healthy", "errors": "runtime errors",
                   "unknown": "NOT OBSERVED"}[status]
    status_class = {"clean": "ok", "errors": "bad", "unknown": "unknown"}[status]

    # Scene trees
    scenes_html = []
    for scene in _as_list(payload.get("scenes")):
        scene = _as_dict(scene)
        st = _as_dict(scene.get("static"))
        rtv = scene.get("runtime") or {}
        scenes_html.append(f"""
        <section class="scene">
          <h3>{_esc(scene.get('scene'))}</h3>
          <p class="delta">declared <b>{_esc(st.get('child_count'))}</b>
             &rarr; observed <b>{_esc(rtv.get('child_count', '—'))}</b> children</p>
          <div class="cols">
            <div><h4>declared (.tscn)</h4>{_tree_html(_as_dict(st.get('root')))}</div>
            <div><h4>observed (after _ready)</h4>{_tree_html(_as_dict(rtv.get('root')))}</div>
          </div>
        </section>""")

    # Displays timeline
    displays = _as_list(rt.get("observed_displays"))
    disp_rows = "".join(
        f"<tr><td class=num>{_esc(_as_dict(d).get('frame'))}</td>"
        f"<td>{_esc(_as_dict(d).get('speaker') or '—')}</td>"
        f"<td>{_esc(_as_dict(d).get('text'))}</td></tr>" for d in displays)

    # Findings
    findings = _as_list(_as_dict(payload.get("findings")).get("findings"))
    finding_rows = "".join(
        f"<tr><td><span class='layer {_esc(_as_dict(f).get('layer'))}'>{_esc(_as_dict(f).get('layer'))}</span></td>"
        f"<td>{_esc(_as_dict(f).get('file') or '—')}{':' + _esc(_as_dict(f).get('line')) if _as_dict(f).get('line') else ''}</td>"
        f"<td>{_esc(_as_dict(f).get('message'))}</td></tr>" for f in (_as_dict(x) for x in findings))

    # Diagnostics (interpretation, separated)
    diag_html = ""
    if diagnostics:
        items = diagnostics.get("diagnostics") or []
        diag_html = "<h2>Diagnosis <span class=hint>(rule-bound interpretation, not observation)</span></h2>"
        if not items:
            diag_html += "<p class='muted'>No rule matched.</p>"
        for d in items:
            sev = _esc(d.get("severity"))
            diag_html += f"""
            <div class="diag {sev}">
              <div class="diag-head"><span class="sev">{sev}</span>
                <code>{_esc(d.get('code'))}</code>
                <span class="loc">{_esc(d.get('scene') or d.get('file') or '')}
                {(' node=' + _esc(d.get('node'))) if d.get('node') else ''}</span></div>
              <p><b>meaning:</b> {_esc(d.get('meaning'))}</p>
              <p><b>impact:</b> {_esc(d.get('impact'))}</p>
              <p><b>action:</b> {_esc(d.get('action'))}</p>
            </div>"""

    bootstrap = {
        "status": status,
        "source": source_path,
    }

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>godot-lens report — {_esc(project.get('main_scene'))}</title>
<style>
:root{{--bg:#12141a;--fg:#e8eaf0;--dim:#9aa3b2;--line:#262b36;--ok:#3fb950;
--bad:#f85149;--warn:#d29922;--card:#1a1d25;--accent:#58a6ff}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--fg);
font:14px/1.55 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}}
header{{padding:20px 24px;border-bottom:1px solid var(--line);background:var(--card)}}
h1{{margin:0 0 4px;font-size:19px}}
h2{{font-size:15px;margin:26px 0 10px;color:var(--fg)}}
h3{{font-size:14px;margin:0 0 6px}}
h4{{font-size:12px;text-transform:uppercase;letter-spacing:.06em;color:var(--dim);margin:0 0 8px}}
main{{padding:20px 24px;max-width:1400px}}
.badges{{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}}
.badge{{border:1px solid var(--line);border-radius:999px;padding:3px 10px;font-size:12px;color:var(--dim)}}
.badge.ok{{color:var(--ok);border-color:var(--ok)}}
.badge.bad{{color:var(--bad);border-color:var(--bad)}}
.badge.unknown{{color:var(--warn);border-color:var(--warn)}}
.note{{margin-top:12px;padding:10px 12px;border-left:3px solid var(--warn);
background:#20190a;color:#f0d9a0;border-radius:4px;font-size:13px}}
.muted{{color:var(--dim)}}
.hint{{font-size:12px;color:var(--dim);font-weight:400}}
.scene{{background:var(--card);border:1px solid var(--line);border-radius:8px;
padding:14px 16px;margin-bottom:14px}}
.delta{{margin:0 0 12px;color:var(--dim);font-size:12.5px}}
.cols{{display:grid;grid-template-columns:1fr 1fr;gap:18px}}
@media(max-width:900px){{.cols{{grid-template-columns:1fr}}}}
.node{{padding-left:0}}
.node .kids{{padding-left:14px;border-left:1px solid var(--line);margin-left:5px}}
summary{{cursor:pointer;list-style:none;display:flex;align-items:center;gap:6px;padding:1px 0}}
summary::-webkit-details-marker{{display:none}}
.spine{{width:12px;height:12px;flex:0 0 12px;stroke:var(--line);stroke-width:1;fill:none}}
.leaf{{display:flex;align-items:center;gap:6px;padding:1px 0}}
code{{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12.5px}}
em{{color:var(--dim);font-style:normal;font-size:12px}}
.count{{font-size:11px;color:var(--dim);border:1px solid var(--line);border-radius:3px;padding:0 4px}}
.tag{{font-size:10.5px;padding:1px 6px;border-radius:3px;background:#222834;color:var(--dim)}}
.tag.gen{{color:var(--accent)}} .tag.bad{{color:var(--bad)}} .tag.warn{{color:var(--warn)}}
.tag.tex{{color:#7ee787}} .tag.text{{color:#e3b341}}
table{{width:100%;border-collapse:collapse;margin:6px 0 4px;font-size:13px}}
th,td{{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}}
th{{color:var(--dim);font-weight:600;font-size:11.5px;text-transform:uppercase;letter-spacing:.05em}}
td.num{{color:var(--dim);width:46px}}
.layer{{font-size:11px;padding:1px 6px;border-radius:3px;background:#222834;color:var(--dim)}}
.layer.runtime{{color:var(--bad)}} .layer.scene{{color:var(--warn)}} .layer.static{{color:var(--accent)}}
.diag{{border:1px solid var(--line);border-left-width:3px;border-radius:6px;
padding:10px 12px;margin-bottom:10px;background:var(--card)}}
.diag.error{{border-left-color:var(--bad)}} .diag.warning{{border-left-color:var(--warn)}}
.diag.info{{border-left-color:var(--accent)}}
.diag-head{{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:6px}}
.sev{{font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--dim)}}
.diag.error .sev{{color:var(--bad)}} .diag.warning .sev{{color:var(--warn)}} .diag.info .sev{{color:var(--accent)}}
.loc{{color:var(--dim);font-size:12px;margin-left:auto}}
.diag p{{margin:3px 0}}
.panel{{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:14px 16px;margin-bottom:14px}}
</style></head><body>
<header>
  <h1>godot-lens — {_esc(payload.get('schema'))} v{_esc(payload.get('version'))}</h1>
  <div class="muted">{_esc(project.get('main_scene'))} &middot;
     engine {_esc(engine.get('installed'))}</div>
  <div class="badges">
    <span class="badge {status_class}">{_esc(status_text)}</span>
    <span class="badge">ok={_esc(payload.get('ok'))}</span>
    <span class="badge">{_esc(engine.get('class_count') or '?')} API classes</span>
    <span class="badge">api match={_esc(engine.get('api_matches_engine'))}</span>
    <span class="badge">findings {_esc(_as_dict(payload.get('findings')).get('finding_count', 0))}</span>
  </div>
  {f'<div class="note"><b>Runtime behaviour was NOT observed.</b> The game was not started, so this report describes a project whose behaviour is unknown — not a healthy one.</div>' if status == 'unknown' else ''}
  {f'<div class="note">Project was dirty when measured: +{_esc(fp.get("project_dirty_files"))} uncommitted file(s). Results are reproducible only against the same working tree.</div>' if fp.get('project_dirty') else ''}
</header>
<main>
  <div class="panel">
    <h4>Fingerprint</h4>
    <div>fingerprint <code>{_esc(fp.get('project_commit_short') or '(no git)')}</code>
      {('&middot; +' + _esc(fp.get('project_dirty_files')) + ' uncommitted') if fp.get('project_dirty') else '&middot; clean'}
      &middot; schema <code>{_esc(payload.get('schema'))}/{_esc(payload.get('version'))}</code>
      &middot; engine <code>{_esc(engine.get('installed'))}</code></div>
  </div>

  <h2>Observed scenes <span class=hint>(declared vs what exists after _ready)</span></h2>
  {''.join(scenes_html) or '<p class="muted">No scene was observed.</p>'}

  <h2>Observed displays <span class=hint>(a timeline, not a snapshot)</span></h2>
  {f'<table><thead><tr><th>frame</th><th>speaker</th><th>text</th></tr></thead><tbody>{disp_rows}</tbody></table>' if displays else '<p class="muted">No display was observed.</p>'}

  <h2>Findings <span class=hint>(validator output)</span></h2>
  {f'<table><thead><tr><th>layer</th><th>location</th><th>message</th></tr></thead><tbody>{finding_rows}</tbody></table>' if findings else '<p class="muted">None. A clean validator run means the executed paths are healthy, not that the project is correct.</p>'}

  {diag_html}
</main>
<script>
// The payload is inlined so the file opens from file:// with no fetch, which
// would be blocked by CORS. Kept for programmatic access by a future viewer.
window.__GODOT_LENS__ = {json.dumps(bootstrap, ensure_ascii=False)};
</script>
</body></html>
"""
