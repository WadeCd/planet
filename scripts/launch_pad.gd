class_name LaunchPad
extends Area3D

@export var target_planet: Planet
@export var target_position_override: Vector3 = Vector3.ZERO
@export var launch_power: float = 38.0
@export var arc_height: float = 12.0
@export var boost_duration: float = 1.4

@onready var ring_mesh: MeshInstance3D = $Visuals/RingMesh
@onready var particles: GPUParticles3D = $Visuals/GPUParticles3D

var cooldown: float = 0.0

func _ready() -> void:
	body_entered.connect(_on_body_entered)

func _process(delta: float) -> void:
	if cooldown > 0.0:
		cooldown -= delta
	if ring_mesh:
		ring_mesh.rotate_y(2.5 * delta)

func _on_body_entered(body: Node3D) -> void:
	if cooldown > 0.0:
		return
	var player: Player = body as Player
	if player == null:
		return

	cooldown = 2.0
	var destination: Vector3 = target_position_override
	if target_planet != null:
		# Aim towards the surface or center of target planet
		destination = target_planet.global_position

	var dir: Vector3 = (destination - global_position).normalized()
	var pad_up: Vector3 = global_basis.y
	var launch_velocity: Vector3 = dir * launch_power + pad_up * arc_height

	if particles:
		particles.restart()

	player.apply_launch(launch_velocity, boost_duration)
