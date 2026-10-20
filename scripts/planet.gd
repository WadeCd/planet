class_name Planet
extends StaticBody3D

@export var planet_name: String = "小星球"
@export var radius: float = 20.0:
	set(val):
		radius = maxf(2.0, val)
		if is_inside_tree():
			_update_shapes()
			_setup_materials()
@export var gravity_strength: float = 16.0
@export var gravity_radius: float = 55.0
@export var surface_color: Color = Color(0.25, 0.65, 0.35)
@export var accent_color: Color = Color(0.15, 0.45, 0.25)
@export var roughness: float = 0.8
@export var metallic: float = 0.05
@export var has_atmosphere: bool = true
@export var atmosphere_color: Color = Color(0.3, 0.7, 1.0, 0.2)
@export var spin_speed: float = 0.015

func _ready() -> void:
	add_to_group("planets")
	_update_shapes()
	_setup_materials()

func _process(delta: float) -> void:
	var s_mesh: Node3D = get_node_or_null("SurfaceMesh")
	var a_mesh: Node3D = get_node_or_null("AtmosphereMesh")
	if spin_speed != 0.0 and s_mesh:
		s_mesh.rotate_y(spin_speed * delta)
		if a_mesh:
			a_mesh.rotate_y(spin_speed * 1.2 * delta)

func _update_shapes() -> void:
	if not is_inside_tree():
		return
		
	# Create unique collision shape per planet instance
	var col: CollisionShape3D = get_node_or_null("CollisionShape3D") as CollisionShape3D
	if col:
		var sphere_shape: SphereShape3D = SphereShape3D.new()
		sphere_shape.radius = radius
		col.shape = sphere_shape

	# Create unique surface mesh per planet instance
	var s_mesh: MeshInstance3D = get_node_or_null("SurfaceMesh") as MeshInstance3D
	if s_mesh:
		var sphere_mesh: SphereMesh = SphereMesh.new()
		sphere_mesh.radius = radius
		sphere_mesh.height = radius * 2.0
		sphere_mesh.radial_segments = 64
		sphere_mesh.rings = 32
		s_mesh.mesh = sphere_mesh

	# Create unique atmosphere mesh per planet instance
	var a_mesh: MeshInstance3D = get_node_or_null("AtmosphereMesh") as MeshInstance3D
	if a_mesh:
		a_mesh.visible = has_atmosphere
		if has_atmosphere:
			var atmo_mesh: SphereMesh = SphereMesh.new()
			atmo_mesh.radius = radius * 1.08
			atmo_mesh.height = radius * 2.16
			atmo_mesh.radial_segments = 48
			atmo_mesh.rings = 24
			a_mesh.mesh = atmo_mesh

func _setup_materials() -> void:
	var s_mesh: MeshInstance3D = get_node_or_null("SurfaceMesh") as MeshInstance3D
	if s_mesh:
		var mat: StandardMaterial3D = StandardMaterial3D.new()
		mat.albedo_color = surface_color
		mat.roughness = roughness
		mat.metallic = metallic
		s_mesh.material_override = mat

	var a_mesh: MeshInstance3D = get_node_or_null("AtmosphereMesh") as MeshInstance3D
	if a_mesh and has_atmosphere:
		var atmo_mat: StandardMaterial3D = StandardMaterial3D.new()
		atmo_mat.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
		atmo_mat.albedo_color = atmosphere_color
		atmo_mat.rim_enabled = true
		atmo_mat.rim = 1.0
		atmo_mat.rim_tint = 0.6
		atmo_mat.cull_mode = BaseMaterial3D.CULL_BACK
		a_mesh.material_override = atmo_mat

func get_gravity_at(point: Vector3) -> Vector3:
	var diff: Vector3 = global_position - point
	var dist: float = diff.length()
	if dist > gravity_radius or dist <= 0.001:
		return Vector3.ZERO
	var dir: Vector3 = diff.normalized()
	if dist <= radius:
		return dir * gravity_strength
	# Smooth falloff between radius and gravity_radius
	var factor: float = 1.0 - ((dist - radius) / (gravity_radius - radius))
	return dir * (gravity_strength * factor)

func get_up_direction_at(point: Vector3) -> Vector3:
	var diff: Vector3 = point - global_position
	if diff.is_zero_approx():
		return Vector3.UP
	return diff.normalized()

func get_distance_to_surface(point: Vector3) -> float:
	return point.distance_to(global_position) - radius

func is_point_in_gravity_field(point: Vector3) -> bool:
	return point.distance_to(global_position) <= gravity_radius
