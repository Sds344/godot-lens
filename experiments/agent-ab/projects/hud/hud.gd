extends Control
# The HUD is built at run time from a data table.
#
# This file describes what the HUD SHOULD contain. It does not describe what it
# DOES contain: the tree only exists once _ready() has run. Reading this file
# tells you the intent; enumerating the running tree tells you the result.

const LAYOUT: Array = [
	{"kind": "panel",  "name": "StatusPanel",  "parent": ""},
	{"kind": "label",  "name": "TitleLabel",   "parent": "StatusPanel", "text": "Status"},
	{"kind": "hbox",   "name": "ScoreRow",     "parent": "StatusPanel"},
	{"kind": "label",  "name": "ScoreCaption", "parent": "ScoreRow",    "text": "Score"},
	{"kind": "label",  "name": "ScoreValue",   "parent": "ScoreRow",    "text": "0"},
	{"kind": "hbox",   "name": "HealthRow",    "parent": "StatusPanel"},
	{"kind": "label",  "name": "HealthCaption","parent": "HealthRow",   "text": "HP"},
	{"kind": "label",  "name": "HealthValue",  "parent": "HealthRow",   "text": "3"},
	{"kind": "panel",  "name": "ActionPanel",  "parent": ""},
	{"kind": "label",  "name": "ActionTitle",  "parent": "ActionPanel", "text": "Actions"},
	{"kind": "hbox",   "name": "ActionRow",    "parent": "ActionPanel"},
	{"kind": "button", "name": "StartButton",  "parent": "ActionRow",   "text": "Start"},
	{"kind": "button", "name": "RetryButton",  "parent": "ActionRow",   "text": "Retry"},
	{"kind": "button", "name": "QuitButton",   "parent": "ActionRow",   "text": "Quit"},
	{"kind": "hbox",   "name": "ToolBar",      "parent": ""},

	# The toolbar's width is not written anywhere in this file. Each button is
	# sized by the layout engine to fit its label at the current font size, so the
	# row's total width is a property of the running layout, not of this source.
	# Reading this table tells you the labels; it cannot tell you whether the row
	# fits inside the viewport.
	{"kind": "button", "name": "Tool1", "parent": "ToolBar", "text": "New Game"},
	{"kind": "button", "name": "Tool2", "parent": "ToolBar", "text": "Load Game"},
	{"kind": "button", "name": "Tool3", "parent": "ToolBar", "text": "Save Game"},
	{"kind": "button", "name": "Tool4", "parent": "ToolBar", "text": "Settings"},
	{"kind": "button", "name": "Tool5", "parent": "ToolBar", "text": "Credits"},
	{"kind": "button", "name": "Tool6", "parent": "ToolBar", "text": "Achievements"},
]

var _made: Dictionary = {}


func _ready() -> void:
	for spec in LAYOUT:
		var node: Node = _build(spec)
		if node == null:
			continue
		var parent: Node = _resolve_parent(str(spec.get("parent", "")))
		parent.add_child(node)
		node.name = str(spec["name"])
		_made[str(spec["name"])] = node


func _resolve_parent(name: String) -> Node:
	if name == "":
		return self
	return _made.get(name, self)


func _build(spec: Dictionary) -> Node:
	var kind: String = str(spec.get("kind", ""))
	var node: Node = null
	match kind:
		"panel":
			var p := PanelContainer.new()
			p.custom_minimum_size = Vector2(260, 120)
			node = p
		"hbox":
			node = HBoxContainer.new()
		"label":
			var l := Label.new()
			l.text = str(spec.get("text", ""))
			node = l
		"button":
			var b := Button.new()
			b.text = str(spec.get("text", ""))
			node = b
	if node == null:
		return null
	# Every node is positioned by the layout that owns it; give the top-level
	# panels an offset so they are visible.
	if str(spec.get("parent", "")) == "":
		var c := node as Control
		if c != null:
			if str(spec.get("name", "")) == "ToolBar":
				c.position = Vector2(0, 620)
			else:
				c.position = Vector2(20, 20) * (float(_made.size()) + 1.0)
	return node
