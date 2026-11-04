# 阶段二：骨架与蒙皮

## 结论先行

**不要用 Rigify。** 两个原因：

1. metarig 是 T-pose，而图生 3D 的模型是 A-pose，自动权重会错位
2. `armature_human_metarig_add` 在 `wm.open_mainfile` 之后会报 `'Context' object has no attribute 'active_object'`

**不要直接对原网格算骨骼热权重。** AI 生成的网格是几千个分离壳体（实测 2456 个），热权重会全部返回 0 权重——它需要水密流形才能算。

正路：**自建骨架 + 体素重构代理体算权重 + DataTransfer 转回**。

---

## 一、骨架结构（23 骨）

```
root
 └ hips
    ├ spine → chest → upper_chest → neck → head
    │         ├ shoulder.L → upper_arm.L → forearm.L → hand.L
    │         └ shoulder.R → upper_arm.R → forearm.R → hand.R
    ├ thigh.L → shin.L → foot.L → toe.L
    └ thigh.R → shin.R → foot.R → toe.R
```

外加 4 根**非变形**控制骨（父级是 root，`use_deform=False`）：
`ctrl_foot.L`、`ctrl_foot.R`、`ctrl_hand.L`、`ctrl_hand.R`

它们的位置直接取对应骨骼的 head，作为 IK 目标。

### 关键坐标（1.68m 身高参考）

| 骨骼 | head_local | 说明 |
|---|---|---|
| hips | (0, 0.010, 0.905) | |
| thigh | (±0.088, 0.006, 0.900) | 大腿长 0.420 |
| shin | (±0.090, 0.008, 0.480) | 小腿长 0.375 |
| foot | (±0.092, 0.012, 0.105) | 脚长 0.127，踝高 0.105 |
| upper_arm | (±0.120, 0.002, 1.340) | 上臂长 0.252 |
| forearm | (±0.196, -0.004, 1.100) | 前臂长 0.224 |
| hand | (±0.238, -0.012, 0.880) | 长 0.111 |

**臂总长 = 0.252 + 0.224 = 0.476**（不含手），这个数字决定了出拳能伸多远。
**腿总长 = 0.420 + 0.375 = 0.795**。

### 非人形角色（直立小型动物 / 宠物）

不要照搬人形骨架。先看模型实际站姿再定结构——仓鼠图生 3D 出来是**直立站姿**（两条后腿站立、前爪垂在身侧），就按简化人形 + 尾巴来做，28 骨：

```
root [非变形]
 └ hips → spine → chest → neck → head
    │                        └ ear.L / ear.R
    ├ shoulder.L/R → upper_arm → forearm → hand
    ├ thigh.L/R → shin → foot → toe
    └ tail.01 → tail.02 → tail.03 → tail.04
```

要点：

- **尾巴单独 4 骨**。它是这类角色的表演主体，必须能甩；人体骨架里没有对应骨，要按尾巴实际走向取点（沿曲线每隔 3-4cm 一根）
- **耳朵 2 骨**，父级是 head，`use_deform=True`，能微动
- 腿短、脚小，`foot`/`toe` 几乎是贴在一起的两小段，别指望它做脚掌滚动
- **不要加 IK 约束**。这种短肢角色一旦 IK 无 pole target 就会翻转，而两骨解析 IK 在脚本里更可控
- 骨长参考：按包围盒比例取，不要按人体比例硬套

## 二、代理体热权重（核心步骤）

```python
# 1) 复制原网格
proxy = obj.copy()
proxy.data = obj.data.copy()
bpy.context.scene.collection.objects.link(proxy)

# 2) VOXEL Remesh 成水密代理
rm = proxy.modifiers.new("Remesh", "REMESH")
rm.mode = "VOXEL"
rm.voxel_size = 0.008        # ← 甜点，见下方说明
bpy.ops.object.modifier_apply(modifier=rm.name)

# 3) 代理上算热权重（此时必须处于 object mode 且 active 是 proxy）
bpy.context.window.view_layer.objects.active = proxy   # ← 注意用 window.view_layer
proxy.select_set(True)
bpy.ops.object.parent_set(type="ARMATURE_AUTO")

# 4) 把权重转回原网格
dt = obj.modifiers.new("DataTransfer", "DATA_TRANSFER")
dt.object = proxy
dt.use_vert_data = True
dt.data_types_verts = {"VGROUP_WEIGHTS"}
dt.vert_mapping = "POLYINTERP_NEAREST"
dt.layers_vgroup_select_src = "ALL"
dt.layers_vgroup_select_dst = "NAME"
bpy.ops.object.modifier_apply(modifier=dt.name)
```

### voxel_size 选值（实测）

| voxel_size | 代理面数 | 热权重结果 |
|---|---|---|
| 0.0035 | ~90k | **失败**（0 权重） |
| 0.006 | ~38k | **失败**（0 权重） |
| **0.008** | **~22k** | **成功** |

不是越细越好。太细的代理会重新引入原网格的碎片化问题。0.008 在 1.68m 身高上约等于 5mm 体素。

换身高的话按比例缩放：`voxel = 身高 / 210`。

### 小体型角色（仓鼠、宠物、Q 版）实测

0.28m 高的仓鼠，原网格已经是**单一流形壳体**（`shells=1`、非流形边 0），仍然热权重全失败。逐步排查：

| 尝试 | 结果 |
|---|---|
| 原网格直接算 | 149,978 顶点全 0 |
| 代理 voxel 0.0015（84k 顶点） | 全 0 |
| 代理放大 6 倍后同样失败 | 全 0 |
| 代理 voxel 0.004 (11k) / 0.003 / 0.002 | 全 0 |
| **代理 voxel 0.006（5k 顶点）** | **成功** |

两条结论：

1. **放大模型本身不解决问题**。报错提示"请缩放到合适大小"会误导人——真正决定成败的是代理的**体素相对密度**。0.28m 用 0.006 与 1.68m 用 0.036 等效；0.006 在仓鼠上相当于身高的 1/47，和人体那档 0.008/1.68≈1/210 并不一致，所以按 `身高/210` 反推在小型角色上会失败，要往**粗**里试。
2. **失败的特征是"越细越死"**：细体素会忠实地把 AI 生成的伪毛发薄片、内腔重叠面留下来，重新造出破坏热权重的退化几何。粗体素把它们糊掉，反而能算。

判断标准只有一个：代理漏权数。为 0 才继续，否则换更粗的体素重试。粗代理丢掉的细节（薄耳朵、尾巴尖）在 DataTransfer 之后不会失真太多，因为权重本就是低频的。

## 三、权重边界整形（消除折痕）

热权重 + 转回之后，大体积圆润部位（肚子、腮部）常出现两种情况：

- **越界**：腿部权重占了整个下腹，一抬腿整片肚子跟着掀起
- **硬边**：给腿做几何门限后，门限处出现笔直折痕

两步一起做才干净。

### 1. 几何门限（先压范围）

用体积条件而不是纯距离。纯距离在小短腿角色上分不开"脚"和"贴着脚的肚子"。

```python
g_y = smoothstep((-0.030 - Y) / 0.055)     # 只保留前半部
g_z = 1 - smoothstep((Z - 0.030) / 0.060)  # 只保留低处
g_L = smoothstep((X - 0.004) / 0.030)      # 左右分开
fac = g_y * g_z * g_L
```

**关键教训：三条门限都要给，只给 X 会留下"整条下腹带"被腿控制**——实测那正是"窗帘状被拉起"伪影的来源。过渡带要宽（≥0.03），因为 AI 网格三角形大小不均，常有 11mm 的长边，20mm 的过渡带跨不过两条边，等于硬切。

### 2. 拉普拉斯平滑权重（再消硬边）

不管门限怎么调，网格密度不均就会留折痕。写回之后统一平滑：

```python
ev = 边索引 (E,2)
deg = np.bincount(ev.ravel(), minlength=nv)
for _ in range(5):
    acc = np.zeros_like(W)
    np.add.at(acc, ev[:,0], W[ev[:,1]])
    np.add.at(acc, ev[:,1], W[ev[:,0]])
    W = 0.5*W + 0.5*(acc / deg[:,None])
    W /= np.maximum(W.sum(1, keepdims=True), 1e-8)
W[W < 0.004] = 0     # 修剪后用归一化补回
```

5 轮、系数 0.5 的结果：相邻顶点权重差 >0.5 的边从 **941 条降到 85 条**，最大差值 1.42→0.71，折痕消失。平滑后每顶点平均 3.4 根骨，仍然干净。

### 3. 判断"是权重还是几何"的方法

不要靠肉眼看，量两个数：

| 指标 | 含义 |
|---|---|
| 每条边的权重 L1 差 | >0.5 就是权重断层，平滑能修 |
| 每条边的变形/静止长度比 | 99 分位 >2 才是几何被拉坏 |

仓鼠实测：99 分位拉伸比只有 1.19，最大 3.08 且是 0.4mm 的短边——所以那条折痕**不是形变**，是权重断层。再配一张纯色材质（断开所有贴图）重渲，就能排除 UV 岛/法线贴图的影响。

## 四、壳体收尾（两个必做步骤）

原网格的独立壳体被权重拉扯时会出现"长条薄片"和"漂浮碎片"。分两步修：

### 1. 跨区域壳体 → 刚性绑定

同时被手臂和腿拉扯的壳体（权重大于 2 个区域）会在中间被撕开。

```python
# 对每个连通壳体，检查它含哪些区域的权重
regions = {"arm": ["shoulder", "upper_arm", "forearm", "hand"],
           "leg": ["thigh", "shin", "foot", "toe"],
           "torso": ["hips", "spine", "chest", "upper_chest", "neck", "head"]}
# 若一个壳体的顶点同时命中了 ≥2 个区域 -> 整体刚性绑定到权重和最大的那根骨
```

实测 2456 个壳里有 **708 个**属于这种情况。

### 2. 误绑到手的远端壳体 → 改绑最近躯干骨

被绑到 hand/forearm 但离手 >0.10m 的壳体（多半是飘在空中的霓虹条），会跟着手飞。

```python
# 对这类壳体，改绑到最近的 hips / thigh / spine
```

实测修正了 **19 个**。

## 五、MCP 上下文坑（一定要知道）

在 MCP `execute_code` 里执行 bpy 代码时：

```python
# ✗ 不生效
bpy.context.view_layer.objects.active = obj

# ✓ 必须用 window
bpy.context.window.view_layer.objects.active = obj
```

而且**不要用 `bpy.context.temp_override(...)`** —— 用了反而进不了编辑模式（`bpy.ops.object.mode_set(mode="EDIT")` 会失败）。

## 六、验收

摆三个测试姿势并渲染：

1. 一只手抬到胸前（测肩/肘）
2. 一条腿前迈（测胯/膝）
3. 头转向一侧（测颈）

检查：有没有撕裂、长条薄片、关节塌陷、配件跟随错误。

**⛔ 这一步必须让用户看渲染图确认，才能进入动画阶段。**

## 已知限制（提前告知用户）

- 无手指骨时，手是整根骨，握拳做不出来
- 无表情骨（图生 3D 的脸是贴图）
- 极限姿势下肩/肘/胯仍有轻微挤压（没有 corrective shape key）
- 细长配件（霓虹条、鞋带）近看偏"纸片"

---

## 七、通用体型版（自动化路径）

上面的 23 骨数值是**手写参考**，只适用于 1.68 m 标准人形。要让任意体型都能跑，
用 `scripts/` 里的自动管线（详见 `00-generic-pipeline.md`）：

```python
import measure, archetypes, rig_build, weights
m    = measure.measure(mesh_obj)
arch, why = measure.guess_archetype(m)
spec = archetypes.build_spec(m, arch); spec["height"] = m["height"]
rig  = rig_build.build_armature(spec, name="Rig")
rig_build.attach_mesh(mesh_obj, rig)          # 内含烘焙网格变换
stats = weights.auto_weights(mesh_obj, rig, spec)   # 默认包络权重
```

与手写流程的三处关键差异：

1. **权重用包络法**（`envelope.py`），不是热权重。热权重在 AI 网格上要么全 0、
   要么一根骨吃掉 60%（实测 `tail.01` 吞 67%）。包络只看距离，与拓扑无关。
2. **必须先烘焙网格变换**：AI 模型常带 1.44 倍缩放 + −90° 旋转，
   骨骼是世界坐标而 Armature 修改器在局部空间生效 → 不烘焙就整体错位、模型缩水。
3. **零约束、纯 FK**。腿的 IK 用解析式两骨解算写成旋转关键帧，
   避免 IK 无 pole target 时翻膝（单帧 46°、位移 241mm）。

`bpy.ops` 相关的上下文坑见 `ctx.py`：`open_mainfile` 之后 `bpy.context.window` 是 None。
