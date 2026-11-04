"""体型模板与骨架生成：把「测到的几何」变成「可直接建骨的 spec」。

核心思路
--------
不写死坐标。每个体型存一份**归一化模板**（z 按身高、x/y 按身高归一，前为 -y），
再按测量值做三件事：

1. 整体映射：world = (x0 + nx*H, cy - ny*H*forward_sign, z0 + nz*H)
2. 腿链重算：髋高 = 裆高、踝高 = 实测足高，膝按 55% 分配
3. 臂链重算：横向根数按该高度的实测半宽缩放

这样同一份模板能套到 1.7m 的人、0.28m 的仓鼠、或者任何比例。
"""

import math

import bpy
import numpy as np


def _T(bones, leg_chain=None, arm_chain=None, tail=None):
    return {"bones": bones, "leg_chain": leg_chain, "arm_chain": arm_chain, "tail": tail}


# 归一化坐标：z 从地面算起 / 身高；x 从中轴；y 前为负
HUMANOID = _T([
    ("root",        (0.000,  0.020, 0.000), (0.000, -0.040, 0.000), None,          False),
    ("hips",        (0.000,  0.005, 0.520), (0.000,  0.012, 0.560), "root",        True),
    ("spine",       (0.000,  0.012, 0.560), (0.000,  0.018, 0.630), "hips",        True),
    ("chest",       (0.000,  0.018, 0.630), (0.000,  0.024, 0.700), "spine",       True),
    ("upper_chest", (0.000,  0.024, 0.700), (0.000,  0.028, 0.755), "chest",       True),
    ("neck",        (0.000,  0.026, 0.755), (0.000,  0.020, 0.820), "upper_chest", True),
    ("head",        (0.000,  0.020, 0.820), (0.000,  0.010, 0.945), "neck",        True),
    ("shoulder.L",  (0.030,  0.028, 0.752), (0.085,  0.026, 0.748), "upper_chest", True),
    ("upper_arm.L", (0.085,  0.026, 0.748), (0.135,  0.022, 0.620), "shoulder.L",  True),
    ("forearm.L",   (0.135,  0.022, 0.620), (0.155,  0.016, 0.500), "upper_arm.L", True),
    ("hand.L",      (0.155,  0.016, 0.500), (0.160,  0.012, 0.440), "forearm.L",   True),
    ("shoulder.R",  (-0.030, 0.028, 0.752), (-0.085, 0.026, 0.748), "upper_chest", True),
    ("upper_arm.R", (-0.085, 0.026, 0.748), (-0.135, 0.022, 0.620), "shoulder.R",  True),
    ("forearm.R",   (-0.135, 0.022, 0.620), (-0.155, 0.016, 0.500), "upper_arm.R", True),
    ("hand.R",      (-0.155, 0.016, 0.500), (-0.160, 0.012, 0.440), "forearm.R",   True),
    ("thigh.L",     (0.058,  0.020, 0.520), (0.060,  0.024, 0.300), "hips",        True),
    ("shin.L",      (0.060,  0.024, 0.300), (0.062,  0.028, 0.075), "thigh.L",     True),
    ("foot.L",      (0.062,  0.028, 0.075), (0.062, -0.030, 0.020), "shin.L",      True),
    ("toe.L",       (0.062, -0.030, 0.020), (0.062, -0.070, 0.018), "foot.L",      True),
    ("thigh.R",     (-0.058, 0.020, 0.520), (-0.060, 0.024, 0.300), "hips",        True),
    ("shin.R",      (-0.060, 0.024, 0.300), (-0.062, 0.028, 0.075), "thigh.R",     True),
    ("foot.R",      (-0.062, 0.028, 0.075), (-0.062, -0.030, 0.020), "shin.R",     True),
    ("toe.R",       (-0.062, -0.030, 0.020), (-0.062, -0.070, 0.018), "foot.R",    True),
], leg_chain=["thigh.L", "shin.L", "foot.L"],
   arm_chain=["shoulder.L", "upper_arm.L", "forearm.L", "hand.L"])

# 矮胖两足小动物（仓鼠/鼠/吉祥物）：腿短、腹贴地、尾巴是表演主体
BIPED_SMALL = _T([
    ("root",        (0.000,  0.070, 0.000), (0.000, -0.140, 0.000), None,      False),
    ("hips",        (0.000, -0.070, 0.186), (0.000, -0.105, 0.321), "root",    True),
    ("spine",       (0.000, -0.105, 0.321), (0.000, -0.150, 0.436), "hips",    True),
    ("chest",       (0.000, -0.150, 0.436), (0.000, -0.196, 0.557), "spine",   True),
    ("neck",        (0.000, -0.196, 0.557), (0.000, -0.236, 0.643), "chest",   True),
    ("head",        (0.000, -0.236, 0.643), (0.000, -0.307, 0.886), "neck",    True),
    ("ear.L",       (0.129, -0.250, 0.829), (0.200, -0.271, 0.993), "head",    True),
    ("ear.R",       (-0.129, -0.250, 0.829), (-0.200, -0.271, 0.993), "head",  True),
    ("shoulder.L",  (0.064, -0.171, 0.536), (0.179, -0.221, 0.493), "chest",   True),
    ("upper_arm.L", (0.179, -0.221, 0.493), (0.221, -0.286, 0.371), "shoulder.L", True),
    ("forearm.L",   (0.221, -0.286, 0.371), (0.243, -0.321, 0.279), "upper_arm.L", True),
    ("hand.L",      (0.243, -0.321, 0.279), (0.250, -0.354, 0.207), "forearm.L", True),
    ("shoulder.R",  (-0.064, -0.171, 0.536), (-0.179, -0.221, 0.493), "chest", True),
    ("upper_arm.R", (-0.179, -0.221, 0.493), (-0.221, -0.286, 0.371), "shoulder.R", True),
    ("forearm.R",   (-0.221, -0.286, 0.371), (-0.243, -0.321, 0.279), "upper_arm.R", True),
    ("hand.R",      (-0.243, -0.321, 0.279), (-0.250, -0.354, 0.207), "forearm.R", True),
    ("thigh.L",     (0.107, -0.161, 0.207), (0.136, -0.221, 0.107), "hips",    True),
    ("shin.L",      (0.136, -0.221, 0.107), (0.150, -0.286, 0.039), "thigh.L", True),
    ("foot.L",      (0.150, -0.286, 0.039), (0.154, -0.371, 0.025), "shin.L",  True),
    ("toe.L",       (0.154, -0.371, 0.025), (0.154, -0.429, 0.021), "foot.L",  True),
    ("thigh.R",     (-0.107, -0.161, 0.207), (-0.136, -0.221, 0.107), "hips",  True),
    ("shin.R",      (-0.136, -0.221, 0.107), (-0.150, -0.286, 0.039), "thigh.R", True),
    ("foot.R",      (-0.150, -0.286, 0.039), (-0.154, -0.371, 0.025), "shin.R", True),
    ("toe.R",       (-0.154, -0.371, 0.025), (-0.154, -0.429, 0.021), "foot.R", True),
    ("tail.01",     (0.111,  0.071, 0.050), (0.207,  0.200, 0.043), "hips",    True),
    ("tail.02",     (0.207,  0.200, 0.043), (0.286,  0.314, 0.054), "tail.01", True),
    ("tail.03",     (0.286,  0.314, 0.054), (0.321,  0.407, 0.150), "tail.02", True),
    ("tail.04",     (0.321,  0.407, 0.150), (0.357,  0.521, 0.307), "tail.03", True),
], leg_chain=["thigh.L", "shin.L", "foot.L"],
   arm_chain=["shoulder.L", "upper_arm.L", "forearm.L", "hand.L"],
   tail=["tail.01", "tail.02", "tail.03", "tail.04"])

# 四足：脊柱沿 Y 水平，肩在胸、髋在骨盆
QUADRUPED = _T([
    ("root",        (0.000,  0.000, 0.000), (0.000, -0.100, 0.000), None,    False),
    ("hips",        (0.000,  0.180, 0.560), (0.000,  0.060, 0.610), "root",  True),
    ("spine",       (0.000,  0.060, 0.610), (0.000, -0.080, 0.620), "hips",  True),
    ("chest",       (0.000, -0.080, 0.620), (0.000, -0.200, 0.610), "spine", True),
    ("neck",        (0.000, -0.200, 0.610), (0.000, -0.280, 0.640), "chest", True),
    ("head",        (0.000, -0.280, 0.640), (0.000, -0.400, 0.680), "neck",  True),
    ("ear.L",       (0.070, -0.270, 0.700), (0.090, -0.290, 0.790), "head",  True),
    ("ear.R",       (-0.070, -0.270, 0.700), (-0.090, -0.290, 0.790), "head", True),
    ("shoulder.L",  (0.060, -0.170, 0.580), (0.125, -0.175, 0.520), "chest", True),
    ("upper_arm.L", (0.125, -0.175, 0.520), (0.130, -0.180, 0.300), "shoulder.L", True),
    ("forearm.L",   (0.130, -0.180, 0.300), (0.132, -0.150, 0.070), "upper_arm.L", True),
    ("hand.L",      (0.132, -0.150, 0.070), (0.132, -0.200, 0.030), "forearm.L", True),
    ("shoulder.R",  (-0.060, -0.170, 0.580), (-0.125, -0.175, 0.520), "chest", True),
    ("upper_arm.R", (-0.125, -0.175, 0.520), (-0.130, -0.180, 0.300), "shoulder.R", True),
    ("forearm.R",   (-0.130, -0.180, 0.300), (-0.132, -0.150, 0.070), "upper_arm.R", True),
    ("hand.R",      (-0.132, -0.150, 0.070), (-0.132, -0.200, 0.030), "forearm.R", True),
    ("thigh.L",     (0.110,  0.170, 0.560), (0.120,  0.130, 0.320), "hips",  True),
    ("shin.L",      (0.120,  0.130, 0.320), (0.122,  0.160, 0.070), "thigh.L", True),
    ("foot.L",      (0.122,  0.160, 0.070), (0.122,  0.110, 0.030), "shin.L", True),
    ("toe.L",       (0.122,  0.110, 0.030), (0.122,  0.060, 0.026), "foot.L", True),
    ("thigh.R",     (-0.110, 0.170, 0.560), (-0.120, 0.130, 0.320), "hips", True),
    ("shin.R",      (-0.120, 0.130, 0.320), (-0.122, 0.160, 0.070), "thigh.R", True),
    ("foot.R",      (-0.122, 0.160, 0.070), (-0.122, 0.110, 0.030), "shin.R", True),
    ("toe.R",       (-0.122, 0.110, 0.030), (-0.122, 0.060, 0.026), "foot.R", True),
    ("tail.01",     (0.000,  0.230, 0.560), (0.000,  0.330, 0.520), "hips",  True),
    ("tail.02",     (0.000,  0.330, 0.520), (0.000,  0.430, 0.470), "tail.01", True),
    ("tail.03",     (0.000,  0.430, 0.470), (0.000,  0.520, 0.420), "tail.02", True),
], leg_chain=["thigh.L", "shin.L", "foot.L"],
   arm_chain=["shoulder.L", "upper_arm.L", "forearm.L", "hand.L"],
   tail=["tail.01", "tail.02", "tail.03"])

# 无肢体（史莱姆/幽灵/球）：只有脊柱与头，靠形变和位移表演
BLOB = _T([
    ("root",  (0.000, 0.000, 0.000), (0.000, -0.060, 0.000), None,   False),
    ("hips",  (0.000, 0.000, 0.220), (0.000, 0.000, 0.400), "root",  True),
    ("spine", (0.000, 0.000, 0.400), (0.000, 0.000, 0.560), "hips",  True),
    ("chest", (0.000, 0.000, 0.560), (0.000, 0.000, 0.700), "spine", True),
    ("neck",  (0.000, 0.000, 0.700), (0.000, 0.000, 0.780), "chest", True),
    ("head",  (0.000, 0.000, 0.780), (0.000, 0.000, 0.950), "neck",  True),
], leg_chain=None, arm_chain=None)

TEMPLATES = {
    "humanoid": HUMANOID,
    "biped_small": BIPED_SMALL,
    "quadruped": QUADRUPED,
    "blob": BLOB,
}


# --------------------------------------------------------------------- 建 spec
def build_spec(meas, archetype="humanoid", drop=("ear.L", "ear.R"), keep_tail=None):
    """把归一化模板映射成世界坐标 spec。

    drop      : 要去掉的骨骼名（比如这个角色没有尾巴/耳朵）
    keep_tail : True/False 强制保留或去掉尾巴；None 用模板默认
    """
    tpl = TEMPLATES[archetype]
    H = meas["height"]
    z0 = meas["z_min"]
    x0 = meas["axis_x"]
    # 模板里 y 负 = 前方。角色朝 -Y 时 forward_sign=-1，要把模板的负 y 映射到世界负 y
    ysign = meas["forward_sign"]

    lo, hi = np.array(meas["bbox"]["min"]), np.array(meas["bbox"]["max"])
    cy = float((lo[1] + hi[1]) / 2.0)

    # 实测脚高：地面接触块的高度
    patches = meas["ground_patches"]
    if patches:
        foot_h = float(np.mean([p["zmax"] for p in patches[:2]])) * H
        foot_h = min(max(foot_h, H * 0.018), H * 0.11)
    else:
        foot_h = H * 0.045

    # 髋高以 hips 骨为基准：裆高在"肚子贴地"的角色上会被测成 2%H，不可用
    hips_head_z = next(hd[2] for name, hd, tl, p, d in tpl["bones"] if name == "hips") * H
    hip_z = max(hips_head_z + H * 0.02, foot_h + H * 0.10)
    hip_z = min(hip_z, H * 0.85)

    bones = []
    for name, hd, tl, parent, deform in tpl["bones"]:
        bones.append([name, list(hd), list(tl), parent, deform])

    # ---- 腿链重算：髋=裆高，踝=实测足高，膝按 55%
    legc = tpl["leg_chain"]
    if legc:
        for side in ("L", "R"):
            th, sh, ft = "thigh." + side, "shin." + side, "foot." + side
            toe = "toe." + side
            rec = {b[0]: b for b in bones}
            if th not in rec:
                continue
            hx = rec[th][1][0]
            hy = rec[th][1][1]
            knee_z = foot_h + (hip_z - foot_h) * 0.55
            rec[th][1][2] = hip_z / H
            rec[th][2] = [rec[th][2][0], rec[th][2][1], knee_z / H]
            if sh in rec:
                rec[sh][1] = list(rec[th][2])
                rec[sh][2] = [rec[sh][2][0], rec[sh][2][1], foot_h / H]
            if ft in rec:
                rec[ft][1] = list(rec[sh][2])
                fy = rec[ft][2][1]
                rec[ft][2] = [rec[ft][2][0], fy, (foot_h * 0.65) / H]
            if toe in rec:
                rec[toe][1] = list(rec[ft][2])

    # ---- 臂链重算：横向按该高度实测半宽缩放
    armc = tpl["arm_chain"]
    if armc:
        # 只钳制上限：手臂不能长到包围盒外面去。模板比例保留。
        half_w = max(hi[0] - x0, x0 - lo[0]) / H * 0.96
        for entry in bones:
            nm, hd, tl, parent, deform = entry
            if any(k in nm for k in ("shoulder", "arm", "hand")):
                for v in (hd, tl):
                    if abs(v[0]) > half_w:
                        v[0] = math.copysign(half_w, v[0])

    # ---- 映射到世界坐标
    out = []
    for name, hd, tl, parent, deform in bones:
        def w(p):
            return (x0 + p[0] * H, cy - p[1] * H * ysign, z0 + p[2] * H)
        out.append((name, w(hd), w(tl), parent, deform))

    # ---- 过滤
    drop_set = set(drop)
    if keep_tail is False:
        drop_set |= {b[0] for b in out if b[0].startswith("tail")}
    out = [b for b in out if b[0] not in drop_set]
    # 父级被删则改挂到最近的祖先
    names = {b[0] for b in out}
    fixed = []
    for name, hd, tl, parent, deform in out:
        if parent and parent not in names:
            parent = next((b[3] for b in out if b[0] == parent), None)
            if parent and parent not in names:
                parent = "hips" if "hips" in names else None
        fixed.append((name, hd, tl, parent, deform))

    def sided(chain):
        """把只有 L 侧的链展开成 {'L':[...], 'R':[...]}，并顺带并入 toe（若有）"""
        out = {}
        for side in ("L", "R"):
            c = [b[:-2] + "." + side for b in (chain or [])]
            c = [b for b in c if b in names]
            is_leg = bool(chain) and ("thigh" in chain[0] or "leg" in chain[0])
            if c and is_leg and ("toe." + side) in names:
                c = c + ["toe." + side]
            if c:
                out[side] = c
        return out

    spec = {
        "archetype": archetype,
        "bones": fixed,
        "leg_chain": [b for b in (tpl["leg_chain"] or []) if b in names],
        "arm_chain": [b for b in (tpl["arm_chain"] or []) if b in names],
        "leg_chains": sided(tpl["leg_chain"]),
        "arm_chains": sided(tpl["arm_chain"]),
        "tail": [b for b in (tpl["tail"] or []) if b in names],
    }
    spec["bone_head"] = {b[0]: b[1] for b in fixed}
    spec["bone_len"] = {
        b[0]: math.dist(b[1], b[2]) for b in fixed
    }
    spec["key_bones"] = [b[0] for b in fixed if b[4]]
    return spec


def mirror_name(name):
    if name.endswith(".L"):
        return name[:-2] + ".R"
    if name.endswith(".R"):
        return name[:-2] + ".L"
    return None


def describe(spec):
    lines = ["体型: %s  骨数 %d（变形 %d）"
             % (spec["archetype"], len(spec["bones"]), len(spec["key_bones"]))]
    lines.append("腿链: %s" % (spec["leg_chain"] or "无"))
    lines.append("臂链: %s" % (spec["arm_chain"] or "无"))
    lines.append("尾链: %s" % (spec["tail"] or "无"))
    for name, hd, tl, parent, deform in spec["bones"]:
        lines.append("  %-14s z %.3f→%.3f  x %+.3f  父级 %s%s"
                     % (name, hd[2], tl[2], hd[0], parent or "-", "" if deform else " [非变形]"))
    return "\n".join(lines)
