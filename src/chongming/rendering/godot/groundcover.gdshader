shader_type spatial;
render_mode diffuse_burley, specular_disabled;

uniform vec3 base_color = vec3(0.18, 0.34, 0.09);
uniform float variation = 0.12;
varying vec3 world_position;

float hash21(vec2 p) {
	return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453123);
}

float value_noise(vec2 p) {
	vec2 cell = floor(p);
	vec2 f = fract(p);
	f = f * f * (3.0 - 2.0 * f);
	return mix(mix(hash21(cell), hash21(cell + vec2(1.0, 0.0)), f.x),
		mix(hash21(cell + vec2(0.0, 1.0)), hash21(cell + vec2(1.0)), f.x), f.y);
}

void vertex() {
	world_position = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz;
}

void fragment() {
	vec2 p = world_position.xz;
	float broad = value_noise(p * 0.075);
	float cell = hash21(floor(p * 0.42));
	float bands = 0.5 + 0.5 * sin(world_position.x * 0.08 + world_position.z * 0.05);
	float detail = (broad - 0.5) * 0.85 + (cell - 0.5) * 0.35 + (bands - 0.5) * 0.22;
	vec3 dry = vec3(0.26, 0.23, 0.10);
	vec3 tint = mix(base_color, dry, smoothstep(0.58, 0.88, broad) * 0.22);
	ALBEDO = tint * (1.0 + variation * detail);
	ROUGHNESS = 0.93 + 0.05 * cell;
}
