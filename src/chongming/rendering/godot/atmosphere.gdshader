shader_type sky;
// A directional Rayleigh-inspired approximation, not a spectral atmosphere solver.
// No TIME, frame history, random sampling or external textures.
uniform vec3 sky_tint = vec3(0.42, 0.63, 0.86);
uniform vec3 sun_direction = vec3(0.0, 0.7, -0.7);
uniform float sun_radius = 0.004625;
uniform float sun_energy = 1.0;
uniform int cloud_count = 0;
uniform vec4 cloud_layer_0 = vec4(0.0);
uniform vec4 cloud_layer_1 = vec4(0.0);
uniform vec2 camera_horizontal = vec2(0.0);
uniform float camera_altitude = 0.0;
uniform float cloud_time = 0.0;

vec2 gradient(vec2 cell) {
	float angle = fract(sin(dot(cell, vec2(127.1, 311.7))) * 43758.5453) * 6.2831853;
	return vec2(cos(angle), sin(angle));
}

float perlin(vec2 p) {
	vec2 cell = floor(p);
	vec2 f = fract(p);
	vec2 u = f * f * f * (f * (f * 6.0 - 15.0) + 10.0);
	float a = dot(gradient(cell), f);
	float b = dot(gradient(cell + vec2(1.0, 0.0)), f - vec2(1.0, 0.0));
	float c = dot(gradient(cell + vec2(0.0, 1.0)), f - vec2(0.0, 1.0));
	float d = dot(gradient(cell + vec2(1.0)), f - vec2(1.0));
	return mix(mix(a, b, u.x), mix(c, d, u.x), u.y) * 0.5 + 0.5;
}

float voronoi(vec2 p) {
	vec2 cell = floor(p);
	vec2 f = fract(p);
	float distance_to_cell = 8.0;
	for (int y = -1; y <= 1; y++) {
		for (int x = -1; x <= 1; x++) {
			vec2 neighbor = vec2(float(x), float(y));
			vec2 point = gradient(cell + neighbor) * 0.42 + 0.5;
			distance_to_cell = min(distance_to_cell, length(neighbor + point - f));
		}
	}
	return clamp(distance_to_cell, 0.0, 1.0);
}

float cloud_density(vec3 ray, vec4 layer, float layer_index) {
	if (ray.y <= 0.012 || layer.x <= camera_altitude || layer.y <= 0.0) {
		return 0.0;
	}
	float travel = min((layer.x - camera_altitude) / ray.y, 120000.0);
	float scale = 1450.0 + layer_index * 850.0;
	vec2 p = (camera_horizontal + ray.xz * travel - layer.zw * cloud_time) / scale;
	vec2 warp = vec2(perlin(p * 0.47 + vec2(7.1, 19.7)),
		perlin(p * 0.47 + vec2(31.3, 3.9))) - vec2(0.5);
	vec2 q = p + warp * 1.35;
	float p0 = perlin(q);
	float p1 = perlin(q * 2.03 + vec2(13.0, 5.0));
	float p2 = perlin(q * 4.11 + vec2(2.0, 23.0));
	float cells = voronoi(q * 0.82 + vec2(17.0, 11.0));
	float shape = p0 * 0.57 + p1 * 0.28 + p2 * 0.15 - cells * 0.13;
	float threshold = mix(0.57, 0.38, layer.y);
	return smoothstep(threshold, threshold + 0.13, shape) * layer.y;
}

void sky() {
	vec3 ray = normalize(EYEDIR);
	float mu = clamp(dot(ray, sun_direction), -1.0, 1.0);
	float elevation = max(ray.y, 0.0);
	float horizon = pow(1.0 - elevation, 4.0);
	float phase = 0.75 * (1.0 + mu * mu);
	vec3 zenith = sky_tint * vec3(0.32, 0.55, 0.95) * (0.75 + 0.25 * phase);
	vec3 haze = mix(sky_tint, vec3(0.72, 0.76, 0.79), 0.65);
	vec3 color = mix(zenith, haze, horizon * 0.85);
	if (ray.y < 0.0) {
		color = mix(haze, vec3(0.13, 0.16, 0.12), min(-ray.y * 2.0, 1.0));
	}
	float clouds = 0.0;
	if (cloud_count > 0) {
		clouds = cloud_density(ray, cloud_layer_0, 0.0);
	}
	if (cloud_count > 1) {
		clouds = 1.0 - (1.0 - clouds) * (1.0 - cloud_density(ray, cloud_layer_1, 1.0));
	}
	float sun_transmission = exp(-clouds * 3.4);
	if (sun_radius > 0.0 && sun_direction.y > 0.0) {
		float angle = acos(mu);
		float disc = 1.0 - smoothstep(sun_radius * 0.92, sun_radius * 1.08, angle);
		float halo = exp(-angle / max(sun_radius * 12.0, 0.01)) * 0.22;
		// Cloud extinction and warm forward scattering share the same deterministic mask.
		color += vec3(1.0, 0.86, 0.65) * (disc * 6.0 + halo) * sun_energy * sun_transmission;
	}
	if (clouds > 0.0) {
		float silver_lining = pow(max(mu, 0.0), 18.0) * (1.0 - clouds * 0.55);
		vec3 cloud_color = mix(vec3(0.52, 0.55, 0.58), vec3(0.96, 0.94, 0.88),
			clamp(ray.y * 0.75 + silver_lining, 0.0, 1.0));
		color = mix(color, cloud_color + vec3(1.0, 0.78, 0.48) * silver_lining * 0.18,
			clamp(clouds * 0.92, 0.0, 0.92));
	}
	COLOR = color;
}
