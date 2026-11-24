"""序界（LiveBlocks）实现包。

当前进度：

- LB-001：工程骨架、命令行入口与测试框架；
- LB-002：合成卡片图像生成器（:mod:`liveblocks.synth`），按卡片字典与版式定义生成
  ArUco 卡片图像 + 真值 JSON，**不做识别**；
- LB-003：识别模块（:mod:`liveblocks.vision`），标记检测 + 卡片字典映射 →
  ``MarkerCandidateList`` / ``CardCandidateList``（协议 §3），并给出准确率/漏检统计，
  **不做排序、解析、执行**；
- LB-004：排列层（:mod:`liveblocks.layout`），卡片候选 → ``Layout``（协议 §4：分簇、阅读方向
  排序、朝向归一、去重、歧义诊断），**不解析语法**；
- LB-005：语法层（:mod:`liveblocks.parser`），``Layout`` → ``Ast``（协议 §5），P0 最小文法
  「语句序列 + 字面参数」，错误报 ``LB-P0xx`` 并定位到行/槽位/卡片，**不求值、不执行**；
- LB-006：规范化层（:mod:`liveblocks.ir`），``Ast`` → ``Ir``（协议 §6），确定性输出、
  显式 ``limits``、op 表校验与入库 JSON Schema，问题报 ``LB-I0xx``，**不执行**。

执行器与界面按 ``docs/ROADMAP.md`` 的 LB-007 起逐条加入，并各自带测试。
"""

from __future__ import annotations

__version__ = "0.0.1"

__all__ = ["__version__"]
