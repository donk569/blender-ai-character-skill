"""共用工具：C1 连续的单调三次插值（Fritsch-Carlson）
替换原来分段余弦缓动（每个控制点导数归零 -> 周期性微停顿）
"""
import math


def smooth_table(table, phase, cyclic=True):
    """单调三次 Hermite 插值。C1 连续，且不过冲（保持每段单调）。
    局部极值处切线为 0（物理上正确：抛体最高点速度为 0）。"""
    pts = sorted(table)
    xs = [p[0] for p in pts]
    vs = [p[1] for p in pts]
    n = len(vs)

    closed = False
    if cyclic and n > 3 and abs(xs[0]) < 1e-9 and abs(xs[-1] - 1.0) < 1e-9:
        # 首尾同相位的闭合表：丢掉末尾重复点
        xs = xs[:-1]
        vs = vs[:-1]
        n -= 1
        closed = True

    if n < 2:
        return vs[0]

    p = (phase % 1.0) if closed else max(xs[0], min(xs[-1], phase))

    def right_x(k):
        if k + 1 < n:
            return xs[k + 1]
        return xs[0] + 1.0 if closed else xs[-1]

    # 段索引
    i = n - 2
    for k in range(n - 1):
        if xs[k] <= p <= right_x(k):
            i = k
            break
    else:
        i = 0 if p <= xs[0] else n - 2

    x0, x1 = xs[i], right_x(i)
    y0, y1 = vs[i], vs[i + 1 if i + 1 < n else 0]
    h = max(1e-9, x1 - x0)
    t = max(0.0, min(1.0, (p - x0) / h))

    # 段斜率
    d = [(vs[(k + 1) % n] - vs[k]) / max(1e-9, right_x(k) - xs[k]) for k in range(n)]

    # 控制点切线（Fritsch-Carlson 限幅，保证不过冲）
    m = [0.0] * n
    for k in range(n):
        dm = d[(k - 1) % n] if (closed or k > 0) else d[0]
        dp = d[k] if (closed or k < n - 1) else d[-1]
        if dm * dp <= 0.0:
            m[k] = 0.0
        else:
            mk = 0.5 * (dm + dp)
            lim = 3.0 * min(abs(dm), abs(dp))
            if abs(mk) > lim:
                mk = math.copysign(lim, mk)
            m[k] = mk

    m0 = m[i] * h
    m1 = m[(i + 1) % n] * h
    t2 = t * t
    t3 = t2 * t
    return ((2 * t3 - 3 * t2 + 1) * y0 + (t3 - 2 * t2 + t) * m0
            + (-2 * t3 + 3 * t2) * y1 + (t3 - t2) * m1)
