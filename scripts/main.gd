class_name MainScene
extends Node3D

@onready var player: Player = $Player
@onready var camera_rig: CameraRig = $CameraRig
@onready var hud: HUD = $HUD

func _ready() -> void:
	# Connect camera to player
	if camera_rig and player:
		camera_rig.target = player
		player.camera_rig = camera_rig

	# Connect HUD signals
	if player and hud:
		player.star_collected.connect(hud.update_stars)
		player.planet_changed.connect(hud.show_planet_name)
		
		# Initial UI state
		var total_stars: int = get_tree().get_nodes_in_group("collectibles").size()
		hud.update_stars(0, total_stars)
		hud.show_planet_name("绿洲星 (Oasis Planet)")

func _process(_delta: float) -> void:
	if player and hud:
		var speed: float = player.velocity.length()
		var altitude: float = 0.0
		if player.current_planet != null:
			altitude = maxf(0.0, player.current_planet.get_distance_to_surface(player.global_position))
		hud.update_telemetry(speed, altitude)
