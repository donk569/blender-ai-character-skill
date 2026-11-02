"""建模公共工具：材质、基础体、霓虹曲线、集合管理。
在 Blender 里通过 exec(open(...).read()) 执行，自动注入到命名空间。
"""
import bpy
import bmesh
import math
from mathutils import Vector, Matrix

TAU = math.pi * 2


# ---------------------------------------------------------------- 场景整理
def clean_scene():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for coll in list(bpy.data.collections):
        if coll.name != "Collection":
            bpy.data.collections.remove(coll)
    for block in (bpy.data.meshes, bpy.data.materials, bpy.data.curves, bpy.data.lights, bpy.data.cameras):
        for item in list(block):
            if item.users == 0:
                block.remove(item)


def get_coll(name, parent=None):
    coll = bpy.data.collections.get(name)
    if coll is None:
        coll = bpy.data.collections.new(name)
        (parent or bpy.context.scene.collection).children.link(coll)
    return coll


def link_to(obj, coll):
    for c in list(obj.users_collection):
        c.objects.unlink(obj)
    coll.objects.link(obj)


# ---------------------------------------------------------------- 材质
def new_mat(name, color=(0.02, 0.02, 0.025), rough=0.75, metal=0.0,
            emit=None, emit_strength=0.0, sheen=0.0, alpha=1.0, coat=0.0):
    mat = bpy.data.materials.get(name)
    if mat:
        return mat
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf is None:
        for n in mat.node_tree.nodes:
            if n.bl_idname == "ShaderNodeBsdfPrincipled":
                bsdf = n
                break
    if bsdf is None:
        bsdf = mat.node_tree.nodes.new("ShaderNodeBsdfPrincipled")
        out = next(n for n in mat.node_tree.nodes if n.bl_idname == "ShaderNodeOutputMaterial")
        mat.node_tree.links.new(bsdf.outputs[0], out.inputs[0])
    def si(key, val):
        if key in bsdf.inputs:
            bsdf.inputs[key].default_value = val
            return True
        return False

    si("Base Color", (*color, 1.0))
    si("Roughness", rough)
    si("Metallic", metal)
    si("Sheen Weight", sheen)
    si("Coat Weight", coat)
    if emit is not None:
        if not si("Emission Color", (*emit, 1.0)):
            si("Emission", (*emit, 1.0))
        si("Emission Strength", emit_strength)
    if alpha < 1.0:
        si("Alpha", alpha)
        mat.blend_method = "BLEND"
    mat.use_backface_culling = False
    return mat


def set_smooth(obj):
    for p in obj.data.polygons:
        p.use_smooth = True


def add_bevel(obj, width=0.004, segments=3):
    m = obj.modifiers.new("Bevel", "BEVEL")
    m.width = width
    m.segments = segments
    m.limit_method = "ANGLE"
    m.angle_limit = math.radians(40)
    return m


def add_subsurf(obj, levels=2):
    m = obj.modifiers.new("Subsurf", "SUBSURF")
    m.levels = 1
    m.render_levels = levels
    return m


# ---------------------------------------------------------------- 基础形体
def sphere(name, loc, scale, mat, segs=32, rings=16, rot=(0, 0, 0), coll=None):
    bpy.ops.mesh.primitive_uv_sphere_add(segments=segs, ring_count=rings, radius=1.0, location=loc)
    o = bpy.context.object
    o.name = name
    o.scale = scale
    o.rotation_euler = rot
    set_smooth(o)
    o.data.materials.append(mat)
    if coll:
        link_to(o, coll)
    return o


def cube(name, loc, size, mat, rot=(0, 0, 0), bevel=0.006, coll=None):
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=loc)
    o = bpy.context.object
    o.name = name
    o.scale = size
    o.rotation_euler = rot
    o.data.materials.append(mat)
    if bevel:
        add_bevel(o, bevel, 3)
    if coll:
        link_to(o, coll)
    return o


def cylinder(name, loc, radius, depth, mat, rot=(0, 0, 0), verts=32, coll=None):
    bpy.ops.mesh.primitive_cylinder_add(vertices=verts, radius=radius, depth=depth, location=loc)
    o = bpy.context.object
    o.name = name
    o.rotation_euler = rot
    set_smooth(o)
    o.data.materials.append(mat)
    if coll:
        link_to(o, coll)
    return o


def cone_between(name, p1, p2, r1, r2, mat, verts=24, caps=True, coll=None):
    """两点之间的锥/柱体，用于四肢、袖管、裤管。"""
    p1, p2 = Vector(p1), Vector(p2)
    d = p2 - p1
    depth = d.length
    bpy.ops.mesh.primitive_cone_add(vertices=verts, radius1=r1, radius2=r2,
                                    depth=depth, end_fill_type="NGON" if caps else "NOTHING",
                                    location=(p1 + p2) / 2)
    o = bpy.context.object
    o.name = name
    o.rotation_mode = "QUATERNION"
    o.rotation_quaternion = d.to_track_quat("Z", "Y")
    o.rotation_mode = "XYZ"
    set_smooth(o)
    o.data.materials.append(mat)
    if coll:
        link_to(o, coll)
    return o


def torus(name, loc, major, minor, mat, rot=(0, 0, 0), coll=None, boxy=False):
    bpy.ops.mesh.primitive_torus_add(location=loc, major_radius=major, minor_radius=minor,
                                     major_segments=40, minor_segments=12, rotation=rot)
    o = bpy.context.object
    o.name = name
    set_smooth(o)
    o.data.materials.append(mat)
    if boxy:
        for v in o.data.vertices:
            v.co.z *= 0.55
    if coll:
        link_to(o, coll)
    return o


def rounded_box(name, loc, size, mat, rot=(0, 0, 0), bevel=0.01, segs=4, subdiv=1, coll=None):
    o = cube(name, loc, size, mat, rot=rot, bevel=0, coll=coll)
    m = o.modifiers.new("Bevel", "BEVEL")
    m.width = bevel
    m.segments = segs
    if subdiv:
        s = o.modifiers.new("Subsurf", "SUBSURF")
        s.levels = subdiv
        s.render_levels = subdiv
    set_smooth(o)
    return o


def neon_curve(name, points, radius, mat, coll=None, cyclic=False, resolution=6):
    """折线/曲线霓虹灯带。points 为世界坐标列表。"""
    cu = bpy.data.curves.new(name, type="CURVE")
    cu.dimensions = "3D"
    cu.resolution_u = 12
    cu.bevel_depth = radius
    cu.bevel_resolution = resolution
    cu.use_fill_caps = True
    sp = cu.splines.new("BEZIER")
    sp.bezier_points.add(len(points) - 1)
    for bp, co in zip(sp.bezier_points, points):
        bp.co = Vector(co)
        bp.handle_left_type = "AUTO"
        bp.handle_right_type = "AUTO"
    sp.use_cyclic_u = cyclic
    ob = bpy.data.objects.new(name, cu)
    cu.materials.append(mat)
    (coll or bpy.context.scene.collection).objects.link(ob)
    return ob


# ---------------------------------------------------------------- 变换与合并
def apply_mods(obj):
    bpy.context.view_layer.objects.active = obj
    for m in list(obj.modifiers):
        try:
            bpy.ops.object.modifier_apply(modifier=m.name)
        except RuntimeError:
            pass


def join_objs(objs, name):
    objs = [o for o in objs if o and o.name in bpy.data.objects]
    bpy.ops.object.select_all(action="DESELECT")
    for o in objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = objs[0]
    bpy.ops.object.join()
    o = bpy.context.object
    o.name = name
    return o


def sym_pair(fn, side_axis="x"):
    """对称生成左右两份：fn(sign) -> object"""
    return [fn(1), fn(-1)]


# ---------------------------------------------------------------- 放样（躯干、裤管等）
def loft(name, rings, mat, seg=32, coll=None, cap_top=True, cap_bottom=True, smooth=True):
    """rings: [(z, rx, ry, cx, cy), ...] 由下到上，椭圆截面逐层放样。"""
    verts, faces = [], []
    for (z, rx, ry, cx, cy) in rings:
        for i in range(seg):
            a = TAU * i / seg
            verts.append((cx + rx * math.cos(a), cy + ry * math.sin(a), z))
    n = len(rings)
    for r in range(n - 1):
        for i in range(seg):
            a = r * seg + i
            b = r * seg + (i + 1) % seg
            c = (r + 1) * seg + (i + 1) % seg
            d = (r + 1) * seg + i
            faces.append((a, b, c, d))
    if cap_bottom:
        faces.append(tuple(range(seg - 1, -1, -1)))
    if cap_top:
        base = (n - 1) * seg
        faces.append(tuple(base + i for i in range(seg)))
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, [], faces)
    me.validate()
    me.update()
    ob = bpy.data.objects.new(name, me)
    me.materials.append(mat)
    (coll or bpy.context.scene.collection).objects.link(ob)
    if smooth:
        set_smooth(ob)
    return ob


def delete_verts_by(obj, pred):
    """删除满足条件的顶点（世界近似判断用 obj.matrix_world 前的局部坐标）。"""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    doomed = [v for v in bm.verts if pred(v.co)]
    bmesh.ops.delete(bm, geom=doomed, context="VERTS")
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()


def tube(name, pts, radii, mat, seg=20, coll=None, squash=1.0, caps=True, smooth=True):
    """沿任意折线放样管体（四肢、袖管），截面垂直于路径。radii 与 pts 等长。
    squash < 1 时把截面在“左右”方向压扁。"""
    pts = [Vector(p) for p in pts]
    n = len(pts)
    rings = []
    up_ref = Vector((0, 0, 1))
    for i, p in enumerate(pts):
        if i == 0:
            tangent = (pts[1] - pts[0])
        elif i == n - 1:
            tangent = (pts[-1] - pts[-2])
        else:
            tangent = (pts[i + 1] - pts[i - 1])
        tangent.normalize()
        ref = up_ref if abs(tangent.dot(up_ref)) < 0.95 else Vector((0, 1, 0))
        side = tangent.cross(ref).normalized()
        up = side.cross(tangent).normalized()
        ring = []
        for k in range(seg):
            a = TAU * k / seg
            off = side * (math.cos(a) * radii[i] * squash) + up * (math.sin(a) * radii[i])
            ring.append(tuple(p + off))
        rings.append(ring)

    verts, faces = [], []
    for ring in rings:
        verts.extend(ring)
    for r in range(n - 1):
        for i in range(seg):
            a = r * seg + i
            b = r * seg + (i + 1) % seg
            c = (r + 1) * seg + (i + 1) % seg
            d = (r + 1) * seg + i
            faces.append((a, b, c, d))
    if caps:
        faces.append(tuple(range(seg - 1, -1, -1)))
        base = (n - 1) * seg
        faces.append(tuple(base + i for i in range(seg)))
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, [], faces)
    me.validate()
    me.update()
    ob = bpy.data.objects.new(name, me)
    me.materials.append(mat)
    (coll or bpy.context.scene.collection).objects.link(ob)
    if smooth:
        set_smooth(ob)
    return ob




# ---------------------------------------------------------------- 高级：发丝、眼睑、鞋楦放样
def taper_curve(name, pts, radii, mat, coll=None, resolution=2):
    """带半径渐变的曲线（发丝、管线）。pts/radii 等长。"""
    cu = bpy.data.curves.new(name, type="CURVE")
    cu.dimensions = "3D"
    cu.resolution_u = 6
    cu.bevel_depth = 1.0
    cu.bevel_resolution = resolution
    cu.use_fill_caps = True
    sp = cu.splines.new("BEZIER")
    sp.bezier_points.add(len(pts) - 1)
    for i, (bp, co) in enumerate(zip(sp.bezier_points, pts)):
        bp.co = Vector(co)
        bp.handle_left_type = "AUTO"
        bp.handle_right_type = "AUTO"
        bp.radius = radii[i]
    ob = bpy.data.objects.new(name, cu)
    cu.materials.append(mat)
    (coll or bpy.context.scene.collection).objects.link(ob)
    return ob


def bezier_pts(p0, p1, p2, t_steps=5):
    """二次贝塞尔采样。"""
    out = []
    for i in range(t_steps):
        t = i / (t_steps - 1)
        a = (1 - t) ** 2
        b = 2 * (1 - t) * t
        c = t ** 2
        out.append((a * p0[0] + b * p1[0] + c * p2[0],
                    a * p0[1] + b * p1[1] + c * p2[1],
                    a * p0[2] + b * p1[2] + c * p2[2]))
    return out


def loft_y(name, rings, mat, seg=28, coll=None, caps=True, smooth=True):
    """沿 Y 轴放样（鞋楦）：rings = [(y, rx, rz, cx, cz), ...]"""
    verts, faces = [], []
    for (y, rx, rz, cx, cz) in rings:
        for i in range(seg):
            a = TAU * i / seg
            verts.append((cx + rx * math.cos(a), y, cz + rz * math.sin(a)))
    n = len(rings)
    for r in range(n - 1):
        for i in range(seg):
            a = r * seg + i
            b = r * seg + (i + 1) % seg
            c = (r + 1) * seg + (i + 1) % seg
            d = (r + 1) * seg + i
            faces.append((a, b, c, d))
    if caps:
        faces.append(tuple(range(seg - 1, -1, -1)))
        base = (n - 1) * seg
        faces.append(tuple(base + i for i in range(seg)))
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, [], faces)
    me.validate()
    me.update()
    ob = bpy.data.objects.new(name, me)
    me.materials.append(mat)
    (coll or bpy.context.scene.collection).objects.link(ob)
    if smooth:
        set_smooth(ob)
    return ob


def shell_from_sphere(name, loc, scale, mat, keep, coll=None, segs=32, rings=20):
    """从球体切出球壳片段（眼睑、兜帽片）。keep(co) 为 True 的顶点保留。"""
    o = sphere(name, loc, scale, mat, segs=segs, rings=rings, coll=coll)
    delete_verts_by(o, lambda co: not keep(co))
    sol = o.modifiers.new("Solid", "SOLIDIFY")
    sol.thickness = scale[0] * 0.12
    return o
