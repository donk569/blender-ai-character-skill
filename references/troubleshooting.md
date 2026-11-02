# 症状 → 根因 速查表

遇到问题先查这张表，按"症状"定位，不要凭感觉调参数。

---

## 动画

### 动作一顿一顿 / 不丝滑

| 检查 | 方法 |
|---|---|
| 脚速序列是否锯齿 | 逐帧算脚踝位移，看有没有 `62 → 6 → 36` 这种断崖 |
| 插值是不是分段余弦 | 看 `tbl()` 有没有 `0.5 - 0.5*cos(πt)` |

**根因**：控制点处导数为 0。
**修法**：换 `gspline.smooth_table()`。

### 顺拐（同侧手脚同时向前）

| 检查 | 方法 |
|---|---|
| 相关系数 | `corr(footL_y, handL_y)` 为正是顺拐 |
| 波形谷值位置 | 腿的谷值在 ph=0.36，手臂如果也是 cos 但谷值在 0.54，差 0.18 |

**根因**：两条曲线形状不同，极值点没对齐。
**修法**：`arm_wave(q) = -cos(2π(q - stance))`，把手臂谷值放到腿的谷值处。

> 注意：双腿交叉瞬间（两脚 y 都接近 0）用"位置中点"判定会误报同侧。以相关系数为准。

### 手臂不动 / 只在原地晃 / 双臂挤在胸前

| 检查 | 方法 |
|---|---|
| 手的世界 Y 范围 | 如果只有 `-0.44 ~ +0.05`，说明手永远在身前 |
| 摆臂用的是欧拉角还是世界方向 | `set_rot(upper_arm, -42*cos, ...)` 就是错的 |

**根因**：
1. 欧拉角受骨骼 roll 影响，正反角度都往身前走
2. 前臂角范围不对称（−6° ~ 110°），后摆时前臂仍朝前，手甩不到臀后

**修法**：用 `aim_bone` 世界方向；前臂后摆端必须是**负角**（−37°），手才能到身后。

### 单帧猛转（抽搐）

| 检查 | 方法 |
|---|---|
| 最大角跳变在哪根骨、哪一帧 | 逐帧算世界四元数差 |
| 是否 IK 约束无 pole | 看 `pb.constraints` |
| 是否四元数符号翻转 | 看写帧时有没有做 `dot < 0` 取反 |
| 是否手臂 d 太小 | 算 `|target - shoulder|`，小于 0.185 就是奇异区 |

**修法**：删约束改解析 IK / 强制符号连续 / 提高 `dmin`。

### 姿势改了但没用 / 数值对不上 / 首帧错乱

**根因**：action 已挂载，`view_layer.update()` 用当前帧关键帧把手摆的姿势冲掉了。

**修法**：构建期间 `rig.animation_data.action = None`，先记录后写帧。

### 人歪着走 / 跛脚

**根因**：扭转写进了 `rotation_euler[2]`（侧倾轴）。

**修法**：改写 `rotation_euler[1]`（沿骨轴 = 偏航）。

### 脚穿地 / 脚浮空

| 情况 | 修法 |
|---|---|
| 支撑相穿地 | 脚掌 world 朝向每帧强制回 rest |
| 全程穿地 | 加逐帧贴地校正（测 min_z，整体平移髋 + IK 目标） |
| 落地帧一下沉 4-5cm | 骨盆高度表在腾空↔支撑之间做了布尔硬切，改成连续查表 |

### 跑步看起来像大步走

**检查**：有没有腾空帧（`min_z > 5mm`）、膝盖有没有抬起来、躯干前倾够不够。

**修法**：支撑相占比降到 35-40%、抬脚高度加到 15cm、前倾 8-10°。

---

## 骨骼 / 蒙皮

### 骨骼热权重全部返回 0

**根因**：AI 生成网格有几千个分离壳体，不是水密流形。

**修法**：VOXEL Remesh 0.008 做代理 → 代理算权重 → DataTransfer 转回。

### 摆姿势时出现长条薄片 / 漂浮碎片

**根因**：跨区域的壳体被多根骨拉扯撕开，或远端壳体被误绑到手。

**修法**：跨区域壳体刚性绑定到最大权重骨；离心 >0.10m 的手部壳体改绑最近躯干骨。

### `bpy.ops.object.mode_set` 报 "context is incorrect"

**根因**：MCP `execute_code` 里 `bpy.context.view_layer.objects.active` 不生效。

**修法**：用 `bpy.context.window.view_layer.objects.active`，且**不要**用 `temp_override`。

### Rigify 报 `'Context' object has no attribute 'active_object'`

**根因**：`wm.open_mainfile` 之后上下文重建，metarig 算子在 MCP 环境里拿不到活动对象；且 metarig 是 T-pose 与 A-pose 模型不匹配。

**修法**：放弃 Rigify，自建 23 骨骨架。

---

## 渲染 / 材质

### 人物渲染成铬金属

**根因**：图生 3D 的 GLB 里 metallic/roughness 是打包贴图，Blender 解释后金属度接近 1。

**修法**：断开 Metallic/Roughness 的贴图 link，改 `metallic=0`、`roughness=0.62`。

### 霓虹/发光材质变成白色

**根因**：AgX 色彩管理会把高亮发光冲成白色。

**修法**：`scene.view_settings.view_transform = "Standard"`，并用 `exposure` 微调亮度。

### 渲染引擎报错 `BLENDER_EEVEE_NEXT` not found

**根因**：不同 Blender 版本枚举名不同。

```python
try:
    sc.render.engine = "BLENDER_EEVEE"
except TypeError:
    sc.render.engine = "BLENDER_EEVEE_NEXT"
```

---

## 工程 / 环境

### `ConnectionRefusedError [WinError 10061]`

**根因**：Blender 没在运行，或插件的 socket 服务没启动。

**修法**：`blender.exe --python scripts/boot_blender.py`，脚本会自动启用 addon 并启动 9876 服务。

### 命令行传 base64 图片失败

**根因**：Windows 命令行参数上限 32KB。

**修法**：在 Python 内部构造 `sys.argv` 再 `runpy`，不要拼 shell 命令。

### 大文件下载中断

**根因**：schannel / 网络抖动，30-50MB 的 GLB 经常在中间断。

**修法**：断点续传（`Range: bytes=pos-`）+ 重试循环。

### glTF 导出后动画丢了一半

**根因**：action 没有 fake user，被清理；或导出模式不对。

**修法**：`act.use_fake_user = True`；`export_animation_mode="ACTIONS"` + `export_bake_animation=True`。
