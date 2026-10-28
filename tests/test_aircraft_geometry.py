"""Bundled aircraft glb models are converted once, in Python, to the body frame.

Body frame: x right, y forward (nose), z up, like the built-in aircraft mesh.
Truth geometry and the mesh sent to Godot come from the same conversion.
"""

from __future__ import annotations

import unittest

import numpy as np

from chongming.contracts import scene as s
from chongming.modules.scene import VisibilityModule
from chongming.rendering.geometry import asset_mesh, contains_point, surface_hits
from chongming.rendering.godot import _entity
from chongming.rendering.occlusion_index import entity_bounds

IDENTITY = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
POSE = s.KinematicState((0.0, 0.0, 0.0), (0.0, 0.0, 0.0), IDENTITY, (0.0, 0.0, 0.0))
DOWN = np.array([[0.0, 0.0, -1.0]])
# East/north offsets where a vertical ray from above crosses each model's body.
OVER_BODY = {"f86": (0.0, 0.0), "la11": (0.0, -1.0)}

# Triangle count and body-frame vertex bounds after the glb node transform.
EXPECTED = {
    "f86": (1810, (-3.1824, -5.3424, -0.8863), (3.1824, 1.1865, 1.7100)),
    "la11": (1439, (-5.1265, -6.3156, -1.3877), (5.1265, 2.5149, 2.1104)),
}


class AircraftAssetGeometryTests(unittest.TestCase):
    def test_mesh_is_in_the_body_frame(self) -> None:
        for name, (triangles, lo, hi) in EXPECTED.items():
            mesh = asset_mesh(name)
            vertices = np.asarray(mesh.vertices_body_m)
            self.assertEqual(len(mesh.triangle_vertex_indices), triangles, name)
            np.testing.assert_allclose(vertices.min(axis=0), lo, atol=1e-3, err_msg=name)
            np.testing.assert_allclose(vertices.max(axis=0), hi, atol=1e-3, err_msg=name)
            self.assertLess(max(max(t) for t in mesh.triangle_vertex_indices), len(vertices))

    def test_nose_points_forward_and_fin_points_up(self) -> None:
        for name in EXPECTED:
            vertices = np.asarray(asset_mesh(name).vertices_body_m)
            length = vertices[:, 1]
            rear = vertices[length < np.percentile(length, 8)]
            front = vertices[length > np.percentile(length, 92)]
            # The vertical fin stands at the tail; the nose is low and narrow.
            self.assertGreater(np.ptp(rear[:, 2]), np.ptp(front[:, 2]), name)
            self.assertGreater(rear[:, 2].max(), front[:, 2].max(), name)
            self.assertEqual(vertices[np.argmax(vertices[:, 2]), 1] < 0, True, name)
            self.assertLess(np.ptp(front[:, 0]), np.ptp(vertices[:, 0]) / 4, name)

    def test_faces_are_outward_counter_clockwise(self) -> None:
        for name in EXPECTED:
            mesh = asset_mesh(name)
            vertices = np.asarray(mesh.vertices_body_m)
            a, b, c = (vertices[list(face)] for face in zip(*mesh.triangle_vertex_indices))
            volume = np.einsum("ij,ij->i", a, np.cross(b, c)).sum() / 6
            self.assertGreater(volume, 0.0, name)

    def test_godot_receives_the_same_mesh_as_truth(self) -> None:
        for name in EXPECTED:
            mesh = asset_mesh(name)
            payload = _entity(
                2,
                (0.0, 0.0, 0.0),
                POSE,
                s.ExternalAssetGeometry(name),
                s.SurfaceAppearance((0.4, 0.4, 0.4), 0.8),
                object_id=f"aircraft/{name}",
            )
            self.assertEqual(payload["vertices"], [list(v) for v in mesh.vertices_body_m])
            self.assertEqual(payload["triangles"], [list(t) for t in mesh.triangle_vertex_indices])

    def test_unknown_model_is_rejected(self) -> None:
        with self.assertRaises(KeyError):
            asset_mesh("cessna")

    def test_rays_hit_the_model_and_miss_beside_it(self) -> None:
        for name in EXPECTED:
            geometry = s.ExternalAssetGeometry(name)
            east, north = OVER_BODY[name]
            depth, _ = surface_hits((east, north, 10.0), DOWN, POSE, geometry)
            self.assertTrue(np.isfinite(depth[0]), name)
            self.assertLess(depth[0], 10.0 + 1e-6)
            miss, _ = surface_hits((50.0, 50.0, 10.0), DOWN, POSE, geometry)
            self.assertTrue(np.isinf(miss[0]), name)
            self.assertFalse(contains_point((50.0, 50.0, 10.0), POSE, geometry))

    def test_bounds_and_sight_line_blocking_use_the_model(self) -> None:
        geometry = s.ExternalAssetGeometry("f86")
        blocker = s.WorldObjectSpec(
            s.WorldObjectId("aircraft/f86"),
            "f86",
            s.WorldObjectKind.OBSTACLE,
            "aircraft",
            POSE,
            geometry,
            s.SurfaceAppearance((0.4, 0.4, 0.4), 0.8),
            True,
        )
        bounds = entity_bounds(blocker)
        assert bounds is not None
        np.testing.assert_allclose(bounds[0], EXPECTED["f86"][1], atol=1e-3)
        np.testing.assert_allclose(bounds[1], EXPECTED["f86"][2], atol=1e-3)
        hit = VisibilityModule._segment_hit(blocker, (0.0, 0.0, 10.0), (0.0, 0.0, -10.0))
        self.assertIsNotNone(hit)
        assert hit is not None
        self.assertAlmostEqual(hit[1].segment_fraction, hit[0])
        self.assertIsNone(
            VisibilityModule._segment_hit(blocker, (50.0, 0.0, 10.0), (50.0, 0.0, -10.0))
        )


if __name__ == "__main__":
    unittest.main()
