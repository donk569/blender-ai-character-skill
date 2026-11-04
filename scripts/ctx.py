"""上下文兜底：bpy.ops 需要 window，但 wm.open_mainfile 之后 bpy.context.window 会变成 None。

MCP / 脚本执行环境里这个坑很常见，表现为 `poll() 上下文缺失活动物体`。
所有需要 bpy.ops 的地方都应该包一层 ov()。
"""

import contextlib

import bpy


def view_layer():
    win = getattr(bpy.context, "window", None)
    if win is not None and getattr(win, "view_layer", None) is not None:
        return win.view_layer
    vl = getattr(bpy.context, "view_layer", None)
    if vl is not None:
        return vl
    return bpy.context.scene.view_layers[0]


def _window():
    win = getattr(bpy.context, "window", None)
    if win is not None:
        return win
    wins = list(bpy.context.window_manager.windows)
    return wins[0] if wins else None


@contextlib.contextmanager
def ov(active=None, objects=None):
    """在需要时构造上下文覆盖（仅在缺少 window 时生效）"""
    win = _window()
    vl = view_layer()
    if active is not None:
        try:
            vl.objects.active = active
        except Exception:
            pass
        try:
            active.select_set(True)
        except Exception:
            pass
    if win is None:
        yield
        return
    sc = bpy.context.scene
    sel = list(objects) if objects else ([active] if active is not None else [])
    try:
        with bpy.context.temp_override(window=win, scene=sc, view_layer=vl,
                                       active_object=active, object=active,
                                       selected_objects=sel,
                                       selected_editable_objects=sel):
            yield
    except TypeError:
        yield


def ensure_object_mode(active=None):
    if active is not None:
        try:
            view_layer().objects.active = active
            active.select_set(True)
        except Exception:
            pass
    if bpy.context.mode != "OBJECT":
        with ov(active=active):
            try:
                bpy.ops.object.mode_set(mode="OBJECT")
            except RuntimeError:
                pass


def enter_edit(active):
    view_layer().objects.active = active
    try:
        active.select_set(True)
    except Exception:
        pass
    with ov(active=active):
        bpy.ops.object.mode_set(mode="EDIT")


def leave_edit(active):
    with ov(active=active):
        bpy.ops.object.mode_set(mode="OBJECT")
