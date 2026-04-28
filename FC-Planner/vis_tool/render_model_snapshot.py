#!/usr/bin/env python3
import argparse
import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector


def parse_args():
    if "--" in sys.argv:
        argv = sys.argv[sys.argv.index("--") + 1 :]
    else:
        argv = sys.argv[1:]
    p = argparse.ArgumentParser()
    p.add_argument("--model_path", required=True)
    p.add_argument("--output_path", required=True)
    p.add_argument("--width", type=int, default=1280)
    p.add_argument("--height", type=int, default=720)
    p.add_argument("--samples", type=int, default=8)
    return p.parse_args(argv)


def clear_scene():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)


def import_model(path: Path):
    ext = path.suffix.lower()
    if ext == ".obj":
        bpy.ops.import_scene.obj(filepath=str(path), use_image_search=True)
    elif ext == ".dae":
        bpy.ops.wm.collada_import(filepath=str(path))
    elif ext in (".gltf", ".glb"):
        bpy.ops.import_scene.gltf(filepath=str(path))
    else:
        raise RuntimeError(f"Unsupported format: {ext}")


def get_mesh_objects():
    return [o for o in bpy.context.scene.objects if o.type == "MESH"]


def scene_bounds(meshes):
    mins = Vector((1e9, 1e9, 1e9))
    maxs = Vector((-1e9, -1e9, -1e9))
    for obj in meshes:
        mw = obj.matrix_world
        for corner in obj.bound_box:
            wc = mw @ Vector(corner)
            mins.x = min(mins.x, wc.x)
            mins.y = min(mins.y, wc.y)
            mins.z = min(mins.z, wc.z)
            maxs.x = max(maxs.x, wc.x)
            maxs.y = max(maxs.y, wc.y)
            maxs.z = max(maxs.z, wc.z)
    center = (mins + maxs) * 0.5
    diag = (maxs - mins).length
    if diag < 1e-6:
        diag = 1.0
    return center, diag


def look_at(obj, target: Vector):
    direction = target - obj.location
    rot = direction.to_track_quat("-Z", "Y")
    obj.rotation_euler = rot.to_euler()


def setup_camera(center: Vector, diag: float):
    cam_data = bpy.data.cameras.new("PreviewCam")
    cam_obj = bpy.data.objects.new("PreviewCam", cam_data)
    bpy.context.scene.collection.objects.link(cam_obj)

    cam_obj.location = center + Vector((diag * 1.4, -diag * 1.8, diag * 0.95))
    look_at(cam_obj, center)

    cam_data.lens = 42
    cam_data.clip_start = 0.01
    cam_data.clip_end = max(1000.0, diag * 30.0)

    bpy.context.scene.camera = cam_obj


def setup_light(center: Vector, diag: float):
    key_data = bpy.data.lights.new(name="KeyLight", type="SUN")
    key_data.energy = 3.2
    key = bpy.data.objects.new(name="KeyLight", object_data=key_data)
    key.rotation_euler = (math.radians(45), 0.0, math.radians(35))
    bpy.context.scene.collection.objects.link(key)

    fill_data = bpy.data.lights.new(name="FillLight", type="AREA")
    fill_data.energy = 480
    fill_data.size = max(2.0, diag * 0.4)
    fill = bpy.data.objects.new(name="FillLight", object_data=fill_data)
    fill.location = center + Vector((-diag * 1.2, diag * 1.2, diag * 0.8))
    look_at(fill, center)
    bpy.context.scene.collection.objects.link(fill)


def setup_render(output_path: Path, width: int, height: int, samples: int):
    sc = bpy.context.scene
    sc.render.engine = "BLENDER_EEVEE"
    sc.eevee.taa_render_samples = max(1, samples)
    sc.render.resolution_x = width
    sc.render.resolution_y = height
    sc.render.resolution_percentage = 100
    sc.render.image_settings.file_format = "PNG"
    sc.render.filepath = str(output_path)

    world = bpy.data.worlds.get("World")
    if world is not None:
        world.use_nodes = True
        bg = world.node_tree.nodes.get("Background")
        if bg:
            bg.inputs[0].default_value = (0.94, 0.96, 0.99, 1.0)
            bg.inputs[1].default_value = 1.0


def main():
    args = parse_args()
    model_path = Path(args.model_path).resolve()
    output_path = Path(args.output_path).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    clear_scene()
    import_model(model_path)
    meshes = get_mesh_objects()
    if not meshes:
        raise RuntimeError("No mesh imported")
    center, diag = scene_bounds(meshes)

    setup_camera(center, diag)
    setup_light(center, diag)
    setup_render(output_path, args.width, args.height, args.samples)
    bpy.ops.render.render(write_still=True)
    print(f"[DONE] preview saved: {output_path}")


if __name__ == "__main__":
    main()
