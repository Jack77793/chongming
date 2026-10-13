import Foundation
import Metal

struct RenderRequest: Decodable {
    let width: Int
    let height: Int
    let focal: [Float]
    let origin: [Float]
    let rotation: [Float]
    let skew: Float
    let sky: [Float]
    let sun: [Float]
    let illuminance: Float
    let terrainElevation: Float
    let entities: [EntityRequest]
}

struct EntityRequest: Decodable {
    let type: Int
    let position: [Float]
    let rotation: [Float]
    let color: [Float]
    let reflectance: Float
    let parameters: [Float]
    let vertices: [[Float]]?
    let triangles: [[Int]]?
}

struct EntityGPU {
    var type: UInt32
    var vertexOffset: UInt32
    var triangleOffset: UInt32
    var triangleCount: UInt32
    var position: SIMD4<Float>
    var color: SIMD4<Float>
    var parameters: SIMD4<Float>
    var rotation0: SIMD4<Float>
    var rotation1: SIMD4<Float>
    var rotation2: SIMD4<Float>
}

struct VertexGPU { var position: SIMD4<Float> }
struct TriangleGPU { var indices: SIMD4<UInt32> }

struct CameraGPU {
    var origin: SIMD4<Float>
    var focal: SIMD4<Float>
    var rotation0: SIMD4<Float>
    var rotation1: SIMD4<Float>
    var rotation2: SIMD4<Float>
    var sky: SIMD4<Float>
    var sun: SIMD4<Float>
    var settings: SIMD4<Float>
    var dimensions: SIMD4<UInt32>
}

let shaderSource = """
#include <metal_stdlib>
using namespace metal;

struct EntityGPU {
    uint type; uint vertexOffset; uint triangleOffset; uint triangleCount;
    float4 position; float4 color; float4 parameters;
    float4 rotation0; float4 rotation1; float4 rotation2;
};
struct VertexGPU { float4 position; };
struct TriangleGPU { uint4 indices; };
struct CameraGPU {
    float4 origin; float4 focal; float4 rotation0; float4 rotation1;
    float4 rotation2; float4 sky; float4 sun; float4 settings; uint4 dimensions;
};

float3 columnsMultiply(float3 v, float4 r0, float4 r1, float4 r2) {
    return float3(dot(v, float3(r0.x, r1.x, r2.x)),
                  dot(v, float3(r0.y, r1.y, r2.y)),
                  dot(v, float3(r0.z, r1.z, r2.z)));
}
float3 rowsMultiply(float3 v, float4 r0, float4 r1, float4 r2) {
    return float3(dot(v, r0.xyz), dot(v, r1.xyz), dot(v, r2.xyz));
}
float3 toWorld(float3 local, const device EntityGPU &e) {
    return rowsMultiply(local, e.rotation0, e.rotation1, e.rotation2);
}
float3 toLocal(float3 world, const device EntityGPU &e) {
    return columnsMultiply(world, e.rotation0, e.rotation1, e.rotation2);
}

bool sphereHit(float3 o, float3 d, float radius, thread float &t, thread float3 &n) {
    float a = dot(d, d); float b = dot(o, d); float c = dot(o, o) - radius * radius;
    float disc = b * b - a * c;
    if (disc < 0.0 || a < 1e-12) return false;
    float root = sqrt(disc); float nearT = (-b - root) / a; float farT = (-b + root) / a;
    t = nearT > 1e-5 ? nearT : farT;
    if (t <= 1e-5) return false;
    n = normalize(o + t * d) ; return true;
}

bool boxHit(float3 o, float3 d, float3 h, thread float &t, thread float3 &n) {
    float3 nearT = (-h - o) / d; float3 farT = (h - o) / d;
    float3 entry = min(nearT, farT); float3 exit = max(nearT, farT);
    float tNear = max(max(entry.x, entry.y), entry.z);
    float tFar = min(min(exit.x, exit.y), exit.z);
    t = tNear > 1e-5 ? tNear : tFar;
    if (tFar < tNear || t <= 1e-5) return false;
    float3 p = o + t * d; float3 q = abs(p / h);
    if (q.x >= q.y && q.x >= q.z) n = float3(sign(p.x), 0, 0);
    else if (q.y >= q.z) n = float3(0, sign(p.y), 0);
    else n = float3(0, 0, sign(p.z));
    return true;
}

bool triangleHit(float3 o, float3 d, float3 v0, float3 v1, float3 v2,
                 thread float &t, thread float3 &n) {
    float3 e1 = v1 - v0, e2 = v2 - v0;
    float3 p = cross(d, e2); float det = dot(e1, p);
    if (abs(det) < 1e-8) return false;
    float inv = 1.0 / det; float3 s = o - v0;
    float u = dot(s, p) * inv; if (u < -1e-6 || u > 1.0 + 1e-6) return false;
    float3 q = cross(s, e1); float v = dot(d, q) * inv;
    if (v < -1e-6 || u + v > 1.0 + 1e-6) return false;
    t = dot(e2, q) * inv; if (t <= 1e-5) return false;
    n = normalize(cross(e1, e2)); if (dot(n, d) > 0.0) n = -n;
    return true;
}

kernel void renderKernel(
    device uchar *output [[buffer(0)]],
    constant CameraGPU &camera [[buffer(1)]],
    device const EntityGPU *entities [[buffer(2)]],
    device const VertexGPU *vertices [[buffer(3)]],
    device const TriangleGPU *triangles [[buffer(4)]],
    uint2 gid [[thread_position_in_grid]]) {
    uint width = camera.dimensions.x, height = camera.dimensions.y;
    if (gid.x >= width || gid.y >= height) return;
    float x = (float(gid.x) - camera.focal.z);
    float y = (float(gid.y) - camera.focal.w);
    float3 rayCamera = float3((x - camera.settings.x * y / camera.focal.y) / camera.focal.x,
                              y / camera.focal.y, 1.0);
    float3 rayWorld = rowsMultiply(rayCamera, camera.rotation0, camera.rotation1, camera.rotation2);
    float3 origin = camera.origin.xyz;
    float3 sun = normalize(camera.sun.xyz);
    float3 color = camera.sky.xyz;
    float closest = INFINITY; float3 closestNormal = float3(0);
    float3 closestColor = color; float closestReflectance = 1.0;
    float groundT = (camera.settings.z - origin.z) / rayWorld.z;
    if (groundT > 1e-5 && groundT < closest) {
        closest = groundT; closestNormal = float3(0, 0, 1);
        closestColor = float3(0.36, 0.41, 0.32); closestReflectance = 0.9;
    }
    for (uint index = 0; index < camera.dimensions.z; index++) {
        const device EntityGPU &entity = entities[index];
        float3 localOrigin = toLocal(origin - entity.position.xyz, entity);
        float3 localDirection = toLocal(rayWorld, entity);
        float hitT = INFINITY; float3 localNormal = float3(0);
        bool hit = false;
        if (entity.type == 0) hit = sphereHit(localOrigin, localDirection, entity.parameters.x, hitT, localNormal);
        else if (entity.type == 1) hit = boxHit(localOrigin, localDirection, entity.parameters.xyz, hitT, localNormal);
        else {
            for (uint tri = 0; tri < entity.triangleCount; tri++) {
                uint3 ids = triangles[entity.triangleOffset + tri].indices.xyz;
                float t; float3 n;
                if (triangleHit(localOrigin, localDirection,
                                vertices[entity.vertexOffset + ids.x].position.xyz,
                                vertices[entity.vertexOffset + ids.y].position.xyz,
                                vertices[entity.vertexOffset + ids.z].position.xyz, t, n) && t < hitT) {
                    hitT = t; localNormal = n; hit = true;
                }
            }
        }
        if (hitT < closest && hit) {
            closest = hitT; closestNormal = toWorld(localNormal, entity);
            closestColor = entity.color.xyz; closestReflectance = entity.color.w;
        }
    }
    if (closest < INFINITY) {
        float diffuse = max(0.0, dot(normalize(closestNormal), sun));
        color = closestColor * closestReflectance * (0.3 + 0.7 * diffuse) * (camera.settings.y / 20000.0);
    }
    color = clamp(color, 0.0, 1.0);
    uint offset = (gid.y * width + gid.x) * 3;
    uchar3 rgb = uchar3(color * 255.0);
    output[offset] = rgb.x;
    output[offset + 1] = rgb.y;
    output[offset + 2] = rgb.z;
}
"""

final class MetalRenderer {
    let device: MTLDevice
    let queue: MTLCommandQueue
    let pipeline: MTLComputePipelineState
    var outputBuffer: MTLBuffer?
    var outputCapacity = 0
    var cameraBuffer: MTLBuffer?
    var cameraCapacity = 0
    var entityBuffer: MTLBuffer?
    var entityCapacity = 0
    var vertexBuffer: MTLBuffer?
    var vertexCapacity = 0
    var triangleBuffer: MTLBuffer?
    var triangleCapacity = 0

    init() throws {
        guard let device = MTLCreateSystemDefaultDevice() else { throw NSError(domain: "Metal", code: 1) }
        self.device = device
        guard let queue = device.makeCommandQueue() else { throw NSError(domain: "Metal", code: 2) }
        self.queue = queue
        let library = try device.makeLibrary(source: shaderSource, options: nil)
        guard let function = library.makeFunction(name: "renderKernel") else { throw NSError(domain: "Metal", code: 3) }
        self.pipeline = try device.makeComputePipelineState(function: function)
    }

    func reusableBuffer(
        _ buffer: inout MTLBuffer?,
        capacity: inout Int,
        required: Int
    ) throws -> MTLBuffer {
        let size = max(1, required)
        if let buffer, capacity >= size { return buffer }
        guard let replacement = device.makeBuffer(length: size, options: .storageModeShared) else {
            throw NSError(domain: "Metal", code: 7)
        }
        buffer = replacement
        capacity = size
        return replacement
    }

    func copyBytes<T>(_ value: inout T, to buffer: MTLBuffer, length: Int) {
        withUnsafeBytes(of: &value) { raw in
            buffer.contents().copyMemory(from: raw.baseAddress!, byteCount: length)
        }
    }

    func copyArray<T>(_ values: [T], to buffer: MTLBuffer) {
        guard !values.isEmpty else { return }
        values.withUnsafeBytes { raw in
            buffer.contents().copyMemory(from: raw.baseAddress!, byteCount: raw.count)
        }
    }

    func render(_ request: RenderRequest) throws -> (Data, UInt64) {
        guard request.width > 0, request.height > 0, request.focal.count == 4,
              request.origin.count == 3, request.rotation.count == 9,
              request.sky.count == 3, request.sun.count == 3 else {
            throw NSError(domain: "Metal", code: 4)
        }
        var entities = [EntityGPU](); var vertices = [VertexGPU](); var triangles = [TriangleGPU]()
        for entity in request.entities {
            guard entity.position.count == 3, entity.rotation.count == 9,
                  entity.color.count == 3, entity.parameters.count <= 4 else { throw NSError(domain: "Metal", code: 5) }
            let vo = UInt32(vertices.count), to = UInt32(triangles.count)
            for vertex in entity.vertices ?? [] { guard vertex.count == 3 else { throw NSError(domain: "Metal", code: 6) }; vertices.append(VertexGPU(position: SIMD4(vertex[0], vertex[1], vertex[2], 0))) }
            for triangle in entity.triangles ?? [] { guard triangle.count == 3 else { throw NSError(domain: "Metal", code: 7) }; triangles.append(TriangleGPU(indices: SIMD4(UInt32(triangle[0]), UInt32(triangle[1]), UInt32(triangle[2]), 0))) }
            var p = SIMD4<Float>(repeating: 0); for i in 0..<entity.parameters.count { p[i] = entity.parameters[i] }
            entities.append(EntityGPU(type: UInt32(entity.type), vertexOffset: vo, triangleOffset: to, triangleCount: UInt32(triangles.count) - to,
                                      position: SIMD4(entity.position[0], entity.position[1], entity.position[2], 0),
                                      color: SIMD4(entity.color[0], entity.color[1], entity.color[2], entity.reflectance), parameters: p,
                                      rotation0: SIMD4(entity.rotation[0], entity.rotation[1], entity.rotation[2], 0),
                                      rotation1: SIMD4(entity.rotation[3], entity.rotation[4], entity.rotation[5], 0),
                                      rotation2: SIMD4(entity.rotation[6], entity.rotation[7], entity.rotation[8], 0)))
        }
        var camera = CameraGPU(origin: SIMD4(request.origin[0], request.origin[1], request.origin[2], 0),
                               focal: SIMD4(request.focal[0], request.focal[1], request.focal[2], request.focal[3]),
                               rotation0: SIMD4(request.rotation[0], request.rotation[1], request.rotation[2], 0),
                               rotation1: SIMD4(request.rotation[3], request.rotation[4], request.rotation[5], 0),
                               rotation2: SIMD4(request.rotation[6], request.rotation[7], request.rotation[8], 0),
                               sky: SIMD4(request.sky[0], request.sky[1], request.sky[2], 0),
                               sun: SIMD4(request.sun[0], request.sun[1], request.sun[2], 0),
                               settings: SIMD4(request.skew, request.illuminance, request.terrainElevation, 0),
                               dimensions: SIMD4(UInt32(request.width), UInt32(request.height), UInt32(entities.count), 0))
        let outputLength = request.width * request.height * 3
        let output = try reusableBuffer(&outputBuffer, capacity: &outputCapacity, required: outputLength)
        let cameraBuffer = try reusableBuffer(&self.cameraBuffer, capacity: &cameraCapacity, required: MemoryLayout<CameraGPU>.stride)
        let entityBuffer = try reusableBuffer(&self.entityBuffer, capacity: &entityCapacity, required: max(1, entities.count) * MemoryLayout<EntityGPU>.stride)
        let vertexBuffer = try reusableBuffer(&self.vertexBuffer, capacity: &vertexCapacity, required: max(1, vertices.count) * MemoryLayout<VertexGPU>.stride)
        let triangleBuffer = try reusableBuffer(&self.triangleBuffer, capacity: &triangleCapacity, required: max(1, triangles.count) * MemoryLayout<TriangleGPU>.stride)
        copyBytes(&camera, to: cameraBuffer, length: MemoryLayout<CameraGPU>.stride)
        copyArray(entities, to: entityBuffer)
        copyArray(vertices, to: vertexBuffer)
        copyArray(triangles, to: triangleBuffer)
        guard let command = queue.makeCommandBuffer(), let encoder = command.makeComputeCommandEncoder() else { throw NSError(domain: "Metal", code: 8) }
        encoder.setBuffer(output, offset: 0, index: 0); encoder.setBuffer(cameraBuffer, offset: 0, index: 1)
        encoder.setBuffer(entityBuffer, offset: 0, index: 2); encoder.setBuffer(vertexBuffer, offset: 0, index: 3); encoder.setBuffer(triangleBuffer, offset: 0, index: 4)
        encoder.setComputePipelineState(pipeline)
        let w = pipeline.threadExecutionWidth, h = max(1, pipeline.maxTotalThreadsPerThreadgroup / w)
        encoder.dispatchThreads(MTLSize(width: request.width, height: request.height, depth: 1), threadsPerThreadgroup: MTLSize(width: w, height: h, depth: 1)); encoder.endEncoding()
        command.commit(); command.waitUntilCompleted()
        guard command.status == .completed else { throw command.error ?? NSError(domain: "Metal", code: 9) }
        let gpuNs = UInt64(max(0, (command.gpuEndTime - command.gpuStartTime) * 1_000_000_000))
        // Requests are serial and stdout is written synchronously before the
        // next dispatch. Borrow the reusable Metal buffer through that write
        // instead of making another full-frame CPU copy.
        return (Data(bytesNoCopy: output.contents(), count: outputLength, deallocator: .none), gpuNs)
    }
}

func readExactly(_ handle: FileHandle, count: Int) throws -> Data {
    var data = Data(); data.reserveCapacity(count)
    while data.count < count { let part = try handle.read(upToCount: count - data.count) ?? Data(); if part.isEmpty { throw NSError(domain: "Metal", code: 10) }; data.append(part) }
    return data
}
func uint32(_ data: Data) -> UInt32 { data.withUnsafeBytes { $0.load(as: UInt32.self) } }
let input = FileHandle.standardInput; let output = FileHandle.standardOutput
do {
    let renderer = try MetalRenderer(); let decoder = JSONDecoder()
    while true {
        let header: Data
        do { header = try readExactly(input, count: 4) } catch { break }
        let length = Int(uint32(header)); let request = try decoder.decode(RenderRequest.self, from: try readExactly(input, count: length)); let (pixels, gpuNs) = try renderer.render(request)
        var response = Data(); var pixelLength = UInt64(pixels.count).littleEndian; var gpuTime = gpuNs.littleEndian
        response.append(Data(bytes: &pixelLength, count: 8)); response.append(Data(bytes: &gpuTime, count: 8))
        try output.write(contentsOf: response)
        try output.write(contentsOf: pixels)
    }
} catch { FileHandle.standardError.write(Data("metal renderer failed: \(error)\n".utf8)); exit(1) }
