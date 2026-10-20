class_name Collectible
extends Area3D

@export var rotation_speed: float = 2.0
@export var bob_amplitude: float = 0.25
@export var bob_frequency: float = 3.0
@export var star_color: Color = Color(1.0, 0.85, 0.2)

@onready var mesh: MeshInstance3D = $MeshInstance3D
@onready var collision_shape: CollisionShape3D = $CollisionShape3D
@onready var spark_particles: GPUParticles3D = $GPUParticles3D

var time_passed: float = 0.0
var base_pos: Vector3 = Vector3.ZERO
var collected: bool = false

func _ready() -> void:
	add_to_group("collectibles")
	body_entered.connect(_on_body_entered)
	base_pos = position
	time_passed = randf() * TAU

func _process(delta: float) -> void:
	if collected:
		return
	time_passed += delta
	if mesh:
		mesh.rotate_y(rotation_speed * delta)
		mesh.position.y = sin(time_passed * bob_frequency) * bob_amplitude

func _on_body_entered(body: Node3D) -> void:
	if collected:
		return
	var player: Player = body as Player
	if player == null:
		return

	collected = true
	if collision_shape:
		collision_shape.set_deferred("disabled", true)
	player.add_star()

	# Visual scale animation on mesh, avoiding zero scale on Area3D physics shape
	if mesh:
		var tween: Tween = create_tween()
		tween.tween_property(mesh, "scale", mesh.scale * 1.5, 0.12).set_trans(Tween.TRANS_BACK).set_ease(Tween.EASE_OUT)
		tween.tween_property(mesh, "scale", Vector3(0.01, 0.01, 0.01), 0.12).set_trans(Tween.TRANS_QUAD).set_ease(Tween.EASE_IN)
		tween.tween_callback(queue_free)
	else:
		queue_free()
