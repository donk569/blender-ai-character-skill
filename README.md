# blender-character-pipeline

> 给 AI 助手用的 Blender 角色流水线：**三视图 → 模型 → 骨架 → 动画**，每个阶段强制停下来让人确认。

一套在 Blender 里用 Python 做完整角色资产的**可复用方法论 + 工具脚本**。不是教程，是踩完坑之后的成品流程——每一条结论都有实测数据支撑。

## 为什么需要它

用代码在 Blender 里做角色动画，绝大多数人（包括 AI）会掉进同样的三个坑：

**坑一：用几何体拼人。** 圆柱当胳膊、球当脑袋。做出来永远是"积木人"，调多少参数都救不回来。

**坑二：直接用骨骼热权重。** AI 生成的网格有几千个分离壳体，热权重会全部返回 0 权重——它需要水密流形。

**坑三：动画靠手调数值。** 关键帧数字看着对，动起来抽搐、顺拐、脚飘。因为真正的原因是**插值方法、坐标系、相位定义**，不是参数。

## 三阶段流程

```
三视图 ──▶ 阶段一 建模 ──▶ ⛔ 确认 ──▶ 阶段二 骨架 ──▶ ⛔ 确认 ──▶ 阶段三 动画 ──▶ 交付
```

| 阶段 | 产物 | 卡点 |
|---|---|---|
| 一、建模 | `.blend` + 四视图渲染 | 像不像？确认才继续 |
| 二、骨架 | `_rig.blend` + 姿势验证图 | 摆姿势会不会撕裂？ |
| 三、动画 | `_anim.blend` + `.glb` + GIF + 指标表 | 动作自不自然？ |

**每个阶段必须停下来让用户看，确认后才能进下一阶段。** 一次性跑完三段再给结果，等于让人在错误的模型上雕刻。

## 核心经验（摘自踩坑记录）

### 建模

用图生 3D（混元 / Tripo / Meshy）出底模，再在 Blender 里修材质、对齐。手搓代码建模的天花板是"能看出是这个角色"。

必须做的一步：**图生 3D 导出的 GLB 里 metallic/roughness 是打包贴图**，Blender 解释后金属度接近 1，渲染成铬金属人。断开贴图 link，改 `metallic=0`、`roughness=0.62`。

### 绑骨

- **别用 Rigify**：metarig 是 T-pose，与 A-pose 模型不符；且算子在这个上下文里拿不到 active_object
- **别直接算热权重**：2456 个壳体的 AI 网格会全部返回 0
- 正路：VOXEL Remesh **0.008** 做水密代理 → 代理算权重 → DataTransfer 转回（0.006 和 0.0035 都会失败，0.008 是甜点）
- 收尾：708 个跨区域壳体改刚性绑定，19 个误绑到手部的远端壳体改绑躯干

### 动画

五个根因覆盖 90% 的问题：

| 根因 | 症状 | 修法 |
|---|---|---|
| action 已挂载时摆姿势 | 数值对不上、首帧错乱 | 构建期间 `action=None`，先记录后写帧 |
| 分段余弦插值 | 动作一顿一顿 | 换单调三次 Hermite（C1 连续） |
| 坐标系用错 | 人歪着走、双臂挤胸前 | 扭转写 Y 轴不写 Z；摆臂用世界方向 |
| 相位形状不匹配 | 顺拐 | `arm_wave(q) = -cos(2π(q - stance))` |
| IK 无 pole / 进奇异区 | 膝盖肘部单帧翻 40-160° | 解析两骨 IK + `dmin=0.185` |

**几组实测数据**：

- 换插值后，跑步摆动相的脚速从 `6 → 36 → 24 → 51 → 60 → 23 → 52 → 84`（锯齿）变成 `10 → 24 → 38 → 41 → 45 → 49 → 56 → 59`（平滑钟形）
- 修顺拐后，`corr(同侧腿,臂)` 从 −0.530 变成 **−0.965**，`corr(对侧)` 从 +0.479 变成 **+0.979**
- Attack 的力量感来自**速度对比**不是角度：蓄力 14mm/帧 → 爆发 153mm/帧 → 命中骤停 7mm/帧（10.7 倍）

## 目录

```
SKILL.md                     入口：三阶段流程 + 确认卡点 + 禁止事项
references/
  01-modeling.md             图生 3D、材质修复、尺寸对齐
  02-rigging.md              骨架结构、代理体热权重、壳体收尾
  03-animation.md            相位设计、解析 IK、C1 插值、贴地、爆发曲线
  troubleshooting.md         症状 → 根因 速查表
scripts/
  blender_bridge.py          socket 桥接（不需要 MCP 工具注册）
  boot_blender.py            无 UI 自动拉起 Blender + 启动服务
  gspline.py                 C1 连续单调三次插值
  pipeline_common.py         骨骼工具：aim / 解析 IK / 记录写帧 / 贴地
  verify.py                  动画验收指标
  bcommon.py                 建模辅助（放样、管体、材质）
```

## 用法

**前提**：Blender 装了 `mcp-for-blender` 插件（addon 名 `blender_mcp`）。

```bash
# 1. Blender 没开时自动拉起
blender.exe --python scripts/boot_blender.py

# 2. 确认连通
python scripts/blender_bridge.py scene

# 3. 执行任意 bpy 脚本（脚本落盘再执行，别用命令行传大段代码）
python scripts/blender_bridge.py run my_step.py
```

在 Blender 内用公共库：

```python
import sys
sys.path.insert(0, r"<repo>/scripts")
from pipeline_common import *
from gspline import smooth_table
from verify import verify_all
```

## Reference 角色规格

本项目的方法在一只 1.68m 角色上验证过，可作为初始参数：

| 项 | 值 |
|---|---|
| 身高 | 1.68 m |
| 骨架 | 23 骨（22 变形 + root），+ 4 控制骨 |
| 大腿 / 小腿 | 0.420 / 0.375 m |
| 上臂 / 前臂 | 0.252 / 0.224 m |
| 网格 | 299,866 三角面，2456 个壳体 |
| 动作 | Idle 180f / Walk 30f / Run 28f / Attack 36f @30fps |

## 已知限制

这套流程做不到的（提前知道比事后返工好）：

- 没有手指骨，握拳做不出来
- 没有表情骨（脸是贴图）
- 极限姿势下肩/肘/胯仍有轻微挤压（无 corrective shape key）
- 图生 3D 的头发偏扁、手指细节靠贴图
- 程序化动画仍缺真人动捕的不规则性

## License

MIT
