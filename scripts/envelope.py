"""包络权重（最近骨骼 + 距离衰减）：任意体型都能跑的默认蒙皮方案。

为什么不用骨骼热权重当默认
--------------------------------
热权重（bone heat）要求网格水密、无自交、无碎片。AI 生成的网格恰恰相反：
几千个壳体、重叠薄片、非流形边。实测在这类网格上热权重**要么整体失败（全 0），
要么某根骨吃掉 60% 顶点**，而且失败模式与体素尺寸、缩放、骨长强相关，很难通用。

包络法只看「顶点到骨骼线段的距离」，与网格拓扑无关：
    权重 ∝ 1 / d^p，只取最近的几根骨，左右分侧防止串腿。
再叠 2 轮拉普拉斯平滑把边界磨软。对任意体型都稳定，代价是关节处的
细节不如手工刷的权重，但对「自动流水线」这是正确的取舍。
"""

import numpy as np


def _verts(mesh_obj):
    me = mesh_obj.data
    n = len(me.vertices)
    co = np.empty(n * 3, dtype=np.float64)
    me.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    mw = np.array(mesh_obj.matrix_world)
    return co @ mw[:3, :3].T + mw[:3, 3]


def _dist_to_segment(co, a, b):
    ab = b - a
    L2 = float(ab @ ab)
    if L2 <= 1e-12:
        return np.linalg.norm(co - a, axis=1)
    t = np.clip((co - a) @ ab / L2, 0.0, 1.0)
    proj = a[None, :] + t[:, None] * ab[None, :]
    return np.linalg.norm(co - proj, axis=1)


def _smooth(mesh_obj, W, iters=2, alpha=0.5):
    """沿网格边做拉普拉斯平滑（权重层面，与骨长/网格密度无关）"""
    me = mesh_obj.data
    ne = len(me.edges)
    if ne == 0 or iters <= 0:
        return W
    e = np.empty(ne * 2, dtype=np.int64)
    me.edges.foreach_get("vertices", e)
    e = e.reshape(-1, 2)
    i, j = e[:, 0], e[:, 1]
    deg = np.bincount(np.concatenate([i, j]), minlength=len(W)).astype(np.float64)
    deg[deg == 0] = 1.0

    for _ in range(iters):
        acc = np.zeros_like(W)
        np.add.at(acc, i, W[j])
        np.add.at(acc, j, W[i])
        avg = acc / deg[:, None]
        W = (1.0 - alpha) * W + alpha * avg
        s = W.sum(axis=1, keepdims=True)
        s[s <= 1e-12] = 1.0
        W = W / s
    return W


def envelope_weights(mesh_obj, rig, spec, log=print, power=3.2, max_bones=4,
                     side_gate=True, side_band=0.012, smooth_iters=2):
    """返回权重矩阵并写入网格顶点组。"""
    if spec is None:
        raise ValueError("需要 spec（由 archetypes.build_spec 产出）")

    deform = [b for b in spec["bones"] if b[4]]
    for b in deform:
        if b[0] not in mesh_obj.vertex_groups:
            mesh_obj.vertex_groups.new(name=b[0])
    names = [g.name for g in mesh_obj.vertex_groups]
    gidx = {n: i for i, n in enumerate(names)}

    co = _verts(mesh_obj)
    nv = len(co)
    H = spec.get("height") or float(co[:, 2].max() - co[:, 2].min())
    x0 = spec.get("axis_x", float(co[:, 0].mean()))

    bones = [(n, np.array(h, dtype=np.float64), np.array(t, dtype=np.float64))
             for n, h, t, p, d in spec["bones"] if d and n in gidx]
    if not bones:
        raise ValueError("没有可用的变形骨")

    D = np.empty((nv, len(bones)), dtype=np.float32)
    for j, (n, h, t) in enumerate(bones):
        D[:, j] = _dist_to_segment(co, h, t)

    # 左右分侧：跨过中线的顶点不参与对侧肢体，避免左腿骨拽到右腿
    if side_gate:
        off = co[:, 0] - x0
        for j, (n, h, t) in enumerate(bones):
            if n.endswith(".L"):
                D[off < -side_band * H, j] = 1e3
            elif n.endswith(".R"):
                D[off > side_band * H, j] = 1e3

    dmin = D.min(axis=1, keepdims=True)
    floor = dmin * 1.9 + 0.012 * H
    keep = D <= floor

    if len(bones) > max_bones:
        order = np.argsort(D, axis=1)[:, :max_bones]
        mask = np.zeros_like(keep)
        mask[np.arange(nv)[:, None], order] = True
        keep &= mask

    d = np.maximum(D.astype(np.float64), 1e-5)
    W = np.where(keep, 1.0 / (d ** power), 0.0)

    # 极端情况：某顶点被分侧规则关掉了所有骨 → 退回最近的骨
    dead = W.sum(axis=1) <= 0
    if dead.any():
        idx = np.argsort(D[dead], axis=1)[:, 0]
        rows = np.nonzero(dead)[0]
        W[rows, idx] = 1.0

    s = W.sum(axis=1, keepdims=True)
    W = W / np.maximum(s, 1e-12)

    full = np.zeros((nv, len(names)), dtype=np.float64)
    for j, (n, h, t) in enumerate(bones):
        full[:, gidx[n]] = W[:, j]

    if smooth_iters:
        full = _smooth(mesh_obj, full, iters=smooth_iters)

    # 写入（只保留前 4 个，避免权重表爆炸）
    top = np.argsort(-full, axis=1)[:, :4]
    sparse = np.zeros_like(full)
    sparse[np.arange(nv)[:, None], top] = full[np.arange(nv)[:, None], top]
    ss = sparse.sum(axis=1, keepdims=True)
    ss[ss <= 1e-12] = 1.0
    sparse = sparse / ss

    write_matrix(mesh_obj, sparse)
    used = int((sparse.max(axis=0) > 0.01).sum())
    log("[envelope] %d 顶点 / %d 骨（生效 %d 根），平滑 %d 轮" % (nv, len(names), used, smooth_iters))
    return sparse


def write_matrix(mesh_obj, W, eps=1e-4, bins=200):
    """批量写入顶点组。

    Blender 只有 `group.add(索引列表, 权重)` 这个接口，逐点调用在 20 万顶点上
    要几十万次、耗几分钟。把权重按 bins 量化后按 (组, 权重档) 批量提交，
    调用次数降到 组数×档数，几秒钟完成。
    """
    groups = mesh_obj.vertex_groups
    for i, g in enumerate(groups):
        col = W[:, i]
        idx = np.nonzero(col > eps)[0]
        if len(idx) == 0:
            continue
        q = np.clip(np.round(col[idx] * bins).astype(np.int64), 1, bins)
        for b in np.unique(q):
            sel = idx[q == b]
            g.add([int(v) for v in sel], float(b) / bins, "REPLACE")
    return W
