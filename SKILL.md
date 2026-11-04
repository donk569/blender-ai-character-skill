---
name: blender-character-pipeline
description: 从三视图（或任意单张角色图）到带动画角色的 Blender 流水线——建模、绑骨、动画三阶段，每阶段强制人工确认。适用于人形、Q 版、动物、四足、胖圆体型等任意比例；先量体型再生成骨架，不预设头身比与骨骼命名。当用户给出角色图片要求"按图建模""绑骨骼""做走路跑步动画""生成带动画的角色"，或需要把参考图变成可用的 .blend/.glb 资产时使用。
---

# Blender 角色流水线（通用体型）

把一张（或三张）角色图变成可动画的 3D 角色。**三阶段，每阶段结束必须停下来给用户看，确认后才继续。**

```
角色图 ──▶ ① 建模 ──▶ ⛔ 确认 ──▶ ② 骨架+蒙皮 ──▶ ⛔ 确认 ──▶ ③ 动画 ──▶ 交付
```

## 五条铁律

1. **不假设体型。** 没有任何"标准人形"参数可以套。头身比 1:3 的 Q 版、四足动物、贴地的胖仓鼠，都靠测量决定骨架比例，见 `scripts/measure.py`。
2. **不假设朝向。** 面的正前方不一定是 `-Y`。用脚（脚掌一定朝前伸）判定，不要用头（长发/耳朵/角会反向）。
3. **不假设网格是好的。** AI 生成的网格常见几千个壳体、重叠薄片、非流形边。所有算法必须对这种网格成立。
4. **每阶段先备份。** 新阶段另存 `*_model.blend` / `*_rig.blend` / `*_anim.blend`，不覆盖上一阶段。
5. **不靠"脚本跑通"验收。** 必须渲染出图看，并用数值指标（角度跳变、脚底高度、权重覆盖率）交叉验证。

## 环境准备

Blender 5.0 + 插件 `blender_mcp`（socket 9876）。若 Blender 没开：

```bash
blender.exe --python scripts/boot_blender.py     # 自动启用插件、开服务、加载工程
```

与 Blender 通信（不依赖 MCP 工具注册，直接走 socket）：

```bash
python scripts/blender_bridge.py scene                     # 看场景
python scripts/blender_bridge.py run path/to/script.py     # 在 Blender 里执行脚本
```

脚本里 `import` 本 skill 的模块：

```python
import sys; sys.path.insert(0, r"<skill>/scripts")
import measure, archetypes, rig_build, weights, envelope, grounding, gait, ctx
```

---

## 阶段一：建模

**目标**：拿到一个比例、配色、服装都对得上参考图的网格。目标不是可交付的成品模型。

1. 从参考图切出**单张正视图**（三视图时取正面那张；单图直接用）。
2. 图生 3D 出底模（`buddy-multimodal-generation` skill，`3d --image-base64 ... --enable-pbr --face-count 300000`）。
3. 下载要**断点续传**（`Range: bytes=pos-`），几十 MB 一次 curl 常断。
4. 导入 Blender：按包围盒缩放到目标身高、脚底贴 z=0、水平居中、apply 变换。
5. **材质必查**：GLB 打包的 metallic/roughness 是贴图，Blender 解释后金属度接近 1 → 渲染成铬金属人。断开贴图输入、`metallic=0`、`roughness≈0.6`。
6. 渲染四视图 + 正面特写。

**⛔ 停。** 给用户看四视图，逐条列出"对上了什么"和"没做到什么"（缺件、破面、比例偏差），等确认。

详见 `references/01-modeling.md`。

## 阶段二：骨架 + 蒙皮

**目标**：任意体型都能自动生成骨架并蒙皮，且摆姿势不撕裂。

```python
m    = measure.measure(mesh_obj)                       # 量体型
arch, why = measure.guess_archetype(m)                # humanoid / biped_small / quadruped
spec = archetypes.build_spec(m, arch)                 # 生成骨骼清单（世界坐标）
rig  = rig_build.build_armature(spec, name="Rig")     # 建骨架（纯 FK，零约束）
rig_build.attach_mesh(mesh_obj, rig)                  # 烘焙变换 + 挂 Armature
stats = weights.auto_weights(mesh_obj, rig, spec)      # 默认包络权重
feas = measure.feasibility(m, spec)                   # 能不能迈步（阶段三要用）
```

- **先用 `measure.report()` 打印全部测量值**，检查裆高、朝向、接触块是否符合肉眼判断。测错则整条链都错。
- 骨架是**纯 FK、零约束**。IK 约束没有 pole target 会在某些角度突然翻膝（单帧 46°，人眼即"抽搐"），解析式两骨 IK 写成 FK 才是稳的。
- 蒙皮默认**包络权重**（`envelope.py`）：只看顶点到骨骼线段的距离，与网格拓扑无关。热权重在 AI 网格上要么全 0、要么一根骨吃掉 60%，只作为可选（`method="heat"`）。
- 摆极限姿势渲染（抬臂、抬腿、转腰、甩尾）逐张看。

**⛔ 停。** 给用户看"静止 vs 姿势"对照图，报告权重覆盖率与越界位移，等确认。

详见 `references/02-rigging.md`。

## 阶段三：动画

**目标**：物理与解剖都成立的循环动画，且每个量都有实测依据。

**先做可行性预检**（`measure.feasibility`），它决定这一阶段的所有设计：

| 实测 | 含义 | 采取的方案 |
|---|---|---|
| `leg_extend ≈ 1.0`（>0.99） | 腿已伸直，没有弯膝余量 | 不能迈步 → 改原地踏步 `in_place_march` |
| `horiz_margin < 3mm` | 脚向前迈会够不到地面 | 同上，步幅归零 |
| `belly_clearance < 5mm` | 肚子贴地，抬髋就穿模 | 起伏交给脚，不给髋 |
| 三项都宽松 | 正常双足 | `step`：支撑相脚钉地、摆动相前摆 |

```python
signs = gait.calibrate_signs(rig, spec)   # 旋转正负号必须实测标定，别凭直觉
plan  = gait.plan_gait(feas, m["height"], "walk")
```

动画制作的六条硬规则：

1. **先记录后写帧**：构建期间 `rig.animation_data.action = None`，逐帧摆姿势记录 loc/quat，最后统一写关键帧。否则 `view_layer.update()` 会用旧关键帧把手摆的姿势冲掉。
2. **插值用 C1 连续单调三次样条**（`gspline.smooth_table`），不要用 `0.5-0.5cos(πt)` 分段缓动——后者在每个控制点导数归零，动作会一顿一顿。
3. **骨骼轴要标定**：扭转走 `rotation_euler[1]`（沿骨轴），俯仰 `[0]`，侧倾 `[2]`。写错轴会让"扭转"变成"歪着走"。
4. **摆臂用世界方向瞄准**（`grounding.aim_matrix`），不要用欧拉角——骨骼 roll 会让正负角都朝前，出现双臂挤胸、同手同脚。
5. **脚掌世界朝向单独控制**（`grounding.place_feet`）：IK 只固定踝位置，脚骨仍随小腿翻转。
6. **逐帧贴地闭环**：量变形后网格最低点，修正脚目标高度迭代直到 |z| < 1mm。

**⛔ 停。** 交付循环预览 GIF（至少 3 个周期）+ 关键帧条 + 指标表，等确认。

详见 `references/03-animation.md`。

## 验收指标（每阶段都要报）

| 指标 | 合格线 | 测法 |
|---|---|---|
| 单帧最大角跳变 | < 20°/帧（非循环动作 < 40°） | 逐帧相邻骨骼世界四元数夹角 |
| 地面 min_z | > -3mm | 变形后网格逐帧最低点 |
| 漏权顶点 | 0 | 顶点权重和 < 0.01 的个数 |
| 无权重骨 | 0 | 该骨最大权重 < 0.15 |
| 交叉协调 corr(腿L, 臂L) | < -0.9（循环动作） | 逐帧世界位置或速度的相关系数 |
| 循环接缝 | < 15° / < 100mm | 末帧 vs 首帧 |
| 根通道 fcurve | 0 条 | 原地动画不得有 root 位移 |

## 脚本清单

| 文件 | 职责 |
|---|---|
| `measure.py` | 体型测量、体型分类、可行性预检、报告 |
| `archetypes.py` | 骨骼模板与 spec 生成（按实测比例缩放） |
| `rig_build.py` | 建骨架、烘焙网格变换、挂 Armature |
| `envelope.py` | 包络权重（默认蒙皮） |
| `weights.py` | 热权重/代理体/解析权重（可选路径）+ 校验 |
| `grounding.py` | 脚部落点、世界朝向瞄准、贴地 |
| `gait.py` | 正负号标定、步态规划、周期函数、写 Action |
| `ctx.py` | `bpy.ops` 上下文兜底（open_mainfile 后 window 为 None 的坑） |
| `blender_bridge.py` | socket 桥接 |
| `boot_blender.py` | 无 UI 拉起 Blender 并开服务 |

## 已知边界（必须向用户说明）

1. **包络权重对复杂分层服装会拉伸**：连帽衫的帽/下摆与袖子是同一张连续面，转臂时会被拉成"帘子"。这类资产建议 `method="heat"` 或手工补权重。
2. **腿已伸直的模型做不出真实步幅**，只能原地踏步；要真迈步得改模型（腿加长 / 腹部抬高）。
3. **重叠薄片会导致"重影"**：AI 网格常有位置重合的重复面片，摆姿势时各自飞到不同位置。`envelope.unify_coincident` / 权重按位置统一能缓解，但几何本身还是多股。
4. **没有手指骨、表情骨**：手掌是单骨，脸是贴图。
5. 程序化动画缺少真人动捕的细微不规则；要再上一个台阶需 BVH 重定向。
