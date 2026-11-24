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
  显式 ``limits``、op 表校验与入库 JSON Schema，问题报 ``LB-I0xx``，**不执行**；
- LB-007：模拟执行层（:mod:`liveblocks.runtime`），``Ir`` → ``ExecEvent[]`` + ``ExecResult``
  + 最终状态（协议 §7），虚拟时钟、来自 IR 的 ``limits``、步骤边界中断，问题报 ``LB-R0xx``；
  **只有 ``backend: "sim"``**，不接硬件、不做界面；
- LB-008：调试视图（:mod:`liveblocks.ui`），把一份 §8 运行记录（JSONL）渲染成**一屏五层**
  （帧叠加 / 排列 / AST / IR / 事件流 + 分层耗时），支持回放与单步；坏记录只报 ``LB-U0xx``
  可见提示而不崩溃。**只读**、只服务开发者，不做产品级 UI、不对外发布。

运行记录的**生产**（``RunHeader`` 打包与全链路落盘）仍属 ``docs/ROADMAP.md`` 的 LB-009，
``fixtures/runs/`` 里的记录目前由 ``scripts/build_run_records.py`` 把已有各层真跑一遍拼出来。
"""

from __future__ import annotations

__version__ = "0.0.1"

__all__ = ["__version__"]
