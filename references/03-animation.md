# 阶段三：动画

## 五个根因，覆盖 90% 的"动作不对"

所有"抽搐 / 顺拐 / 脚飘 / 不丝滑"的问题，最后都归到这五件事：

| # | 根因 | 症状 |
|---|---|---|
| 1 | **action 已挂载时摆姿势** | 手动摆的姿势被当前帧关键帧冲掉，数值对不上 |
| 2 | **分段余弦插值** | 每个控制点导数归零 → 周期性微停顿，动作一顿一顿 |
| 3 | **坐标系用错** | 扭转写进侧倾轴 → 人歪着走；欧拉角摆臂 → 双臂挤在胸前 |
| 4 | **相位定义不匹配** | 手臂用 cos、腿用三角波 → 顺拐 |
| 5 | **IK 无 pole 或进入奇异区** | 膝盖/肘部单帧翻转 40-160° |

---

## 一、先确定朝向

不要假定 -Y 是前方。读脚骨：

```python
pb = rig.pose.bones["foot.L"]
d = (pb.tail - pb.head).normalized()
# 脚尖方向 = d 的水平投影
```

实测这个模型：脚 bone 从 (0.092, 0.012, 0.105) 指向 (0.094, -0.090, 0.030)，**水平分量指向 -Y**，所以角色朝 -Y，`y < 0` 是前方。

## 二、解析两骨 IK（不用 constraints）

```python
def solve_leg(side, ankle_target):
    H = rig.pose.bones[f"thigh.{side}"].head.copy()
    T = Vector(ankle_target)
    v = T - H
    d = v.length
    dmax = (L1 + L2) * 0.995          # 留 0.5% 余量，别锁死
    dmin = abs(L1 - L2) + 0.02
    if d > dmax: v = v.normalized() * dmax; T = H + v; d = dmax
    if d < dmin: v = v.normalized() * dmin; T = H + v; d = dmin
    u = v.normalized()

    # 余弦定理求髋角
    cos_a = (L1*L1 + d*d - L2*L2) / (2*L1*d)
    alpha = math.acos(max(-1.0, min(1.0, cos_a)))

    # 膝盖朝向由 pole 方向决定（这里固定朝前）
    axis = u.cross(Vector((0, -1, 0))).normalized()
    thigh_dir = (Quaternion(axis, alpha) @ u).normalized()
    K = H + thigh_dir * L1

    aim_bone(f"thigh.{side}", H, thigh_dir)
    aim_bone(f"shin.{side}", rig.pose.bones[f"shin.{side}"].head.copy(), (T - K).normalized())
    return K
```

**为什么不用 IK constraint**：两骨 IK 在没有 pole target 时，膝盖朝向由求解器自选。脚踝目标一旦跨过髋-踝连线平面，膝盖就翻转——实测单帧跳变 **46.5°（Walk）/ 56°（Run）/ 272mm 位移**。

**为什么不用约束**：导出 glTF 时约束要烘焙，多一层不确定性；而且约束在 action 层面看不见，排查困难。写死成 FK 旋转最干净。

### 手臂的奇异区

手臂用同样的解法，但 `dmin` 必须设大：

```python
dmin = 0.185   # 不是 |L1-L2|+0.03
```

因为手臂长只有 0.252/0.224，`d` 太小时前臂方向对肘位置极度敏感——实测 `d=0.11` 时会单帧翻转 **166°**。护面姿势（手靠近脸）尤其容易踩到，所以护手必须放在离肩 ≥22cm 处。

## 三、相位查表 + C1 连续插值（解决"不丝滑"）

### 错误做法

```python
def tbl_bad(table, phase):
    ...
    t = 0.5 - 0.5 * math.cos(math.pi * t)   # 每个控制点导数 = 0
    return v0 + (v1 - v0) * t
```

每个控制点速度归零。`RUN_SWING` 有 8 个控制点 → 摆动相脚出现 8 次微停顿。实测脚速：

```
62 62 62 62 61 61 61 61 62 53 | 6 36 24 51 60 23 52 84 60 17 57 ...
                               ↑ 支撑相匀速突然掉到 6，然后乱跳
```

### 正确做法

用 `scripts/gspline.py` 的 `smooth_table()`：**单调三次 Hermite（Fritsch-Carlson 限幅）**。

- C1 连续：控制点处速度连续
- 不过冲：单调段严格保单调（脚不会穿地、手臂不会甩过头）
- 局部极值处切线为 0（物理正确：抛体最高点速度本来就是 0）

修完同一段：

```
62 62 62 62 61 61 61 61 62 53 | 10 24 38 41 45 49 56 59 57 50 48 45 ...
                               ↑ 平滑钟形
```

**这一处改动同时修好了 Run 的流畅度和 Attack 的爆发曲线。**

## 四、先记录后写（解决"姿势被冲掉"）

**这是最容易踩、最难查的坑。**

action 挂载状态下，每次 `view_layer.update()` 都会用当前帧的关键帧重新求值，把你刚手摆的姿势覆盖掉。表现是：贴地校正全失效、数值对不上、首帧姿势错乱（还带上一支动作的残留）。

```python
# 摆姿势期间：action 必须是 None
rig.animation_data.action = None
for f in range(1, N + 1):
    reset_pose()
    ...  # 摆姿势、解 IK、贴地
    record(f)          # 只记录，不写关键帧

# 全部摆完之后再统一写
rig.animation_data.action = bpy.data.actions.new(name)
for f in sorted(REC):
    for bone, (loc, quat) in REC[f].items():
        pb.rotation_quaternion = quat
        pb.keyframe_insert("rotation_quaternion", frame=f)
```

## 五、四元数符号连续（解决 q/-q 翻转）

```python
prev_q = {}
for f in sorted(rec):
    q = Quaternion(quat)
    if bone in prev_q and q.dot(prev_q[bone]) < 0.0:
        q = Quaternion((-q.w, -q.x, -q.y, -q.z))   # 同符号
    prev_q[bone] = q
```

否则相邻两帧一个是 q 一个是 -q（数学上同一个旋转），插值时会走"另一条路"，视觉上猛转 180°。

## 六、骨骼局部轴映射（解决"歪着走"）

这个模型的骨骼：

| 轴 | 含义 |
|---|---|
| `rotation_euler[0]` (X) | 俯仰（前后倾） |
| `rotation_euler[1]` (Y) | **沿骨方向**（对竖直骨来说 = 世界 Z 的偏航/扭转） |
| `rotation_euler[2]` (Z) | **侧倾**（左右歪） |

把"躯干扭转"写进 index 2，人就会一直左右歪——看起来像跛脚。

## 七、交叉协调的相位（解决"顺拐"）

### 现象

左腿在前时左臂也在前。

### 诊断方法（用数据，不靠肉眼）

逐帧取左脚/左手的世界 Y，算相关系数：

```python
corr(foot_L_y, hand_L_y)   # 同侧应为负（反相）
corr(foot_L_y, hand_R_y)   # 对侧应为正（同向）
```

顺拐时同侧会变成正相关。

### 根因

腿和手臂的**波形形状不同**：

- 腿：支撑相线性 + 摆动相查表 ≈ 三角波，谷值在 ph=0.36
- 手臂：`cos`，谷值在 ph=0.54

差 0.18 周期。周期中段腿已经往前摆了，手臂还在往后走——这一小段就是同侧同向。

只调相位差没用，因为形状不同，对齐一个点会错开另一个点。

### 解法

把正弦的**谷值放到腿的谷值处**：

```python
def arm_wave(q, stance=0.36):
    """+1 = 最前, -1 = 最后。腿在 q=0 最前、q=stance 最后。"""
    q = q % 1.0
    return -math.cos(2.0 * math.pi * (q - stance))

# 右臂用左腿相位，左臂用右腿相位 → 交叉协调
leg_q = t + (0.5 if side == "L" else 0.0)
sw = arm_wave(leg_q - 0.03)   # 0.03 是手臂相对腿的自然滞后
```

三种方案的实测对比：

| 方案 | corr(同侧) | corr(对侧) | 单帧最大角跳变 |
|---|---|---|---|
| 原余弦（谷值 0.54） | −0.530 | +0.479 | — |
| 与腿同曲线 + 幂整形 | −0.936 | +0.947 | **56.2°**（幂函数零点导数发散） |
| **相位对齐正弦** | **−0.965** | **+0.979** | **15.9°** |

## 八、摆臂必须用世界方向（解决"双臂挤在胸前"）

```python
# ✗ 欧拉角：骨骼 roll 导致正反角度都把手带到身前
set_rot("upper_arm.L", -42 * cos, 0, 0)

# ✓ 世界方向
ua_ang = math.radians(UA_CENTER + UA_AMP * sw)
ua_dir = Vector((sg * 0.17, -math.sin(ua_ang), -math.cos(ua_ang)))
aim_bone("upper_arm.L", head, ua_dir)
```

`aim_bone` 只保证骨骼 Y 轴朝向，roll 保持不变：

```python
def aim_bone(name, head_world, direction):
    rest_dir = (REST3[name] @ Vector((0, 1, 0))).normalized()
    q = rest_dir.rotation_difference(Vector(direction).normalized())
    m = (q.to_matrix() @ REST3[name]).to_4x4()
    m.translation = head_world
    rig.pose.bones[name].matrix = m
```

### 摆臂参数（跑步参考值）

```python
UA_CENTER = -11.0    # 上臂摆动中心（略偏后，让后摆更明显）
UA_AMP    = 30.0     # 上臂摆幅 -> 前 19° / 后 -41°
FA_CENTER = 35.0     # 前臂绝对角中心
FA_AMP    = 72.0     # 前臂摆幅 -> -37° / +107°
```

结果：手的前后范围 0.615m（身前 41cm ~ 身后 20cm）。后摆时前臂角必须变成**负值**（朝后下），手才真的能甩到臀后。

### 手腕不要单独 aim

```python
# ✗ aim 的 roll 会抖（单帧 52-65°）
aim_bone("hand.R", head, dir)

# ✓ 继承前臂 + 绕局部 Y 固定扭转
rig.pose.bones["hand.R"].rotation_quaternion = \
    Quaternion((0.0, 1.0, 0.0), math.radians(-55 * tw_factor))
```

## 九、贴地

逐帧测网格最低点，**整体平移髋 + 双脚 IK 目标**（1:1 修正，不改变姿态）：

```python
def snap_to_ground(hips_pb, ctrl_feet, iters=4):
    base = list(hips_pb.location)
    zoff = 0.0
    for _ in range(iters):
        for s, pos in ctrl_feet.items():
            set_ctrl_world(f"ctrl_foot.{s}", (pos.x, pos.y, pos.z + zoff))
        hips_pb.location = (base[0], base[1] + zoff, base[2])
        bpy.context.view_layer.update()
        mz = min_z()
        if abs(mz) < 0.0012:
            break
        zoff -= mz * 0.95        # 阻尼 0.95，避免震荡
```

**脚掌世界朝向每帧强制回 rest**，否则会随小腿翻转导致脚尖入地：

```python
def set_foot_flat(side, pitch_deg=0.0):
    pb = rig.pose.bones[f"foot.{side}"]
    m = (Matrix.Rotation(math.radians(-pitch_deg), 3, "X") @ REST3[f"foot.{side}"]).to_4x4()
    m.translation = pb.head.copy()
    pb.matrix = m
```

## 十、跑步循环的分相设计

```
相位   0.00      0.36            1.00
      ├─ 支撑相 ─┼──── 摆动相 ────┤
       脚从前往后        脚从后往前
```

关键表（28 帧 / 0.93s / 步幅 0.62m / 支撑 36%）：

```python
RUN_HIPZ  = 骨盆高度，双峰双谷（支撑缓冲最低、腾空最高）
RUN_LIFT  = 抬脚高度，离地后先慢后快（避免一帧抬 90mm）
RUN_SWING = 摆动位移进度，前段慢（后踢）→ 中段快 → 末段减速准备落地
RUN_ANK   = 脚踝俯仰，支撑相中立、蹬地跖屈、摆动背屈
```

**双峰双谷必须左右严格对称**（0.5 相位偏移），否则会左右步态不一致。

腾空判定：`min_z > 5mm` 的帧就是腾空帧，跑步每周期应有 2 段（本模型 f13/14、f27/28）。

## 十一、Attack 的力量感

**力量感 ≠ 加大角度，是加大速度对比。**

### 速度曲线（腕部位移 mm/帧）

```
起势   30-40
蓄力   30 → 20 → 14      ← 几乎静止
爆发   42 → 113 → 153 → 112
命中   24 → 7            ← 骤停
收招   27 → 72 → 91 → 87 → 80 → 66 → 55 → 47 → 40 → 32 → 27
```

**动态对比 10.7 倍**（蓄力 14 vs 峰值 153），命中后掉到峰值的 4.5%。

### 三个关键设计

**① 躯干惯性分离** — 拳头峰值在 f19，骨盆扭转峰值刻意放在 f21（晚 2 帧）。拳头到位后躯干还在转，这是"甩"而不是"推"的来源。

**② 重心前后移动** — 蓄力后坐 22mm，出拳前压 −30mm（实测重心水平移动 52mm）。

局部坐标换算：

```python
world_off = Vector((0.0, hip_y, hip_z + 0.020))
hips_pb.location = REST3["hips"].inverted() @ world_off
```

**③ 局部偏移必须减去骨骼 head**：

```python
# ✗ 把世界绝对 Z 当成了相对偏移 → 手臂被甩到头顶上方 1.3m
chest.matrix @ Vector((0.132, -0.214, 1.346))

# ✓ 先算相对偏移，再转到骨骼局部
off = Vector((0.150, -0.205, 0.142))          # 相对 chest head
local = REST3["chest"].inverted() @ off
target = rig.pose.bones["chest"].matrix @ local
```

## 十二、验收（必须做，不能只跑脚本）

```bash
python scripts/verify.py            # 输出全部指标表
```

| 指标 | 合格线 | 不合格说明 |
|---|---|---|
| 单帧最大角跳变 | < 20° | 超过 30° 一定有翻转或坐标系错 |
| 地面 min_z | −3 ~ 0 mm | 负太多 = 穿地；正太多 = 浮空 |
| 支撑脚单帧位移 | 恒定 | 忽快忽慢 = 插值在控制点归零 |
| corr(同侧腿,臂) | < −0.9 | 变正 = 顺拐 |
| corr(对侧腿,臂) | > +0.9 | 变负 = 顺拐 |
| 循环接缝 | 与其他帧相当 | 明显偏大 = 首尾没对上 |
| root 通道 fcurve | 0 | 原地动画不该有根位移 |
| 前臂/手到躯干间隙 | > 0.12 m | 小于说明穿模 |

然后**逐帧渲染 → 合成 GIF → 实际看**。数据合格但视觉不对的情况是存在的（比如视角遮挡导致的误判，以及"数据对但姿势丑"）。

## 十三、导出 glTF

```python
bpy.ops.export_scene.gltf(
    filepath=out,
    export_format="GLB",
    export_animation_mode="ACTIONS",     # 每个 action 一段 clip
    export_bake_animation=True,          # 有约束时会自动烘焙
)
```

导出后检查 GLB 里的动画列表：

```python
import json, struct
with open(path, "rb") as f:
    f.read(12)
    clen, _ = struct.unpack("<II", f.read(8))
    g = json.loads(f.read(clen).decode())
print([a.get("name") for a in g.get("animations", [])])
```

所有 action 都要 `use_fake_user = True`，否则关闭工程后丢失。
