"""通用自动蒙皮：不依赖角色是什么，只依赖「骨架几何 + 网格」。

流程（每一步都是踩过的坑换来的）

1. 直接对原网格算骨骼热权重
2. 失败 → 体素代理体搜索：**从细到粗**（细的保细节，粗的能成功；两者都试）
3. 代理上算权重 → DataTransfer 转回原网格
4. 肢体整形：距离封顶（去掉拽体表的冗余影响）+ 沿骨骼链弧长重分配
5. 拉普拉斯平滑权重（消除断层折痕）
6. 报告每根骨的最大权重与覆盖顶点数，检查有没有"没有顶点听它的"死骨
"""

import math

import bmesh
import bpy
import numpy as np

def _view_layer():
    """wm.open_mainfile 之后 bpy.context.window 可能是 None，按可用性逐级回退"""
    win = getattr(bpy.context, "window", None)
    if win is not None and getattr(win, "view_layer", None) is not None:
        return win.view_layer
    vl = getattr(bpy.context, "view_layer", None)
    if vl is not None:
        return vl
    return bpy.context.scene.view_layers[0]



# --------------------------------------------------------------------- 工具
def _vl():
    return _view_layer()


def _sel_only(*objs, active=None):
    bpy.ops.object.select_all(action="DESELECT")
    for o in objs:
        o.select_set(True)
    _vl().objects.active = active or (objs[0] if objs else None)


def _clear_weights(mesh_obj):
    for m in list(mesh_obj.modifiers):
        if m.type == "ARMATURE":
            mesh_obj.modifiers.remove(m)
    mesh_obj.parent = None
    for g in list(mesh_obj.vertex_groups):
        mesh_obj.vertex_groups.remove(g)


def _ensure_groups(mesh_obj, rig):
    for b in rig.data.bones:
        if b.use_deform and b.name not in mesh_obj.vertex_groups:
            mesh_obj.vertex_groups.new(name=b.name)


def _weight_matrix(mesh_obj):
    nv = len(mesh_obj.data.vertices)
    ng = len(mesh_obj.vertex_groups)
    W = np.zeros((nv, ng), dtype=np.float32)
    for v in mesh_obj.data.vertices:
        for g in v.groups:
            W[v.index, g.group] = g.weight
    return W


def _write_weights(mesh_obj, W):
    me = mesh_obj.data
    bm = bmesh.new()
    bm.from_mesh(me)
    bm.verts.ensure_lookup_table()
    dl = bm.verts.layers.deform.verify()
    for i, v in enumerate(bm.verts):
        dv = v[dl]
        dv.clear()
        row = W[i]
        for j in np.nonzero(row)[0]:
            dv[int(j)] = float(row[j])
    bm.to_mesh(me)
    bm.free()
    me.update()


def _zero_count(obj):
    return sum(1 for v in obj.data.vertices if sum(g.weight for g in v.groups) < 1e-6)


def _renorm(W):
    s = W.sum(axis=1, keepdims=True)
    return W / np.maximum(s, 1e-9)


# --------------------------------------------------------------------- 热权重
def _heat(mesh_obj, rig):
    _sel_only(mesh_obj, rig, active=rig)
    try:
        bpy.ops.object.parent_set(type="ARMATURE_AUTO")
    except Exception:
        return len(mesh_obj.data.vertices)
    return _zero_count(mesh_obj)


def _make_proxy(mesh_obj, voxel):
    sc = bpy.context.scene
    proxy = mesh_obj.copy()
    proxy.data = mesh_obj.data.copy()
    proxy.name = mesh_obj.name + "_proxy"
    sc.collection.objects.link(proxy)
    proxy.parent = None
    proxy.matrix_world = mesh_obj.matrix_world.copy()
    for m in list(proxy.modifiers):
        proxy.modifiers.remove(m)
    for g in list(proxy.vertex_groups):
        proxy.vertex_groups.remove(g)

    _sel_only(proxy, active=proxy)
    rm = proxy.modifiers.new("Remesh", "REMESH")
    rm.mode = "VOXEL"
    rm.voxel_size = voxel
    rm.use_smooth_shade = True
    bpy.ops.object.modifier_apply(modifier=rm.name)
    sm = proxy.modifiers.new("Smooth", "SMOOTH")
    sm.factor = 0.5
    sm.iterations = 3
    bpy.ops.object.modifier_apply(modifier=sm.name)
    return proxy


def _proxy_quality(proxy, temp_bones=1):
    """热权重"成功"不等于"合理"：一根骨吃掉大半张网就是废结果"""
    W = _weight_matrix(proxy)
    if W.shape[1] == 0:
        return False, "无顶点组"
    if float(W.sum()) < 1e-6:
        return False, "全 0 权重"
    dom = W.argmax(axis=1)
    share = np.bincount(dom, minlength=W.shape[1]) / max(len(dom), 1)
    j = int(share.argmax())
    top = float(share[j])
    name = proxy.vertex_groups[j].name if j < len(proxy.vertex_groups) else "?"
    dead = int((W.max(axis=0) < 0.15).sum())
    if top > 0.45:
        return False, "单骨 %s 占 %.0f%% 顶点" % (name, top * 100)
    if dead > max(2, temp_bones * 0.3):
        return False, "%d 根骨无影响" % dead
    return True, "ok"


def _transfer(src, from_obj, to_obj_had_armature=False):
    """把 from_obj 的权重复制到 src（同名顶点组）"""
    _sel_only(src, active=src)
    dt = src.modifiers.new("WTransfer", "DATA_TRANSFER")
    dt.object = from_obj
    dt.use_object_transform = False
    dt.use_vert_data = True
    dt.data_types_verts = {"VGROUP_WEIGHTS"}
    dt.vert_mapping = "POLYINTERP_NEAREST"
    dt.layers_vgroup_select_src = "ALL"
    dt.layers_vgroup_select_dst = "NAME"
    dt.mix_mode = "REPLACE"
    dt.mix_factor = 1.0
    bpy.ops.object.modifier_move_to_index(modifier=dt.name, index=0)
    bpy.ops.object.modifier_apply(modifier=dt.name)


# --------------------------------------------------------------------- 肢体整形
def _polyline(spec, chain):
    segs = []
    for name in chain:
        b = spec["bones"]
        head = spec["bone_head"][name]
        tail = next(t for n, h, t, p, d in b if n == name)
        segs.append((np.array(head, dtype=float), np.array(tail, dtype=float)))
    return segs


def _closest(co, segs):
    """返回 (最小距离, 沿链归一化弧长)"""
    lens = [float(np.linalg.norm(b - a)) for a, b in segs]
    total = sum(lens) or 1.0
    cum = [0.0]
    for l in lens:
        cum.append(cum[-1] + l)
    best_d = np.full(len(co), 1e9)
    best_s = np.zeros(len(co))
    for j, (a, b) in enumerate(segs):
        ab = b - a
        L2 = float(ab @ ab)
        if L2 < 1e-12:
            d = np.linalg.norm(co - a, axis=1)
            t = np.zeros(len(co))
        else:
            t = np.clip((co - a) @ ab / L2, 0.0, 1.0)
            d = np.linalg.norm(co - (a + t[:, None] * ab[None, :]), axis=1)
        s = (cum[j] + t * lens[j]) / total
        mask = d < best_d
        best_d[mask] = d[mask]
        best_s[mask] = s[mask]
    return best_d, best_s


def shape_limb(mesh_obj, rig, spec, chain, co, W, gidx, log=print):
    """把一条肢体链的权重重塑：距离封顶 + 弧长重分配。

    返回被处理的顶点数。
    """
    if not chain or len(chain) < 3:
        return 0
    names = [g.name for g in mesh_obj.vertex_groups]
    idx = [gidx[n] for n in chain if n in gidx]
    if len(idx) < 2:
        return 0
    segs = _polyline(spec, chain)
    total = sum(float(np.linalg.norm(b - a)) for a, b in segs)
    d, s = _closest(co, segs)

    caps = []
    cum_l = np.cumsum([0.0] + [float(np.linalg.norm(b - a)) for a, b in segs])
    for k in range(len(segs)):
        caps.append((cum_l[k] + cum_l[k + 1]) / 2.0 / max(total, 1e-9))
    caps = np.array(caps)

    cap_dist = 0.55 * total           # 超过这个距离就完全不算这条肢体
    soft = 0.18 * total               # 封顶的过渡宽度

    body_idx = [j for j, n in enumerate(names) if n not in chain]
    moved = 0
    for i in range(len(co)):
        tot_limb = W[i, idx].sum()
        if tot_limb < 0.02:
            continue
        gate = 1.0 if d[i] <= cap_dist else max(0.0, 1.0 - (d[i] - cap_dist) / soft)
        if gate <= 0.0:
            new_tot = 0.0
        else:
            new_tot = tot_limb * gate
        # 弧长分布
        wv = np.exp(-((s[i] - caps) / 0.16) ** 2)
        if wv.sum() < 1e-9:
            wv = np.ones(len(idx)) / len(idx)
        else:
            wv = wv / wv.sum()
        W[i, idx] = 0.0
        W[i, idx] = wv * new_tot
        # 被削掉的部分并回最近的躯干骨（取原有的最大非肢体权重）
        lost = tot_limb - new_tot
        if lost > 1e-6 and body_idx:
            j = max(body_idx, key=lambda k: W[i, k])
            W[i, j] += lost
        moved += 1
    return moved


def unify_coincident(mesh_obj, W, quant=None):
    """把位置重合的顶点权重取平均。

    AI 生成的网格常有大量重叠面片（伪毛发薄片、双层面）。它们各自被映射到
    代理的不同面，拿到不同权重，一摆姿势就"散开成几股"。按位置统一即可。
    """
    co = np.empty(len(mesh_obj.data.vertices) * 3, dtype=np.float64)
    mesh_obj.data.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    if quant is None:
        span = float(co[:, 2].max() - co[:, 2].min()) or 1.0
        quant = span * 0.012   # ~3mm/1.7m 身高；太细（1e-4）合并不了相邻的重复薄片
    key = np.round(co / quant).astype(np.int64)
    _, inv = np.unique(key, axis=0, return_inverse=True)
    inv = inv.ravel()
    m = int(inv.max()) + 1
    cnt = np.bincount(inv, minlength=m).astype(np.float64)
    acc = np.zeros((m, W.shape[1]), dtype=np.float64)
    np.add.at(acc, inv, W)
    mean = acc / np.maximum(cnt, 1.0)[:, None]
    return _renorm(mean[inv])


def smooth_weights(mesh_obj, W, iters=2, alpha=0.5, prune=0.004):
    me = mesh_obj.data
    nv = len(me.vertices)
    ev = np.empty(len(me.edges) * 2, dtype=np.int32)
    me.edges.foreach_get("vertices", ev)
    ev = ev.reshape(-1, 2)
    deg = np.maximum(np.bincount(ev.ravel(), minlength=nv).astype(np.float64), 1.0)[:, None]
    for _ in range(iters):
        acc = np.zeros_like(W)
        np.add.at(acc, ev[:, 0], W[ev[:, 1]])
        np.add.at(acc, ev[:, 1], W[ev[:, 0]])
        W = (1.0 - alpha) * W + alpha * (acc / deg)
        W = _renorm(W)
    if prune > 0:
        W[W < prune] = 0.0
        W = _renorm(W)
    return W




# --------------------------------------------------------------------- 解析权重
def _chain_segs(spec, chain):
    segs, lens = [], []
    for name in chain:
        a = np.array(spec["bone_head"][name], dtype=float)
        b = np.array(next(t for n, h, t, p, d in spec["bones"] if n == name), dtype=float)
        segs.append((a, b))
        lens.append(float(np.linalg.norm(b - a)))
    return segs, lens


def analytic_weights(mesh_obj, rig, spec, log=print):
    """完全由骨架几何决定的权重：不依赖网格是否流形、是否碎片化。

    每个顶点先分配给「够近」的肢体链（沿骨骼链弧长分布），
    剩下的份额按到躯干各骨的距离做软分配。
    """
    # 顶点组可能还是空的（刚挂上骨架），先按骨骼建齐
    for b in spec["bones"]:
        if b[4] and b[0] not in mesh_obj.vertex_groups:
            mesh_obj.vertex_groups.new(name=b[0])

    co = np.empty(len(mesh_obj.data.vertices) * 3, dtype=np.float64)
    mesh_obj.data.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    nv = len(co)
    names = [g.name for g in mesh_obj.vertex_groups]
    gidx = {n: i for i, n in enumerate(names)}
    H = spec.get("height") or (co[:, 2].max() - co[:, 2].min())

    chains = {}
    for side, ch in (spec.get("leg_chains") or {}).items():
        chains["leg_" + side] = ch
    for side, ch in (spec.get("arm_chains") or {}).items():
        chains["arm_" + side] = ch
    if spec.get("tail"):
        chains["tail"] = spec["tail"]
    extras = [b for b in spec["key_bones"]
              if b.split(".")[0].lower() in ("ear", "wing", "horn", "antenna")]
    for b in extras:
        chains["ex_" + b] = [b]

    in_chain = set()
    for ch in chains.values():
        in_chain |= set(ch)
    body = [b for b in spec["key_bones"] if b not in in_chain]

    # 肢体：距离门限 + 弧长分布
    limb_fac = {}
    limb_dist = {}
    for key, ch in chains.items():
        if not ch:
            continue
        segs, lens = _chain_segs(spec, ch)
        total = sum(lens) or 1.0
        d, s = _closest(co, segs)
        if key.startswith("ex_"):
            cap = 0.10 * H
        else:
            cap = 0.55 * total
        soft = max(cap * 0.6, 1e-6)
        g = np.clip(1.0 - (d - cap * 0.45) / soft, 0.0, 1.0)
        g = g * g * (3 - 2 * g)

        centres = np.cumsum([0.0] + lens)[:-1] + np.array(lens) / 2.0
        centres = centres / total
        wv = np.exp(-((s[:, None] - centres[None, :]) / 0.18) ** 2)
        wv = wv / np.maximum(wv.sum(axis=1, keepdims=True), 1e-9)
        limb_fac[key] = g
        limb_dist[key] = wv
        log("    链 %-8s 长度 %.4f cap %.4f 覆盖(>0.5) %d 顶点"
            % (key, total, cap, int((g > 0.5).sum())))

    # 躯干：距离软分配
    Temp = 0.10 * H
    bw = np.zeros((nv, len(body)), dtype=np.float64)
    for j, name in enumerate(body):
        a = np.array(spec["bone_head"][name], dtype=float)
        b = np.array(next(t for n, h, t, p, d in spec["bones"] if n == name), dtype=float)
        ab = b - a
        L2 = float(ab @ ab)
        t = np.clip((co - a) @ ab / L2, 0.0, 1.0) if L2 > 1e-12 else np.zeros(nv)
        dd = np.linalg.norm(co - (a + t[:, None] * ab[None, :]), axis=1)
        bw[:, j] = np.exp(-((dd / Temp) ** 2))
    bw = bw / np.maximum(bw.sum(axis=1, keepdims=True), 1e-9)

    # 合成：先按肢体优先级（腿 > 臂 > 尾 > 附肢）分配，剩余给躯干
    order = [k for k in ("leg_L", "leg_R", "arm_L", "arm_R", "tail") if k in chains]
    order += [k for k in chains if k.startswith("ex_")]
    W = np.zeros((nv, len(names)), dtype=np.float64)
    remaining = np.ones(nv, dtype=np.float64)
    for key in order:
        g = limb_fac[key] * remaining
        ch = chains[key]
        for j, name in enumerate(ch):
            if name in gidx:
                W[:, gidx[name]] += g * limb_dist[key][:, j]
        remaining -= g
        remaining = np.clip(remaining, 0.0, 1.0)

    for j, name in enumerate(body):
        if name in gidx:
            W[:, gidx[name]] += remaining * bw[:, j]

    W = _renorm(W)
    _write_weights(mesh_obj, W)
    log("[weights] 解析权重完成: %d 顶点点, 肢体链 %s, 躯干骨 %d 根"
        % (nv, list(chains.keys()), len(body)))
    return W


def validate_weights(mesh_obj, spec, limb_min=0.30):
    """检查是否出现「某根肢体骨完全没权重」这类废结果"""
    W = _weight_matrix(mesh_obj)
    names = [g.name for g in mesh_obj.vertex_groups]
    gidx = {n: i for i, n in enumerate(names)}
    if W.size == 0 or len(names) == 0:
        return ["权重矩阵为空（顶点组未建立）"]
    problems = []
    for side, ch in (spec.get("leg_chains") or {}).items():
        for b in ch:
            if b in gidx and (W[:, gidx[b]] > limb_min).sum() < 20:
                problems.append(b)
    for side, ch in (spec.get("arm_chains") or {}).items():
        for b in ch:
            if b in gidx and (W[:, gidx[b]] > limb_min).sum() < 20:
                problems.append(b)
    dom = W.argmax(axis=1)
    share = np.bincount(dom, minlength=len(names)) / max(len(dom), 1)
    if float(share.max()) > 0.5:
        problems.append("单骨占 %.0f%%" % (share.max() * 100))
    return problems


# --------------------------------------------------------------------- 主入口
def auto_weights(mesh_obj, rig, spec, log=print, smooth_iters=2, verbose=True,
                 method="envelope"):
    """完整自动蒙皮。返回统计字典。

    method="envelope"  默认。最近骨骼 + 距离衰减，与网格拓扑无关，任意体型都稳。
    method="heat"      骨骼热权重（网格质量好时更精细，AI 网格上常整体失败）。
    """
    H = spec.get("height") or _estimate_height(mesh_obj)

    if method == "envelope":
        try:
            from . import envelope as _env
        except ImportError:
            import envelope as _env
        _clear_weights(mesh_obj)
        _ensure_groups(mesh_obj, rig)
        W = _env.envelope_weights(mesh_obj, rig, spec, log=log, smooth_iters=smooth_iters)
        _attach(mesh_obj, rig)
        names = [g.name for g in mesh_obj.vertex_groups]
        dead, coverage = [], {}
        for j, nm in enumerate(names):
            col = W[:, j]
            coverage[nm] = (float(col.max()) if len(col) else 0.0, int((col > 0.5).sum()))
            if coverage[nm][0] < 0.15:
                dead.append(nm)
        zero2 = _zero_count(mesh_obj)
        bad = validate_weights(mesh_obj, spec)
        if verbose:
            log("[weights] 完成: 方法=envelope 漏权=%d 无权重骨=%s 校验=%s"
                % (zero2, dead or "无", bad or "OK"))
        return {"method": "envelope", "unweighted": zero2, "dead_bones": dead,
                "coverage": coverage, "stumpy_leg": False, "stumpy_arm": False,
                "voxel": None, "validate": bad}

    _clear_weights(mesh_obj)
    _ensure_groups(mesh_obj, rig)

    # ---- 1) 直接热权重
    zero = _heat(mesh_obj, rig)
    nv = len(mesh_obj.data.vertices)
    method = "direct_heat"
    log("[weights] 直接热权重: 漏权 %d / %d" % (zero, nv))

    if zero > nv * 0.5:
        _clear_weights(mesh_obj)
        _ensure_groups(mesh_obj, rig)

        # ---- 2) 代理体搜索（从细到粗：细的保细节，粗的能成功）
        ratios = [1 / 250.0, 1 / 180.0, 1 / 130.0, 1 / 90.0, 1 / 60.0, 1 / 40.0]
        proxy = None
        used_voxel = None
        for r in ratios:
            vox = H * r
            if proxy:
                bpy.data.objects.remove(proxy, do_unlink=True)
            proxy = _make_proxy(mesh_obj, vox)
            npx = len(proxy.data.vertices)

            # 用对象缩放（不烘焙）把两边一起放大到 1m 高，规避小网格的数值问题。
            # 注意：一旦 transform_apply 烘焙进数据，后面就还原不回去了。
            scale = 1.0 / H
            proxy.scale = (scale, scale, scale)

            temp = rig.copy()
            temp.data = rig.data.copy()
            temp.name = "TempRig"
            bpy.context.scene.collection.objects.link(temp)
            temp.parent = None
            temp.location = (0, 0, 0)
            temp.rotation_euler = (0, 0, 0)
            temp.scale = (scale, scale, scale)

            z0 = _heat(proxy, temp)
            log("[weights] 代理 voxel=%.4f (H/%.0f, %d 顶点): 漏权 %d"
                % (vox, 1 / r, npx, z0))
            bpy.data.objects.remove(temp, do_unlink=True)

            ok, why = _proxy_quality(proxy, temp_bones=len([b for b in rig.data.bones if b.use_deform]))
            if z0 < npx * 0.05 and ok:
                used_voxel = vox
                break
            L_ = log
            L_("[weights]   该体素尺寸被否决: %s" % why)
            bpy.data.objects.remove(proxy, do_unlink=True)
            proxy = None

        method = "proxy_heat"
        if proxy is None:
            log("[weights] 所有体素尺寸都失败，退回距离权重")
            _clear_weights(mesh_obj)
            _ensure_groups(mesh_obj, rig)
            _distance_fallback(mesh_obj, rig, spec)
        else:
            # 还原对象缩放（数据从未被烘焙，直接改 scale 即可）
            proxy.scale = (1, 1, 1)
            proxy.parent = None
            bpy.context.view_layer.update()

            for b in rig.data.bones:
                if b.use_deform and b.name not in mesh_obj.vertex_groups:
                    mesh_obj.vertex_groups.new(name=b.name)
            _attach(mesh_obj, rig)
            _transfer(mesh_obj, proxy)
            bpy.data.objects.remove(proxy, do_unlink=True)

    if not any(m.type == "ARMATURE" for m in mesh_obj.modifiers):
        _attach(mesh_obj, rig)

    # ---- 3) 肢体整形
    co = np.empty(len(mesh_obj.data.vertices) * 3, dtype=np.float64)
    mesh_obj.data.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    W = _weight_matrix(mesh_obj)
    n_before = len(W)
    W = unify_coincident(mesh_obj, W)
    gidx = {g.name: g.index for g in mesh_obj.vertex_groups}

    leg_len = sum(spec["bone_len"].get(b, 0) for b in spec["leg_chain"])
    arm_len = sum(spec["bone_len"].get(b, 0) for b in spec["arm_chain"])
    stumpy_leg = 0 < leg_len < 0.35 * H
    stumpy_arm = 0 < arm_len < 0.28 * H

    if stumpy_leg:
        for side, chain in (spec.get("leg_chains") or {}).items():
            n = shape_limb(mesh_obj, rig, spec, chain, co, W, gidx, log)
            log("[weights] 腿短（%.1f%%H）→ %s 侧距离封顶+弧长重分配 %d 顶点"
                % (leg_len / H * 100, side, n))
    if stumpy_arm:
        for side, chain in (spec.get("arm_chains") or {}).items():
            n = shape_limb(mesh_obj, rig, spec, chain, co, W, gidx, log)
            log("[weights] 臂短（%.1f%%H）→ %s 侧整形 %d 顶点" % (arm_len / H * 100, side, n))

    W = _renorm(W)
    W = smooth_weights(mesh_obj, W, iters=smooth_iters)
    _write_weights(mesh_obj, W)

    # 校验：肢体骨必须有足够的影响顶点，否则换解析权重重做
    bad = validate_weights(mesh_obj, spec)
    if bad:
        log("[weights] 热权重结果不合格（%s）→ 改用解析权重" % ", ".join(bad[:4]))
        analytic_weights(mesh_obj, rig, spec, log=log)
        W = _weight_matrix(mesh_obj)
        W = unify_coincident(mesh_obj, W)
        W = smooth_weights(mesh_obj, W, iters=smooth_iters)
        _write_weights(mesh_obj, W)
        method = "analytic"

    # ---- 4) 检查死骨
    W2 = _weight_matrix(mesh_obj)
    names = [g.name for g in mesh_obj.vertex_groups]
    dead = []
    coverage = {}
    for j, nm in enumerate(names):
        col = W2[:, j]
        mx = float(col.max()) if len(col) else 0.0
        n051 = int((col > 0.5).sum())
        coverage[nm] = (mx, n051)
        if mx < 0.15:
            dead.append(nm)

    zero2 = _zero_count(mesh_obj)
    stats = {
        "method": method,
        "unweighted": zero2,
        "dead_bones": dead,
        "coverage": coverage,
        "stumpy_leg": stumpy_leg,
        "stumpy_arm": stumpy_arm,
        "voxel": used_voxel,
    }
    if verbose:
        log("[weights] 完成: 方法=%s 漏权=%d 死骨=%s" % (method, zero2, dead or "无"))
        for nm in sorted(coverage, key=lambda k: -coverage[k][0])[:6]:
            log("    %-14s max=%.2f  n(>0.5)=%d" % (nm, coverage[nm][0], coverage[nm][1]))
    return stats


def _attach(mesh_obj, rig):
    mesh_obj.parent = rig
    mesh_obj.matrix_parent_inverse = rig.matrix_world.inverted()
    mod = mesh_obj.modifiers.new("Armature", "ARMATURE")
    mod.object = rig


def _estimate_height(mesh_obj):
    co = np.array([(mesh_obj.matrix_world @ v.co)[:] for v in mesh_obj.data.vertices])
    return float(co[:, 2].max() - co[:, 2].min())


def _distance_fallback(mesh_obj, rig, spec):
    """最后的兜底：按到骨骼线段的距离反比给权重（质量一般，但不会全 0）"""
    co = np.empty(len(mesh_obj.data.vertices) * 3, dtype=np.float64)
    mesh_obj.data.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    bones = [(b.name, np.array(b.head_local), np.array(b.tail_local))
             for b in rig.data.bones if b.use_deform]
    W = np.zeros((len(co), len(mesh_obj.vertex_groups)), dtype=np.float32)
    gidx = {g.name: g.index for g in mesh_obj.vertex_groups}
    for i, p in enumerate(co):
        ws = []
        for name, a, b in bones:
            ab = b - a
            L2 = float(ab @ ab)
            t = 0.0 if L2 < 1e-12 else float(np.clip((p - a) @ ab / L2, 0, 1))
            d = float(np.linalg.norm(p - (a + t * ab)))
            ws.append((name, 1.0 / (d ** 4 + 1e-8)))
        ws.sort(key=lambda kv: -kv[1])
        top = ws[:4]
        s = sum(w for _, w in top)
        for name, w in top:
            W[i, gidx[name]] = w / s
    _write_weights(mesh_obj, W)
