"""通用步态：先标定骨骼的正负号，再按几何可行性决定步态模式。

两个关键点（都是踩坑换来的）

1. **符号不能假设**。同样是「绕局部 X 转正角度」，腿可能是往前、尾巴可能是往下。
   建完骨架先做一次 5 秒钟的标定实验，把符号量出来写进配置。
2. **步态模式要由几何决定**。腿已经伸直、腹部贴地时，"支撑脚钉地迈步"
   在物理上做不到，硬做只会得到悬空的脚。先算余量，再选模式。
"""

import math

import bpy
import numpy as np
from mathutils import Quaternion, Vector


# --------------------------------------------------------------------- 标定
def calibrate_signs(rig, spec, restore=True):
    """量出「绕局部 +X 旋转」对这个骨架意味着什么方向。

    返回 {'tail_lift': ±1, 'thigh_forward': ±1, 'arm_forward': ±1, 'ear_back': ±1}
    之后所有动画都乘上这些符号，换任何体型都不用改代码。
    """
    pose = rig.pose.bones
    saved = {pb.name: (tuple(pb.rotation_quaternion), tuple(pb.location)) for pb in pose}

    def reset():
        for pb in pose:
            pb.rotation_quaternion = (1, 0, 0, 0)
            pb.location = (0, 0, 0)
            pb.rotation_mode = "QUATERNION"
        bpy.context.view_layer.update()

    def probe(bone, axis, deg):
        pb = pose[bone]
        pb.rotation_quaternion = Quaternion(axis, math.radians(deg))
        bpy.context.view_layer.update()

    out = {"tail_lift": 1.0, "thigh_forward": 1.0, "arm_forward": 1.0, "ear_back": 1.0}

    # 尾：+15° 看尾尖升高还是降低
    tail = spec.get("tail") or []
    if tail:
        reset()
        tip0 = pose[tail[-1]].tail.z
        probe(tail[0], (1, 0, 0), 15)
        out["tail_lift"] = 1.0 if pose[tail[-1]].tail.z > tip0 else -1.0
        reset()

    # 腿：+15° 看脚往 -Y（前）还是 +Y（后）走
    leg = spec.get("leg_chain") or []
    if leg:
        reset()
        y0 = pose[leg[2]].head.y
        probe(leg[0], (1, 0, 0), 15)
        out["thigh_forward"] = -1.0 if pose[leg[2]].head.y < y0 else 1.0
        reset()

    # 臂：+15° 看手往哪边走
    arm = spec.get("arm_chain") or []
    if len(arm) >= 3:
        reset()
        y0 = pose[arm[-1]].head.y
        probe(arm[1] if len(arm) > 2 else arm[0], (1, 0, 0), 15)
        out["arm_forward"] = -1.0 if pose[arm[-1]].head.y < y0 else 1.0
        reset()

    # 耳：+15° 看耳尖往 +Y（后）还是 -Y（前）
    ears = [b for b in spec.get("key_bones", []) if "ear" in b.lower()]
    if ears:
        reset()
        y0 = pose[ears[0]].tail.y
        probe(ears[0], (1, 0, 0), 15)
        out["ear_back"] = 1.0 if pose[ears[0]].tail.y > y0 else -1.0
        reset()

    if restore:
        for pb in pose:
            q, loc = saved[pb.name]
            pb.rotation_quaternion = q
            pb.location = loc
        bpy.context.view_layer.update()
    return out


# --------------------------------------------------------------------- 步态规划
def plan_gait(feas, H, style="walk"):
    """按可行性给出一组步态参数。

    mode:
      stride          正常迈步（脚可以钉在原地滚动）
      short_stride    小步幅
      in_place_march  原地踏步（脚只抬落，不做前后位移）
      no_legs         无腿（靠位移/形变表演）
    """
    mode = feas.get("mode", "in_place_march")
    if mode == "no_legs":
        return {"mode": mode, "stance": 1.0, "stride": 0.0, "lift": 0.0, "bob": 0.02}

    run = style == "run"
    if mode == "stride":
        base = {"walk": (0.60, 0.16, 0.045, 0.020),
                "run": (0.38, 0.42, 0.075, 0.045)}[style]
        stance, stride_k, lift_k, bob_k = base
    elif mode == "short_stride":
        base = {"walk": (0.60, 0.09, 0.030, 0.012),
                "run": (0.42, 0.20, 0.050, 0.024)}[style]
        stance, stride_k, lift_k, bob_k = base
    else:  # in_place_march
        base = {"walk": (0.62, 0.0, 0.026, 0.000),
                "run": (0.38, 0.0, 0.052, 0.000)}[style]
        stance, stride_k, lift_k, bob_k = base

    return {
        "mode": mode,
        "stance": stance,
        "stride": stride_k * H,
        "lift": lift_k * H,
        "bob": bob_k * H,
        "cycle": 0.80 if style == "walk" else 0.53,
    }


# --------------------------------------------------------------------- 周期曲线
def smoothstep(t):
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)


def foot_cycle(q, stance, stride, lift):
    """返回 (相对前后位移, 抬起高度)。脚在支撑相线性后移，摆动相 smoothstep 前摆"""
    q = q % 1.0
    if q < stance:
        if stance <= 1e-9:
            return 0.0, 0.0
        return -stride / 2.0 + stride * (q / stance), 0.0
    s = (q - stance) / (1.0 - stance)
    return stride / 2.0 - stride * smoothstep(s), lift * math.sin(math.pi * s)


def arm_wave(q, stance):
    """与同相位腿极值对齐的摆臂波形，+1 = 最前。

    这是修「顺拐」的关键：手臂和腿必须用**极值点对齐**的波形，
    而不是两条形状不同的曲线各自摆。
    """
    return -math.cos(2.0 * math.pi * (q % 1.0 - stance))


def leg_phase_signal(q, stance):
    """腿部相位信号（+1 最前 / -1 最后），用于验收相关性"""
    q = q % 1.0
    if q < stance:
        return 1.0 - 2.0 * (q / stance)
    s = (q - stance) / (1.0 - stance)
    return -1.0 + 2.0 * smoothstep(s)


def tail_wave(i, n, t, amp_base=5.0, amp_gain=1.35, lag=0.5, sign=1.0, cycle=1.0):
    """尾巴行波：逐节滞后、振幅向尖端放大。sign 来自标定。"""
    ph = 2.0 * math.pi * t * cycle - lag * i
    pitch = (amp_base * (amp_gain ** i)) * math.sin(ph + 1.2)
    yaw = (amp_base * 2.0 * (amp_gain ** i)) * math.sin(ph)
    return sign * pitch, yaw


# --------------------------------------------------------------------- 关键帧
def record_pose(rig, bone_names, store, f):
    store[f] = {
        b: (tuple(rig.pose.bones[b].location), tuple(rig.pose.bones[b].rotation_quaternion))
        for b in bone_names
        if rig.pose.bones.get(b)
    }


def iter_fcurves(act):
    if hasattr(act, "fcurves"):
        for fc in act.fcurves:
            yield fc
        return
    for layer in getattr(act, "layers", []):
        for strip in getattr(layer, "strips", []):
            for bag in getattr(strip, "channelbags", []):
                for fc in getattr(bag, "fcurves", []):
                    yield fc


def write_action(rig, name, rec, linear=True):
    """写入 Action：强制四元数符号连续（避免 q/-q 翻转造成抽搐）"""
    old = bpy.data.actions.get(name)
    if old:
        old.name = name + "_prev"
        old.use_fake_user = True
    act = bpy.data.actions.new(name)
    act.use_fake_user = True
    if rig.animation_data is None:
        rig.animation_data_create()
    rig.animation_data.action = act

    prev = {}
    for f in sorted(rec):
        for bn, (loc, quat) in rec[f].items():
            pb = rig.pose.bones.get(bn)
            if pb is None:
                continue
            q = Quaternion(quat)
            if bn in prev and q.dot(prev[bn]) < 0.0:
                q = Quaternion((-q.w, -q.x, -q.y, -q.z))
            prev[bn] = q
            pb.location = loc
            pb.rotation_quaternion = q
            pb.keyframe_insert("location", frame=f)
            pb.keyframe_insert("rotation_quaternion", frame=f)

    if linear:
        for fc in iter_fcurves(act):
            for kp in fc.keyframe_points:
                kp.interpolation = "LINEAR"
    return act
