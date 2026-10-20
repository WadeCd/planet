class_name SoundGenerator
extends Node

static var jump_sound: AudioStreamWAV
static var land_sound: AudioStreamWAV
static var star_sound: AudioStreamWAV
static var launch_sound: AudioStreamWAV
static var victory_sound: AudioStreamWAV

static func initialize_sounds() -> void:
	if jump_sound != null:
		return
	jump_sound = _create_jump_sound()
	land_sound = _create_land_sound()
	star_sound = _create_star_sound()
	launch_sound = _create_launch_sound()
	victory_sound = _create_victory_sound()

static func play_sound_2d(parent: Node, stream: AudioStream, volume_db: float = 0.0) -> void:
	if stream == null or parent == null:
		return
	var player: AudioStreamPlayer = AudioStreamPlayer.new()
	player.stream = stream
	player.volume_db = volume_db
	parent.add_child(player)
	player.play()
	player.finished.connect(player.queue_free)

static func _create_jump_sound() -> AudioStreamWAV:
	# Frequency sweeps up: 200 Hz -> 550 Hz
	return _synthesize_sound(200.0, 550.0, 0.18, 0.4, "sine")

static func _create_land_sound() -> AudioStreamWAV:
	# Low thump: 120 Hz -> 50 Hz with quick decay
	return _synthesize_sound(120.0, 50.0, 0.15, 0.5, "triangle")

static func _create_star_sound() -> AudioStreamWAV:
	# Sparkly 2-tone chime: D5 (587Hz) and A5 (880Hz)
	var sample_rate: int = 22050
	var duration: float = 0.35
	var num_samples: int = int(sample_rate * duration)
	var data: PackedByteArray = PackedByteArray()
	data.resize(num_samples)
	
	var phase1: float = 0.0
	var phase2: float = 0.0
	for i in range(num_samples):
		var t: float = float(i) / float(num_samples)
		var freq1: float = 587.33 if t < 0.35 else 880.0
		var freq2: float = 880.0 if t < 0.35 else 1174.66
		phase1 += freq1 * (TAU / float(sample_rate))
		phase2 += freq2 * (TAU / float(sample_rate))
		
		var sample: float = (sin(phase1) * 0.6) + (sin(phase2) * 0.4)
		var env: float = (1.0 - t) * (1.0 - t)
		var val: int = clampi(int((sample * env * 0.45 * 127.0) + 128.0), 0, 255)
		data[i] = val
		
	var wav: AudioStreamWAV = AudioStreamWAV.new()
	wav.format = AudioStreamWAV.FORMAT_8_BITS
	wav.mix_rate = sample_rate
	wav.data = data
	return wav

static func _create_launch_sound() -> AudioStreamWAV:
	# Dramatic whoosh/charge sound: 180 Hz -> 900 Hz with square/sine blend
	return _synthesize_sound(180.0, 920.0, 0.45, 0.4, "sine")

static func _create_victory_sound() -> AudioStreamWAV:
	# 4-note victory fanfare: C5, E5, G5, C6
	var notes: Array[float] = [523.25, 659.25, 783.99, 1046.50]
	var sample_rate: int = 22050
	var note_dur: float = 0.18
	var total_dur: float = note_dur * 4.0
	var num_samples: int = int(sample_rate * total_dur)
	var data: PackedByteArray = PackedByteArray()
	data.resize(num_samples)
	
	var phase: float = 0.0
	for i in range(num_samples):
		var t_total: float = float(i) / float(sample_rate)
		var note_idx: int = clampi(int(t_total / note_dur), 0, 3)
		var note_t: float = fmod(t_total, note_dur) / note_dur
		
		var freq: float = notes[note_idx]
		phase += freq * (TAU / float(sample_rate))
		var sample: float = sin(phase) * 0.7 + sin(phase * 2.0) * 0.3
		var env: float = 1.0 - (note_t * 0.8)
		if note_idx == 3:
			env = (1.0 - (note_t * 0.4)) # sustain last note
		var val: int = clampi(int((sample * env * 0.45 * 127.0) + 128.0), 0, 255)
		data[i] = val
		
	var wav: AudioStreamWAV = AudioStreamWAV.new()
	wav.format = AudioStreamWAV.FORMAT_8_BITS
	wav.mix_rate = sample_rate
	wav.data = data
	return wav

static func _synthesize_sound(freq_start: float, freq_end: float, duration: float, volume: float, wave_type: String) -> AudioStreamWAV:
	var sample_rate: int = 22050
	var num_samples: int = int(sample_rate * duration)
	var data: PackedByteArray = PackedByteArray()
	data.resize(num_samples)
	
	var phase: float = 0.0
	for i in range(num_samples):
		var t: float = float(i) / float(num_samples)
		var freq: float = lerp(freq_start, freq_end, t)
		phase += freq * (TAU / float(sample_rate))
		
		var sample: float = 0.0
		if wave_type == "sine":
			sample = sin(phase)
		elif wave_type == "triangle":
			sample = asin(sin(phase)) * (2.0 / PI)
		elif wave_type == "square":
			sample = 1.0 if sin(phase) >= 0.0 else -1.0
			
		var env: float = 1.0 - (t * t)
		var val: int = clampi(int((sample * env * volume * 127.0) + 128.0), 0, 255)
		data[i] = val
		
	var wav: AudioStreamWAV = AudioStreamWAV.new()
	wav.format = AudioStreamWAV.FORMAT_8_BITS
	wav.mix_rate = sample_rate
	wav.data = data
	return wav
