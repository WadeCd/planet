class_name Player
extends CharacterBody3D

signal star_collected(count: int, total: int)
signal planet_changed(planet_name: String)

@export var walk_speed: float = 9.0
@export var sprint_speed: float = 16.0
@export var jump_force: float = 14.0
@export var acceleration: float = 45.0
@export var air_acceleration: float = 18.0
@export var friction: float = 35.0
@export var rotation_speed: float = 14.0
@export var floor_snap_distance: float = 0.8

@export var camera_rig: CameraRig

var current_up: Vector3 = Vector3.UP
var target_up: Vector3 = Vector3.UP
var current_gravity: Vector3 = Vector3.ZERO
var current_planet: Planet = null

var is_sprinting: bool = false
var was_on_floor: bool = false
var launch_timer: float = 0.0
var jump_timer: float = 0.0
var _space_was_down: bool = false

var stars_collected: int = 0
var total_stars: int = 0
var respawn_position: Vector3 = Vector3.ZERO
var respawn_up: Vector3 = Vector3.UP

@onready var visuals: Node3D = $Visuals
@onready var collision_shape: CollisionShape3D = $CollisionShape3D
@onready var thruster_left: GPUParticles3D = $Visuals/Backpack/ThrusterLeft/GPUParticles3D
@onready var thruster_right: GPUParticles3D = $Visuals/Backpack/ThrusterRight/GPUParticles3D
@onready var dust_particles: GPUParticles3D = $Visuals/DustParticles

# Procedural animation parameters
var run_cycle: float = 0.0
var landing_squash: float = 0.0

func _ready() -> void:
	SoundGenerator.initialize_sounds()
	respawn_position = global_position
	respawn_up = Vector3.UP
	
	# Godot 4 CharacterBody3D Floor Settings for Spherical Surfaces
	floor_snap_length = floor_snap_distance
	floor_constant_speed = true
	floor_stop_on_slope = false
	floor_max_angle = deg_to_rad(65.0)
	
	call_deferred("_count_total_stars")

func _count_total_stars() -> void:
	total_stars = get_tree().get_nodes_in_group("collectibles").size()

func _physics_process(delta: float) -> void:
	if _is_reset_requested():
		respawn()
		return

	# 1. Calculate Gravity & Alignment
	_update_gravity_and_up(delta)

	# 2. Decompose Velocity into Tangent (along sphere surface) and Radial (normal to surface)
	var radial_vel: Vector3 = velocity.project(current_up)
	var tangent_vel: Vector3 = velocity - radial_vel

	# 3. Handle Jump & Gravity
	if launch_timer > 0.0:
		launch_timer -= delta
		floor_snap_length = 0.0
	else:
		# Apply gravitational pull along gravity vector
		radial_vel += current_gravity * delta

	if jump_timer > 0.0:
		jump_timer -= delta
		floor_snap_length = 0.0
	else:
		# Check landing
		if is_on_floor():
			floor_snap_length = floor_snap_distance
			if not was_on_floor:
				_on_land()

			# Push slightly inward when grounded to stay tightly adhered to the sphere curvature
			if radial_vel.dot(current_up) < 0.0:
				radial_vel = -current_up * 1.5

			if _is_jump_requested():
				jump_timer = 0.15 # Allow clean takeoff
				floor_snap_length = 0.0
				radial_vel = current_up * jump_force
				_on_jump()
	
	was_on_floor = is_on_floor()

	# 4. Handle Tangential Movement (Camera-Relative on Spherical Tangent Plane)
	is_sprinting = _is_sprint_active()
	var target_speed: float = sprint_speed if is_sprinting else walk_speed

	# Multi-layer input polling (Action Map -> Arrow Keys -> Physical Keys)
	var input_vec: Vector2 = _get_movement_input()

	var wish_dir: Vector3 = Vector3.ZERO
	if input_vec.length_squared() > 0.01:
		var cam_basis: Basis = camera_rig.global_basis if camera_rig != null else global_basis
		var cam_fwd: Vector3 = -cam_basis.z
		var cam_right: Vector3 = cam_basis.x
		
		# Project camera axes onto local tangent plane
		var t_fwd: Vector3 = (cam_fwd - cam_fwd.project(current_up)).normalized()
		var t_right: Vector3 = (cam_right - cam_right.project(current_up)).normalized()
		
		if t_fwd.is_zero_approx():
			t_fwd = (current_up.cross(cam_right)).normalized()
		if t_right.is_zero_approx():
			t_right = (t_fwd.cross(current_up)).normalized()

		wish_dir = (t_fwd * -input_vec.y + t_right * input_vec.x).normalized()

	var accel: float = acceleration if is_on_floor() else air_acceleration
	if wish_dir.length_squared() > 0.01:
		tangent_vel = tangent_vel.move_toward(wish_dir * target_speed, accel * delta)
	else:
		tangent_vel = tangent_vel.move_toward(Vector3.ZERO, friction * delta)

	# 5. Recombine Velocity & Move
	velocity = tangent_vel + radial_vel
	
	# Godot 4: up_direction must be updated to local surface normal!
	up_direction = current_up
	move_and_slide()

	# 6. Update Visual Orientation and Procedural Animation
	_update_visuals(wish_dir, tangent_vel, delta)

func _get_movement_input() -> Vector2:
	var move: Vector2 = Vector2.ZERO
	
	# Layer 1: Custom Actions
	if InputMap.has_action("move_left") and InputMap.has_action("move_right") and InputMap.has_action("move_forward") and InputMap.has_action("move_backward"):
		move = Input.get_vector("move_left", "move_right", "move_forward", "move_backward")
		
	# Layer 2: Godot built-in UI navigation actions (Arrow keys)
	if move.is_zero_approx():
		move = Input.get_vector("ui_left", "ui_right", "ui_up", "ui_down")
		
	# Layer 3: Physical key polling (bypasses Windows Chinese IME completely!)
	if move.is_zero_approx():
		if Input.is_physical_key_pressed(KEY_W) or Input.is_key_pressed(KEY_W) or Input.is_physical_key_pressed(KEY_UP) or Input.is_key_pressed(KEY_UP):
			move.y -= 1.0
		if Input.is_physical_key_pressed(KEY_S) or Input.is_key_pressed(KEY_S) or Input.is_physical_key_pressed(KEY_DOWN) or Input.is_key_pressed(KEY_DOWN):
			move.y += 1.0
		if Input.is_physical_key_pressed(KEY_A) or Input.is_key_pressed(KEY_A) or Input.is_physical_key_pressed(KEY_LEFT) or Input.is_key_pressed(KEY_LEFT):
			move.x -= 1.0
		if Input.is_physical_key_pressed(KEY_D) or Input.is_key_pressed(KEY_D) or Input.is_physical_key_pressed(KEY_RIGHT) or Input.is_key_pressed(KEY_RIGHT):
			move.x += 1.0
		if move.length_squared() > 1.0:
			move = move.normalized()
			
	return move

func _is_jump_requested() -> bool:
	if Input.is_action_just_pressed("jump") or Input.is_action_just_pressed("ui_accept"):
		return true
	var space_down: bool = Input.is_physical_key_pressed(KEY_SPACE) or Input.is_key_pressed(KEY_SPACE)
	if space_down:
		if not _space_was_down:
			_space_was_down = true
			return true
	else:
		_space_was_down = false
	return false

func _is_sprint_active() -> bool:
	return Input.is_action_pressed("sprint") or Input.is_physical_key_pressed(KEY_SHIFT) or Input.is_key_pressed(KEY_SHIFT)

func _is_reset_requested() -> bool:
	return Input.is_action_just_pressed("reset_player") or Input.is_physical_key_pressed(KEY_R) or Input.is_key_pressed(KEY_R)

func _update_gravity_and_up(delta: float) -> void:
	current_gravity = GravityManager.get_gravity_at(global_position, get_tree())
	var dominant: Planet = GravityManager.get_dominant_planet(global_position, get_tree())
	
	if dominant != current_planet:
		current_planet = dominant
		if current_planet:
			planet_changed.emit(current_planet.planet_name)
		else:
			planet_changed.emit("Zero-G Space")

	if current_gravity.length_squared() > 0.01:
		target_up = -current_gravity.normalized()
	elif current_planet:
		target_up = current_planet.get_up_direction_at(global_position)

	# Slerp current up direction to target up vector smoothly
	if not current_up.is_equal_approx(target_up) and target_up.length_squared() > 0.01:
		current_up = current_up.slerp(target_up, 14.0 * delta).normalized()

func _update_visuals(wish_dir: Vector3, tangent_vel: Vector3, delta: float) -> void:
	if not visuals:
		return

	# Align visual Y basis with current_up, and Z basis with facing
	var facing_dir: Vector3 = wish_dir
	if facing_dir.length_squared() < 0.01:
		# Use current facing projected onto tangent plane
		var cur_fwd: Vector3 = -visuals.global_basis.z
		facing_dir = (cur_fwd - cur_fwd.project(current_up)).normalized()
		if facing_dir.length_squared() < 0.01:
			facing_dir = current_up.cross(Vector3.RIGHT).normalized()

	var right_dir: Vector3 = facing_dir.cross(current_up).normalized()
	facing_dir = current_up.cross(right_dir).normalized()
	var target_basis: Basis = Basis(right_dir, current_up, -facing_dir).orthonormalized()

	visuals.global_basis = Basis(
		visuals.global_basis.get_rotation_quaternion().slerp(
			target_basis.get_rotation_quaternion(),
			rotation_speed * delta
		)
	).orthonormalized()

	# Procedural Animation: Walk/Run tilt & bob
	var speed: float = tangent_vel.length()
	if is_on_floor() and speed > 0.5:
		run_cycle += speed * delta * (18.0 if is_sprinting else 14.0)
		var bob: float = sin(run_cycle) * 0.08
		var tilt: float = sin(run_cycle * 0.5) * 0.06
		visuals.position.y = bob
		visuals.rotation.z = tilt
		
		# Footstep dust
		if dust_particles and not dust_particles.emitting:
			dust_particles.emitting = true
	else:
		run_cycle = 0.0
		visuals.position.y = lerpf(visuals.position.y, 0.0, 10.0 * delta)
		visuals.rotation.z = lerpf(visuals.rotation.z, 0.0, 10.0 * delta)
		if dust_particles and dust_particles.emitting:
			dust_particles.emitting = false

	# Landing squash recover
	if landing_squash > 0.0:
		landing_squash = maxf(0.0, landing_squash - 4.0 * delta)
		visuals.scale = Vector3(1.0 + landing_squash * 0.25, 1.0 - landing_squash * 0.35, 1.0 + landing_squash * 0.25)
	else:
		visuals.scale = Vector3.ONE

	# Thruster particles
	var should_thruster: bool = not is_on_floor() or is_sprinting
	if thruster_left:
		thruster_left.emitting = should_thruster
	if thruster_right:
		thruster_right.emitting = should_thruster

func _on_jump() -> void:
	SoundGenerator.play_sound_2d(self, SoundGenerator.jump_sound, -4.0)
	if dust_particles:
		dust_particles.restart()

func _on_land() -> void:
	landing_squash = 0.6
	SoundGenerator.play_sound_2d(self, SoundGenerator.land_sound, -2.0)
	if dust_particles:
		dust_particles.restart()

func apply_launch(launch_velocity: Vector3, boost_duration: float = 1.0) -> void:
	velocity = launch_velocity
	launch_timer = boost_duration
	jump_timer = boost_duration
	floor_snap_length = 0.0
	landing_squash = 0.0
	SoundGenerator.play_sound_2d(self, SoundGenerator.launch_sound, 0.0)
	if camera_rig:
		camera_rig.boost_fov_temporarily(95.0, boost_duration)

func add_star() -> void:
	stars_collected += 1
	SoundGenerator.play_sound_2d(self, SoundGenerator.star_sound, -2.0)
	star_collected.emit(stars_collected, total_stars)
	if stars_collected >= total_stars and total_stars > 0:
		SoundGenerator.play_sound_2d(self, SoundGenerator.victory_sound, 2.0)

func snap_to_orientation(new_up: Vector3) -> void:
	current_up = new_up.normalized()
	target_up = current_up
	up_direction = current_up

func respawn() -> void:
	global_position = respawn_position
	velocity = Vector3.ZERO
	current_up = respawn_up
	target_up = respawn_up
	up_direction = current_up
	launch_timer = 0.0
	jump_timer = 0.0
