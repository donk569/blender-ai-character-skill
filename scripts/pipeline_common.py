"""角色动画公共库 —— 在 Blender 内执行（bpy 环境）。

包含建模/绑骨/动画三个阶段都会用到的工具：
  - aim_bone            世界方向对准骨骼
  - solve_leg / solve_arm   解析两骨 IK（不用 constraints）
  - set_foot_flat       脚掌世界朝向固定
  - record / write_action   先记录后写帧（避免姿势被 action 求值冲掉）
  - snap_to_ground      逐帧贴地
  - min_z               网格最低点
  - rest_z / bbox       基础查询

用法：放在脚本目录下，在动画脚本里
    sys.path.insert(0, r"<scripts目录>")
    from pipeline_common import *
"""

import math
import numpy as np
from mathutils import Vector, Matrix, Euler, Quaternion

try:
    import bpy
except ImportError:
    bpy = None


# ---------------------------------------------------------------- 基础查询
def get_rig_mesh():
    """取场景里的骨架和网格（按修改器关联，不靠命名）。"""
    rig = next(o for o in bpy.data.objects if o.type == "ARMATURE")
    mesh = None
    for o in bpy.data.objects:
        if o.type != "MESH":
            continue
        for m in o.modifiers:
            if m.type == "ARMATURE" and m.object == rig:
                mesh = o
                break
        if mesh:
            break
    return rig, mesh


def min_z(mesh_obj):
    """评估后的网格最低世界 Z。"""
    ev = mesh_obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    me = ev.to_mesh()
    co = np.empty(len(me.vertices) * 3, dtype=np.float32)
    me.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    m = np.array(ev.matrix_world)
    z = co @ m[2, :3] + m[2, 3]
    ev.to_mesh_clear()
    return float(z.min())


def foot_forward(rig):
    """从脚骨 head->tail 的水平分量判断角色朝向。返回单位向量（水平）。"""
    pb = rig.pose.bones.get("foot.L") or rig.pose.bones[0]
    d = (pb.tail - pb.head)
    d.z = 0.0
    return d.normalized()


# ---------------------------------------------------------------- 骨骼朝向
def aim_bone(rig, REST3, name, head_world, direction):
    """把骨骼的 Y 轴（沿骨方向）对准 direction，保持 rest 的 roll。"""
    pb = rig.pose.bones[name]
    rest_dir = (REST3[name] @ Vector((0, 1, 0))).normalized()
    q = rest_dir.rotation_difference(Vector(direction).normalized())
    m = (q.to_matrix() @ REST3[name]).to_4x4()
    m.translation = head_world
    pb.matrix = m


def set_rot(rig, name, rx=0.0, ry=0.0, rz=0.0):
    """局部欧拉角（度）。

    注意本管线常见骨骼的轴映射：
      index 0 (X) = 俯仰
      index 1 (Y) = 沿骨方向（竖直骨 -> 世界 Z 的偏航）
      index 2 (Z) = 侧倾      <-- 扭转不要写这里
    """
    pb = rig.pose.bones.get(name)
    if pb:
        pb.rotation_quaternion = Euler(
            (math.radians(rx), math.radians(ry), math.radians(rz)), "XYZ"
        ).to_quaternion()


def reset_pose(rig):
    for pb in rig.pose.bones:
        pb.rotation_mode = "QUATERNION"
        pb.rotation_quaternion = (1, 0, 0, 0)
        pb.location = (0, 0, 0)
        pb.scale = (1, 1, 1)


# ---------------------------------------------------------------- 解析两骨 IK
def solve_2bone(rig, REST3, root_name, mid_name, target, pole_dir,
                dmin_margin=0.02, reach_margin=0.995, dmin_abs=None):
    """通用解析两骨 IK。

    root_name: 上段骨（thigh / upper_arm）
    mid_name : 下段骨（shin / forearm）
    target   : 末端（踝 / 腕）的世界坐标
    pole_dir : 中间关节的朝向参考（膝盖朝前 / 肘朝下外）
    dmin_abs : 强制最小距离（手臂传 0.185 避开奇异区）
    """
    L1 = (rig.data.bones[root_name].tail_local - rig.data.bones[root_name].head_local).length
    L2 = (rig.data.bones[mid_name].tail_local - rig.data.bones[mid_name].head_local).length

    H = rig.pose.bones[root_name].head.copy()
    T = Vector(target)
    v = T - H
    d = v.length

    dmax = (L1 + L2) * reach_margin
    dmin = dmin_abs if dmin_abs is not None else abs(L1 - L2) + dmin_margin
    if d > dmax:
        v = v.normalized() * dmax
        T = H + v
        d = dmax
    if d < dmin:
        v = v.normalized() * dmin
        T = H + v
        d = dmin

    u = v.normalized()
    cos_a = max(-1.0, min(1.0, (L1 * L1 + d * d - L2 * L2) / (2 * L1 * d)))
    alpha = math.acos(cos_a)

    axis = u.cross(Vector(pole_dir).normalized())
    if axis.length < 1e-4:
        axis = u.cross(Vector((1, 0, 0)))
    axis.normalize()

    root_dir = (Quaternion(axis, alpha) @ u).normalized()
    K = H + root_dir * L1

    aim_bone(rig, REST3, root_name, H, root_dir)
    bpy.context.view_layer.update()
    aim_bone(rig, REST3, mid_name, rig.pose.bones[mid_name].head.copy(), (T - K).normalized())
    bpy.context.view_layer.update()
    return K


def solve_leg(rig, REST3, side, ankle_target):
    """腿：膝盖朝前（-Y，按实际朝向调整 pole）。"""
    return solve_2bone(rig, REST3, f"thigh.{side}", f"shin.{side}", ankle_target,
                       pole_dir=(0, -1, 0))


def solve_arm(rig, REST3, side, wrist_target, pole_dir=(-0.95, 0.18, -0.60)):
    """臂：dmin 固定 0.185，避开小折叠半径的奇异区。"""
    return solve_2bone(rig, REST3, f"upper_arm.{side}", f"forearm.{side}", wrist_target,
                       pole_dir=pole_dir, dmin_abs=0.185)


def set_foot_flat(rig, REST3, side, pitch_deg=0.0):
    """脚掌世界朝向固定为 rest（可叠加俯仰），防止随小腿翻转导致穿地。"""
    pb = rig.pose.bones[f"foot.{side}"]
    m = (Matrix.Rotation(math.radians(-pitch_deg), 3, "X") @ REST3[f"foot.{side}"]).to_4x4()
    m.translation = pb.head.copy()
    pb.matrix = m


# ---------------------------------------------------------------- 记录 -> 写帧
KEY_BONES_DEFAULT = [
    "hips", "spine", "chest", "upper_chest", "neck", "head",
    "shoulder.L", "shoulder.R",
    "thigh.L", "thigh.R", "shin.L", "shin.R", "foot.L", "foot.R", "toe.L", "toe.R",
    "upper_arm.L", "upper_arm.R", "forearm.L", "forearm.R", "hand.L", "hand.R",
]


def record_frame(rig, store, f, bones=KEY_BONES_DEFAULT):
    store[f] = {
        bn: (tuple(rig.pose.bones[bn].location), tuple(rig.pose.bones[bn].rotation_quaternion))
        for bn in bones if rig.pose.bones.get(bn)
    }


def iter_fcurves(act):
    if hasattr(act, "fcurves"):
        yield from act.fcurves
        return
    for layer in getattr(act, "layers", []):
        for strip in getattr(layer, "strips", []):
            for bag in getattr(strip, "channelbags", []):
                for fc in getattr(bag, "fcurves", []):
                    yield fc


def write_action(rig, name, rec, linear=True):
    """把记录的姿势写成 action。

    四元数强制符号连续：相邻帧 dot<0 时取反，杜绝 q/-q 翻转造成的抽搐。
    """
    act = bpy.data.actions.new(name)
    act.use_fake_user = True
    rig.animation_data.action = act

    prev_q = {}
    for f in sorted(rec):
        for bn, (loc, quat) in rec[f].items():
            pb = rig.pose.bones.get(bn)
            if not pb:
                continue
            q = Quaternion(quat)
            if bn in prev_q and q.dot(prev_q[bn]) < 0.0:
                q = Quaternion((-q.w, -q.x, -q.y, -q.z))
            prev_q[bn] = q
            pb.location = loc
            pb.rotation_quaternion = q
            pb.keyframe_insert("location", frame=f)
            pb.keyframe_insert("rotation_quaternion", frame=f)

    for fc in iter_fcurves(act):
        for kp in fc.keyframe_points:
            kp.interpolation = "LINEAR" if linear else "BEZIER"
        fc.update()
    return act


# ---------------------------------------------------------------- 贴地
def snap_to_ground(rig, REST3, mesh_obj, hips_name="hips", iters=4, tol=0.0012):
    """把网格最低点对齐到 z=0：沿世界 Z 平移髋部，1:1 修正且不改变姿态。

    迭代带阻尼（0.95），避免过冲震荡。
    注意：这会改变腿的伸展程度（脚位由 IK 目标在世界坐标里固定，髋抬了腿就伸）。
    """
    hb = rig.pose.bones[hips_name]
    base = hb.location.copy()
    local_up = (REST3[hips_name].inverted() @ Vector((0.0, 0.0, 1.0)))
    zoff = 0.0

    mz = min_z(mesh_obj)
    for _ in range(iters):
        if abs(mz) < tol:
            break
        zoff -= mz * 0.95
        hb.location = base + local_up * zoff
        bpy.context.view_layer.update()
        mz = min_z(mesh_obj)
    return mz
