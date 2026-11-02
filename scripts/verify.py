"""动画验收脚本 —— 在 Blender 内执行。

输出一张指标表，判定动作是否达标。用法：
    sys.path.insert(0, "<scripts目录>")
    from verify import verify_all
    verify_all(["Idle", "Walk", "Run", "Attack"], out_path="verify.txt")

判定线（不合格就应该继续改，不要交付）：
    单帧最大角跳变  < 20°
    地面 min_z      -3 ~ +3 mm（跑步允许正的腾空帧）
    corr(同侧腿,臂)  < -0.9
    corr(对侧腿,臂)  > +0.9
    root fcurve 数   == 0
"""

import math
import numpy as np
from mathutils import Vector

try:
    import bpy
except ImportError:
    bpy = None


def _fcurves(act):
    if hasattr(act, "fcurves"):
        yield from act.fcurves
        return
    for layer in getattr(act, "layers", []):
        for strip in getattr(layer, "strips", []):
            for bag in getattr(strip, "channelbags", []):
                for fc in getattr(bag, "fcurves", []):
                    yield fc


def _min_z(mesh_obj):
    ev = mesh_obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    me = ev.to_mesh()
    co = np.empty(len(me.vertices) * 3, dtype=np.float32)
    me.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    m = np.array(ev.matrix_world)
    z = co @ m[2, :3] + m[2, 3]
    ev.to_mesh_clear()
    return float(z.min())


def _corr(a, b):
    a, b = np.array(a), np.array(b)
    if a.std() < 1e-9 or b.std() < 1e-9:
        return 0.0
    return float(np.corrcoef(a, b)[0, 1])


def verify_action(rig, mesh_obj, name, out=print, cyclic=True):
    act = bpy.data.actions.get(name)
    if not act:
        out("  [%s] action 不存在" % name)
        return None

    sc = bpy.context.scene
    rig.animation_data.action = act
    mw = rig.matrix_world
    f0, f1 = int(act.frame_range[0]), int(act.frame_range[1])
    bones = [b.name for b in rig.data.bones if b.name != "root"]

    out("")
    out("=== %s  frames %d-%d (%.2fs) ===" % (name, f0, f1, (f1 - f0 + 1) / 30.0))

    prev = None
    worst = (0.0, 0, "")
    zs = []
    footL, footR, handL, handR = [], [], [], []

    for f in range(f0, f1 + 1):
        sc.frame_set(f)
        bpy.context.view_layer.update()
        zs.append(_min_z(mesh_obj))
        footL.append((mw @ rig.pose.bones["foot.L"].head).copy())
        footR.append((mw @ rig.pose.bones["foot.R"].head).copy())
        handL.append((mw @ rig.pose.bones["hand.L"].head).copy())
        handR.append((mw @ rig.pose.bones["hand.R"].head).copy())

        if prev:
            for bn in bones:
                a = math.degrees(prev[bn].rotation_difference(
                    rig.pose.bones[bn].matrix.to_quaternion()).angle)
                if a > 180:
                    a = 360 - a
                if a > worst[0]:
                    worst = (a, f, bn)
        prev = {bn: rig.pose.bones[bn].matrix.to_quaternion() for bn in bones}

    ok_jump = worst[0] < (20.0 if cyclic else 45.0)
    out("  单帧最大角跳变  %6.1f deg @f%d(%s)   %s"
        % (worst[0], worst[1], worst[2], "OK" if ok_jump else "<-- 超标"))

    lo, hi = min(zs) * 1000, max(zs) * 1000
    ok_ground = lo > -3.0
    out("  地面 min_z      %+.1f ~ %+.1f mm        %s"
        % (lo, hi, "OK" if ok_ground else "<-- 穿地"))

    flight = [f0 + i for i, z in enumerate(zs) if z > 0.005]
    if flight:
        out("  腾空帧           %s" % flight)

    # 交叉协调：只对循环位移动作有意义（Attack 是单次动作，手脚相位不构成循环关系）
    yL = [p.y for p in footL]; yR = [p.y for p in footR]
    aL = [p.y for p in handL]; aR = [p.y for p in handR]
    if cyclic:
        c_same = _corr(yL, aL)
        c_cross = _corr(yL, aR)
        ok_cross = (c_same < -0.9) and (c_cross > 0.9)
        out("  corr(腿L,臂L)   %+.3f    corr(腿L,臂R) %+.3f   %s"
            % (c_same, c_cross, "OK" if ok_cross else "<-- 可能顺拐/弱耦合"))
        out("  corr(臂L,臂R)   %+.3f" % _corr(aL, aR))
    else:
        c_same = c_cross = 0.0
        out("  交叉协调        （非循环位移动作，跳过）")

    # 手前后摆幅
    out("  手前后摆幅      L %.3f m   R %.3f m"
        % (max(aL) - min(aL), max(aR) - min(aR)))

    # 循环接缝
    sc.frame_set(f0)
    bpy.context.view_layer.update()
    q0 = {bn: rig.pose.bones[bn].matrix.to_quaternion() for bn in bones}
    p0 = {bn: (mw @ rig.pose.bones[bn].head).copy() for bn in bones}
    sc.frame_set(f1)
    bpy.context.view_layer.update()
    ma = mp = 0.0
    for bn in bones:
        a = math.degrees(q0[bn].rotation_difference(
            rig.pose.bones[bn].matrix.to_quaternion()).angle)
        if a > 180:
            a = 360 - a
        ma = max(ma, a)
        mp = max(mp, ((mw @ rig.pose.bones[bn].head) - p0[bn]).length)
    note = "OK" if cyclic else "（非循环动作，仅作参考）"
    out("  首尾接缝        %.1f deg / %.0f mm        %s" % (ma, mp * 1000, note))

    # 根位移
    root_fc = sum(1 for fc in _fcurves(act) if '"root"' in fc.data_path)
    out("  root fcurve 数  %d                        %s"
        % (root_fc, "OK" if root_fc == 0 else "<-- 有根位移"))

    # 穿模：前臂/手到躯干
    def seg_dist(p, a, b):
        ab = b - a
        t = max(0.0, min(1.0, (p - a).dot(ab) / max(ab.length_squared, 1e-9)))
        return (p - (a + ab * t)).length

    torso = [(bn, mw @ rig.pose.bones[bn].head, mw @ rig.pose.bones[bn].tail)
             for bn in ("spine", "chest", "upper_chest")]
    best = (9.9, 0, "")
    for f in range(f0, f1 + 1):
        sc.frame_set(f)
        bpy.context.view_layer.update()
        for bn in ("forearm.L", "forearm.R", "hand.L", "hand.R"):
            pb = rig.pose.bones[bn]
            a0, a1 = mw @ pb.head, mw @ pb.tail
            for tn, t0, t1 in torso:
                d = min(seg_dist(a0, t0, t1), seg_dist(a1, t0, t1),
                        seg_dist(t0, a0, a1), seg_dist(t1, a0, a1))
                if d < best[0]:
                    best = (d, f, "%s/%s" % (bn, tn))
    ok_gap = best[0] > 0.12
    out("  前臂到躯干间隙  %.3f m @f%d(%s)  %s"
        % (best[0], best[1], best[2], "OK" if ok_gap else "<-- 疑似穿模"))

    return {
        "name": name, "max_jump": worst[0], "ground_lo": lo, "ground_hi": hi,
        "corr_same": c_same, "corr_cross": c_cross,
        "seam_deg": ma, "seam_mm": mp * 1000, "root_fc": root_fc,
        "gap": best[0],
    }


def verify_all(names, out_path=None, rig=None, mesh_obj=None):
    if rig is None or mesh_obj is None:
        rig = next(o for o in bpy.data.objects if o.type == "ARMATURE")
        mesh_obj = None
        for o in bpy.data.objects:
            if o.type == "MESH":
                mesh_obj = o
                break

    lines = []
    def collect(s):
        lines.append(s)
        print(s)

    results = []
    for nm in names:
        cyclic = nm.lower() in ("idle", "walk", "run")
        r = verify_action(rig, mesh_obj, nm, out=collect, cyclic=cyclic)
        if r:
            results.append(r)

    if out_path:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
    return results
