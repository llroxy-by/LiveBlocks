"""序界（LiveBlocks）实现包。

当前进度：

- LB-001：工程骨架、命令行入口与测试框架；
- LB-002：合成卡片图像生成器（:mod:`liveblocks.synth`），按卡片字典与版式定义生成
  ArUco 卡片图像 + 真值 JSON，**不做识别**。

识别、排列、解析、执行、界面按 ``docs/ROADMAP.md`` 的 LB-003 起逐条加入，并各自带测试。
"""

from __future__ import annotations

__version__ = "0.0.1"

__all__ = ["__version__"]
