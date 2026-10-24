shader_type spatial;
render_mode diffuse_burley, specular_schlick_ggx;

uniform vec3 base_color = vec3(0.58, 0.57, 0.54);
uniform float seed = 0.0;

varying vec3 world_position;
varying vec3 world_normal;

float hash21(vec2 p) {
	return fract(sin(dot(p + seed, vec2(127.1, 311.7))) * 43758.5453123);
}

void vertex() {
	world_position = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz;
	world_normal = normalize(MODEL_NORMAL_MATRIX * NORMAL);
}

void fragment() {
	vec3 color = base_color;
	float roof = smoothstep(0.72, 0.96, abs(world_normal.y));
	float floor_phase = fract(world_position.y * 0.31);
	float floor_line = smoothstep(0.44 - fwidth(floor_phase), 0.50 + fwidth(floor_phase), floor_phase);
	vec2 wall = abs(world_normal.x) > abs(world_normal.z) ? world_position.zy : world_position.xy;
	vec2 cell = fract(wall * vec2(0.24, 0.42));
	vec2 pixel_span = fwidth(wall * vec2(0.24, 0.42));
	float detail_fade = 1.0 - smoothstep(0.12, 0.65, max(pixel_span.x, pixel_span.y));
	float edge = max(0.001, max(pixel_span.x, pixel_span.y));
	float frame = smoothstep(0.15 - edge, 0.15 + edge, cell.x) * (1.0 - smoothstep(0.86 - edge, 0.86 + edge, cell.x));
	frame *= smoothstep(0.18 - edge, 0.18 + edge, cell.y) * (1.0 - smoothstep(0.82 - edge, 0.82 + edge, cell.y));
	float variation = (hash21(floor(world_position.xz * 0.11)) - 0.5) * 0.10;
	vec3 window = vec3(0.06, 0.09, 0.10) * (0.8 + 0.2 * hash21(floor(wall * 0.24)));
	color *= 1.0 + variation;
	color = mix(color, color * 0.72, floor_line * 0.35 * detail_fade);
	color = mix(color, window, (1.0 - roof) * frame * 0.52 * detail_fade);
	color = mix(color, color * 0.72, roof * 0.28);
	ALBEDO = color;
	ROUGHNESS = 0.88;
	SPECULAR = 0.08;
}
