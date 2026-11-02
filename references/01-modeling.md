# 阶段一：建模

## 结论先行

代码建模有两条路，都试过：

| 路线 | 结果 | 代价 |
|---|---|---|
| 基础几何体拼装（球、圆柱、圆环） | "积木人"，永远不像 | 低 |
| metaball 雕塑 + 体素重构 | "人体模特"，结构对但没有风格细节 | 高（要大量手调） |
| **图生 3D 出底模 + Blender 后处理** | **能直接看出是这个角色** | **中（一次成功，服装/发型/配色都到位）** |

正路是第三条。前两条只在没有可用图生 3D 服务时作为备选。

---

## 步骤

### 1. 切出单张正视图

三视图通常是一张图里三格。按比例切开：

```python
from PIL import Image
im = Image.open(src).convert("RGB")
W, H = im.size
boxes = {
    "front": (int(W*0.05), int(H*0.02), int(W*0.35), int(H*0.93)),
    "side":  (int(W*0.35), int(H*0.02), int(W*0.63), int(H*0.93)),
    "back":  (int(W*0.63), int(H*0.02), int(W*0.99), int(H*0.93)),
}
for k, b in boxes.items():
    im.crop(b).save(f"ref/{k}.png")
```

切完**先看一眼正视图像不像**，构图歪了（人物没居中、被裁到）就调整 box 再切。

### 2. 图生 3D

用云服务（混元 3D / Tripo / Meshy / Rodin 任选）。参数经验：

- `--enable-pbr`：开，否则没贴图
- `--face-count 300000`：单人角色够用
- `--result-format`：GLB 或 FBX 都行，GLB 带贴图更完整

**Windows 上的坑**：把图片转 base64 塞进命令行参数会超过 32KB 限制。要在 Python 内部构造 `sys.argv` 再 `runpy`，而不是拼 shell 命令。

**下载的坑**：30-50MB 的 GLB 一次 `curl` 会断（schannel 报错）。必须断点续传：

```python
for attempt in range(6):
    pos = os.path.getsize(out) if os.path.exists(out) else 0
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0", "Range": f"bytes={pos}-"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r, open(out, "ab" if pos else "wb") as f:
            while chunk := r.read(1 << 15):
                f.write(chunk)
        break
    except Exception as e:
        time.sleep(1)
```

### 3. 导入并对齐

```python
bpy.ops.import_scene.gltf(filepath=glb_path)
obj = [o for o in bpy.context.selected_objects if o.type == "MESH"][0]

# 按包围盒缩放到目标身高
bb = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
zs = [v.z for v in bb]
height = max(zs) - min(zs)
scale = 1.68 / height          # 目标身高
obj.scale = (scale, scale, scale)
bpy.context.view_layer.update()

# 脚底贴 z=0 + 水平居中
bb = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
xs = [v.x for v in bb]; ys = [v.y for v in bb]; zs = [v.z for v in bb]
obj.location.x -= (max(xs) + min(xs)) / 2
obj.location.y -= (max(ys) + min(ys)) / 2
obj.location.z -= min(zs)
bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
```

### 4. 修材质（必做，否则渲染成铬金属人）

图生 3D 导出的 GLB，metallic/roughness 通常是打包贴图。Blender 的 glTF 导入器会把它们接到 Principled BSDF 上，解释出来金属度接近 1 → 整个人像不锈钢。

```python
for mat in bpy.data.materials:
    if not mat.use_nodes:
        continue
    for node in mat.node_tree.nodes:
        if node.type != "BSDF_PRINCIPLED":
            continue
        for name, value in (("Metallic", 0.0), ("Roughness", 0.62)):
            inp = node.inputs.get(name)
            if inp is None:
                continue
            # 断开所有连到该口的 link
            for link in list(inp.links):
                mat.node_tree.links.remove(link)
            inp.default_value = value
```

### 5. 渲染四视图

正交相机，四个机位（front / side / back / three-quarter），EEVEE + `view_transform="Standard"`。

> **色彩管理**：必须用 `Standard` 而不是 AgX。AgX 会把高亮发光冲成白色，霓虹材质全变白。

---

## 验收标准

- 角色比例对（身高、头身比）
- 服装/发型/配色能对上参考
- 四个视角都没有明显破面
- 渲染不是金属质感

## 已知限制

图生 3D 的典型缺陷，需要提前告诉用户：

- 脸上常有一条 UV 接缝横线
- 头发偏扁，像一整块壳
- 手指、鞋带这类细节是贴图撑的，近看会软
- 发光材质是烘死在贴图里的，不会真的亮（想让它亮要单独做发光几何或颜色遮罩）
