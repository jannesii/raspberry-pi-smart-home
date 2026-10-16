"""Saved meshes versus exact geometry; explicit 0.5 mm recess-ledges, no slicer claim."""

import hashlib
import json
from pathlib import Path

import numpy as np
import trimesh
from check_redesign import NAMES

ROOT = Path(__file__).resolve().parents[1]


def main():
    geometry = json.loads((ROOT / "reports/geometry.json").read_text())
    report = {
        "units": "mm",
        "mesh_faceting_allowance_deg": 0.2,
        "size_tolerance_mm": 0.001,
        "relative_volume_tolerance": 0.002,
        "slicer_verified": False,
        "parts": {},
    }
    for name in NAMES:
        path = ROOT / f"STL/{name}.stl"
        mesh = trimesh.load_mesh(path, process=True)
        assert mesh.is_watertight and mesh.is_winding_consistent
        assert mesh.body_count == 1 and mesh.volume > 0
        assert abs(mesh.bounds[0, 2]) < 0.001
        assert np.allclose(mesh.extents, geometry["parts"][name]["size_mm"], atol=0.001)
        error = abs(mesh.volume / geometry["parts"][name]["volume_mm3"] - 1)
        assert error < 0.002
        down = mesh.face_normals[:, 2] < -1e-6
        above_bed = mesh.triangles[:, :, 2].max(axis=1) > mesh.bounds[0, 2] + 1e-5
        selected = down & above_bed
        angles = np.degrees(np.arccos(np.clip(-mesh.face_normals[selected, 2], 0, 1)))
        steep = selected.copy()
        steep[selected] = angles < 45 - 0.2
        # Blind pilots/counterbores have a 0.5 mm radial underside ledge at Z=1.
        # Bound the exact feature and area; this is geometric evidence only.
        if steep.any():
            assert name in ("housing", "fit_coupon", "adapter_socket_coupon")
            assert np.allclose(mesh.triangles[steep, :, 2], 1, atol=0.001)
            centres = (
                [(0, 0)]
                if name == "adapter_socket_coupon"
                else [(x, y) for x in (-30, 30) for y in (-15, 15)]
            )
            for triangle in mesh.triangles[steep]:
                assert any(
                    np.linalg.norm(triangle[:, :2] - (x, y), axis=1).max() <= 2.201
                    for x, y in centres
                )
            assert mesh.area_faces[steep].sum() < (6.2 if name == "adapter_socket_coupon" else 24.8)
        report["parts"][name] = {
            "stl_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "watertight": bool(mesh.is_watertight),
            "winding_consistent": bool(mesh.is_winding_consistent),
            "bodies": int(mesh.body_count),
            "volume_mm3": float(mesh.volume),
            "relative_volume_error": float(error),
            "downward_surface_area_above_bed_mm2": float(mesh.area_faces[selected].sum()),
            "recess_ledge_area_mm2": float(mesh.area_faces[steep].sum()),
            "recess_ledge_radial_span_mm": 0.5 if steep.any() else 0,
            "support_free_slicer_evidence": "not available",
        }
    (ROOT / "reports/mesh.json").write_text(json.dumps(report, indent=2) + "\n")
    print("All nine saved meshes: watertight, consistent winding and exact-geometry agreement")


if __name__ == "__main__":
    main()
