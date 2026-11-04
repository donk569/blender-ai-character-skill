"""通用体型测量：给任意角色网格，量出建骨架和判断能否走路所需的全部数据。

设计原则
--------
不假设角色是人、是动物、是几个头身。只从几何本身提取：

- 高度 / 包围盒 / 对称轴（AI 生成的网格常整体偏一点）
- 地面接触块的数量与位置  → 判断两足还是四足
- 裆高（左右腿在哪个高度合并）→ 判断是人形还是矮胖小动物
- 沿高度的宽度剖面        → 判断头部、躯干、腿的比例
- 腹部离地间隙、腿伸展率、水平可达余量 → 决定能不能迈步

所有长度都以身高 H 归一化后输出，调用方再用绝对尺寸换算。
"""

import math

import bpy
import numpy as np


# --------------------------------------------------------------------- 基础
def _verts_world(mesh_obj):
    me = mesh_obj.data
    n = len(me.vertices)
    co = np.empty(n * 3, dtype=np.float64)
    me.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    mw = np.array(mesh_obj.matrix_world)
    return co @ mw[:3, :3].T + mw[:3, 3]


def _cluster_xy(points, eps, min_n):
    """在 XY 平面聚类（points 为 3D，用 z 记高度），返回 [(中心xy, 点数, zmax), ...]"""
    if len(points) == 0:
        return []
    xy = points[:, :2]
    used = np.zeros(len(points), dtype=bool)
    out = []
    for i in range(len(points)):
        if used[i]:
            continue
        centre = xy[i]
        mask = np.linalg.norm(xy - centre, axis=1) < eps
        for _ in range(8):
            sel = xy[mask & ~used]
            if len(sel) == 0:
                break
            new_c = sel.mean(axis=0)
            new_mask = (np.linalg.norm(xy - new_c, axis=1) < eps) & ~used
            if new_mask.sum() == mask.sum():
                break
            mask = new_mask
            centre = new_c
        idx = mask & ~used
        used |= idx
        if idx.sum() >= min_n:
            sel = points[idx]
            out.append({
                "center": (float(sel[:, 0].mean()), float(sel[:, 1].mean())),
                "n": int(idx.sum()),
                "zmax": float(sel[:, 2].max()),
            })
    out.sort(key=lambda d: -d["n"])
    return out


def _crotch_height(co, x0, H, zmin):
    """裆高：左右腿「分得最开」的最低高度。

    只找中线附近有没有顶点是不行的 —— AI 网格常带游离碎片（挂饰、飘带），
    会在很低的位置就命中，把裆高测成 0。改用「横截面是否真的分成左右两簇」：
    从 55%H 往下扫，取最后一个仍分成两簇的高度。
    """
    lowest = None
    for k in range(55, 4, -1):
        z = zmin + H * k / 100.0
        band = co[np.abs(co[:, 2] - z) < H * 0.02]
        if len(band) < 60:
            continue
        xs = band[:, 0]
        nl = int((xs < x0 - H * 0.022).sum())
        nr = int((xs > x0 + H * 0.022).sum())
        nmid = int((np.abs(xs - x0) <= H * 0.022).sum())
        if nl > 25 and nr > 25 and nmid < 0.35 * (nl + nr):
            return float(z)      # 从上往下扫，第一次分裂即为裆高
    return zmin


# --------------------------------------------------------------------- 主入口
def measure(mesh_obj, forward=None):
    """返回测量字典（长度已按身高归一化，另有 *_abs 的绝对值）"""
    co = _verts_world(mesh_obj)
    lo, hi = co.min(axis=0), co.max(axis=0)
    H = float(hi[2] - lo[2])
    if H <= 1e-9:
        raise ValueError("网格高度为 0，无法测量")

    # 对称轴：地面接触块中心的平均值比包围盒中心更稳（AI 网格常整体偏一点）
    z0 = lo[2]
    ground = co[co[:, 2] < z0 + H * 0.06]
    patches = _cluster_xy(ground, eps=H * 0.045, min_n=30)
    if len(patches) >= 2:
        x0 = float(np.mean([p["center"][0] for p in patches[:2]]))
    else:
        x0 = float((lo[0] + hi[0]) / 2.0)

    # 朝向判定
    #   主信号：脚（或爪、蹄）一定朝前伸 —— 脚掌重心相对髋部所在高度明显偏前
    #   辅信号：头相对躯干的前伸
    #   不能只看头：长发、耳朵、角会往后伸，把方向判反（cyberpunk_girl 就这样翻过车）
    head = co[co[:, 2] > z0 + H * 0.80]
    body_cy = float(co[:, 1].mean())
    head_cy = float(head[:, 1].mean()) if len(head) else body_cy
    hip_band = co[(co[:, 2] > z0 + H * 0.42) & (co[:, 2] < z0 + H * 0.58)]
    hip_cy = float(hip_band[:, 1].mean()) if len(hip_band) else body_cy
    if patches:
        wsum = sum(p["n"] for p in patches)
        feet_cy = sum(p["center"][1] * p["n"] for p in patches) / max(wsum, 1)
    else:
        feet_cy = hip_cy
    if forward is None:
        d_feet = feet_cy - hip_cy
        if abs(d_feet) > H * 0.008:
            forward_sign = -1.0 if d_feet < 0 else 1.0
        else:
            forward_sign = -1.0 if head_cy < body_cy else 1.0
        detect = "feet" if abs(d_feet) > H * 0.008 else "head"
    else:
        forward_sign = 1.0 if forward == "+Y" else -1.0
        detect = "manual"

    crotch = _crotch_height(co, x0, H, z0)

    # 沿高度的剖面
    profile = []
    for k in range(20):
        z = z0 + H * (k + 0.5) / 20.0
        band = co[np.abs(co[:, 2] - z) < H * 0.025]
        if len(band) < 10:
            profile.append({"z": (z - z0) / H, "w": 0.0, "d": 0.0, "n": 0})
            continue
        profile.append({
            "z": (z - z0) / H,
            "w": float((band[:, 0].max() - band[:, 0].min()) / H),
            "d": float((band[:, 1].max() - band[:, 1].min()) / H),
            "n": int(len(band)),
        })

    # 腹部离地间隙：中线附近、中段 Y 的最低点
    # 腹部离地：中线列的最低点，但要排除「侧向接触块」（那是脚/鞋，不是肚子）
    side = [p for p in patches if abs(p["center"][0] - x0) > H * 0.03]
    center_col = (np.abs(co[:, 0] - x0) < H * 0.06) & \
                 (np.abs(co[:, 1] - body_cy) < (hi[1] - lo[1]) * 0.35)
    if side:
        foot_zone = np.zeros(len(co), dtype=bool)
        for p in side:
            cx, cy = p["center"]
            foot_zone |= (np.abs(co[:, 0] - cx) < H * 0.10) & (np.abs(co[:, 1] - cy) < H * 0.10)
        center_col &= ~foot_zone
    belly_clear = float(co[center_col, 2].min() - z0) if center_col.any() else H * 0.30

    # 网格健康度
    me = mesh_obj.data
    verts = len(me.vertices)
    tris = len(me.polygons)

    return {
        "height": H,
        "height_abs": H,
        "z_min": z0,
        "bbox": {"min": lo.tolist(), "max": hi.tolist()},
        "size": (hi - lo).tolist(),
        "axis_x": x0,
        "axis_x_offset": x0 - float((lo[0] + hi[0]) / 2.0),
        "forward_sign": forward_sign,
        "forward_axis": "-Y" if forward_sign < 0 else "+Y",
        "crotch": (crotch - z0) / H,
        "ground_patches": [
            {"center": p["center"], "n": p["n"], "zmax": (p["zmax"] - z0) / H}
            for p in patches
        ],
        "profile": profile,
        "head_cy_rel": (head_cy - body_cy) / H,
        "forward_detect": detect,
        "belly_clearance": belly_clear / H,
        "belly_clearance_abs": belly_clear,
        "verts": verts,
        "tris": tris,
        "density": tris / max(len(patches), 1),
    }


def guess_archetype(m):
    """根据地面接触点的「前后跨度」与裆高判断体型类型。

    不能只数接触块：脚跟/脚尖会各自成块，四足也可能只有 3 块。
    决定性信号是**前后跨度**——四足的前爪与后爪相距很远，两足的脚跟到脚尖很近。
    """
    patches = m["ground_patches"]
    H = max(m["height"], 1e-9)
    mx = max((p["n"] for p in patches), default=0)
    major = [p for p in patches if p["n"] >= 0.55 * mx] if patches else []
    m["major_contacts"] = len(major)
    crotch = m["crotch"]

    xs = [p["center"][0] for p in major]
    ys = [p["center"][1] for p in major]
    axis = m["axis_x"]
    two_sided = any(x < axis - 0.02 * H for x in xs) and any(x > axis + 0.02 * H for x in xs)
    spread = (max(ys) - min(ys)) / H if ys else 0.0

    if len(major) >= 3 and two_sided and spread > 0.25:
        return "quadruped", "接触点 %d 个、前后跨度 %.0f%%H（四足）" % (len(major), spread * 100)
    if crotch >= 0.34:
        return "humanoid", "两足，裆高 %.0f%%H" % (crotch * 100)
    return "biped_small", "两足矮胖型，裆高 %.0f%%H、接触点 %d 个" % (crotch * 100, len(major))


def feasibility(m, spec=None):
    """能否做「支撑脚钉地迈步」的预检。这是本流水线最容易被忽略、代价最大的一步。

    返回 dict：
      leg_extend      静止时腿的伸展率（1.0 = 完全伸直，没有弯曲余地）
      horiz_margin    踝高度不变时，脚还能水平移动多少（m）
      knee_room       髋能下降多少才会碰到腹部（m）
      mode            建议的步态模式
    """
    H = m["height"]
    out = {"leg_extend": None, "horiz_margin": None, "knee_room": None,
           "belly_clearance": m["belly_clearance_abs"], "mode": "in_place_march",
           "reasons": []}

    if spec is None:
        out["reasons"].append("未提供骨架 spec，无法计算腿部余量")
        return out

    leg = spec.get("leg_chain")
    if not leg:
        out["mode"] = "no_legs"
        out["reasons"].append("骨架无腿链")
        return out

    l1 = spec["bone_len"][leg[0]]
    l2 = spec["bone_len"][leg[1]]
    hip = np.array(spec["bone_head"][leg[0]])
    ankle = np.array(spec["bone_head"][leg[2]])
    total = l1 + l2
    d_rest = float(np.linalg.norm(ankle - hip))
    out["leg_extend"] = d_rest / total

    dz = float(hip[2] - ankle[2])
    horiz_max = math.sqrt(max(0.0, total ** 2 - dz ** 2))
    horiz_now = float(math.hypot(ankle[0] - hip[0], ankle[1] - hip[1]))
    out["horiz_margin"] = horiz_max - horiz_now
    out["knee_room"] = max(0.0, m["belly_clearance_abs"])

    # 判定
    hm = out["horiz_margin"] / H      # 归一化
    kr = out["knee_room"] / H
    if out["leg_extend"] > 0.985 and kr < 0.02:
        out["mode"] = "in_place_march"
        out["reasons"].append(
            "腿伸展率 %.1f%% + 腹部离地仅 %.1f%%H → 迈步必然悬空，只能原地踏步"
            % (out["leg_extend"] * 100, kr * 100))
    elif hm < 0.02:
        out["mode"] = "in_place_march"
        out["reasons"].append("水平可达余量仅 %.1f%%H，步幅受限" % (hm * 100))
    elif out["leg_extend"] > 0.96:
        out["mode"] = "short_stride"
        out["reasons"].append("腿较直（%.0f%%），只能小步幅" % (out["leg_extend"] * 100))
    else:
        out["mode"] = "stride"
        out["reasons"].append("腿部有弯曲余量，可正常迈步")
    return out


def report(m, archetype, reason, feas=None):
    """人读的报告文本"""
    L = []
    L.append("身高 %.3f m  三角面 %d" % (m["height"], m["tris"]))
    L.append("对称轴 x=%.4f（相对包围盒中心偏 %+.1f mm）"
             % (m["axis_x"], m["axis_x_offset"] * 1000))
    L.append("朝向判定 %s（头部重心相对身体 %+.1f%%H）"
             % (m["forward_axis"], m["head_cy_rel"] * 100))
    L.append("地面接触块 %d 个: %s" % (
        len(m["ground_patches"]),
        ", ".join("(%.3f,%.3f) n=%d" % (p["center"][0], p["center"][1], p["n"])
                  for p in m["ground_patches"][:4])))
    L.append("裆高 %.1f%%H   腹部离地 %.2f mm" % (m["crotch"] * 100, m["belly_clearance_abs"] * 1000))
    L.append("体型判定: %s  （依据:%s）" % (archetype, reason))
    if feas:
        if feas["leg_extend"] is not None:
            L.append("腿伸展率 %.1f%%   水平可达余量 %.2f mm   髋部下降空间 %.2f mm"
                     % (feas["leg_extend"] * 100, feas["horiz_margin"] * 1000,
                        feas["knee_room"] * 1000))
        L.append("步态建议: %s" % feas["mode"])
        for r in feas["reasons"]:
            L.append("  - " + r)
    return "\n".join(L)


def measure_scene(mesh_name=None, forward=None, log_path=None):
    """便捷入口：测量场景里的网格并写报告"""
    if mesh_name:
        obj = bpy.data.objects[mesh_name]
    else:
        obj = next(o for o in bpy.data.objects if o.type == "MESH")
    m = measure(obj, forward=forward)
    arch, reason = guess_archetype(m)
    txt = report(m, arch, reason)
    if log_path:
        open(log_path, "w", encoding="utf-8").write(txt)
    return m, arch, reason
