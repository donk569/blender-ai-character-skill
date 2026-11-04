"""按 spec 建骨架（任意体型），并把网格挂上去。"""

import bpy

try:
    from . import ctx
except ImportError:
    import ctx

_view_layer = ctx.view_layer



def bake_transform(mesh_obj):
    """把网格对象的变换烘焙进顶点数据，使 matrix_world 变成单位阵。

    骨骼坐标是按世界坐标算的，而 Armature 修改器在**网格局部空间**里套用骨骼变换。
    只要网格对象带缩放/旋转（AI 生成的模型经常是 1.445 倍缩放），
    两者就会错位 —— 表现为「绑定后模型缩水、变形完全不对」。
    """
    from mathutils import Matrix
    mw = mesh_obj.matrix_world.copy()
    ident = Matrix.Identity(4)
    if max(abs(mw[i][j] - ident[i][j]) for i in range(4) for j in range(4)) < 1e-6:
        return False
    flip = mw.to_3x3().determinant() < 0
    mesh_obj.data.transform(mw)
    mesh_obj.matrix_world = Matrix.Identity(4)
    if flip:
        me = mesh_obj.data
        me.flip_normals()
    return True


def build_armature(spec, name="Character_Rig"):
    sc = bpy.context.scene
    vl = ctx.view_layer()

    old = bpy.data.objects.get(name)
    if old:
        bpy.data.objects.remove(old, do_unlink=True)

    arm = bpy.data.armatures.new(name + "_Armature")
    rig = bpy.data.objects.new(name, arm)
    sc.collection.objects.link(rig)
    rig.location = (0, 0, 0)
    rig.rotation_euler = (0, 0, 0)
    rig.scale = (1, 1, 1)

    ctx.enter_edit(rig)

    eb = arm.edit_bones
    for bname, head, tail, parent, deform in spec["bones"]:
        b = eb.new(bname)
        b.head = head
        b.tail = tail
        b.use_deform = bool(deform)
        b.roll = 0.0
        if parent:
            b.parent = eb[parent]
            b.use_connect = (
                (spec["bone_head"].get(bname) is not None
                 and _close(spec["bone_head"][bname], spec["bone_head"].get(parent))
                 and _close(head, _tail_of(spec, parent)))
            )

    ctx.leave_edit(rig)
    rig.select_set(False)

    # 分组，方便在大纲里选
    try:
        groups = {
            "Body": [b for b in spec["key_bones"]
                     if any(k in b for k in ("hips", "spine", "chest", "neck", "head"))],
            "Arms": spec["arm_chain"] + [b for b in spec["key_bones"] if "arm" in b or "hand" in b],
            "Legs": spec["leg_chain"] + [b for b in spec["key_bones"] if "thigh" in b or "shin" in b or "foot" in b or "toe" in b],
            "Tail": spec["tail"],
            "Extras": [b for b in spec["key_bones"] if "ear" in b or "wing" in b],
        }
        for gname, names in groups.items():
            names = [n for n in dict.fromkeys(names) if arm.bones.get(n)]
            if not names:
                continue
            col = arm.collections.new(gname)
            for n in names:
                col.assign(arm.bones[n])
    except Exception:
        pass

    rig.show_in_front = True
    arm.display_type = "OCTAHEDRAL"
    return rig


def _tail_of(spec, bname):
    for n, h, t, p, d in spec["bones"]:
        if n == bname:
            return t
    return None


def _close(a, b, eps=1e-6):
    if a is None or b is None:
        return False
    return all(abs(float(x) - float(y)) < eps for x, y in zip(a, b))


def attach_mesh(mesh_obj, rig, add_modifier=True):
    """把网格挂到骨架；权重由 weights.py 单独写入

    先烘焙网格对象变换（AI 模型常带 1.4 倍缩放 + −90° 旋转），
    否则 Armature 修改器在局部空间套用世界坐标的骨骼 → 绑定后模型缩水、变形错乱。
    """
    bake_transform(mesh_obj)
    for m in list(mesh_obj.modifiers):
        if m.type == "ARMATURE":
            mesh_obj.modifiers.remove(m)
    mesh_obj.parent = rig
    mesh_obj.matrix_parent_inverse = rig.matrix_world.inverted()
    if add_modifier:
        mod = mesh_obj.modifiers.new("Armature", "ARMATURE")
        mod.object = rig
    return mesh_obj
