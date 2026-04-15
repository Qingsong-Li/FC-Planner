#!/usr/bin/env python3
"""
Convert an OBJ mesh to two PCD files:
1) dense fullcloud pcd for hcplanner/fullcloud
2) sparse pcd for rosa_main/pcd

Dependency-free implementation (only Python stdlib + numpy).
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path
from typing import List, Sequence, Tuple

import numpy as np


def parse_obj(obj_path: Path) -> Tuple[np.ndarray, np.ndarray]:
    vertices: List[Tuple[float, float, float]] = []
    triangles: List[Tuple[int, int, int]] = []

    with obj_path.open("r", encoding="utf-8", errors="ignore") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue

            if line.startswith("v "):
                parts = line.split()
                if len(parts) < 4:
                    continue
                vertices.append((float(parts[1]), float(parts[2]), float(parts[3])))
                continue

            if line.startswith("f "):
                tokens = line.split()[1:]
                if len(tokens) < 3:
                    continue
                face_indices: List[int] = []
                for tok in tokens:
                    # face token may be:
                    # v
                    # v/vt
                    # v//vn
                    # v/vt/vn
                    v_str = tok.split("/")[0]
                    if not v_str:
                        continue
                    idx = int(v_str)
                    # OBJ supports negative index (relative to current end)
                    if idx < 0:
                        idx = len(vertices) + idx
                    else:
                        idx = idx - 1
                    face_indices.append(idx)

                if len(face_indices) < 3:
                    continue

                # Fan triangulation for polygons with >3 vertices.
                root = face_indices[0]
                for i in range(1, len(face_indices) - 1):
                    triangles.append((root, face_indices[i], face_indices[i + 1]))

    if not vertices:
        raise ValueError(f"No vertices parsed from: {obj_path}")
    if not triangles:
        raise ValueError(f"No faces parsed from: {obj_path}")

    verts = np.asarray(vertices, dtype=np.float64)
    tris = np.asarray(triangles, dtype=np.int64)

    if np.any(tris < 0) or np.any(tris >= len(verts)):
        raise ValueError("OBJ face index out of vertex range.")

    return verts, tris


def sample_points_on_mesh(
    vertices: np.ndarray, triangles: np.ndarray, n_points: int, rng: np.random.Generator
) -> np.ndarray:
    v0 = vertices[triangles[:, 0]]
    v1 = vertices[triangles[:, 1]]
    v2 = vertices[triangles[:, 2]]

    # Triangle area = 0.5 * |(v1-v0) x (v2-v0)|
    cross = np.cross(v1 - v0, v2 - v0)
    areas = 0.5 * np.linalg.norm(cross, axis=1)

    valid = areas > 1e-12
    if not np.any(valid):
        raise ValueError("All triangles have near-zero area.")

    v0 = v0[valid]
    v1 = v1[valid]
    v2 = v2[valid]
    areas = areas[valid]

    probs = areas / areas.sum()
    tri_ids = rng.choice(len(areas), size=n_points, p=probs)

    a = v0[tri_ids]
    b = v1[tri_ids]
    c = v2[tri_ids]

    # Uniform sampling inside triangle:
    # r1, r2 ~ U(0,1), then
    # p = (1-sqrt(r1))*a + sqrt(r1)*(1-r2)*b + sqrt(r1)*r2*c
    r1 = rng.random(n_points)
    r2 = rng.random(n_points)
    sr1 = np.sqrt(r1)

    w0 = 1.0 - sr1
    w1 = sr1 * (1.0 - r2)
    w2 = sr1 * r2
    pts = (w0[:, None] * a) + (w1[:, None] * b) + (w2[:, None] * c)
    return pts.astype(np.float32, copy=False)


def voxel_downsample(points: np.ndarray, voxel_size: float) -> np.ndarray:
    if voxel_size <= 0.0:
        raise ValueError("voxel_size must be > 0.")
    if len(points) == 0:
        return points

    grid = np.floor(points / voxel_size).astype(np.int64)
    uniq, idx = np.unique(grid, axis=0, return_index=True)
    _ = uniq  # keep intent clear
    down = points[idx]
    return down


def random_downsample(points: np.ndarray, target_count: int, rng: np.random.Generator) -> np.ndarray:
    if target_count <= 0:
        raise ValueError("target_count must be > 0.")
    if len(points) <= target_count:
        return points
    idx = rng.choice(len(points), size=target_count, replace=False)
    return points[idx]


def write_pcd_xyz_ascii(points: np.ndarray, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    n = len(points)
    with out_path.open("w", encoding="utf-8") as f:
        f.write("# .PCD v0.7 - Point Cloud Data file format\n")
        f.write("VERSION 0.7\n")
        f.write("FIELDS x y z\n")
        f.write("SIZE 4 4 4\n")
        f.write("TYPE F F F\n")
        f.write("COUNT 1 1 1\n")
        f.write(f"WIDTH {n}\n")
        f.write("HEIGHT 1\n")
        f.write("VIEWPOINT 0 0 0 1 0 0 0\n")
        f.write(f"POINTS {n}\n")
        f.write("DATA ascii\n")
        for p in points:
            f.write(f"{p[0]:.6f} {p[1]:.6f} {p[2]:.6f}\n")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Convert OBJ mesh to dense fullcloud PCD and sparse ROSA PCD."
    )
    parser.add_argument("--obj", required=True, help="Input OBJ path.")
    parser.add_argument(
        "--out-prefix",
        default="scene",
        help="Output file prefix. Outputs: <prefix>_fullcloud.pcd and <prefix>_rosa.pcd",
    )
    parser.add_argument(
        "--out-dir",
        default="FC-Planner/src/hierarchical_coverage_planner/data",
        help="Output directory.",
    )
    parser.add_argument(
        "--full-points",
        type=int,
        default=500000,
        help="Dense fullcloud sample point count.",
    )
    parser.add_argument(
        "--rosa-voxel",
        type=float,
        default=0.05,
        help="Voxel size (meters) for sparse ROSA downsampling.",
    )
    parser.add_argument(
        "--rosa-max-points",
        type=int,
        default=120000,
        help="If sparse cloud exceeds this count, random downsample to this size.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for reproducibility.",
    )
    return parser


def main() -> None:
    parser = build_arg_parser()
    args = parser.parse_args()

    obj_path = Path(args.obj).expanduser().resolve()
    if not obj_path.exists():
        raise FileNotFoundError(f"OBJ not found: {obj_path}")

    out_dir = Path(args.out_dir).expanduser().resolve()
    out_prefix = args.out_prefix
    full_points = int(args.full_points)
    rosa_voxel = float(args.rosa_voxel)
    rosa_max_points = int(args.rosa_max_points)
    rng = np.random.default_rng(int(args.seed))

    if full_points <= 0:
        raise ValueError("--full-points must be > 0")
    if rosa_voxel <= 0:
        raise ValueError("--rosa-voxel must be > 0")
    if rosa_max_points <= 0:
        raise ValueError("--rosa-max-points must be > 0")

    print(f"[INFO] Loading OBJ: {obj_path}")
    vertices, triangles = parse_obj(obj_path)
    print(f"[INFO] Vertices: {len(vertices)}, Triangles: {len(triangles)}")

    print(f"[INFO] Sampling dense fullcloud: {full_points} points")
    fullcloud = sample_points_on_mesh(vertices, triangles, full_points, rng)

    print(f"[INFO] Voxel downsampling for ROSA: voxel={rosa_voxel}")
    rosa = voxel_downsample(fullcloud, rosa_voxel)
    print(f"[INFO] ROSA points after voxel downsample: {len(rosa)}")

    if len(rosa) > rosa_max_points:
        print(f"[INFO] Random downsampling ROSA to {rosa_max_points}")
        rosa = random_downsample(rosa, rosa_max_points, rng)

    full_out = out_dir / f"{out_prefix}_fullcloud.pcd"
    rosa_out = out_dir / f"{out_prefix}_rosa.pcd"

    print(f"[INFO] Writing fullcloud PCD: {full_out}")
    write_pcd_xyz_ascii(fullcloud, full_out)
    print(f"[INFO] Writing ROSA PCD: {rosa_out}")
    write_pcd_xyz_ascii(rosa, rosa_out)

    print("[DONE] Generated files:")
    print(f"  - {full_out}")
    print(f"  - {rosa_out}")
    print("[NOTE] Ensure mesh and output PCDs use the same meter-scale and coordinate frame.")


if __name__ == "__main__":
    main()


#  python3 obj_to_pcd.py --obj FC-Planner/src/hierarchical_coverage_planner/data/mesh/kangaroo.obj --out-prefix kangaroo --out-dir FC-Planner/src/hierarchical_coverage_planner/data --full-points 500000 --rosa-voxel 0.05 --rosa-max-points 120000 && mv FC-Planner/src/hierarchical_coverage_planner/data/kangaroo_rosa.pcd FC-Planner/src/hierarchical_coverage_planner/data/kangaroo.pcd && mv FC-Planner/src/hierarchical_coverage_planner/data/kangaroo_fullcloud.pcd FC-Planner/src/hierarchical_coverage_planner/data/kangaroomore.pcd