shader_type spatial;
render_mode diffuse_burley, specular_disabled;

uniform vec3 base_color = vec3(0.18, 0.34, 0.09);
uniform float variation = 0.12;
varying vec3 world_position;

float hash21(vec2 p) {
	return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453123);
}

void vertex() {
	world_position = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz;
}

void fragment() {
	float cell = hash21(floor(world_position.xz * 0.11));
	float bands = 0.5 + 0.5 * sin(world_position.x * 0.08 + world_position.z * 0.05);
	ALBEDO = base_color * (1.0 + variation * ((cell - 0.5) * 1.5 + (bands - 0.5) * 0.35));
	ROUGHNESS = 0.96;
}
