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

	# --- Stable identity for programmatic consumers (L3) -----------------------
	# A node the engine created from code has a generated name such as
	# `@Sprite2D@6`, and that name is NOT stable across runs: it depends on how
	# many nodes happened to be created before it. Readable for a person, useless
	# as a key for a program — a consumer tracking "@Sprite2D@6" between runs is
	# tracking whatever node landed there first.
	#
	# `owner_path` answers "which node in the scene owns this?", which is what
	# makes a node addressable by path rather than by creation order.
	# `scene_file_path` answers "which scene should I edit?", and an empty value is
	# itself informative: a generated sprite belongs to code, not to content, so
	# there is no .tscn to open. `generated_name` is the machine-checkable form of
	# "the engine named this, so it was built by code rather than declared".
	d["node_path"] = String(n.get_path())
	if n.owner != null:
		d["owner_path"] = String(n.owner.get_path())
	var scene_file: String = ""
	if n.scene_file_path != "":
		scene_file = String(n.scene_file_path)
	elif n.owner != null and n.owner.scene_file_path != "":
		scene_file = String(n.owner.scene_file_path)
	d["scene_file_path"] = scene_file
	d["generated_name"] = String(n.name).begins_with("@")

	# Script attached? This is the code<->scene link an agent needs.
	var scr: Script = n.get_script()
	if scr != null:
		d["script"] = scr.resource_path
		# Exported members and whether they were assigned.
		#
		# This closes an observation gap rather than adding a rule. An exported
		# `NodePath` or resource reference that is never assigned reads as null at
		# runtime, and the resulting failure surfaces much later and somewhere else
		# — which is why it accounts for 35.9% of structural failures in
		# GameDevBench's failure analysis. It was undetectable here not because the
		# check was hard but because the exporter's value was never observed at all.
		#
		# Only exported properties with storage are reported, so the payload stays
		# about the scene rather than becoming a script dump.
		var exported := {}
		for prop in n.get_property_list():
			if not (int(prop.get("usage", 0)) & PROPERTY_USAGE_SCRIPT_VARIABLE):
				continue
			if not (int(prop.get("usage", 0)) & PROPERTY_USAGE_STORAGE):
				continue
			if str(prop.get("name", "")).begins_with("_"):
				continue
			var pname := str(prop.get("name", ""))
			var value = n.get(pname)
			exported[pname] = {
				"type": _type_name(int(prop.get("type", 0))),
				# A reference is "set" when it actually points at something. An
				# empty NodePath, a null Object and an empty string are all unset.
				"set": _is_set(value),
				"value": _short_value(value),
			}
		if not exported.is_empty():
			d["exported"] = exported

	# Only for nodes that have them, to keep the payload small.
	if n is Node2D:
		d["position"] = _v(n.position)
	if n is Control:
		var c := n as Control
		d["visible"] = c.visible
		d["anchors_preset"] = c.anchors_preset
		# Position AND size, because neither answers a layout question alone.
		#
		# `size` was reported but the computed position was not, which made one
		# whole class of fault unobservable: whether a widget ends up inside the
		# viewport, or overlapping a sibling, depends on where the layout engine
		# put it. GameDevBench attributes 19.9% of failures to "incorrect UI
		# layout, spacing, sizing, or anchoring", and the numeric half of that is
		# decidable from a rectangle — but only if both halves are present.
		#
		# The position is the layout result, not the value written in the .tscn:
		# containers assign it at run time, so it does not exist in any file.
		d["position"] = _v(c.position)
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


func _type_name(t: int) -> String:
	"""Variant type as a name, so a consumer can filter without a lookup table.

	A raw integer would force every consumer to hardcode Godot's enum, and that
	enum is exactly the kind of detail that shifts between engine versions.
	"""
	var names := {
		TYPE_NIL: "Nil", TYPE_BOOL: "bool", TYPE_INT: "int", TYPE_FLOAT: "float",
		TYPE_STRING: "String", TYPE_VECTOR2: "Vector2", TYPE_VECTOR3: "Vector3",
		TYPE_NODE_PATH: "NodePath", TYPE_OBJECT: "Object", TYPE_ARRAY: "Array",
		TYPE_DICTIONARY: "Dictionary", TYPE_COLOR: "Color", TYPE_RID: "RID",
	}
	return names.get(t, "Type" + str(t))


func _is_set(value: Variant) -> bool:
	"""Whether a reference actually points at something.

	Deliberately treats empty-as-unset: an exported `NodePath` left as `""` is
	indistinguishable in effect from one never assigned, and reporting the empty
	one as "set" would miss exactly the fault this exists to find.
	"""
	if value == null:
		return false
	if value is NodePath:
		return str(value) != ""
	if value is String:
		return (value as String) != ""
	if value is Array:
		return not (value as Array).is_empty()
	return true


func _short_value(value: Variant) -> String:
	var s := str(value)
	return s.substr(0, 80)
