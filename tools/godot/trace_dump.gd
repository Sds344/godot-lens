extends SceneTree
# trace_dump.gd — record a frame-stamped timeline of what a running game DISPLAYS.
#
# Run via:  godot --headless --script trace_dump.gd -- <scene> [--frames N] [--inputs N] [--depth D]
#
# Why this exists
# ---------------
# scene_tree_dump.gd answers "what exists at frame N". A snapshot cannot see
# anything created and freed inside the window — a transient dialog, a menu
# opened and closed, a label set and then cleared. For a branching, text-driven
# game that is most of the interesting behaviour.
#
# This emits a timeline: one event per OBSERVED CHANGE, frame-stamped.
#
# The load-bearing constraint: this observes the SCENE, never the application.
# It does not read story.json, does not know what any particular converter is,
# and does not parse the game's own log output. It polls the node tree for
# display state and reports what changed. That is deliberate — an observation
# layer that depends on the observed program's internal symbols cannot observe a
# program that lacks them, and it breaks silently when they are renamed.
#
# Consequence: events say "a Label at this position changed to this text", not
# "the story reached line 15". Binding events to source lines is a separate step
# that needs a specification, and it lives in experiments/, not here.
#
# Advancing the game
# ------------------
# Under `--script` the engine's per-frame `_process` dispatch does not reach
# scene nodes, so a game that advances itself in `_process` appears frozen and
# the trace reports zero events on a game that is visibly running (observed
# directly while building this). Rather than depend on `_process`, this drives
# the game the way a player does: by delivering an "accept" input event to the
# node that handles it. That path is exercised by every real playthrough, so it
# cannot be silently bypassed by an engine version change.
#
# A game that requires a genuine choice stops advancing when no input changes
# anything. That is recorded as `stalled`, and it is a real coverage boundary —
# not a failure.

const BEGIN := "<<<TRACE_JSON_BEGIN>>>"
const END := "<<<TRACE_JSON_END>>>"

# Location of the display labels is discovered, not assumed. A position is the
# only stable handle on nodes the engine generated and left unnamed.
const SETTLE_FRAMES := 2      # polls of no change before considering a state settled
const STALL_TOLERANCE := 3    # settled states with no progress before giving up

var _scene_path := ""
var _max_frames := 600
var _max_inputs := 40
var _max_depth := 24

var _frame := 0
var _known := {}          # node path -> last observed state
var _events: Array = []
var _input_source: Node = null
var _inputs_sent := 0
var _stalled := false
var _stall_reason := ""
# What was on screen before any change; a change-only timeline cannot show it.
var _initial := {}


func _init() -> void:
	var argv := OS.get_cmdline_user_args()
	var i := 0
	while i < argv.size():
		var a: String = argv[i]
		if a == "--frames" and i + 1 < argv.size():
			_max_frames = int(argv[i + 1]); i += 1
		elif a == "--inputs" and i + 1 < argv.size():
			_max_inputs = int(argv[i + 1]); i += 1
		elif a == "--depth" and i + 1 < argv.size():
			_max_depth = int(argv[i + 1]); i += 1
		elif not a.begins_with("--"):
			_scene_path = a
		i += 1

	if _scene_path == "":
		_emit({"error": "no scene given"}); return

	var packed: PackedScene = load(_scene_path)
	if packed == null:
		_emit({"error": "could not load scene: " + _scene_path}); return
	var root: Node = packed.instantiate()
	if root == null:
		_emit({"error": "could not instantiate scene: " + _scene_path}); return

	get_root().add_child(root)
	# A change-only timeline cannot report what was already on screen, so the
	# display state is sampled BEFORE the first input is delivered. Sampling it
	# after would miss the opening line, because the first advance replaces it —
	# the trace would then start at the second line and every later pairing would
	# be shifted by one.
	await process_frame
	_poll(root)
	# Frame 0 is "the state the player is first shown". Each subsequent frame is
	# numbered after the input that produced it.
	_initial = _initial_display()

	var no_progress := 0
	for _step in range(_max_inputs):
		if _frame >= _max_frames:
			break
		var before := _state_fingerprint()
		_send_accept(root)
		# Let the input take effect and the display update.
		for _f in range(SETTLE_FRAMES):
			if _frame >= _max_frames:
				break
			await process_frame
			_frame += 1
			_poll(root)
		if _state_fingerprint() == before:
			no_progress += 1
			if no_progress >= STALL_TOLERANCE:
				_stalled = true
				_stall_reason = ("no display change after %d accept inputs; the game "
					% STALL_TOLERANCE
					+ "is waiting for something an accept input does not satisfy "
					+ "(a menu choice, a timeout, or the end of the story)")
				break
		else:
			no_progress = 0

	_emit({})


func _emit(extra: Dictionary) -> void:
	var payload := {
		"scene": _scene_path,
		"frames_observed": _frame,
		"inputs_sent": _inputs_sent,
		"stalled": _stalled,
		"event_count": _events.size(),
		"events": _events,
		"initial_display": _initial,
	}
	if _stalled:
		payload["stall_reason"] = _stall_reason
	for k in extra:
		payload[k] = extra[k]
	print(BEGIN)
	print(JSON.stringify(payload, "  "))
	print(END)
	quit(0 if not extra.has("error") else 1)


func _initial_display() -> Dictionary:
	"""The text-bearing nodes visible before any input, taken from the frame-0 poll.

	Returns the raw list plus the frame they were seen at. The pairing rule —
	which node is body text, which is the speaker plate — deliberately does NOT
	live here: it lives once, in the comparison, so frame 0 is interpreted exactly
	like every later frame. Pre-pairing here would let the two drift, and the
	symptom of that drift is a speaker mismatch on the first line of every
	project.
	"""
	var nodes: Array = []
	for path in _known.keys():
		var s: Dictionary = _known[path]
		if s.has("text") and str(s["text"]).strip_edges() != "":
			nodes.append({
				"text": s["text"],
				"path": path,
				"type": s.get("type", ""),
				"position": s.get("position", ""),
				"y": s.get("y", 0.0),
			})
	return {"nodes": nodes, "frame": _frame}


func _collect_text(n: Node, out: Array) -> void:
	var text := ""
	if n is Label:
		text = (n as Label).text
	elif n is RichTextLabel:
		text = (n as RichTextLabel).get_parsed_text()
	if text.strip_edges() != "":
		var y := 0.0
		var position := ""
		if n is Control:
			y = (n as Control).position.y
			position = _v((n as Control).position)
		elif n is Node2D:
			y = (n as Node2D).position.y
			position = _v((n as Node2D).position)
		out.append({
			"text": text, "path": String(n.get_path()),
			"type": n.get_class(), "position": position, "y": y,
		})
	for c in n.get_children():
		_collect_text(c, out)


# --- driving the game ---------------------------------------------------------

func _find_input_handler(n: Node) -> Node:
	"""The node that handles "accept", found by capability, not by name."""
	if n.has_method("_unhandled_key_input") and n.get_script() != null:
		return n
	for c in n.get_children():
		var found := _find_input_handler(c)
		if found != null:
			return found
	return null


func _send_accept(root: Node) -> void:
	if _input_source == null:
		_input_source = _find_input_handler(root)
	if _input_source == null:
		_stalled = true
		_stall_reason = "no node in the scene handles key input; cannot advance"
		return
	var ev := InputEventAction.new()
	ev.action = "ui_accept"
	ev.pressed = true
	_input_source.call("_unhandled_key_input", ev)
	_inputs_sent += 1


# --- observing the scene ------------------------------------------------------

func _state_fingerprint() -> String:
	"""Cheap digest of everything currently displayed, to detect progress."""
	var parts: Array = []
	for path in _known.keys():
		var s: Dictionary = _known[path]
		if s.has("text"):
			parts.append(path + "=" + str(s["text"]))
	parts.sort()
	return "|".join(parts)


func _poll(root: Node) -> void:
	var seen := {}
	_walk(root, "", seen, 0)
	# A node that vanished is an event too: for a transient node it may be the
	# only evidence it existed at all.
	for path in _known.keys():
		if not seen.has(path):
			_events.append({"frame": _frame, "kind": "removed", "path": path})
			_known.erase(path)
	for path in seen.keys():
		if not _known.has(path):
			_events.append({
				"frame": _frame, "kind": "added", "path": path,
				"type": seen[path]["type"],
			})


func _walk(n: Node, prefix: String, seen: Dictionary, depth: int) -> void:
	if depth > _max_depth:
		return
	var path := prefix + "/" + String(n.name)
	var state := _observe(n)
	seen[path] = state

	var prev = _known.get(path)
	if prev != null and prev is Dictionary:
		# Record change only. Re-reporting unchanged text every frame would bury
		# the timeline in noise and make it as useless as the snapshot it
		# replaces.
		if prev.has("text") and state.has("text") and prev["text"] != state["text"]:
			_events.append({
				"frame": _frame, "kind": "text", "path": path,
				"type": state["type"],
				"from": prev["text"], "to": state["text"],
				"position": state.get("position", ""),
			})
		if prev.has("visible") and state.has("visible") and prev["visible"] != state["visible"]:
			_events.append({
				"frame": _frame, "kind": "visible", "path": path,
				"visible": state["visible"],
			})
	_known[path] = state

	for c in n.get_children():
		_walk(c, path, seen, depth + 1)


func _observe(n: Node) -> Dictionary:
	var d := {"type": n.get_class()}
	if n is CanvasItem:
		d["visible"] = (n as CanvasItem).visible
	if n is Control:
		var c := n as Control
		d["position"] = _v(c.position)
		d["size"] = _v(c.size)
		# Layout is what distinguishes one unnamed Label from another, so the
		# vertical coordinate is carried through the whole pipeline rather than
		# re-parsed from the rendered position string.
		d["y"] = c.position.y
	if n is Label:
		d["text"] = (n as Label).text
	elif n is RichTextLabel:
		d["text"] = (n as RichTextLabel).get_parsed_text()
	elif n is BaseButton:
		d["text"] = (n as BaseButton).text
	if n is Node2D:
		d["position"] = _v((n as Node2D).position)
		if n is Sprite2D:
			var sp := n as Sprite2D
			d["texture"] = (sp.texture.resource_path if sp.texture != null else "")
	return d


func _v(v: Variant) -> String:
	return str(v)
