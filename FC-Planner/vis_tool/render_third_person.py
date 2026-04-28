#!/usr/bin/env python3
import argparse
import math
import random
import re
import sys
from pathlib import Path

import bpy
from mathutils import Vector


TIMESTAMP_RE = re.compile(r"TIMESTAMP:\s*([-\d\.eE+]+)")
POINT_RE = re.compile(r"\(([-\d\.eE+]+),\s*([-\d\.eE+]+),\s*([-\d\.eE+]+)\)")


def parse_args():
    if "--" in sys.argv:
        raw_args = sys.argv[sys.argv.index("--") + 1 :]
    else:
        raw_args = sys.argv[1 :]
    p = argparse.ArgumentParser()
    p.add_argument("--scene_model_path", required=True)
    p.add_argument("--drone_model_path", required=True)
    p.add_argument("--traj_path", required=True)
    p.add_argument("--cloud_path", required=True)
    p.add_argument("--renderout_path", required=True)
    p.add_argument("--fps", type=int, default=20)
    p.add_argument("--sample_step", type=int, default=5)
    p.add_argument("--point_radius", type=float, default=0.03)
    p.add_argument("--point_add_per_frame", type=int, default=120)
    p.add_argument("--point_max_total", type=int, default=25000)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--preview_only", action="store_true")
    p.add_argument("--max_frames", type=int, default=0)
    p.add_argument("--target_frames", type=int, default=0)
    return p.parse_args(raw_args)


def clear_scene():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()


def import_scene_model(path: Path):
    ext = path.suffix.lower()
    if ext == ".obj":
        bpy.ops.import_scene.obj(filepath=str(path))
    elif ext == ".dae":
        bpy.ops.wm.collada_import(filepath=str(path))
    elif ext in (".glb", ".gltf"):
        bpy.ops.import_scene.gltf(filepath=str(path))
    else:
        raise RuntimeError(f"Unsupported scene model format: {ext}")


def rotate_scene_model_x_minus_90():
    for obj in bpy.context.scene.objects:
        if obj.type in ("MESH", "EMPTY", "ARMATURE"):
            obj.rotation_euler[0] += math.radians(-90.0)


def apply_structure_only_style():
    # Remove texture/material dependence: render target as neutral gray structure.
    mat = bpy.data.materials.new("StructureGray")
    mat.use_nodes = True
    mat.blend_method = "BLEND"
    mat.shadow_method = "NONE"
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = (0.62, 0.62, 0.62, 1.0)
        bsdf.inputs["Alpha"].default_value = 0.35
        bsdf.inputs["Roughness"].default_value = 0.9
        bsdf.inputs["Specular"].default_value = 0.05
    for obj in bpy.context.scene.objects:
        if obj.type != "MESH":
            continue
        obj.data.materials.clear()
        obj.data.materials.append(mat)


def import_drone_model(path: Path):
    before = set(bpy.data.objects.keys())
    ext = path.suffix.lower()
    if ext == ".dae":
        bpy.ops.wm.collada_import(filepath=str(path))
    elif ext == ".obj":
        bpy.ops.import_scene.obj(filepath=str(path))
    else:
        raise RuntimeError(f"Unsupported drone model format: {ext}")
    after = set(bpy.data.objects.keys())
    new_names = after - before
    objs = [bpy.data.objects[n] for n in new_names if bpy.data.objects[n].type in ("MESH", "EMPTY", "ARMATURE")]
    if not objs:
        raise RuntimeError("No drone objects imported.")
    return objs


def make_emission_material(name: str, color_rgba, strength: float):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    em = nt.nodes.new("ShaderNodeEmission")
    em.inputs["Color"].default_value = color_rgba
    em.inputs["Strength"].default_value = strength
    nt.links.new(em.outputs["Emission"], out.inputs["Surface"])
    return mat


def create_drone_rig(drone_objs):
    rig = bpy.data.objects.new("DroneRig", None)
    bpy.context.scene.collection.objects.link(rig)
    for o in drone_objs:
        world = o.matrix_world.copy()
        o.parent = rig
        o.matrix_parent_inverse = rig.matrix_world.inverted()
        o.matrix_world = world
    return rig


def recenter_drone_model(drone_objs):
    mesh_objs = [o for o in drone_objs if o.type == "MESH"]
    if not mesh_objs:
        return
    mins = Vector((1e9, 1e9, 1e9))
    maxs = Vector((-1e9, -1e9, -1e9))
    for obj in mesh_objs:
        for c in obj.bound_box:
            wc = obj.matrix_world @ Vector(c)
            mins.x, mins.y, mins.z = min(mins.x, wc.x), min(mins.y, wc.y), min(mins.z, wc.z)
            maxs.x, maxs.y, maxs.z = max(maxs.x, wc.x), max(maxs.y, wc.y), max(maxs.z, wc.z)
    center = (mins + maxs) * 0.5
    for obj in drone_objs:
        obj.location -= center


def drone_bbox_diag(drone_objs):
    mesh_objs = [o for o in drone_objs if o.type == "MESH"]
    if not mesh_objs:
        return 1.0
    mins = Vector((1e9, 1e9, 1e9))
    maxs = Vector((-1e9, -1e9, -1e9))
    for obj in mesh_objs:
        for c in obj.bound_box:
            wc = obj.matrix_world @ Vector(c)
            mins.x, mins.y, mins.z = min(mins.x, wc.x), min(mins.y, wc.y), min(mins.z, wc.z)
            maxs.x, maxs.y, maxs.z = max(maxs.x, wc.x), max(maxs.y, wc.y), max(maxs.z, wc.z)
    return max(1e-6, (maxs - mins).length)


def normalize_drone_scale(drone_objs, traj_radius: float):
    # Target a compact drone size compared with scene/trajectory scale.
    cur = drone_bbox_diag(drone_objs)
    target = max(0.8, traj_radius * 0.8)
    s = target / cur
    for o in drone_objs:
        o.scale = (o.scale.x * s, o.scale.y * s, o.scale.z * s)


def apply_final_drone_scale_on_rig(drone_rig, factor: float):
    drone_rig.scale = (factor, factor, factor)


def clear_drone_embedded_animation(drone_objs):
    for o in drone_objs:
        if o.animation_data:
            o.animation_data_clear()


def parse_traj(traj_path: Path):
    poses = []
    with traj_path.open("r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line.startswith("TIMESTAMP:"):
                continue
            parts = [p.strip() for p in line.split(",")]
            kv = {}
            for p in parts:
                if ":" not in p:
                    continue
                k, v = p.split(":", 1)
                kv[k.strip()] = v.strip()
            try:
                poses.append(
                    (
                        float(kv["X"]),
                        float(kv["Y"]),
                        float(kv["Z"]),
                        float(kv.get("PITCH", "0")),
                        float(kv.get("YAW", "0")),
                    )
                )
            except Exception:
                continue
    if not poses:
        raise RuntimeError(f"No valid trajectory parsed from {traj_path}")
    return poses


def parse_cloud(cloud_path: Path):
    frames = []
    with cloud_path.open("r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("TIMESTAMP:"):
            pts = []
            if i + 1 < len(lines):
                pts_line = lines[i + 1]
                for m in POINT_RE.finditer(pts_line):
                    pts.append((float(m.group(1)), float(m.group(2)), float(m.group(3))))
            frames.append(pts)
            i += 2
        else:
            i += 1
    return frames


def build_uniform_indices(total: int, target: int):
    if total <= 0:
        return []
    if target <= 0 or target >= total:
        return list(range(total))
    if target == 1:
        return [0]
    idx = []
    for i in range(target):
        j = int(round(i * (total - 1) / (target - 1)))
        if not idx or j != idx[-1]:
            idx.append(j)
    return idx


def set_evee_render_settings(out_dir: Path, fps: int):
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE"
    scene.eevee.taa_render_samples = 8
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.resolution_x = 1280
    scene.render.resolution_y = 720
    scene.render.fps = fps
    scene.render.film_transparent = False
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.exposure = 0.2
    scene.view_settings.gamma = 1.0
    scene.world.use_nodes = True
    wbg = scene.world.node_tree.nodes.get("Background")
    if wbg:
        wbg.inputs["Color"].default_value = (0.03, 0.03, 0.03, 1.0)
        wbg.inputs["Strength"].default_value = 0.8
    scene.render.filepath = str(out_dir) + "/"


def setup_third_person_camera(center: Vector, radius: float):
    cam_data = bpy.data.cameras.new("ThirdCam")
    cam_obj = bpy.data.objects.new("ThirdCam", cam_data)
    bpy.context.scene.collection.objects.link(cam_obj)
    bpy.context.scene.camera = cam_obj
    cam_obj.location = (center.x + radius * 2.8, center.y - radius * 2.8, center.z + radius * 1.8)
    direction = center - cam_obj.location
    cam_obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    cam_data.lens = 55.0
    cam_data.clip_end = 10000.0
    return cam_obj


def compute_scene_center_radius():
    mins = Vector((1e9, 1e9, 1e9))
    maxs = Vector((-1e9, -1e9, -1e9))
    for obj in bpy.context.scene.objects:
        if obj.type != "MESH":
            continue
        for c in obj.bound_box:
            wc = obj.matrix_world @ Vector(c)
            mins.x, mins.y, mins.z = min(mins.x, wc.x), min(mins.y, wc.y), min(mins.z, wc.z)
            maxs.x, maxs.y, maxs.z = max(maxs.x, wc.x), max(maxs.y, wc.y), max(maxs.z, wc.z)
    center = (mins + maxs) * 0.5
    radius = (maxs - mins).length * 0.5
    return center, max(5.0, radius)


def compute_traj_center_radius(poses):
    xs = [p[0] for p in poses]
    ys = [p[1] for p in poses]
    zs = [p[2] for p in poses]
    min_v = Vector((min(xs), min(ys), min(zs)))
    max_v = Vector((max(xs), max(ys), max(zs)))
    center = (min_v + max_v) * 0.5
    radius = (max_v - min_v).length * 0.5
    return center, max(3.0, radius)


def add_lighting(center: Vector, radius: float):
    sun_data = bpy.data.lights.new("KeySun", type="SUN")
    sun_data.energy = 4.0
    sun_obj = bpy.data.objects.new("KeySun", sun_data)
    bpy.context.scene.collection.objects.link(sun_obj)
    sun_obj.location = (center.x + radius * 2.0, center.y - radius * 1.5, center.z + radius * 3.0)
    sun_obj.rotation_euler = (math.radians(45), 0.0, math.radians(35))

    fill_data = bpy.data.lights.new("FillArea", type="AREA")
    fill_data.energy = 800.0
    fill_data.size = radius * 2.0
    fill_obj = bpy.data.objects.new("FillArea", fill_data)
    bpy.context.scene.collection.objects.link(fill_obj)
    fill_obj.location = (center.x - radius * 1.0, center.y + radius * 1.2, center.z + radius * 1.2)
    fill_obj.rotation_euler = (math.radians(70), 0.0, math.radians(-25))


def create_scanned_points_object(point_radius: float, mat_name: str, color=(0.2, 1.0, 0.25, 1.0), em=(0.16, 0.95, 0.22, 1.0), em_strength=3.0):
    mesh = bpy.data.meshes.new("ScannedPointsMesh")
    obj = bpy.data.objects.new("ScannedPoints", mesh)
    bpy.context.scene.collection.objects.link(obj)

    inst = bpy.ops.mesh.primitive_ico_sphere_add
    inst(radius=point_radius, location=(0, 0, 0))
    sphere = bpy.context.active_object
    sphere.name = "PointInstanceSphere"
    # Keep the instance source object out of camera view, but renderable so
    # Object Info -> Instance On Points always has valid geometry.
    sphere.location = (1000000.0, 1000000.0, 1000000.0)
    sphere.hide_render = False
    sphere.hide_set(False)

    mat = bpy.data.materials.new(mat_name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = color
        bsdf.inputs["Emission"].default_value = em
        bsdf.inputs["Emission Strength"].default_value = em_strength
        bsdf.inputs["Roughness"].default_value = 0.8
    sphere.data.materials.append(mat)

    mod = obj.modifiers.new(name="PointRender", type="NODES")
    ng = bpy.data.node_groups.new("PointRenderGroup", "GeometryNodeTree")
    mod.node_group = ng
    inp = ng.nodes.new("NodeGroupInput")
    out = ng.nodes.new("NodeGroupOutput")
    ng.inputs.new("NodeSocketGeometry", "Geometry")
    ng.outputs.new("NodeSocketGeometry", "Geometry")
    mesh_to_points = ng.nodes.new("GeometryNodeMeshToPoints")
    mesh_to_points.mode = "VERTICES"
    mesh_to_points.inputs["Radius"].default_value = point_radius
    inst_on_points = ng.nodes.new("GeometryNodeInstanceOnPoints")
    realize = ng.nodes.new("GeometryNodeRealizeInstances")
    obj_info = ng.nodes.new("GeometryNodeObjectInfo")
    obj_info.inputs["Object"].default_value = sphere
    ng.links.new(inp.outputs["Geometry"], mesh_to_points.inputs["Mesh"])
    ng.links.new(mesh_to_points.outputs["Points"], inst_on_points.inputs["Points"])
    ng.links.new(obj_info.outputs["Geometry"], inst_on_points.inputs["Instance"])
    ng.links.new(inst_on_points.outputs["Instances"], realize.inputs["Geometry"])
    ng.links.new(realize.outputs["Geometry"], out.inputs["Geometry"])
    return obj


def build_path_curve(poses):
    crv = bpy.data.curves.new("TravelPath", type="CURVE")
    crv.dimensions = "3D"
    spline = crv.splines.new("POLY")
    spline.points.add(len(poses) - 1)
    for i, p in enumerate(poses):
        spline.points[i].co = (p[0], p[1], p[2], 1.0)
    crv.bevel_depth = 0.14
    obj = bpy.data.objects.new("TravelPath", crv)
    bpy.context.scene.collection.objects.link(obj)
    mat = make_emission_material("PathEmissive", (0.95, 0.1, 0.75, 1.0), 3.0)
    obj.data.materials.append(mat)


def highlight_drone(drone_objs):
    drone_mat = make_emission_material("DroneEmissive", (0.05, 0.95, 1.0, 1.0), 3.2)
    for o in drone_objs:
        if o.type == "MESH":
            o.data.materials.clear()
            o.data.materials.append(drone_mat)


def main():
    args = parse_args()
    scene_model = Path(args.scene_model_path).resolve()
    drone_model = Path(args.drone_model_path).resolve()
    traj_path = Path(args.traj_path).resolve()
    cloud_path = Path(args.cloud_path).resolve()
    out_dir = Path(args.renderout_path).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    random.seed(args.seed)
    clear_scene()
    import_scene_model(scene_model)
    rotate_scene_model_x_minus_90()
    apply_structure_only_style()
    poses_raw = parse_traj(traj_path)[:: max(1, args.sample_step)]
    if not poses_raw:
        raise RuntimeError("No trajectory poses after initial sampling.")
    uniform_idx = build_uniform_indices(len(poses_raw), args.target_frames)
    poses = [poses_raw[i] for i in uniform_idx]
    traj_center, traj_radius = compute_traj_center_radius(poses)
    # Compose camera around trajectory region instead of full-model bbox.
    cam = setup_third_person_camera(traj_center, traj_radius * 1.8)
    cam.rotation_euler = (traj_center - cam.location).to_track_quat("-Z", "Y").to_euler()
    add_lighting(traj_center, traj_radius * 1.6)
    drone_objs = import_drone_model(drone_model)
    clear_drone_embedded_animation(drone_objs)
    recenter_drone_model(drone_objs)
    normalize_drone_scale(drone_objs, traj_radius)
    drone_rig = create_drone_rig(drone_objs)
    apply_final_drone_scale_on_rig(drone_rig, 10.0)
    if drone_rig.animation_data:
        drone_rig.animation_data_clear()
    highlight_drone(drone_objs)
    cloud_raw = parse_cloud(cloud_path)
    if cloud_raw:
        cloud_raw = cloud_raw[:: max(1, args.sample_step)]
        if len(cloud_raw) == len(poses_raw):
            cloud_frames = [cloud_raw[i] for i in uniform_idx]
        else:
            # Fallback: map by ratio if sizes don't align.
            cloud_frames = []
            for i in uniform_idx:
                j = int(round(i * max(0, len(cloud_raw) - 1) / max(1, len(poses_raw) - 1)))
                cloud_frames.append(cloud_raw[min(max(0, j), len(cloud_raw) - 1)])
    else:
        cloud_frames = []
    scanned_obj = create_scanned_points_object(
        args.point_radius * 2.0,
        "ScannedPointsMat",
        color=(0.2, 1.0, 0.25, 1.0),
        em=(0.16, 0.95, 0.22, 1.0),
        em_strength=4.0,
    )
    current_obj = create_scanned_points_object(
        args.point_radius * 3.0,
        "CurrentFramePointsMat",
        color=(1.0, 0.95, 0.2, 1.0),
        em=(1.0, 0.9, 0.1, 1.0),
        em_strength=7.0,
    )
    current_mesh = current_obj.data
    build_path_curve(poses)
    set_evee_render_settings(out_dir, args.fps)

    total_frames = len(poses)
    if args.max_frames and args.max_frames > 0:
        total_frames = min(total_frames, args.max_frames)
    if args.preview_only:
        total_frames = 1

    scene = bpy.context.scene
    scene.frame_start = 1
    scene.frame_end = total_frames

    scanned_points = []
    point_mesh = scanned_obj.data
    cloud_len = len(cloud_frames)

    x0, y0, z0, p0, yaw0 = poses[0]
    _ = (p0, yaw0)
    drone_rig.location = (x0, y0, z0)
    drone_rig.rotation_euler = (0.0, 0.0, 0.0)
    bpy.context.view_layer.update()
    scene.render.filepath = str(out_dir) + "/"
    if args.preview_only:
        scene.frame_set(1)
        scene.render.filepath = str(out_dir / "preview.png")
        bpy.ops.render.render(write_still=True)
        print(f"[DONE] Third-person preview saved to: {out_dir / 'preview.png'}")
    else:
        # Render frame-by-frame so scanned points update is visible over time.
        for fi, pose in enumerate(poses[:total_frames], start=1):
            x, y, z, pitch, yaw = pose
            _ = (pitch, yaw)
            drone_rig.location = (x, y, z)
            drone_rig.rotation_euler = (0.0, 0.0, 0.0)

            if cloud_len > 0:
                idx = min(fi - 1, cloud_len - 1)
                new_pts = cloud_frames[idx]
                current_points = []
                if new_pts:
                    if len(new_pts) > args.point_add_per_frame:
                        new_pts = random.sample(new_pts, args.point_add_per_frame)
                    current_points = list(new_pts)
                    scanned_points.extend(new_pts)
                if len(scanned_points) > args.point_max_total:
                    scanned_points = scanned_points[-args.point_max_total :]
                point_mesh.clear_geometry()
                point_mesh.from_pydata(scanned_points, [], [])
                point_mesh.update()
                current_mesh.clear_geometry()
                current_mesh.from_pydata(current_points, [], [])
                current_mesh.update()

            scene.frame_set(fi)
            scene.render.filepath = str(out_dir / f"{fi:04d}.png")
            bpy.ops.render.render(write_still=True)
        print(f"[DONE] Third-person frames saved to: {out_dir}")


if __name__ == "__main__":
    main()
