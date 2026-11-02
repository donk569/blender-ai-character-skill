---
name: blender-character-pipeline
description: 从三视图到带动画角色的完整 Blender 流水线——输入正面/侧面/背面三视图，产出带骨架、蒙皮和循环动画（Idle/Walk/Run/Attack）的 .blend 与 .glb。三阶段各有强制人工确认卡点。当用户给出角色三视图、要求"按图建模""绑骨骼""做角色动画""生成走路跑步动画"，或需要在 Blender 中通过脚本完成角色资产时使用。
---

# Blender 角色流水线：三视图 → 模型 → 骨架 → 动画

## 这个 skill 解决什么

用代码在 Blender 里做角色，最容易掉进两个坑：

1. **用基础几何体拼人** —— 圆柱当胳膊、球当脑袋。做出来永远是"积木人"，无论怎么调参数。
2. **动画靠手调数值** —— 看着关键帧数字对，动起来抽搐、顺拐、脚飘。

本 skill 是踩完这些坑之后的成品流程。核心结论：

- **建模**：单张视图喂给图生 3D 服务出底模，再在 Blender 里修材质、缩放、对齐。手搓代码建模的天花板是"能看出是这个角色"，够不到"逼真"。
- **绑骨**：AI 生成的网格有几千个分离壳体，骨骼热权重直接失败。必须走「体素重构代理体 → 代理算权重 → 转回原网格」。
- **动画**：抽搐/顺拐/脚飘的根因几乎都不是"参数没调好"，而是**插值方法、坐标系、相位定义**三件事。细节见 `references/03-animation.md`。

## 三阶段流程（每阶段必须停下来等确认）

> **铁律：每个阶段完成后必须交付产物并明确问用户"是否继续"，得到确认才能进入下一阶段。**
> 不要一口气跑完三段再给结果。用户需要在中途看到东西、提修改意见。

### 阶段一：建模

**输入**：用户提供的三视图（正面 / 侧面 / 背面，最好是同一张图里的三格）。

**做法**：
1. 裁出单张正视图（`PIL` 按比例切割）
2. 图生 3D 出底模（混元 / Tripo / Meshy，选一个可用的）
3. 导入 Blender：按包围盒缩放到目标身高、脚底贴 z=0、居中对齐
4. **修材质**（关键）：图生 3D 导出的 GLB 里 metallic/roughness 常是打包贴图，Blender 解释后金属度接近 1，渲染成铬金属人。必须断开 Metallic/Roughness 的贴图输入，改 metallic=0、roughness≈0.62
5. 四视图渲染

**交付物**：`.blend` + 四视图渲染（front/side/back/three-quarter）

**⛔ 停下问用户**：模型像不像？哪里要改？确认后才能绑骨。

详细步骤 → `references/01-modeling.md`

---

### 阶段二：骨架与蒙皮

**输入**：阶段一确认过的网格。

**做法**：
1. 建 23 骨人形骨架（root → hips → spine → chest → upper_chest → neck → head，每侧 shoulder/upper_arm/forearm/hand + thigh/shin/foot/toe）
2. **不要直接对原网格算骨骼热权重** —— AI 网格是几千个分离壳体，热权重会全部返回 0
3. 复制网格 → VOXEL Remesh **0.008**（这个值是试出来的甜点，0.006 和 0.0035 都会让热权重失败）→ 水密代理
4. 代理上算热权重 → DataTransfer（POLYINTERP_NEAREST）转回原网格
5. 收尾：跨区域壳体（同时被手臂和躯干拉扯的）改成刚性绑定；被误绑到手/前臂但离心 >0.10m 的壳体改绑最近的 hips/thigh/spine
6. 加 4 根非变形控制骨（ctrl_foot.L/R、ctrl_hand.L/R）供 IK 用

**交付物**：`_rig.blend` + 姿势验证渲染（抬手、迈腿、转头三张）

**⛔ 停下问用户**：摆姿势会不会撕裂？关节有没有塌陷？确认后才能做动画。

详细步骤 → `references/02-rigging.md`

---

### 阶段三：动画

**输入**：阶段二确认过的骨架。

**做法**：
1. 先确认角色朝向（读 `foot` 骨骼的 head→tail 向量判断哪边是前方，不要假定 -Y）
2. 用**解析两骨 IK** 驱动腿（不用 constraints，见下方"绝对不要做"）
3. 参数全部写成**相位查表**，用 `scripts/gspline.py` 的 C1 连续插值
4. 逐帧摆姿势 → **记录** loc/quat → 全部摆完再统一写关键帧（"先记录后写"）
5. 四元数强制符号连续
6. 逐帧贴地校正
7. **用数据验收**，不是肉眼：单帧角跳变、地面 min_z、左右脚漂移、交叉相关系数、循环接缝
8. 逐帧渲染 → 合成 GIF → 实际看

**交付物**：`_anim.blend` + `_anim.glb` + 每个动作的 GIF + 指标表

**⛔ 停下问用户**：动作自不自然？确认后才交付。

详细步骤 → `references/03-animation.md`

---

## 绝对不要做的事

这些每一条都是实际踩过的坑，代价是几轮返工：

| 不要 | 为什么 |
|---|---|
| 用圆柱/球体拼角色 | 天花板就是积木人，改参数救不回来 |
| 对 AI 生成网格直接算热权重 | 几千个分离壳体，结果全 0 权重 |
| 在 action 已挂载时摆姿势 | `view_layer.update()` 会用当前帧关键帧重新求值，把手摆的姿势冲掉 |
| 用 `t = 0.5 - 0.5*cos(πt)` 分段插值 | 每个控制点导数归零 → 周期性微停顿，动作"一顿一顿" |
| 给骨骼加 IK constraint 但不给 pole target | 膝盖会自己翻转，单帧跳 40°+ |
| 用欧拉角改 `upper_arm.rotation_euler[0]` 摆臂 | 骨骼 roll 导致正反角度都把手带到身前，双臂挤在胸前 |
| 把扭转写进 `rotation_euler[2]` | 骨骼局部 Y 沿骨（= 世界 Z 的偏航），Z 是**侧倾**。写错就变成整个人歪着走 |
| 手臂用 cos、腿用三角波 | 两条曲线谷值差 0.18 周期 → 顺拐。必须极值点严格对齐 |
| `chest.matrix @ Vector((x, y, 绝对Z))` 传绝对坐标 | 局部偏移必须减去骨骼 head，否则手臂被甩到头顶 |
| 单独给 hand 设 aim 朝向 | `rotation_difference` 的 roll 会抖，单帧 52-65°。让 hand 继承前臂 + 绕局部 Y 固定扭转 |
| 只跑脚本成功就宣布完成 | 必须逐帧渲染 + 算指标 + 看 GIF |

## 环境准备

**Blender 端**：装 `mcp-for-blender` 插件（addon 名 `blender_mcp`），开启 socket 服务（默认端口 9876）。

Blender 没在运行时，可以用脚本自动拉起：

```bash
blender.exe --python scripts/boot_blender.py
```

（`boot_blender.py` 会 `addon_enable("blender_mcp")` → `bpy.types.blendermcp_server = BlenderMCPServer(port=9876)` → `.start()` → `wm.open_mainfile`，不需要点 UI。）

**调用端**：不需要 MCP 工具注册，直接用 socket：

```bash
python scripts/blender_bridge.py scene                      # 看场景
python scripts/blender_bridge.py run path/to/script.py      # 执行 py 文件
```

所有操作脚本落盘成 `.py` 文件再执行，**不要**通过命令行传大段代码（Windows 命令行有长度限制，中文路径还会出编码问题）。

## 数据验收指标

每次改完动画，跑 `scripts/verify.py` 拿这张表，指标不合格就不要交付：

| 指标 | 合格线 | 说明 |
|---|---|---|
| 单帧最大角跳变 | < 20° | 超过 30° 一定有翻转或坐标系错 |
| 地面 min_z | −3 ~ 0 mm | 负太多是穿地，正太多是浮空 |
| 支撑脚单帧位移 | 恒定 | 速度忽快忽慢 = 插值在控制点归零 |
| 交叉相关系数 | 同侧 < −0.9，对侧 > +0.9 | 顺拐会变成正相关 |
| 循环接缝 | 与其他帧相当 | 明显偏大说明首尾没对上 |
| root 通道 fcurve 数 | 0 | 原地动画不该有根位移 |

## 参考文件

- `references/01-modeling.md` — 图生 3D 流程、材质修复、尺寸对齐
- `references/02-rigging.md` — 骨架结构、代理体热权重、壳体收尾
- `references/03-animation.md` — 相位设计、解析 IK、C1 插值、贴地、验收
- `references/troubleshooting.md` — 症状 → 根因 速查表
- `scripts/` — 可直接复用的桥接、插值、建模工具、验收脚本
