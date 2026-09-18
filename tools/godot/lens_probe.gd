extends Node
# lens_probe.gd — record which SOURCE SPANS a generated game actually executes.
#
# This is injected into a throwaway COPY of the project under test. It is never
# part of the project, and the project's own files are not edited in place.
#
# Why a probe, and why this is a different instrument from trace_dump.gd
# --------------------------------------------------------------------
# trace_dump.gd observes only the SCENE: "a Label at (64,420) changed to this
# text". That is application-agnostic, and that is what makes it trustworthy, but
# it cannot say WHICH source statement produced a display. A timeline of visible
# changes cannot be matched against a source specification when several source
# lines could each have produced the same pixels.
#
# This probe closes that gap. Before acting on an IR node, the generated runtime
# has that node — including its `source` span (path, line) — in hand. Recording
# it yields an EXECUTION COVERAGE trace of the IR:
#
#     (source path, line, node kind) for every node the game processed
#
# Why this instead of simulating the IR's control flow
# ----------------------------------------------------
# The obvious alternative — evaluate the IR's `if` conditions and remove
# unexecuted branches from the expected trace — means reimplementing expression
# evaluation, variable initialisation and branch selection inside the checker,
# then trusting that reimplementation to agree with the runtime. Every
# disagreement becomes a false accusation against a correct project. Letting the
# runtime report its own coverage makes the runtime the oracle and needs no
# duplicated semantics.
#
# What this DOES see: a source statement that produces no corresponding IR node,
# or one of the wrong kind — the `define narrator` class of defect, where a valid
# project silently says less than the source.
#
# What this does NOT see: whether the engine RENDERED the node correctly. A node
# that executes but draws nothing is invisible here; that is trace_dump.gd's job.
# The two instruments are complementary and neither alone is sufficient.
#
# Output is flushed on EVERY capture rather than on quit. The game may stop at a
# menu, quit early, or be killed by a timeout, and a capture that only lands on a
# clean exit would report "nothing executed" for a game that clearly ran — the
# same false-clean failure this project exists to avoid.

const OUT_ENV := "GODOT_LENS_PROBE_OUT"
# How many "accept" inputs to deliver. A branching game stops advancing when it
# needs a real choice, which is a genuine coverage boundary rather than a
# failure, so this is a bound rather than a target.
const MAX_INPUTS := 30

var _marks: Array = []
var _last := ""
var _path := ""
var _inputs := 0


func _ready() -> void:
	# Autoload: this runs before the main scene, so the path is captured before
	# anything can overwrite the environment.
	_path = OS.get_environment(OUT_ENV)
	call_deferred("_arm")


func _arm() -> void:
	# Drive the game the way a player does, so the runtime reaches statements
	# beyond the first one. Waiting for real input is not an option in a headless
	# batch run, and reading the game's own internals is not an option for an
	# observation tool.
	#
	# `ui_accept` is a Godot built-in action, not a symbol belonging to the
	# project under test, so depending on it does not couple this instrument to
	# the application. The handler is located by capability.
	var timer := Timer.new()
	timer.name = "_lens_input"
	timer.wait_time = 0.05
	timer.one_shot = false
	timer.timeout.connect(_pump)
	add_child(timer)
	timer.start()


func _pump() -> void:
	if _inputs >= MAX_INPUTS:
		_write()
		get_tree().quit(0)
		return
	var handler := _find_input_handler(get_tree().root)
	if handler == null:
		_write()
		get_tree().quit(0)
		return
	var ev := InputEventAction.new()
	ev.action = "ui_accept"
	ev.pressed = true
	handler.call("_unhandled_key_input", ev)
	_inputs += 1


func _find_input_handler(n: Node) -> Node:
	# Located by capability, never by name.
	if n.has_method("_unhandled_key_input") and n.get_script() != null:
		return n
	for c in n.get_children():
		var found := _find_input_handler(c)
		if found != null:
			return found
	return null


# Called from the instrumented runtime, once per IR node processed.
func _lens_capture(node: Dictionary, label: String) -> void:
	var src = node.get("source")
	if not (src is Dictionary):
		return
	var kind := str(node.get("kind", ""))
	var path := str(src.get("path", ""))
	var line := int(src.get("line", -1))
	var mark := "%s|%s|%s|%s" % [label, kind, path, str(line)]
	if mark == _last:
		return          # the same position sampled twice is not a new execution
	_last = mark
	_marks.append({"label": label, "kind": kind, "path": path, "line": line})
	_write()


func _write() -> void:
	if _path == "":
		return
	var f := FileAccess.open(_path, FileAccess.WRITE)
	if f == null:
		return
	f.store_string(JSON.stringify({
		"spans": _marks,
		"count": _marks.size(),
		"note": ("source spans the runtime executed, as reported by the IR node "
			+ "it processed; one entry per distinct executed position"),
	}, "  "))
	f.close()


func _exit_tree() -> void:
	_write()
