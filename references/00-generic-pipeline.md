# 00 · 通用化：任意体型怎么跑通

这一篇解释「为什么不需要预设头身比」。所有数字都来自两个真实案例的实测：
一只 0.28 m 的直立仓鼠，和一个 1.68 m 的赛博朋克少女。

## 一、测量决定一切（`measure.measure`）

```python
m = measure.measure(mesh_obj, forward=None)   # forward 可强制 "+Y"/"-Y"
print(measure.report(m, arch, why))
```

输出的关键字段：

| 字段 | 含义 | 用途 |
|---|---|---|
| `height` | 世界 Z 跨度 | 所有比例归一化的基准 |
| `axis_x` | 对称轴 X（用地面接触块中心，比包围盒中心稳） | 左右分侧 |
| `forward_sign` / `forward_detect` | 前方是 −Y 还是 +Y，以及用什么判出来的 | 骨骼朝向、步态方向 |
| `crotch` | 裆高（占身高比例） | 人形 vs 矮胖 |
| `ground_patches` | 地面接触块（中心/点数）/ `major_contacts` | 两足 vs 四足 |
| `belly_clearance_abs` | 腹部离地间隙 | 能否压低髋 |
| `profile` | 20 层宽度/深度剖面 | 躯干与头部分段 |

### 朝向判定（踩过的坑）

最初用「头部重心相对身体前后偏移」判前方，结果赛博朋克少女判反了：
她的长发往后伸，头的心反而偏后。判反的后果是整副骨架被放到身体背后，
蒙皮时尾巴骨钻进了肚子，`tail.01` 吃掉 67% 权重。

正确信号是**脚**：脚掌/爪/蹄一定朝前伸，脚掌接触块的重心相对髋部所在高度的重心明显偏前。

```python
d_feet = feet_cy - hip_cy            # 髋部高度带 42%~58%H 的 Y 重心
forward_sign = -1 if d_feet < 0 else 1
# 差异小于 0.8%H 时才退回头部判据
```

实测：仓鼠 `d_feet` 明显（脚在腹前），少女 `d_feet` 也明显（鞋头在前），两者都稳定。

### 裆高判定（踩过的坑）

最初「从下往上找中线附近第一个有实体的高度」。遇到 AI 网格里垂下来的挂饰/飘带
就在很低的位置命中，裆高测成 0 —— 于是人形被判成矮胖体型，髋高被压到 0.31 m。

改成**横截面是否真的分成左右两簇**，从上往下扫，第一次分裂即裆高：

```python
for k in range(55, 4, -1):        # 55%H 往下
    band = 该高度 ±2%H 的顶点
    if 左簇 > 25 且 右簇 > 25 且 中线簇 < 35%(左+右):
        return 该高度              # 第一次分裂 = 裆高
```

实测：仓鼠无分裂（肚子贴地）→ 0%H → `biped_small`；少女 → 55%H → `humanoid`。

### 腹部离地

中线列的最低点，但必须**排除侧向接触块**（那是鞋/爪，不是肚子）。
不排除的话人形会得到 `belly_clearance=0`，`feasibility` 误判成"不能压低髋"。

## 二、体型分类（`measure.guess_archetype`）

```
接触块 ≥3 且左右都有 且 前后跨度 > 25%H   → quadruped   四足
裆高 ≥ 34%H                              → humanoid    标准两足
其余                                      → biped_small 矮胖两足（含贴地体型）
```

**不要用"接触块数量"单独判断**：脚跟和脚尖会各自成块，一个正常人也能测出 4 块；
四足也可能只有 3 块。前后跨度才是决定性信号 —— 四足的前爪与后爪离得远。

## 三、骨架模板（`archetypes`）

模板是**归一化坐标**（0~1 × 身高），`build_spec` 按实测数据映射：

```python
spec = archetypes.build_spec(m, arch)
spec["height"] = m["height"]
```

映射时注意三点：

1. **Y 轴符号**：模板里 y 负 = 前方。世界朝 −Y 时 `ysign = forward_sign = -1`，
   直接把模板的负 y 映射到世界负 y。写反了整个骨架会落到身体背后。
2. **髋高基准**：以模板 `hips` 骨的 head 为准，不要用裆高 —— 贴地体型的裆高不可靠。
3. **手臂只做上限钳制**：手臂不能长到包围盒外面，但不按包围盒硬缩放（模板比例本来就合理）。

`spec` 里带的东西：

```python
spec["bones"]       # [[name, head, tail, parent, deform], ...]
spec["bone_head"]   # name -> head
spec["bone_len"]    # name -> 长度
spec["key_bones"]   # 有实际意义的骨（排除 root 等非变形骨）
spec["leg_chain"]   # 单侧腿链 ['thigh.L','shin.L','foot.L']
spec["leg_chains"]  # {'L': [...], 'R': [...]}（含 toe）
spec["arm_chains"]  # 同上
spec["tail"]        # 尾巴链（若有）
```

`sided()` 展开左右链时，**toe 只能并进腿链**。写错会把上臂链变成
`[shoulder.L, upper_arm.L, forearm.L, hand.L, toe.L]` —— 脚趾骨分到手臂权重。

## 四、包络权重（`envelope.envelope_weights`）

```python
W = envelope.envelope_weights(mesh_obj, rig, spec, power=3.2, max_bones=4,
                              side_gate=True, smooth_iters=2)
```

算法：

1. 逐骨算「顶点到骨骼线段」的距离矩阵 `D`（n×b）。
2. **左右分侧**：`x - axis_x` 超过 ±1.2%H 的顶点不参与对侧肢体（防左腿骨拽右腿）。
3. 只保留 `d ≤ dmin×1.9 + 1.2%H` 且最近的 4 根骨。
4. 权重 `∝ 1/d^3.2`（`power` 越大越"硬"，关节越清晰但过渡越生硬）。
5. 沿网格边做 2 轮拉普拉斯平滑（权重层面，与骨长/网格密度无关）。
6. 每顶点只留前 4 个权重，量化到 1/200 后批量写入顶点组
   （`group.add(索引列表, 权重)`，逐点写 20 万顶点要几分钟）。

### 为什么不用骨骼热权重当默认

实测记录（同一套 AI 网格）：

| 方案 | 结果 |
|---|---|
| 原网格直接热权重 | 149,978 顶点**全 0** |
| 代理体 0.0015 / 0.004 / 0.003 / 0.002 | 全 0 |
| 代理体 0.008（人形 1.68 m） | 成功 |
| 代理体 0.006（仓鼠 0.28 m） | 成功 |
| 代理体算完，单骨占比 | `tail.01` 吃掉 67% 顶点 |

规律反直觉：**体素越细越失败** —— 细体素会把 AI 生成的伪毛发薄片原样保留，
重新制造破坏热权重的退化几何。报错提示"缩放到合适大小"是误导，放大 6 倍照样失败。

包络法与拓扑无关，在这两个案例上都是 0 漏权、22 根骨全部生效、校验通过。

## 五、必须烘焙网格变换（`rig_build.bake_transform`）

AI 生成的模型常带 **1.44 倍缩放 + −90° X 旋转**。骨骼坐标是按世界坐标算的，
而 Armature 修改器在**网格局部空间**套用骨骼变换，两者错位 →
绑定后模型缩水到 0.23 m、姿势完全不对。

```python
rig_build.attach_mesh(mesh_obj, rig)   # 内部先 bake_transform，再挂 modifier
```

判定：`matrix_world` 与单位阵差异 < 1e-6 时跳过（幂等）。
行列式为负（镜像）时额外翻转法线。

## 六、`bpy.ops` 的上下文坑（`ctx.py`）

`bpy.ops.wm.open_mainfile()` 之后 `bpy.context.window` 变成 `None`，
所有需要上下文的算子都报 `poll() 上下文缺失活动物体`。

```python
with ctx.ov(active=obj):          # 有 window 时透明，没有时用 temp_override
    bpy.ops.render.render(write_still=True)
rig_build.attach_mesh(...)        # 内部走 ctx.enter_edit / ctx.leave_edit
```

## 七、两个案例的对照

| | 仓鼠 | 赛博朋克少女 |
|---|---|---|
| 身高 | 0.280 m | 1.680 m |
| 拓扑 | 单壳体、149,978 顶点、非流形边 0 | 2,456 壳体、188,019 顶点 |
| 分类 | `biped_small`（裆高 0%H） | `humanoid`（裆高 55%H） |
| 朝向 | −Y（脚判据） | −Y（脚判据，头部判据会判反） |
| 骨骼 | 28 骨 / 27 变形 | 23 骨 / 22 变形 |
| 权重 | 包络，0 漏权 | 包络，0 漏权，22 骨全生效 |
| 可行性 | `leg_extend=0.994` → 原地踏步 | `leg_extend=1.000` → 模板需加屈膝 |
| 摆姿势位移 | 84% 顶点移动，无撕裂 | 84% 顶点移动 |

同一套 API，没有一行按体型写死的参数。
