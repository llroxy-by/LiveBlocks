"""时间来源：采集台只在一处取墙钟时间，测试可以注入固定时钟。

采集 id、``created_at``、``Frame.captured_at_ms`` 都来自这里。测试注入固定时钟后，
落盘目录名与 JSON 时间戳都是确定的——「同一输入 → 同一份样本」因此可以断言。
"""

from __future__ import annotations

from datetime import datetime, timezone


def utc_now() -> datetime:
    """当前 UTC 时间（带时区，序列化成 ``…Z``）。"""
    return datetime.now(timezone.utc)


__all__ = ["utc_now"]
