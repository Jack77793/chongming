shader_type spatial;
render_mode diffuse_burley, specular_schlick_ggx;

uniform vec3 base_color = vec3(0.16, 0.22, 0.12);
uniform float detail_strength = 0.0;
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
	float broad = value_noise(p * 0.012);
	float clumps = value_noise(p * 0.065 + vec2(19.0, 7.0));
	vec2 grit_frequency = p * 0.72;
	vec2 footprint = fwidth(grit_frequency);
	float fine_detail = 1.0 - smoothstep(0.18, 0.8, max(footprint.x, footprint.y));
	float grit = value_noise(grit_frequency + vec2(3.0, 29.0));
	float variation = (broad - 0.5) * 0.24 + (clumps - 0.5) * 0.10
		+ (grit - 0.5) * 0.035 * fine_detail;
	vec3 warm_soil = vec3(0.24, 0.19, 0.11);
	vec3 vegetation_tint = vec3(0.12, 0.25, 0.08);
	vec3 tint = mix(warm_soil, vegetation_tint, smoothstep(0.34, 0.68, broad));
	ALBEDO = mix(base_color, base_color * (1.0 + variation) + tint * 0.16, detail_strength);
	ROUGHNESS = mix(0.95, 0.82 + grit * 0.14, detail_strength);
	SPECULAR = 0.05;
}
