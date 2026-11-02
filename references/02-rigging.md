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

## 三、壳体收尾（两个必做步骤）

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

## 四、MCP 上下文坑（一定要知道）

在 MCP `execute_code` 里执行 bpy 代码时：

```python
# ✗ 不生效
bpy.context.view_layer.objects.active = obj

# ✓ 必须用 window
bpy.context.window.view_layer.objects.active = obj
```

而且**不要用 `bpy.context.temp_override(...)`** —— 用了反而进不了编辑模式（`bpy.ops.object.mode_set(mode="EDIT")` 会失败）。

## 五、验收

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
