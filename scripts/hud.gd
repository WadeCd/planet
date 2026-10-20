class_name HUD
extends CanvasLayer

@onready var star_label: Label = $Control/TopLeft/StarCard/HBox/Margin/StarLabel
@onready var planet_banner: PanelContainer = $Control/TopCenter/PlanetBanner
@onready var planet_label: Label = $Control/TopCenter/PlanetBanner/PlanetLabel
@onready var telemetry_label: Label = $Control/BottomRight/TelemetryLabel
@onready var victory_modal: PanelContainer = $Control/Center/VictoryModal

var banner_timer: float = 0.0

func _ready() -> void:
	if victory_modal:
		victory_modal.visible = false
	if planet_banner:
		planet_banner.modulate.a = 0.0

func _process(delta: float) -> void:
	if banner_timer > 0.0:
		banner_timer -= delta
		if banner_timer <= 0.0 and planet_banner:
			var tween: Tween = create_tween()
			tween.tween_property(planet_banner, "modulate:a", 0.0, 0.6)

func update_stars(collected: int, total: int) -> void:
	if star_label:
		star_label.text = "⭐ 星之碎片: %d / %d" % [collected, total]
		# Pop animation on collection
		var tween: Tween = create_tween()
		tween.tween_property(star_label, "scale", Vector2(1.2, 1.2), 0.1)
		tween.tween_property(star_label, "scale", Vector2.ONE, 0.15)
		
	if collected >= total and total > 0 and victory_modal:
		show_victory()

func show_planet_name(name_str: String) -> void:
	if not planet_label or not planet_banner:
		return
	planet_label.text = "✦ 当前引力域: " + name_str + " ✦"
	banner_timer = 3.5
	var tween: Tween = create_tween()
	tween.tween_property(planet_banner, "modulate:a", 1.0, 0.3)

func update_telemetry(speed: float, altitude: float) -> void:
	if telemetry_label:
		telemetry_label.text = "速度: %4.1f m/s  |  离地高度: %4.1f m" % [speed, altitude]

func show_victory() -> void:
	if not victory_modal:
		return
	victory_modal.visible = true
	victory_modal.modulate.a = 0.0
	victory_modal.scale = Vector2(0.8, 0.8)
	var tween: Tween = create_tween().set_parallel(true)
	tween.tween_property(victory_modal, "modulate:a", 1.0, 0.5)
	tween.tween_property(victory_modal, "scale", Vector2.ONE, 0.5).set_trans(Tween.TRANS_BACK).set_ease(Tween.EASE_OUT)
