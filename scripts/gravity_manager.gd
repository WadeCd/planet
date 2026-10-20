class_name GravityManager
extends Node

static var instance: GravityManager

func _enter_tree() -> void:
	instance = self

static func get_gravity_at(point: Vector3, tree: SceneTree) -> Vector3:
	var total_gravity: Vector3 = Vector3.ZERO
	var planets = tree.get_nodes_in_group("planets")
	for p in planets:
		if p is Planet:
			total_gravity += p.get_gravity_at(point)
	return total_gravity

static func get_dominant_planet(point: Vector3, tree: SceneTree) -> Planet:
	var best_planet: Planet = null
	var min_surface_dist: float = INF
	var planets = tree.get_nodes_in_group("planets")
	for p in planets:
		if p is Planet:
			var s_dist: float = p.get_distance_to_surface(point)
			if s_dist < min_surface_dist:
				min_surface_dist = s_dist
				best_planet = p
	return best_planet
