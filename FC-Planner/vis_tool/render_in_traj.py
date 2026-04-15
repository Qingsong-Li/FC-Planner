# ⭐⭐⭐**********************************************⭐⭐⭐ #
# * Author       :    Chen Feng <cfengag at connect dot ust dot hk>, UAV Group, ECE, HKUST
# * Homepage     :    https://chen-albert-feng.github.io/AlbertFeng.github.io/
# * Date         :    Mar. 2024
# * E-mail       :    cfengag at connect dot ust dot hk.
# * Description  :    This file is the main function of rendering using blender.
# * License      :    GNU General Public License <http://www.gnu.org/licenses/>.
# * Project      :    FC-Planner is free software: you can redistribute it and/or 
# *                   modify it under the terms of the GNU Lesser General Public 
# *                   License as published by the Free Software Foundation, 
# *                   either version 3 of the License, or (at your option) any 
# *                   later version.
# *                   FC-Planner is distributed in the hope that it will be useful,
# *                   but WITHOUT ANY WARRANTY; without even the implied warranty 
# *                   of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. 
# *                   See the GNU General Public License for more details.
# * Website      :    https://hkust-aerial-robotics.github.io/FC-Planner/
# ⭐⭐⭐**********************************************⭐⭐⭐ #

import argparse
import math
import os
import sys
import numpy as np

import bpy
from mathutils import Vector

# 文件格式版本（当前脚本中未直接使用，通常用于与数据格式版本对齐）
FORMAT_VERSION = 6
# 均匀光照模式下的主光方向（单位向量近似）
UNIFORM_LIGHT_DIRECTION = [0.09387503, -0.63953443, -0.7630093]
# 相机参数：像素尺寸、分辨率、水平视场角
PIXEL_SIZE_CCD = 0.05
PIXEL_W = 640
PIXEL_H = 480
FOV_ANGLE = 75
# 轨迹采样步长：每 STEP 个离散轨迹点渲染一帧
STEP = 5

def clear_scene():
    """清空当前场景中的全部对象（模型、灯光、相机等）。"""
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()


def clear_lights():
    """仅删除当前场景中的灯光对象，保留模型与相机。"""
    bpy.ops.object.select_all(action="DESELECT")
    for obj in bpy.context.scene.objects.values():
        if isinstance(obj.data, bpy.types.Light):
            obj.select_set(True)
    bpy.ops.object.delete()

def import_model(path):
    """
    清空场景并导入指定三维模型。

    支持常见格式：OBJ / GLB / GLTF / STL / FBX / DAE / PLY。
    """
    clear_scene()
    _, ext = os.path.splitext(path)
    ext = ext.lower()
    if ext == ".obj":
        bpy.ops.import_scene.obj(filepath=path)
    elif ext in [".glb", ".gltf"]:
        bpy.ops.import_scene.gltf(filepath=path)
    elif ext == ".stl":
        bpy.ops.import_mesh.stl(filepath=path)
    elif ext == ".fbx":
        bpy.ops.import_scene.fbx(filepath=path)
    elif ext == ".dae":
        bpy.ops.wm.collada_import(filepath=path)
    elif ext == ".ply":
        bpy.ops.import_mesh.ply(filepath=path)
    else:
        raise RuntimeError(f"unexpected extension: {ext}")


def rotate_model():
    """将场景中已选对象绕 X 轴旋转 -90 度，用于坐标系对齐。"""
    # 先取消选择，再全选，确保旋转作用于全部对象
    bpy.ops.object.select_all(action='DESELECT')
    bpy.context.view_layer.objects.active = None
    bpy.ops.object.select_all(action='SELECT')

    # Blender 内部使用弧度，-90° -> -pi/2
    for obj in bpy.context.selected_objects:
        obj.rotation_euler[0] += math.radians(-90)

def scene_root_objects():
    """遍历场景中的根对象（没有父节点的对象）。"""
    for obj in bpy.context.scene.objects.values():
        if not obj.parent:
            yield obj

def scene_meshes():
    """遍历场景中的网格对象（Mesh）。"""
    for obj in bpy.context.scene.objects.values():
        if isinstance(obj.data, (bpy.types.Mesh)):
            yield obj

def create_camera():
    """创建并注册一个名为 Camera 的相机对象。"""
    camera_data = bpy.data.cameras.new(name="Camera")
    camera_object = bpy.data.objects.new("Camera", camera_data)
    bpy.context.scene.collection.objects.link(camera_object)
    bpy.context.scene.camera = camera_object

def set_camera(camera_pos, camera_dir):
    """设置相机位姿：位置 camera_pos + 欧拉角方向 camera_dir。"""
    bpy.context.scene.camera.location = camera_pos
    #print("camera=",bpy.context.scene.camera.location)
    # print('camera dir==============',camera_dir)
    bpy.context.scene.camera.rotation_euler = camera_dir
    # 更新依赖图，确保位姿修改立即生效
    bpy.context.view_layer.update()

def scene_bbox(single_obj=None, ignore_matrix=False):
    """
    计算场景（或单个对象）的轴对齐包围盒 AABB。

    参数:
    - single_obj: 指定对象时只计算该对象，否则计算所有 Mesh。
    - ignore_matrix: True 时忽略世界变换矩阵，使用局部坐标包围盒。
    """
    bbox_min = (math.inf,) * 3
    bbox_max = (-math.inf,) * 3
    found = False
    for obj in scene_meshes() if single_obj is None else [single_obj]:
        found = True
        for coord in obj.bound_box:
            coord = Vector(coord)
            if not ignore_matrix:
                coord = obj.matrix_world @ coord
            bbox_min = tuple(min(x, y) for x, y in zip(bbox_min, coord))
            bbox_max = tuple(max(x, y) for x, y in zip(bbox_max, coord))
    if not found:
        raise RuntimeError("no objects in scene to compute bounding box for")
    return Vector(bbox_min), Vector(bbox_max)

def set_camera_fov_horizontal(camera_name, fov): 
    """
    通过水平视场角设置相机焦距。

    公式: f = 0.5 * sensor_width / tan(fov/2)
    其中 sensor_width 由像素宽度与像元尺寸近似得到。
    """
    frame_width = PIXEL_W*PIXEL_SIZE_CCD 
    camera = bpy.data.objects[camera_name].data 
    camera.sensor_fit = 'HORIZONTAL' 
    camera.lens = 0.5 * frame_width / math.tan(0.5 * math.radians(fov))

def process_traj_info(traj, sample_stride):
    """
    解析轨迹文本并按步长采样，输出 Nx5 的相机轨迹数组。

    输出列顺序: [X, Y, Z, PITCH, YAW]
    """
    
    point_num = len(traj)
    print(point_num)
    traj_dim = np.array(['TIMESTAMP:', 'X:', 'Y:', 'Z:', 'PITCH:', 'YAW:'])

    camera_traj = [0] * point_num
    camera_waypoint_5D = [0] * point_num

    i = 0
    for traj_point in traj[::sample_stride]:
        # print("traj_point",traj_point)
        # 轨迹文件按“标签-数值”交替存储，因此维度=长度/2
        dimension = int(len(traj_point) / 2)
        camera_traj_point = [0] * 6
        for d in range(dimension):

            if traj_point[d * 2] == traj_dim[d]:
                # print("index correct")
                separated = traj_point[2 * d + 1].split(',', 1)
                # print("separated",separated[0])
                # 仅使用逗号前第一项（兼容某些额外字段）
                camera_traj_point[d] = float(separated[0])
            elif traj_dim[d] != traj_dim[d]:
                print("index incorrect!!!")
        # print("camera_traj_point",camera_traj_point)

        camera_traj[i] = camera_traj_point
        i = i + 1

    point_num_sampled = i
    print(len(camera_traj[:point_num_sampled]))

    camera_waypoint_5D = np.array(camera_traj[:point_num_sampled])[:, [1, 2, 3, 4, 5]]
    
    return camera_waypoint_5D

def render_in_traj(traj_path:str,renderout_path: str,light_mode: str,fast_mode:bool):
    """
    按轨迹逐帧渲染图像并保存到输出目录。

    处理流程:
    1) 读取轨迹文件
    2) 按固定步长采样
    3) 每个采样点更新相机位姿
    4) 渲染并保存为连续编号 PNG
    """
    
    input_traj = np.loadtxt(traj_path, dtype=str)
    # sample image timestamp = step * t_discrete
    step = STEP
    camera_traj = process_traj_info(input_traj, step)
    (frame_num,traj_dim)=np.shape(camera_traj)
    # print('frame_array',camera_traj[0])
    if traj_dim != 5:
        print("incorrect dimension of camera's trajectory ")
    for i in range(frame_num): 
        x = camera_traj[i][0]
        y = camera_traj[i][1]
        z = camera_traj[i][2]
        pitch = camera_traj[i][3]
        yaw = camera_traj[i][4]
        # print("euler============",pitch,'0',yaw)
        camera_pos = (x,y,z)
        # 由 pitch/yaw 生成前向单位向量，再转换为 Blender 旋转
        camera_dir = Vector([math.cos(pitch)*math.cos(yaw), math.cos(pitch)*math.sin(yaw),math.sin(pitch)])
        # camera_dir = - Vector(camera_pos)
        rot_quat = camera_dir.to_track_quat("-Z", "Y")
        camera_dir = rot_quat.to_euler()
        # print('camera pos==============',camera_pos)
        # print('camera dir==============',camera_dir)
        set_camera(camera_pos=camera_pos, camera_dir=camera_dir)


        # create_uniform_light()
        render_scene(
            # 4 位数字补零编号：0000.png, 0001.png, ...
            os.path.join(renderout_path, f"{i:04}.png"),
            fast_mode=fast_mode,
        )

def create_light(location, energy=1.0, angle=0.5 * math.pi / 180):
    """在指定位置创建太阳光，并令其朝向场景中心（原点方向）。"""
    light_data = bpy.data.lights.new(name="Light", type="SUN")
    light_data.energy = energy
    light_data.angle = angle
    light_object = bpy.data.objects.new(name="Light", object_data=light_data)

    direction = -location
    rot_quat = direction.to_track_quat("-Z", "Y")
    light_object.rotation_euler = rot_quat.to_euler()
    bpy.context.view_layer.update()

    bpy.context.collection.objects.link(light_object)
    light_object.location = location


def create_uniform_light(backend):
    """
    创建双向均匀主光，减少单侧阴影过重。

    CYCLES 下使用更小光源角增强方向性；
    其他后端用较大角度获得更平滑照明。
    """
    clear_lights()
    # 使用固定但非轴对齐方向，避免与模型坐标轴共线导致的视觉偏差
    pos = 100*Vector(UNIFORM_LIGHT_DIRECTION)
    angle = 0.0092 if backend == "CYCLES" else math.pi
    create_light(pos, energy=10.0, angle=angle)
    create_light(-pos, energy=10.0, angle=angle)


def setup_nodes(renderout_path, capturing_material_alpha: bool = False):
    """
    配置 Blender 合成节点图。

    目前主要完成颜色空间转换与通道拆分的基础搭建。
    """
    tree = bpy.context.scene.node_tree
    links = tree.links

    for node in tree.nodes:
        tree.nodes.remove(node)

    # 内部工具：封装数学节点创建，便于后续扩展深度/法线等后处理
    def node_op(op: str, *args, clamp=False):
        node = tree.nodes.new(type="CompositorNodeMath")
        node.operation = op
        if clamp:
            node.use_clamp = True
        for i, arg in enumerate(args):
            if isinstance(arg, (int, float)):
                node.inputs[i].default_value = arg
            else:
                links.new(arg, node.inputs[i])
        return node.outputs[0]

    def node_clamp(x, maximum=1.0):
        return node_op("MINIMUM", x, maximum)

    def node_mul(x, y, **kwargs):
        return node_op("MULTIPLY", x, y, **kwargs)

    input_node = tree.nodes.new(type="CompositorNodeRLayers")
    input_node.scene = bpy.context.scene

    # 将 Render Layers 输出端口缓存到字典，按名字检索更直观
    input_sockets = {}
    for output in input_node.outputs:
        input_sockets[output.name] = output

    if capturing_material_alpha:
        color_socket = input_sockets["Image"]
    else:
        raw_color_socket = input_sockets["Image"]
        color_node = tree.nodes.new(type="CompositorNodeConvertColorSpace")
        color_node.from_color_space = "Linear"
        color_node.to_color_space = "sRGB"
        tree.links.new(raw_color_socket, color_node.inputs[0])
        color_socket = color_node.outputs[0]
    # 拆分 RGBA，方便后续分别处理透明度/颜色通道
    split_node = tree.nodes.new(type="CompositorNodeSepRGBA")
    tree.links.new(color_socket, split_node.inputs[0])

    if capturing_material_alpha:
        return


def render_scene(renderout_path, fast_mode: bool):
    """
    执行单帧渲染并写盘。

    会根据渲染后端与 fast_mode 自动调整采样参数，以平衡速度和质量。
    """
    use_workbench = bpy.context.scene.render.engine == "BLENDER_WORKBENCH"
    if use_workbench:
        # Workbench 不适合此处流程，切到 EEVEE 渲染
        bpy.context.scene.render.engine = "BLENDER_EEVEE"
        bpy.context.scene.eevee.taa_render_samples = 5  # 快速模式采样
    if fast_mode:
        if bpy.context.scene.render.engine == "BLENDER_EEVEE":
            bpy.context.scene.eevee.taa_render_samples = 5
        elif bpy.context.scene.render.engine == "CYCLES":
            bpy.context.scene.cycles.samples = 256
    else:
        if bpy.context.scene.render.engine == "CYCLES":
            # 非快速模式下限制单帧时间，避免极端慢渲染
            bpy.context.scene.cycles.time_limit = 5
    bpy.context.view_layer.update()
    bpy.context.scene.use_nodes = True
    bpy.context.scene.view_layers["ViewLayer"].use_pass_z = True
    # 颜色空间在节点图里显式处理，这里保持 Raw 输出
    bpy.context.scene.view_settings.view_transform = "Raw"
    bpy.context.scene.render.film_transparent = True
    bpy.context.scene.render.resolution_x = PIXEL_W
    bpy.context.scene.render.resolution_y = PIXEL_H
    bpy.context.scene.render.image_settings.file_format = "PNG"
    bpy.context.scene.render.image_settings.color_mode = "RGBA"
    bpy.context.scene.render.image_settings.color_depth = "16"
    bpy.context.scene.render.filepath = renderout_path
    setup_nodes(renderout_path)
    bpy.ops.render.render(write_still=True)


def save_rendering_dataset(
    model_path: str,
    traj_path: str,
    renderout_path: str,
    backend: str,
    light_mode: str,
    fast_mode: bool,
):
    """
    数据集渲染入口函数：
    导入模型 -> 初始化渲染器与灯光 -> 创建相机 -> 按轨迹批量渲染。
    """
    assert light_mode in ["random", "uniform", "camera"]

    import_model(model_path) 
    rotate_model()
    # print('scene bounding box======', scene_bbox())
    bpy.context.scene.render.engine = backend
    # normalize_scene()
    create_uniform_light(backend)

    create_camera()

    set_camera_fov_horizontal("Camera", FOV_ANGLE)

    # 按采样后的轨迹点逐帧渲染
    render_in_traj(traj_path,renderout_path,fast_mode,light_mode)

def main():
    """命令行入口：解析参数并触发渲染流程。"""
    raw_args = sys.argv[1 :]
    parser = argparse.ArgumentParser()
    # 默认路径请按本地环境修改
    parser.add_argument("--model_path", type=str,default='/Users/liqingsong/Desktop/毕设/FC-Planner/FC-Planner/vis_tool/assets/model/EiffelTower/EiffelTower.obj')
    parser.add_argument("--traj_path", type=str,default='/Users/liqingsong/Desktop/毕设/FC-Planner/FC-Planner/src/hierarchical_coverage_planner/solution/Traj/TrajInfoEiffelTower.txt')
    parser.add_argument("--renderout_path", type=str,default='/Users/liqingsong/Desktop/毕设/FC-Planner/FC-Planner/vis_tool/result/eiffel_tower')
    parser.add_argument("--backend", type=str, default="BLENDER_EEVEE")
    parser.add_argument("--light_mode", type=str, default="uniform")
    parser.add_argument("--fast_mode", action="store_true")
    # 预留参数（当前脚本中未使用）
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1280)
    args = parser.parse_args(raw_args)

    save_rendering_dataset(
        model_path=args.model_path,
        traj_path=args.traj_path,
        renderout_path=args.renderout_path,
        backend=args.backend,
        light_mode=args.light_mode,
        fast_mode=args.fast_mode,
    )

if __name__ == "__main__":
    main()
