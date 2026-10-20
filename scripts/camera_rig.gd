class_name CameraRig
extends Node3D

@export var target: Node3D
@export var mouse_sensitivity: float = 0.003
@export var keyboard_turn_speed: float = 2.4
@export var zoom_speed: float = 1.0
@export var min_zoom: float = 3.0
@export var max_zoom: float = 18.0
@export var default_zoom: float = 7.5
@export var alignment_speed: float = 10.0

@onready var spring_arm: SpringArm3D = $SpringArm3D
@onready var camera: Camera3D = $SpringArm3D/Camera3D

var pitch: float = deg_to_rad(-15.0)
var is_mouse_captured: bool = true

func _ready() -> void:
	if spring_arm:
		spring_arm.spring_length = default_zoom
	Input.set_mouse_mode(Input.MOUSE_MODE_CAPTURED)

func _unhandled_input(event: InputEvent) -> void:
	if event.is_action_pressed("toggle_mouse") and not event.is_echo():
		is_mouse_captured = not is_mouse_captured
		Input.set_mouse_mode(Input.MOUSE_MODE_CAPTURED if is_mouse_captured else Input.MOUSE_MODE_VISIBLE)
		get_viewport().set_input_as_handled()
		return

	if not is_mouse_captured:
		if event is InputEventMouseButton and event.pressed and event.button_index == MOUSE_BUTTON_LEFT:
			is_mouse_captured = true
			Input.set_mouse_mode(Input.MOUSE_MODE_CAPTURED)
			get_viewport().set_input_as_handled()
		return

	if event is InputEventMouseMotion:
		# Yaw (horizontal orbit around local up)
		rotate_object_local(Vector3.UP, -event.relative.x * mouse_sensitivity)
		# Pitch (vertical angle clamped)
		pitch = clampf(pitch - event.relative.y * mouse_sensitivity, deg_to_rad(-70.0), deg_to_rad(65.0))
		_apply_pitch()
		get_viewport().set_input_as_handled()

	elif event is InputEventMouseButton:
		if event.button_index == MOUSE_BUTTON_WHEEL_UP and event.pressed:
			spring_arm.spring_length = clampf(spring_arm.spring_length - zoom_speed, min_zoom, max_zoom)
		elif event.button_index == MOUSE_BUTTON_WHEEL_DOWN and event.pressed:
			spring_arm.spring_length = clampf(spring_arm.spring_length + zoom_speed, min_zoom, max_zoom)

func _process(delta: float) -> void:
	# Q and E keyboard camera rotation
	var cam_turn: float = 0.0
	if Input.is_physical_key_pressed(KEY_Q) or Input.is_key_pressed(KEY_Q):
		cam_turn += 1.0
	if Input.is_physical_key_pressed(KEY_E) or Input.is_key_pressed(KEY_E):
		cam_turn -= 1.0
	if cam_turn != 0.0:
		rotate_object_local(Vector3.UP, cam_turn * keyboard_turn_speed * delta)

	if not target:
		return

	# Smoothly follow target position
	global_position = global_position.lerp(target.global_position, 20.0 * delta)

	# Smoothly align camera rig's up vector to target's current_up
	var target_up: Vector3 = Vector3.UP
	if "current_up" in target:
		target_up = target.current_up

	var cur_up: Vector3 = global_basis.y
	if not cur_up.is_equal_approx(target_up) and target_up.length_squared() > 0.01:
		var rot_quat: Quaternion = Quaternion(cur_up, target_up)
		var desired_basis: Basis = Basis(rot_quat) * global_basis
		global_basis = Basis(global_basis.get_rotation_quaternion().slerp(desired_basis.get_rotation_quaternion(), alignment_speed * delta)).orthonormalized()
		_apply_pitch()

func _apply_pitch() -> void:
	if spring_arm:
		spring_arm.rotation.x = pitch

func boost_fov_temporarily(boost_fov: float, duration: float) -> void:
	if not camera:
		return
	var tween: Tween = create_tween()
	tween.tween_property(camera, "fov", boost_fov, duration * 0.2).set_trans(Tween.TRANS_QUAD).set_ease(Tween.EASE_OUT)
	tween.tween_property(camera, "fov", 75.0, duration * 0.8).set_trans(Tween.TRANS_QUAD).set_ease(Tween.EASE_IN_OUT)
