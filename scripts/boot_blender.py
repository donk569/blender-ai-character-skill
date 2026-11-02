import bpy, sys, os

LOG = r"D:\桌面\blender建模测试\.workbuddy\blender\boot.txt"
def L(*a):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(" ".join(str(x) for x in a) + "\n")

L("boot start")

try:
    bpy.ops.preferences.addon_enable(module="blender_mcp")
    L("addon enabled")
except Exception as e:
    L("addon_enable failed:", e)

BLEND = r"D:\桌面\blender建模测试\cyberpunk_girl_anim_v5.blend"
if os.path.exists(BLEND):
    bpy.ops.wm.open_mainfile(filepath=BLEND)
    L("opened", BLEND)
else:
    L("blend missing")

mod = sys.modules.get("blender_mcp")
L("module:", mod)
try:
    if not getattr(bpy.types, "blendermcp_server", None):
        bpy.types.blendermcp_server = mod.BlenderMCPServer(port=9876)
    srv = bpy.types.blendermcp_server
    srv.start()
    L("server started, running =", srv.running, "port 9876")
except Exception as e:
    L("server start failed:", repr(e))

L("boot done")
