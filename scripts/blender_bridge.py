"""Blender 桥接：直接通过 9876 端口与 MCP for Blender 插件的 socket 服务通信。
用法:
  python blender_bridge.py scene
  python blender_bridge.py run <py文件路径>
"""
import json
import socket
import sys
import time

HOST, PORT = "127.0.0.1", 9876
BUF = 65536


def send_command(cmd_type, params=None, timeout=600):
    payload = {"type": cmd_type, "params": params or {}}
    s = socket.create_connection((HOST, PORT), timeout=timeout)
    try:
        s.sendall(json.dumps(payload).encode("utf-8"))
        chunks = []
        deadline = time.time() + timeout
        while time.time() < deadline:
            s.settimeout(max(1, deadline - time.time()))
            try:
                chunk = s.recv(BUF)
            except socket.timeout:
                break
            if not chunk:
                break
            chunks.append(chunk)
            data = b"".join(chunks)
            try:
                return json.loads(data.decode("utf-8", "replace"))
            except json.JSONDecodeError:
                if data.endswith(b"}") or data.endswith(b"}\n"):
                    continue
                continue
        raw = b"".join(chunks).decode("utf-8", "replace")
        return {"status": "error", "message": "no valid response", "raw": raw[:2000]}
    finally:
        s.close()


def run_file(path, timeout=600):
    code = open(path, encoding="utf-8").read()
    return send_command("execute_code", {"code": code}, timeout=timeout)


def brief(res, limit=3000):
    out = json.dumps(res, ensure_ascii=False)
    return out if len(out) <= limit else out[:limit] + f"... (共{len(out)}字符)"


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "scene"
    if mode == "scene":
        print(brief(send_command("get_scene_info")))
    elif mode == "run":
        t0 = time.time()
        res = run_file(sys.argv[2])
        print(brief(res))
        print(f"[耗时 {time.time()-t0:.1f}s]")
    else:
        print(brief(send_command(mode, json.loads(sys.argv[2]) if len(sys.argv) > 2 else {})))
