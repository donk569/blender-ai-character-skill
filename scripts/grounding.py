"""通用放脚与贴地：解析两骨 IK + 闭环校正。

为什么要闭环：腿一伸直，解析算出来的脚高会和网格实际最低点差 1~3mm；
髋部一旦带俯仰/侧倾，误差更大。每帧量一次真实脚底高度再修正目标，
能把误差压到 0.5mm 内，而且不依赖任何体型假设。
"""

import math

import bpy
import numpy as np
from mathutils import Matrix, Quaternion, Vector


def rest_pitch(rig, foot_bone):
    """脚掌静止时的世界俯仰（度，正=脚尖朝上）"""
    m = rig.data.bones[foot_bone].matrix_local.to_3x3()
    d = m @ Vector((0.0, 1.0, 0.0))
    return math.degrees(math.asin(max(-1.0, min(1.0, d.z))))


def aim_matrix(direction, up=Vector((0.0, -1.0, 0.0))):
    y = Vector(direction).normalized()
    z = up - y * up.dot(y)
    if z.length < 1e-5:
        alt = Vector((1.0, 0.0, 0.0))
        z = alt - y * alt.dot(y)
        if z.length < 1e-5:
            alt = Vector((0.0, 0.0, 1.0))
            z = alt - y * alt.dot(y)
    z.normalize()
    x = y.cross(z).normalized()
    return Matrix(((x.x, y.x, z.x, 0.0),
                   (x.y, y.y, z.y, 0.0),
                   (x.z, y.z, z.z, 0.0),
                   (0.0, 0.0, 0.0, 1.0)))


class LegSolver:
    """一条腿（3 节）的解析 IK：膝盖朝前，脚掌俯仰单独控制"""

    def __init__(self, rig, spec, chain, update=None):
        self.rig = rig
        self.thigh, self.shin, self.foot = chain[0], chain[1], chain[2]
        self.l1 = spec["bone_len"][self.thigh]
        self.l2 = spec["bone_len"][self.shin]
        self.rest = rig.data.bones[self.foot].matrix_local.to_3x3()
        self.pitch0 = rest_pitch(rig, self.foot)
        self.update = update or (lambda: bpy.context.view_layer.update())

    def set_pitch(self, deg):
        pb = self.rig.pose.bones[self.foot]
        theta = self.pitch0 - deg
        m = (Matrix.Rotation(math.radians(theta), 3, "X") @ self.rest).to_4x4()
        m.translation = pb.head
        pb.matrix = m

    def solve(self, target, foot_pitch=0.0, pole=Vector((0.0, -1.0, 0.0))):
        th = self.rig.pose.bones[self.thigh]
        sh = self.rig.pose.bones[self.shin]
        H = th.head.copy()
        T = Vector(target)
        v = T - H
        l1, l2 = self.l1, self.l2
        d = max(abs(l1 - l2) + 1e-3 * (l1 + l2),
                min(l1 + l2 - 2e-4 * (l1 + l2), v.length))
        if v.length < 1e-6:
            v = Vector((0.0, 0.0, -1.0))
        ydir = v.normalized()
        cosa = (l1 * l1 + d * d - l2 * l2) / (2.0 * l1 * d)
        hip_a = math.acos(max(-1.0, min(1.0, cosa)))
        axis = ydir.cross(pole)
        if axis.length < 1e-6:
            axis = Vector((1.0, 0.0, 0.0))
        axis.normalize()
        thigh_dir = (Quaternion(axis, hip_a) @ ydir).normalized()
        m = aim_matrix(thigh_dir)
        m.translation = H
        th.matrix = m
        self.update()

        K = th.tail.copy()
        shin_dir = T - K
        if shin_dir.length < 1e-6:
            shin_dir = ydir
        m2 = aim_matrix(shin_dir.normalized())
        m2.translation = K
        sh.matrix = m2
        self.update()
        self.set_pitch(foot_pitch)


# --------------------------------------------------------------------- 网格侧
def eval_zs(mesh_obj):
    dg = bpy.context.evaluated_depsgraph_get()
    ev = mesh_obj.evaluated_get(dg)
    m = ev.to_mesh()
    a = np.empty(len(m.vertices) * 3, dtype=np.float64)
    m.vertices.foreach_get("co", a)
    z = a.reshape(-1, 3)[:, 2].copy()
    ev.to_mesh_clear()
    return z


def verts_of_group(mesh_obj, group, thr=0.35, cache=None):
    if cache is not None and group in cache:
        return cache[group]
    gi = {g.name: g.index for g in mesh_obj.vertex_groups}
    if group not in gi:
        return np.array([], dtype=np.int64)
    W = np.zeros((len(mesh_obj.data.vertices), len(mesh_obj.vertex_groups)), dtype=np.float32)
    for v in mesh_obj.data.vertices:
        for g in v.groups:
            W[v.index, g.group] = g.weight
    out = np.nonzero(W[:, gi[group]] > thr)[0]
    if cache is not None:
        cache[group] = out
    return out


def place_feet(mesh_obj, solvers, targets, soles, pitches, iters=3, tol=0.0005):
    """闭环放脚：解 IK → 量脚底实际最低点 → 修正目标高度"""
    zt = {}
    for side, sv in solvers.items():
        zt[side] = targets[side][2]
    for it in range(iters):
        for side, sv in solvers.items():
            x, y = targets[side][0], targets[side][1]
            sv.solve(Vector((x, y, zt[side])), pitches[side])
        z = eval_zs(mesh_obj)
        bad = False
        for side, sv in solvers.items():
            idx = _foot_idx(mesh_obj, sv)
            if len(idx) == 0:
                continue
            mz = float(z[idx].min())
            err = soles[side] - mz
            if abs(err) > tol and it < iters - 1:
                zt[side] += err * 0.9
                bad = True
        if not bad:
            break
    return zt


_foot_cache = {}


def _foot_idx(mesh_obj, sv):
    key = (mesh_obj.name, sv.foot)
    if key not in _foot_cache:
        _foot_cache[key] = verts_of_group(mesh_obj, sv.foot)
    return _foot_cache[key]


def settle_body(rig, mesh_obj, hips_bone, vert_mask=None, tol=-0.0025, rounds=3):
    """若身体某处穿地，上抬髋部并重新放脚。vert_mask 用来排除「本来就该贴地」的部位。"""
    HIPS_INV = rig.data.bones[hips_bone].matrix_local.to_3x3().inverted()
    for _ in range(rounds):
        z = eval_zs(mesh_obj)
        mz = float(z[vert_mask].min()) if vert_mask is not None else float(z.min())
        if mz >= tol:
            return mz
        pb = rig.pose.bones[hips_bone]
        pb.location = pb.location + HIPS_INV @ Vector((0.0, 0.0, -mz))
        bpy.context.view_layer.update()
    return mz


def non_appendage_mask(mesh_obj, spec, verts_thr=0.5):
    """排除尾巴这类「本来就贴地」的部件，否则它们一擦地就把全身抬起来"""
    tail = spec.get("tail") or []
    gi = {g.name: g.index for g in mesh_obj.vertex_groups}
    idx = [gi[t] for t in tail if t in gi]
    if not idx:
        return None
    W = np.zeros((len(mesh_obj.data.vertices), len(mesh_obj.vertex_groups)), dtype=np.float32)
    for v in mesh_obj.data.vertices:
        for g in v.groups:
            W[v.index, g.group] = g.weight
    keep = W[:, idx].sum(axis=1) < verts_thr
    return np.nonzero(keep)[0]
