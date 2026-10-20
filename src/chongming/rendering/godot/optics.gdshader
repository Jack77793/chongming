shader_type canvas_item;
render_mode unshaded;
uniform sampler2D scene_texture : filter_linear, repeat_disable;
uniform vec2 pixel_size;
uniform float strength = 0.35;
uniform vec2 sun_uv = vec2(-10.0);
uniform vec2 sun_radius_uv = vec2(0.0);
uniform vec2 focal_uv = vec2(1.0);
uniform float god_rays_strength = 0.0;
uniform bool fxaa_enabled = false;
varying float exposure_gain;
varying float solar_signal;

void vertex() {
	// The four vertices share the same deterministic metering grid. No temporal
	// adaptation, so reordered cameras, pauses and skipped pixel requests agree.
	float meter = 0.0;
	for (int y = 0; y < 6; y++) {
		for (int x = 0; x < 8; x++) {
			vec3 sample_color = texture(scene_texture, (vec2(float(x), float(y)) + vec2(0.5)) / vec2(8.0, 6.0)).rgb;
			float luminance = dot(sample_color, vec3(0.2126, 0.7152, 0.0722));
			meter += max(luminance - 0.55, 0.0);
		}
	}
	// Meter the actual projected solar image as well as the coarse scene grid.
	// Dark occluders erase this signal. Bright white occluders are deliberately
	// treated as highlights too; this is a cheap image-based lens approximation.
	solar_signal = 0.0;
	for (int y = -1; y <= 1; y++) {
		for (int x = -1; x <= 1; x++) {
			vec2 probe_uv = sun_uv + vec2(float(x), float(y)) * sun_radius_uv * 0.45;
			if (all(greaterThanEqual(probe_uv, vec2(0.0))) && all(lessThanEqual(probe_uv, vec2(1.0)))) {
				vec3 probe = texture(scene_texture, probe_uv).rgb;
				float probe_luminance = dot(probe, vec3(0.2126, 0.7152, 0.0722));
				solar_signal += smoothstep(0.72, 0.98, probe_luminance) / 9.0;
			}
		}
	}
	exposure_gain = 1.0 / (1.0 + strength * (meter / 48.0 * 3.0 + solar_signal * 1.8));
}

void fragment() {
	vec3 base = texture(scene_texture, UV).rgb;
	if (fxaa_enabled) {
		vec3 north = texture(scene_texture, clamp(UV + vec2(0.0, -pixel_size.y), vec2(0.0), vec2(1.0))).rgb;
		vec3 south = texture(scene_texture, clamp(UV + vec2(0.0, pixel_size.y), vec2(0.0), vec2(1.0))).rgb;
		vec3 east = texture(scene_texture, clamp(UV + vec2(pixel_size.x, 0.0), vec2(0.0), vec2(1.0))).rgb;
		vec3 west = texture(scene_texture, clamp(UV + vec2(-pixel_size.x, 0.0), vec2(0.0), vec2(1.0))).rgb;
		vec4 luma = vec4(dot(north, vec3(0.2126, 0.7152, 0.0722)),
			dot(south, vec3(0.2126, 0.7152, 0.0722)),
			dot(east, vec3(0.2126, 0.7152, 0.0722)), dot(west, vec3(0.2126, 0.7152, 0.0722)));
		float center_luma = dot(base, vec3(0.2126, 0.7152, 0.0722));
		float minimum_luma = min(center_luma, min(min(luma.x, luma.y), min(luma.z, luma.w)));
		float maximum_luma = max(center_luma, max(max(luma.x, luma.y), max(luma.z, luma.w)));
		float edge = smoothstep(0.035, 0.15, maximum_luma - minimum_luma);
		vec3 horizontal = base * 0.5 + (east + west) * 0.25;
		vec3 vertical = base * 0.5 + (north + south) * 0.25;
		float horizontal_weight = step(abs(luma.z - luma.w), abs(luma.x - luma.y));
		base = mix(base, mix(vertical, horizontal, horizontal_weight), edge * 0.58);
	}
	vec3 glow = vec3(0.0);
	// Small fixed kernel: adds sensor/lens spread only from visible highlights.
	// This is LDR artistic optics, not calibrated HDR photometry or lens ghosts.
	for (int y = -2; y <= 2; y++) {
		for (int x = -2; x <= 2; x++) {
			vec2 offset = vec2(float(x), float(y)) * pixel_size * 2.0;
			vec3 source = texture(scene_texture, clamp(UV + offset, vec2(0.0), vec2(1.0))).rgb;
			float weight = exp(-float(x*x + y*y) * 0.5);
			glow += max(source - vec3(0.85), vec3(0.0)) * weight / 6.17;
		}
	}
	float angular_distance = length((UV - sun_uv) / focal_uv);
	vec3 veil = vec3(1.0, 0.88, 0.68) * exp(-angular_distance / 0.13) * solar_signal * 0.12;
	vec3 shafts = vec3(0.0);
	vec2 shaft_uv = UV;
	vec2 shaft_step = (sun_uv - UV) * 0.075;
	float decay = 1.0;
	for (int sample_index = 0; sample_index < 8; sample_index++) {
		shaft_uv += shaft_step;
		vec3 source = texture(scene_texture, clamp(shaft_uv, vec2(0.0), vec2(1.0))).rgb;
		float source_luminance = dot(source, vec3(0.2126, 0.7152, 0.0722));
		shafts += max(source - vec3(0.72), vec3(0.0)) * smoothstep(0.72, 1.0, source_luminance) * decay;
		decay *= 0.84;
	}
	float sun_in_frame = (all(greaterThanEqual(sun_uv, vec2(0.0))) && all(lessThanEqual(sun_uv, vec2(1.0)))) ? 1.0 : 0.0;
	shafts *= vec3(1.0, 0.86, 0.66) * god_rays_strength * sun_in_frame * solar_signal / 4.0;
	COLOR = vec4((base + (glow + veil) * strength + shafts) * exposure_gain, 1.0);
}
