extends SceneTree
# scene_tree_dump.gd — serialise a Godot scene's node tree to JSON, headless.
#
# Run via:  godot --headless --script <this> -- <scene> [--runtime]
#
# Two distinct views, because they answer different questions:
#   static  (default) — what the .tscn file actually declares
#   --runtime         — what EXISTS after _ready() has run
#
# The distinction matters: a scene whose root has no children on disk may build
# its entire UI in _ready(). A static-only view of such a scene shows one empty
# node and tells the agent nothing about the real tree.
#
# Emits JSON on stdout between markers so a caller can parse it reliably even
# though Godot prints engine noise to the same stream.

const BEGIN := "<<<SCENE_JSON_BEGIN>>>"
const END := "<<<SCENE_JSON_END>>>"

var _runtime := false
var _scene_path := ""
var _max_depth := 24


func _init() -> void:
	var argv := OS.get_cmdline_user_args()
	for a in argv:
		if a == "--runtime":
			_runtime = true
		elif a == "--depth" and argv.find(a) + 1 < argv.size():
			_max_depth = int(argv[argv.find(a) + 1])
		elif not a.begins_with("--"):
			_scene_path = a

	if _scene_path == "":
		printerr("usage: godot --headless --script scene_tree_dump.gd -- <scene> [--runtime]")
		quit(2)
		return

	var packed: PackedScene = load(_scene_path)
	if packed == null:
		printerr("could not load scene: " + _scene_path)
		quit(1)
		return

	var root: Node = packed.instantiate()
	if root == null:
		printerr("could not instantiate scene: " + _scene_path)
		quit(1)
		return

	if _runtime:
		# Adding to the tree runs _ready and starts one frame, which is what
		# makes script-built children appear.
		get_root().add_child(root)
		await process_frame
		await process_frame
	else:
		# Static view: still needs no tree, but packed children are attached at
		# instantiate() time, so the declared hierarchy is fully visible.
		pass

	var _probe: Dictionary = _node_to_dict(root, 0)
	printerr("PROBE keys=", _probe.keys(), " size=", _probe.size())
	var payload := {
		"scene": _scene_path,
		"view": "runtime" if _runtime else "static",
		"root": _node_to_dict(root, 0),
	}
	print(BEGIN)
	print(JSON.stringify(payload, "  "))
	print(END)

	if _runtime:
		root.queue_free()
		await process_frame
	quit(0)


func _node_to_dict(n: Node, depth: int) -> Dictionary:
	var children: Array = []
	if depth < _max_depth:
		for c in n.get_children():
			children.append(_node_to_dict(c, depth + 1))

	var d := {
		"name": String(n.name),
		"type": n.get_class(),
		"child_count": n.get_child_count(),
		"children": children,
	}

	# Script attached? This is the code<->scene link an agent needs.
	var scr: Script = n.get_script()
	if scr != null:
		d["script"] = scr.resource_path

	# Only for nodes that have them, to keep the payload small.
	if n is Node2D:
		d["position"] = _v(n.position)
	if n is Control:
		var c := n as Control
		d["visible"] = c.visible
		d["anchors_preset"] = c.anchors_preset
		d["size"] = _v(c.size)
	if n is CanvasItem:
		d["visible"] = (n as CanvasItem).visible
	if n is CollisionObject2D:
		var co := n as CollisionObject2D
		d["collision_layer"] = co.collision_layer
		d["collision_mask"] = co.collision_mask
	if n is CollisionShape2D:
		# The single most common "red exclamation mark" cause.
		var cs := n as CollisionShape2D
		d["shape"] = (cs.shape.resource_path if cs.shape != null else null)
	if n is CollisionPolygon2D:
		pass
	if n is Sprite2D:
		var sp := n as Sprite2D
		d["texture"] = (sp.texture.resource_path if sp.texture != null else null)
	if n is Label:
		d["text"] = (n as Label).text
	if n is RichTextLabel:
		d["text_length"] = (n as RichTextLabel).text.length()
	if n is BaseButton:
		d["text"] = (n as BaseButton).text
	if n is AudioStreamPlayer:
		var ap := n as AudioStreamPlayer
		d["stream"] = (ap.stream.resource_path if ap.stream != null else null)

	# Configuration warnings: guarded, because get_configuration_warnings() is
	# NOT defined on every Node subclass. Calling it unguarded raises
	# "Invalid call. Nonexistent function" at runtime, which aborts this whole
	# function and yields an empty payload. Static checking cannot catch this —
	# it only surfaces when the code actually runs.
	#
	# Note: this reports only warnings the node itself declares. The editor's
	# yellow "!" for things like a shape-less CollisionShape2D comes from
	# editor-side validation and is not reachable from a runtime script.
	if n.has_method("get_configuration_warnings"):
		var warns: PackedStringArray = n.get_configuration_warnings()
		if warns.size() > 0:
			d["warnings"] = warns

	return d


func _v(v: Variant) -> String:
	return str(v)
