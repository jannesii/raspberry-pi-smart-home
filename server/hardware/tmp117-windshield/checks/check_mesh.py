"""Independent mesh gate with a small mesh-faceting allowance at the 45-degree boundary."""

import json
from pathlib import Path

import numpy as np
import trimesh

ROOT = Path(__file__).resolve().parents[1]


def main():
    geometry = json.loads((ROOT / "reports/geometry.json").read_text())
    report = {"units": "mm", "mesh_faceting_allowance_deg": 0.2, "parts": {}}
    for name in ("carrier", "foam_bridge"):
        mesh = trimesh.load_mesh(ROOT / f"STL/{name}.stl", process=True)
        assert mesh.is_watertight and mesh.is_winding_consistent
        assert mesh.body_count == 1 and mesh.volume > 0
        assert np.allclose(mesh.extents, geometry["parts"][name]["size_mm"], atol=0.001)
        error = abs(mesh.volume / geometry["parts"][name]["volume_mm3"] - 1)
        assert error < 0.002
        down = mesh.face_normals[:, 2] < -1e-6
        # Ignore faces on the bed, but keep every sloped first-layer rim.
        above_bed = mesh.triangles[:, :, 2].max(axis=1) > mesh.bounds[0, 2] + 1e-5
        selected = down & above_bed
        angles = np.degrees(np.arccos(np.clip(-mesh.face_normals[selected, 2], 0, 1)))
        assert len(angles) == 0 or angles.min() >= 45 - 0.2
        if selected.any():
            assert mesh.triangles[selected, :, 2].max() <= 0.4001
        report["parts"][name] = {
            "watertight": bool(mesh.is_watertight),
            "winding_consistent": bool(mesh.is_winding_consistent),
            "bodies": int(mesh.body_count),
            "volume_mm3": float(mesh.volume),
            "relative_volume_error": float(error),
            "minimum_downward_surface_angle_deg": float(angles.min()) if len(angles) else None,
            "downward_surfaces_above_bed_are_only_shallow_chamfers": True,
        }
    (ROOT / "reports/mesh.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
