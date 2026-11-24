"""命令行入口。

- 骨架阶段（LB-001）：``--version``；
- LB-002：``synth`` 子命令组（``list`` / ``generate`` / ``verify``）；
- LB-003：``recognize`` 子命令组：

  - ``liveblocks recognize detect --image <png>``   输出符合 `docs/PROTOCOL.md` §3 的 JSON
  - ``liveblocks recognize report``                 对 fixtures 目录跑识别并打印准确率/漏检统计

- LB-004：``layout`` 子命令组：

  - ``liveblocks layout arrange --candidates <json>``  卡片候选 → 符合 §4 的 ``Layout``
  - ``liveblocks layout from-image --image <png>``     一步「识别 → 排列」（调试用）
  - ``liveblocks layout report``                       对 ``fixtures/layouts`` 跑排列并打印验收对照

- LB-005：``parse`` 子命令组：

  - ``liveblocks parse ast --layout <json>``   ``Layout`` → 符合 §5 的 ``Ast``（P0 最小文法）
  - ``liveblocks parse report``                对 ``fixtures/ast`` 跑解析并打印验收对照

- LB-006：``ir`` 子命令组：

  - ``liveblocks ir normalize --ast <json>``   ``Ast`` → 符合 §6 的 ``Ir``（确定性、显式 ``limits``）
  - ``liveblocks ir report``                   对 ``fixtures/ir`` 跑规范化并与 golden 快照比对

- LB-007：``exec`` 子命令组（模拟后端，``backend: "sim"``）：

  - ``liveblocks exec run --ir <json>``   ``Ir`` → 事件流（§7.1）+ ``ExecResult``（§7.2）+ 最终状态 JSON
  - ``liveblocks exec report``            对 ``fixtures/exec`` 真跑一遍并打印验收对照（含实测耗时）

- LB-009：``e2e`` 子命令组（端到端回归与运行记录）：

  - ``liveblocks e2e run --scene all --out runs/e2e``  一条命令跑「生成 → 识别 → 排列 → 解析 →
    IR → 模拟」全链路，把 §8 运行记录（JSONL：``RunHeader`` + 逐层对象 + 事件）落盘
  - ``liveblocks e2e regression --dir fixtures/e2e``   全场景回归：记录形状校验 + 黄金基准比对 +
    破坏注入自检 + 固定时间上界；失败时打印失败摘要并点名到「最早失败层」

- LB-008：``ui`` 子命令组（调试视图，**只读**消费 §8 运行记录）：

  - ``liveblocks ui view --record <jsonl>``    一屏五层视图（文本 / 自包含 HTML / 叠加 PNG）
  - ``liveblocks ui replay --record <jsonl>``  回放：打印时间线并核对「两次回放一致」
  - ``liveblocks ui report --dir fixtures/runs`` 对运行记录夹具跑一遍并打印验收对照

- LB-030：``serve-cam`` 子命令组（摄像头采集与人工辅助测试台，零新增依赖）：

  - ``liveblocks serve-cam serve``   一条命令起本地服务（页面 getUserMedia 选摄像头、单帧/连续
    取帧、``/api/frame`` 出 §3 候选与叠加图、``/api/capture`` 落盘到 ``fixtures/live/<时间戳>/``、
    ``/api/captures`` 列样本，「这帧识别错了」的样本单独进 ``fixtures/live/refused/``）
  - ``liveblocks serve-cam page``    把自包含页面导出成单个 HTML（断网可打开，便于取证）

``--version`` 与 ``python -m liveblocks --version`` 的行为保持不变。
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from . import __version__

PROG = "liveblocks"

#: fixtures 的默认随机种子（改它会让入库 fixtures 全部失效，需同时重新生成）。
DEFAULT_SEED = 20261123
DEFAULT_SCENE_DIR = Path("fixtures/scenes")
#: LB-004 的候选夹具目录（``CardCandidateList`` JSON，见 ``fixtures/README.md`` §4）。
DEFAULT_LAYOUT_DIR = Path("fixtures/layouts")
#: LB-005 的语法夹具目录（``Layout`` JSON + 期望结果清单，见 ``fixtures/README.md`` §5）。
DEFAULT_AST_DIR = Path("fixtures/ast")
#: LB-006 的 IR 夹具目录（``Ast`` JSON + golden ``Ir`` + 清单，见 ``fixtures/README.md`` §6）。
DEFAULT_IR_DIR = Path("fixtures/ir")
#: LB-007 的执行夹具目录（``Ir`` JSON + golden 事件流/最终状态 + 清单，见 ``fixtures/README.md`` §7）。
DEFAULT_EXEC_DIR = Path("fixtures/exec")
#: LB-008 的运行记录目录（§8 JSONL + 清单，见 ``fixtures/README.md`` §8）。
DEFAULT_RUN_DIR = Path("fixtures/runs")
#: LB-009 的黄金基准目录（``baseline.json``，见 ``fixtures/README.md`` §9）。
DEFAULT_E2E_DIR = Path("fixtures/e2e")
#: LB-009 的运行记录落盘目录（不入库：``timings_ms`` 是实测墙钟，每跑一次都不一样）。
DEFAULT_E2E_OUT = Path("runs/e2e")
#: LB-030 采集台的落盘根目录（每个样本一个子目录；``refused/`` 下是「识别错了」的样本）。
#: 不入库：实拍样本是人现场采的，一采集就变（见 ``fixtures/README.md`` §10）。
DEFAULT_LIVE_DIR = Path("fixtures/live")
#: LB-030 本地服务的默认端口（只绑 127.0.0.1，不对外）。
DEFAULT_SERVE_CAM_PORT = 8765


def build_parser() -> argparse.ArgumentParser:
    """构造命令行解析器。"""
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="序界（LiveBlocks）命令行入口：版本信息 + 合成卡片图像生成器（LB-002）。",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="打印版本号后退出",
    )

    subparsers = parser.add_subparsers(dest="command")

    synth = subparsers.add_parser(
        "synth",
        help="合成卡片图像生成器（LB-002）：生成虚拟卡片图像与真值 JSON",
        description="按卡片字典与版式定义生成 ArUco 卡片图像；不做识别。",
    )
    synth_sub = synth.add_subparsers(dest="synth_command", required=True)

    generate = synth_sub.add_parser("generate", help="生成图像 + 真值 JSON")
    generate.add_argument(
        "--scene",
        default="all",
        help="场景预设名，逗号分隔；all 表示全部预设（默认 all）",
    )
    generate.add_argument("--seed", type=int, default=DEFAULT_SEED, help=f"随机种子（默认 {DEFAULT_SEED}）")
    generate.add_argument("--out", type=Path, default=DEFAULT_SCENE_DIR, help="输出目录（默认 fixtures/scenes）")
    generate.set_defaults(handler=_cmd_synth_generate)

    listing = synth_sub.add_parser("list", help="列出场景预设、卡面版式与卡片字典")
    listing.set_defaults(handler=_cmd_synth_list)

    verify = synth_sub.add_parser("verify", help="重生成并与目录内已入库的图像/真值比对")
    verify.add_argument("--dir", type=Path, default=DEFAULT_SCENE_DIR, help="fixtures 目录（默认 fixtures/scenes）")
    verify.add_argument(
        "--seed",
        type=int,
        default=None,
        help="强制使用该种子重生成（默认用真值里记录的种子）",
    )
    verify.set_defaults(handler=_cmd_synth_verify)

    recognize = subparsers.add_parser(
        "recognize",
        help="识别模块（LB-003）：ArUco 标记候选 → 卡片候选",
        description="对一张灰度图做标记检测与卡片字典映射，输出协议 §3 的候选列表 JSON。",
    )
    recognize_sub = recognize.add_subparsers(dest="recognize_command", required=True)

    detect = recognize_sub.add_parser("detect", help="识别单张图片，输出候选列表 JSON")
    detect.add_argument("--image", type=Path, required=True, help="输入 PNG（8bit 灰度）")
    detect.add_argument("--frame-id", default=None, help="帧标识（默认由文件名推导 f-<主干>）")
    detect.add_argument(
        "--card-layout",
        default=None,
        help="采集用的卡面版式（默认取 synth_layouts.json 的 default_card_layout）",
    )
    detect.add_argument(
        "--card-dictionary",
        type=Path,
        default=None,
        help="替换卡片字典文件（格式同 data/card_dictionary.json，用于验证 unknown_card）",
    )
    detect.add_argument(
        "--emit",
        choices=("cards", "markers"),
        default="cards",
        help="输出哪个对象：cards（§3.2，默认）或 markers（§3.1）",
    )
    detect.add_argument(
        "--intrinsics",
        default=None,
        help="相机内参 fx,fy,cx,cy；给了才会在 markers 里输出 pose",
    )
    detect.add_argument("--source", default="synthetic", help="帧来源（synthetic|camera|replay）")
    detect.add_argument("--out", type=Path, default=None, help="写到文件；默认打印到标准输出")
    detect.set_defaults(handler=_cmd_recognize_detect)

    report = recognize_sub.add_parser(
        "report", help="对 fixtures 目录跑识别并输出准确率 / 漏检统计"
    )
    report.add_argument("--dir", type=Path, default=DEFAULT_SCENE_DIR, help="场景目录（默认 fixtures/scenes）")
    report.add_argument(
        "--card-layout",
        default=None,
        help="强制使用该卡面版式（默认按每个场景真值里的 card_layout，即采集配置）",
    )
    report.add_argument("--json", type=Path, default=None, help="把统计另存为 JSON")
    report.add_argument(
        "--no-intrinsics",
        action="store_true",
        help="不把真值里的相机内参当作采集配置传入（则输出不含 pose）",
    )
    report.set_defaults(handler=_cmd_recognize_report)

    layout = subparsers.add_parser(
        "layout",
        help="排列层（LB-004）：卡片候选 → Layout",
        description="把 §3.2 的 CardCandidateList 还原成协议 §4 的 Layout（分簇/排序/朝向归一/去重/歧义诊断）。",
    )
    layout_sub = layout.add_subparsers(dest="layout_command", required=True)

    arrange = layout_sub.add_parser(
        "arrange", help="对一份 CardCandidateList JSON 做排列，输出 Layout JSON"
    )
    arrange.add_argument(
        "--candidates", type=Path, required=True, help="输入 CardCandidateList JSON（协议 §3.2）"
    )
    _add_layout_arguments(arrange)
    arrange.add_argument("--out", type=Path, default=None, help="写到文件；默认打印到标准输出")
    arrange.set_defaults(handler=_cmd_layout_arrange)

    from_image = layout_sub.add_parser(
        "from-image", help="先识别一张图片，再直接排列成 Layout（调试用的一步到位）"
    )
    from_image.add_argument("--image", type=Path, required=True, help="输入 PNG（8bit 灰度）")
    from_image.add_argument("--frame-id", default=None, help="帧标识（默认由文件名推导 f-<主干>）")
    from_image.add_argument(
        "--card-layout",
        default=None,
        help="采集用的卡面版式（默认取 synth_layouts.json 的 default_card_layout）",
    )
    from_image.add_argument("--source", default="synthetic", help="帧来源（synthetic|camera|replay）")
    _add_layout_arguments(from_image)
    from_image.add_argument("--out", type=Path, default=None, help="写到文件；默认打印到标准输出")
    from_image.set_defaults(handler=_cmd_layout_from_image)

    layout_report = layout_sub.add_parser(
        "report", help="对候选夹具目录跑排列并打印行/槽位/诊断与验收对照"
    )
    layout_report.add_argument(
        "--dir", type=Path, default=DEFAULT_LAYOUT_DIR, help="候选夹具目录（默认 fixtures/layouts）"
    )
    _add_layout_arguments(layout_report)
    layout_report.add_argument("--json", type=Path, default=None, help="把报告另存为 JSON")
    layout_report.set_defaults(handler=_cmd_layout_report)

    parse = subparsers.add_parser(
        "parse",
        help="语法层（LB-005）：Layout → AST",
        description="把协议 §4 的 Layout 读成 §5 的 Ast（P0 最小文法：语句序列 + 字面参数）；不求值、不执行。",
    )
    parse_sub = parse.add_subparsers(dest="parse_command", required=True)

    ast = parse_sub.add_parser("ast", help="对一份 Layout JSON 做解析，输出 §5 的 Ast JSON")
    ast.add_argument("--layout", type=Path, required=True, help="输入 Layout JSON（协议 §4）")
    ast.add_argument("--out", type=Path, default=None, help="写到文件；默认打印到标准输出")
    ast.add_argument(
        "--fail-on-error",
        action="store_true",
        help="出现 LB-P0xx 语法诊断时以退出码 1 结束（默认 0：诊断随 Ast 一起输出）",
    )
    ast.set_defaults(handler=_cmd_parse_ast)

    parse_report = parse_sub.add_parser(
        "report", help="对语法夹具目录跑解析并打印逐条结果与验收对照"
    )
    parse_report.add_argument(
        "--dir", type=Path, default=DEFAULT_AST_DIR, help="语法夹具目录（默认 fixtures/ast）"
    )
    parse_report.add_argument("--json", type=Path, default=None, help="把报告另存为 JSON")
    parse_report.set_defaults(handler=_cmd_parse_report)

    ir = subparsers.add_parser(
        "ir",
        help="规范化层（LB-006）：Ast → IR",
        description="把协议 §5 的 Ast 规范化成 §6 的 Ir（确定性、显式 limits）；不执行。",
    )
    ir_sub = ir.add_subparsers(dest="ir_command", required=True)

    normalize = ir_sub.add_parser("normalize", help="对一份 Ast JSON 做规范化，输出 §6 的 Ir JSON")
    normalize.add_argument("--ast", type=Path, required=True, help="输入 Ast JSON（协议 §5）")
    normalize.add_argument("--out", type=Path, default=None, help="写到文件；默认打印到标准输出")
    normalize.add_argument(
        "--max-steps",
        type=int,
        default=None,
        help="显式写进 IR 的步数上限（缺省取 §6 示例的 1000）",
    )
    normalize.add_argument(
        "--timeout-ms",
        type=int,
        default=None,
        help="显式写进 IR 的超时上限（毫秒，缺省取 §6 示例的 5000）",
    )
    normalize.add_argument(
        "--fail-on-error",
        action="store_true",
        help="出现 LB-I0xx 规范化诊断时以退出码 1 结束（默认 0：诊断随 Ir 一起输出）",
    )
    normalize.set_defaults(handler=_cmd_ir_normalize)

    ir_report = ir_sub.add_parser(
        "report", help="对 IR 夹具目录跑规范化，并与 golden 快照逐字节比对"
    )
    ir_report.add_argument(
        "--dir", type=Path, default=DEFAULT_IR_DIR, help="IR 夹具目录（默认 fixtures/ir）"
    )
    ir_report.add_argument("--json", type=Path, default=None, help="把报告另存为 JSON")
    ir_report.set_defaults(handler=_cmd_ir_report)

    execute = subparsers.add_parser(
        "exec",
        help="模拟执行层（LB-007）：Ir → 事件流 + ExecResult",
        description=(
            "用模拟后端执行协议 §6 的 Ir：虚拟时钟、来自 Ir.limits 的步数/超时限制、步骤边界中断；"
            "事件一律标 backend=sim。不接硬件、不做界面。"
        ),
    )
    exec_sub = execute.add_subparsers(dest="exec_command", required=True)

    exec_run = exec_sub.add_parser(
        "run", help="执行一份 Ir JSON，打印摘要 + 最终状态 JSON（可另存事件流与结果）"
    )
    exec_run.add_argument("--ir", type=Path, required=True, help="输入 Ir JSON（协议 §6）")
    exec_run.add_argument("--run-id", default=None, help="运行标识（默认 run-<文件名主干>）")
    exec_run.add_argument(
        "--events-out",
        type=Path,
        default=None,
        help="把事件流按 §8 风格写成 JSONL（一行一个 ExecEvent）",
    )
    exec_run.add_argument("--result-out", type=Path, default=None, help="把 ExecResult 写成 JSON")
    exec_run.add_argument(
        "--state-out",
        type=Path,
        default=None,
        help="把最终状态（ExecState）写成 JSON；不给则打印到标准输出",
    )
    exec_run.add_argument(
        "--abort-at-step",
        type=int,
        default=None,
        help="执行到第 N 步之前中断（确定性中断，验收「中断」用）",
    )
    exec_run.add_argument(
        "--abort-at-ms",
        type=float,
        default=None,
        help="虚拟时钟走到 N 毫秒后、下一步之前中断",
    )
    exec_run.set_defaults(handler=_cmd_exec_run)

    exec_report = exec_sub.add_parser(
        "report", help="对执行夹具目录真跑一遍，打印逐条结果、验收对照与实测耗时"
    )
    exec_report.add_argument(
        "--dir", type=Path, default=DEFAULT_EXEC_DIR, help="执行夹具目录（默认 fixtures/exec）"
    )
    exec_report.add_argument("--json", type=Path, default=None, help="把报告另存为 JSON")
    exec_report.set_defaults(handler=_cmd_exec_report)

    # 破坏注入的目标清单只有一个出处（``liveblocks.e2e.protocol``），避免 CLI 与实现漂移。
    from .e2e.protocol import CORRUPT_TARGETS as E2E_CORRUPT_TARGETS

    e2e = subparsers.add_parser(
        "e2e",
        help="端到端回归（LB-009）：一条命令跑全链路并落盘 §8 运行记录",
        description=(
            "跑「生成 → 识别 → 排列 → 解析 → IR → 模拟」全链路，把 §8 运行记录写成 JSONL；"
            "「回归」子命令另外做记录形状校验、黄金基准比对、破坏注入自检与固定时间上界。"
        ),
    )
    e2e_sub = e2e.add_subparsers(dest="e2e_command", required=True)

    e2e_run = e2e_sub.add_parser(
        "run", help="一条命令跑全链路，把运行记录写到 --out（默认 runs/e2e）"
    )
    e2e_run.add_argument(
        "--scene", default="all", help="场景预设名，逗号分隔；all 表示全部预设（默认 all）"
    )
    e2e_run.add_argument("--seed", type=int, default=DEFAULT_SEED, help=f"随机种子（默认 {DEFAULT_SEED}）")
    e2e_run.add_argument(
        "--out", type=Path, default=DEFAULT_E2E_OUT, help=f"记录输出目录（默认 {DEFAULT_E2E_OUT}）"
    )
    e2e_run.add_argument(
        "--corrupt",
        choices=E2E_CORRUPT_TARGETS,
        default="none",
        help="破坏注入（只用于证明回归不是假通过）：scene-image 抹平生成图，层名删该层一个必填字段",
    )
    e2e_run.set_defaults(handler=_cmd_e2e_run)

    e2e_regression = e2e_sub.add_parser(
        "regression",
        help="全场景回归：形状校验 + 黄金基准比对 + 破坏注入自检 + 固定时间上界",
    )
    e2e_regression.add_argument(
        "--dir", type=Path, default=DEFAULT_E2E_DIR, help=f"黄金基准目录（默认 {DEFAULT_E2E_DIR}）"
    )
    e2e_regression.add_argument(
        "--out", type=Path, default=DEFAULT_E2E_OUT, help=f"记录输出目录（默认 {DEFAULT_E2E_OUT}）"
    )
    e2e_regression.add_argument(
        "--scene",
        default=None,
        help="只跑这些场景（逗号分隔；默认跑基准里的全部场景）",
    )
    e2e_regression.add_argument(
        "--seed", type=int, default=None, help=f"随机种子（默认用基准里记的 {DEFAULT_SEED}）"
    )
    e2e_regression.add_argument(
        "--corrupt",
        choices=E2E_CORRUPT_TARGETS,
        default="none",
        help="对全部场景注入同一种破坏（期望回归失败并点名到层）",
    )
    e2e_regression.add_argument(
        "--update-baseline",
        action="store_true",
        help="用当前代码重跑并重写 --dir/baseline.json（黄金基准）",
    )
    e2e_regression.add_argument(
        "--no-self-check",
        action="store_true",
        help="不做破坏注入自检（默认会跑一次，证明「破坏会被抓到」）",
    )
    e2e_regression.add_argument(
        "--no-write",
        action="store_true",
        help="只比对，不把运行记录写到 --out",
    )
    e2e_regression.add_argument("--json", type=Path, default=None, help="把报告另存为 JSON")
    e2e_regression.set_defaults(handler=_cmd_e2e_regression)

    ui = subparsers.add_parser(
        "ui",
        help="调试界面（LB-008）：把 §8 运行记录渲染成一屏分层视图（只读）",
        description=(
            "把一份 §8 运行记录（JSONL）渲染成一屏五层视图：帧叠加、排列、AST、IR、事件流与分层耗时；"
            "支持回放与单步。界面只读，不改任何业务数据。"
        ),
    )
    ui_sub = ui.add_subparsers(dest="ui_command", required=True)

    ui_view = ui_sub.add_parser("view", help="渲染一屏视图（默认打印文本形态）")
    ui_view.add_argument("--record", type=Path, required=True, help="§8 运行记录（JSONL）")
    ui_view.add_argument(
        "--step",
        type=int,
        default=None,
        help="单步位置：处理完前 N 条 ExecEvent（默认末帧；越界自动夹紧）",
    )
    ui_view.add_argument(
        "--format",
        choices=("text", "html", "none"),
        default="text",
        help="打印哪种形态到标准输出（默认 text；html 供浏览器打开）",
    )
    ui_view.add_argument("--html-out", type=Path, default=None, help="把自包含 HTML 写到文件")
    ui_view.add_argument("--text-out", type=Path, default=None, help="把文本形态写到文件")
    ui_view.add_argument("--overlay-out", type=Path, default=None, help="把帧叠加面板的 PNG 写到文件")
    ui_view.add_argument(
        "--fail-on-error",
        action="store_true",
        help="记录里有 error 级问题（LB-U0xx）时以退出码 1 结束（默认 0：照常渲染）",
    )
    ui_view.set_defaults(handler=_cmd_ui_view)

    ui_replay = ui_sub.add_parser("replay", help="回放：打印时间线并核对两次回放是否一致")
    ui_replay.add_argument("--record", type=Path, required=True, help="§8 运行记录（JSONL）")
    ui_replay.add_argument("--step", type=int, default=None, help="只看某一帧（默认打印整条时间线）")
    ui_replay.add_argument("--max-steps", type=int, default=40, help="时间线最多打印多少帧（默认 40）")
    ui_replay.set_defaults(handler=_cmd_ui_replay)

    ui_report = ui_sub.add_parser("report", help="对运行记录夹具目录跑一遍，打印验收对照")
    ui_report.add_argument(
        "--dir", type=Path, default=DEFAULT_RUN_DIR, help="运行记录目录（默认 fixtures/runs）"
    )
    ui_report.add_argument("--json", type=Path, default=None, help="把报告另存为 JSON")
    ui_report.set_defaults(handler=_cmd_ui_report)

    serve_cam = subparsers.add_parser(
        "serve-cam",
        help="摄像头采集台（LB-030）：一条命令起本地服务 + 浏览器页面",
        description=(
            "起一个只跑在本机的采集服务：页面用 getUserMedia 选任意摄像头，单帧/连续取帧，"
            "POST /api/frame 返回 §3 候选与识别叠加图，POST /api/capture 把帧与人工标注落成 "
            "fixtures/live/<时间戳>/（recognize report 可直接消费）。零新增依赖。"
        ),
    )
    serve_cam_sub = serve_cam.add_subparsers(dest="serve_cam_command", required=True)

    serve_cmd = serve_cam_sub.add_parser(
        "serve", help="起本地服务（打印地址后用浏览器打开；Ctrl-C 停止）"
    )
    serve_cmd.add_argument("--host", default="127.0.0.1", help="绑定地址（默认 127.0.0.1；0.0.0.0 会暴露到局域网）")
    serve_cmd.add_argument("--port", type=int, default=DEFAULT_SERVE_CAM_PORT, help=f"端口（默认 {DEFAULT_SERVE_CAM_PORT}；0 = 由内核分配）")
    serve_cmd.add_argument(
        "--live-dir", type=Path, default=DEFAULT_LIVE_DIR, help=f"采集根目录（默认 {DEFAULT_LIVE_DIR}）"
    )
    serve_cmd.add_argument(
        "--card-layout", default=None, help="默认卡面版式（默认取 synth_layouts.json 的 default_card_layout）"
    )
    serve_cmd.add_argument("--verbose", action="store_true", help="把每个请求都打到 stderr（默认只打 4xx/5xx）")
    serve_cmd.add_argument(
        "--print-url-only",
        action="store_true",
        help="只打印将要使用的地址与采集目录后退出（不绑定端口；用于脚本与自检）",
    )
    serve_cmd.set_defaults(handler=_cmd_serve_cam_serve)

    page_cmd = serve_cam_sub.add_parser(
        "page", help="把自包含页面导出成单个 HTML（断网可打开，用于验收③取证）"
    )
    page_cmd.add_argument("--out", type=Path, default=None, help="写到文件；默认打印到标准输出")
    page_cmd.set_defaults(handler=_cmd_serve_cam_page)

    return parser


def _resolve_scenes(requested: str) -> list[str]:
    from .synth import SynthContext

    ctx = SynthContext.load()
    available = sorted(ctx.presets)
    if requested.strip() == "all":
        return available
    names = [part.strip() for part in requested.split(",") if part.strip()]
    if not names:
        raise SystemExit("--scene 不能为空；用 all 或逗号分隔的预设名")
    unknown = [name for name in names if name not in ctx.presets]
    if unknown:
        raise SystemExit(
            f"未知场景预设：{', '.join(unknown)}；可用：{', '.join(available)}"
        )
    return names


def _add_layout_arguments(parser: argparse.ArgumentParser) -> None:
    """``layout`` 子命令共用的排列参数（比例阈值都相对「典型卡距」，见 D-015）。"""
    parser.add_argument(
        "--reading-direction",
        choices=("ltr_ttb", "rtl_ttb", "ttb_ltr"),
        default="ltr_ttb",
        help="阅读方向（默认 ltr_ttb：左上→右下）",
    )
    parser.add_argument(
        "--pitch-px",
        type=float,
        default=None,
        help="显式指定典型卡距（像素）；默认由候选集自适应估计",
    )
    parser.add_argument(
        "--duplicate-gap-ratio",
        type=float,
        default=None,
        help="重复判定阈值 / 卡距（默认 0.35）",
    )
    parser.add_argument(
        "--row-gap-ratio",
        type=float,
        default=None,
        help="行切分阈值 / 卡距（默认 0.75）",
    )
    parser.add_argument(
        "--slot-gap-ratio",
        type=float,
        default=None,
        help="行内间距歧义阈值 / 卡距（默认 0.6）",
    )


def _layout_options(args: argparse.Namespace):
    from .layout import LayoutOptions

    defaults = LayoutOptions()
    return LayoutOptions(
        reading_direction=args.reading_direction,
        pitch_px=args.pitch_px,
        duplicate_gap_ratio=(
            defaults.duplicate_gap_ratio
            if args.duplicate_gap_ratio is None
            else args.duplicate_gap_ratio
        ),
        row_gap_ratio=defaults.row_gap_ratio if args.row_gap_ratio is None else args.row_gap_ratio,
        slot_gap_ratio=defaults.slot_gap_ratio if args.slot_gap_ratio is None else args.slot_gap_ratio,
    )


def _cmd_synth_generate(args: argparse.Namespace) -> int:
    from .synth import SynthContext, generate_scene, write_index, write_scene

    ctx = SynthContext.load()
    names = _resolve_scenes(args.scene)
    scenes = [generate_scene(name, seed=args.seed, context=ctx) for name in names]
    for scene in scenes:
        image_path, truth_path = write_scene(args.out, scene)
        print(
            f"生成 {scene.name}: {image_path.name} ({scene.width}x{scene.height}, "
            f"{len(scene.image_png)} 字节, {len(scene.ground_truth['cards'])} 张卡) + {truth_path.name}"
        )
    if set(names) == set(ctx.presets):
        index_path = write_index(args.out, scenes, seed=args.seed)
        print(f"写入索引: {index_path}")
    print(f"种子 {args.seed}，输出目录 {args.out}")
    return 0


def _cmd_synth_list(args: argparse.Namespace) -> int:
    del args
    from .synth import SynthContext

    ctx = SynthContext.load()
    print(f"卡片字典 {ctx.card_dictionary.version}（{ctx.card_dictionary.aruco_dictionary}）：")
    for card in ctx.card_dictionary.cards:
        print(f"  {card.card_id:<4} marker_id={card.marker_id:<3} {card.card_type:<9} {card.card_face}")
    print("")
    print("卡面版式：")
    for name, layout in sorted(ctx.layouts.items()):
        print(
            f"  {name:<12} {layout.image_width_mm:g}x{layout.image_height_mm:g} mm，"
            f"标记 {layout.marker_size_mm:g} mm，{layout.pixels_per_mm} px/mm"
        )
    print("")
    print("场景预设：")
    for name in sorted(ctx.presets):
        spec = ctx.presets[name]
        print(f"  {name:<16} {spec.width}x{spec.height}，{len(spec.placements)} 张卡 — {spec.description}")
    return 0


def _cmd_synth_verify(args: argparse.Namespace) -> int:
    from .synth import SynthContext
    from .synth.generator import verify_directory

    ctx = SynthContext.load()
    seeds = None
    if args.seed is not None:
        seeds = {name: args.seed for name in ctx.presets}
    rows = verify_directory(args.dir, seeds=seeds, context=ctx)
    if not rows:
        print("没有可验证的场景")
        return 1
    failures = 0
    for row in rows:
        flag = "OK  " if row.ok else "FAIL"
        if not row.ok:
            failures += 1
        detail = f"字节={'同' if row.image_ok else '异'} 像素={'同' if row.pixel_ok else '异'} 真值={'同' if row.ground_truth_ok else '异'}"
        suffix = f" — {row.note}" if row.note else ""
        print(f"{flag} {row.name:<16} seed={row.seed:<10} {detail}{suffix}")
    print(f"\n{len(rows) - failures}/{len(rows)} 个场景与入库 fixtures 完全一致")
    return 0 if failures == 0 else 1


def _parse_intrinsics(text: str):
    from .vision import Intrinsics

    parts = [part.strip() for part in text.split(",") if part.strip()]
    if len(parts) != 4:
        raise SystemExit("--intrinsics 需要 4 个数：fx,fy,cx,cy")
    try:
        fx, fy, cx, cy = (float(part) for part in parts)
    except ValueError as error:
        raise SystemExit(f"--intrinsics 解析失败：{error}") from error
    return Intrinsics(fx=fx, fy=fy, cx=cx, cy=cy)


def _build_registry(card_layout: str | None, card_dictionary: Path | None):
    from .vision import CardRegistry
    from .vision.cards import load_card_dictionary_file

    dictionary = None
    if card_dictionary is not None:
        dictionary = load_card_dictionary_file(card_dictionary)
    return CardRegistry.load(card_dictionary=dictionary, capture_layout=card_layout)


def _cmd_recognize_detect(args: argparse.Namespace) -> int:
    import json

    from .vision import recognize_path

    if not args.image.is_file():
        raise SystemExit(f"找不到图像：{args.image}")
    registry = _build_registry(args.card_layout, args.card_dictionary)
    intrinsics = _parse_intrinsics(args.intrinsics) if args.intrinsics else None
    result = recognize_path(
        args.image,
        registry=registry,
        frame_id=args.frame_id,
        source=args.source,
        intrinsics=intrinsics,
    )
    document = (result.cards if args.emit == "cards" else result.markers).to_dict()
    text = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
    if args.out is None:
        print(text, end="")
        return 0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    kind = document["kind"]
    count = len(document["cards"]) if args.emit == "cards" else len(document["markers"])
    print(f"写入 {args.out}（{kind}，{count} 项，frame_id={document['frame_id']}）")
    return 0


def _cmd_recognize_report(args: argparse.Namespace) -> int:
    import json

    from .vision import evaluate_directory, format_report

    registry = _build_registry(args.card_layout, None)
    report = evaluate_directory(
        args.dir,
        registry=registry,
        layout_override=args.card_layout,
        intrinsics_from_truth=not args.no_intrinsics,
    )
    if not report.scenes:
        print(f"{args.dir} 里没有可评估的合成场景真值")
        return 1
    text = format_report(report)
    print(text, end="")
    if args.json is not None:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"统计已写入 {args.json}")
    return 0


def _cmd_layout_arrange(args: argparse.Namespace) -> int:
    import json

    from .layout import layout_from_document

    if not args.candidates.is_file():
        raise SystemExit(f"找不到候选文件：{args.candidates}")
    document = json.loads(args.candidates.read_text(encoding="utf-8"))
    if document.get("kind") != "CardCandidateList":
        raise SystemExit(
            f"{args.candidates} 的 kind 是 {document.get('kind')!r}，这里需要 CardCandidateList（协议 §3.2）"
        )
    layout = layout_from_document(document, options=_layout_options(args))
    text = json.dumps(layout.to_dict(), ensure_ascii=False, indent=2) + "\n"
    if args.out is None:
        print(text, end="")
        return 0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    print(
        f"写入 {args.out}（Layout，行 {len(layout.rows)}，槽位 {len(layout.slots)}，"
        f"诊断 {len(layout.diagnostics)} 条，frame_id={layout.frame_id}）"
    )
    return 0


def _cmd_layout_from_image(args: argparse.Namespace) -> int:
    import json

    from .layout import layout_from_card_list
    from .vision import recognize_path

    if not args.image.is_file():
        raise SystemExit(f"找不到图像：{args.image}")
    registry = _build_registry(args.card_layout, None)
    result = recognize_path(
        args.image, registry=registry, frame_id=args.frame_id, source=args.source
    )
    layout = layout_from_card_list(result.cards, options=_layout_options(args))
    text = json.dumps(layout.to_dict(), ensure_ascii=False, indent=2) + "\n"
    if args.out is None:
        print(text, end="")
        return 0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    print(
        f"写入 {args.out}（Layout，候选 {len(result.cards.cards)} → 行 {len(layout.rows)} / "
        f"槽位 {len(layout.slots)}，诊断 {len(layout.diagnostics)} 条）"
    )
    return 0


def _cmd_layout_report(args: argparse.Namespace) -> int:
    import json

    from .layout import build_report

    report = build_report(args.dir, options=_layout_options(args))
    if not report.fixtures:
        print(f"{args.dir} 里没有 CardCandidateList 夹具")
        return 1
    print(report.format(), end="")
    if args.json is not None:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"报告已写入 {args.json}")
    return 0 if all(ok for ok, _criterion, _evidence in report.acceptance()) else 1


def _cmd_parse_ast(args: argparse.Namespace) -> int:
    import json

    from .parser import parse_layout_document

    if not args.layout.is_file():
        raise SystemExit(f"找不到 Layout 文件：{args.layout}")
    try:
        document = json.loads(args.layout.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise SystemExit(f"{args.layout} 不是合法 JSON：{error}") from error
    if not isinstance(document, dict) or document.get("kind") != "Layout":
        kind = document.get("kind") if isinstance(document, dict) else type(document).__name__
        raise SystemExit(f"{args.layout} 的 kind 是 {kind!r}，这里需要 Layout（协议 §4）")
    try:
        ast = parse_layout_document(document)
    except ValueError as error:
        raise SystemExit(f"{args.layout} 不是合法的 Layout：{error}") from error

    text = json.dumps(ast.to_dict(), ensure_ascii=False, indent=2) + "\n"
    if args.out is None:
        print(text, end="")
    else:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
        print(
            f"写入 {args.out}（Ast，语句 {len(ast.root.body)}，节点 {len(list(ast.nodes()))}，"
            f"诊断 {len(ast.diagnostics)} 条，frame_id={ast.frame_id}）"
        )
    if ast.diagnostics:
        # 诊断摘要走 stderr，stdout 只有可以原样管道给下游的 JSON。
        located = "，".join(
            f"{entry['code']}@行{entry['span'].get('row', entry['span'].get('row_from'))}"
            f"槽{entry['span'].get('slot_from')}"
            for entry in ast.diagnostics
        )
        print(f"语法诊断 {len(ast.diagnostics)} 条：{located}", file=sys.stderr)
        return 1 if args.fail_on_error else 0
    return 0


def _cmd_parse_report(args: argparse.Namespace) -> int:
    import json

    from .parser import build_report, format_report

    report = build_report(args.dir)
    if not report.fixtures:
        print(f"{args.dir} 里没有语法夹具（或清单缺失）")
        if report.manifest_error:
            print(f"清单问题：{report.manifest_error}")
        return 1
    print(format_report(report), end="")
    if args.json is not None:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"报告已写入 {args.json}")
    return 0 if all(ok for ok, _criterion, _evidence in report.acceptance()) else 1


def _cmd_ir_normalize(args: argparse.Namespace) -> int:
    import json

    from .ir import normalize_ast_document

    if not args.ast.is_file():
        raise SystemExit(f"找不到 Ast 文件：{args.ast}")
    try:
        document = json.loads(args.ast.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise SystemExit(f"{args.ast} 不是合法 JSON：{error}") from error
    if not isinstance(document, dict) or document.get("kind") != "Ast":
        kind = document.get("kind") if isinstance(document, dict) else type(document).__name__
        raise SystemExit(f"{args.ast} 的 kind 是 {kind!r}，这里需要 Ast（协议 §5）")
    try:
        ir = normalize_ast_document(document, max_steps=args.max_steps, timeout_ms=args.timeout_ms)
    except ValueError as error:
        raise SystemExit(f"{args.ast} 不是合法的 Ast（或 limits 不合法）：{error}") from error

    text = ir.canonical_text()
    if args.out is None:
        print(text, end="")
    else:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
        limits = ir.limits.to_dict()
        print(
            f"写入 {args.out}（Ir，步骤 {len(ir.steps)}，诊断 {len(ir.diagnostics)} 条，"
            f"limits max_steps={limits['max_steps']} timeout_ms={limits['timeout_ms']}，"
            f"sha256={ir.sha256()[:16]}）"
        )
    if ir.diagnostics:
        # 诊断摘要走 stderr，stdout 只有可以原样管道给下游的 JSON。
        located = "，".join(
            f"{entry['code']}@行{entry['span'].get('row')}槽{entry['span'].get('slot_from')}"
            for entry in ir.diagnostics
        )
        print(f"规范化诊断 {len(ir.diagnostics)} 条：{located}", file=sys.stderr)
        return 1 if args.fail_on_error else 0
    return 0


def _cmd_ir_report(args: argparse.Namespace) -> int:
    import json

    from .ir import build_report, format_report

    report = build_report(args.dir)
    if not report.fixtures:
        print(f"{args.dir} 里没有 IR 夹具（或清单缺失）")
        if report.manifest_error:
            print(f"清单问题：{report.manifest_error}")
        return 1
    print(format_report(report), end="")
    if args.json is not None:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"报告已写入 {args.json}")
    return 0 if all(ok for ok, _criterion, _evidence in report.acceptance()) else 1


def _exec_abort_hook(args: argparse.Namespace):
    """把 ``--abort-at-step`` / ``--abort-at-ms`` 折成一个「步骤边界中断」回调。"""
    step_limit = args.abort_at_step
    ms_limit = args.abort_at_ms
    if step_limit is None and ms_limit is None:
        return None

    def should_abort(step_index: int, elapsed_ms: float) -> bool:
        if step_limit is not None and step_index >= step_limit:
            return True
        if ms_limit is not None and elapsed_ms >= ms_limit:
            return True
        return False

    return should_abort


def _cmd_exec_run(args: argparse.Namespace) -> int:
    import json

    from .runtime import SimExecutor, canonical_text, ir_from_document, validate_run

    if not args.ir.is_file():
        raise SystemExit(f"找不到 Ir 文件：{args.ir}")
    try:
        document = json.loads(args.ir.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise SystemExit(f"{args.ir} 不是合法 JSON：{error}") from error
    if not isinstance(document, dict) or document.get("kind") != "Ir":
        kind = document.get("kind") if isinstance(document, dict) else type(document).__name__
        raise SystemExit(f"{args.ir} 的 kind 是 {kind!r}，这里需要 Ir（协议 §6）")
    try:
        ir = ir_from_document(document)
    except ValueError as error:
        raise SystemExit(f"{args.ir} 不是合法的 Ir：{error}") from error

    run_id = args.run_id or f"run-{args.ir.stem}"
    executor = SimExecutor(run_id=run_id, should_abort=_exec_abort_hook(args))
    run = executor.run(ir)
    try:
        validate_run(run)
    except ValueError as error:
        raise SystemExit(f"执行产出的文档不合法（这是实现缺陷，请报 Issue）：{error}") from error

    if args.events_out is not None:
        args.events_out.parent.mkdir(parents=True, exist_ok=True)
        args.events_out.write_text(run.events_text(), encoding="utf-8")
    if args.result_out is not None:
        args.result_out.parent.mkdir(parents=True, exist_ok=True)
        args.result_out.write_text(run.result_text(), encoding="utf-8")

    limits = run.state["limits"]
    print(
        f"运行 {run_id}：{run.status}（backend=sim，步骤 {run.steps_executed}/{limits['max_steps']}，"
        f"虚拟耗时 {run.duration_ms:g} ms / {limits['timeout_ms']} ms，事件 {len(run.events)} 条，"
        f"实测 {run.wall_ms:.2f} ms）"
    )
    for entry in run.result["diagnostics"]:
        print(f"  {entry['code']} {entry['severity']}: {entry['message']}", file=sys.stderr)
    if args.events_out is not None:
        print(f"事件流已写入 {args.events_out}（JSONL，{len(run.events)} 行）")
    if args.result_out is not None:
        print(f"ExecResult 已写入 {args.result_out}")
    if args.state_out is not None:
        args.state_out.parent.mkdir(parents=True, exist_ok=True)
        args.state_out.write_text(canonical_text(run.state), encoding="utf-8")
        print(f"最终状态已写入 {args.state_out}")
    else:
        print("最终状态 JSON（ExecState）：")
        print(canonical_text(run.state), end="")
    return 0 if run.status == "ok" else 1


def _cmd_exec_report(args: argparse.Namespace) -> int:
    import json

    from .runtime import build_report, format_report

    report = build_report(args.dir)
    if not report.fixtures:
        print(f"{args.dir} 里没有执行夹具（或清单缺失）")
        if report.manifest_error:
            print(f"清单问题：{report.manifest_error}")
        return 1
    print(format_report(report), end="")
    if args.json is not None:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"报告已写入 {args.json}")
    return 0 if all(ok for ok, _criterion, _evidence in report.acceptance()) else 1


def _cmd_e2e_run(args: argparse.Namespace) -> int:
    """一条命令跑全链路：每个场景一份 §8 记录，退出码 0 = 全部记录形状合格。"""
    from .e2e import run_chain, validate_record

    names = _resolve_scenes(args.scene)
    failures = 0
    for scene in names:
        run = run_chain(scene, out_dir=args.out, seed=args.seed, corrupt=args.corrupt)
        issues = validate_record(
            run.documents,
            scene=scene,
            image_exists=lambda ref, out=args.out: (Path(out) / ref).is_file(),
        )
        print(
            f"{scene:<16} {run.summary()}  记录 {len(run.documents)} 行 → {run.record_path}"
        )
        print(
            f"{'':<16} 分层耗时（实测，不是性能结论）："
            + "，".join(f"{stage} {value:g} ms" for stage, value in run.timings.items())
            + f"；整条 {run.wall_ms:g} ms"
        )
        for note in run.notes:
            print(f"{'':<16} ! {note}")
        for entry in issues:
            failures += 1
            print(f"{'':<16} ! {entry.describe()}", file=sys.stderr)
    print(f"共 {len(names)} 个场景，记录写到 {args.out}")
    return 0 if failures == 0 else 1


def _cmd_e2e_regression(args: argparse.Namespace) -> int:
    """全场景回归：打印逐场景结论 + 破坏注入自检 + 验收对照 + 失败摘要。"""
    import json

    from .e2e import BaselineError, ChainError, build_report, format_report

    scenes = None
    if args.scene:
        scenes = [part.strip() for part in args.scene.split(",") if part.strip()]
    try:
        report = build_report(
            args.dir,
            out_dir=args.out,
            scenes=scenes,
            seed=args.seed,
            corrupt=args.corrupt,
            write_records=not args.no_write,
            self_check=not args.no_self_check,
            update_baseline=args.update_baseline,
        )
    except (BaselineError, ChainError, ValueError) as error:
        # 基准缺失/形状不对、场景不存在、--update-baseline 与 --corrupt 同时给：都只是「跑不起来」。
        print(f"端到端回归无法开始：{error}", file=sys.stderr)
        return 1

    print(format_report(report), end="")
    if args.json is not None:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"报告已写入 {args.json}")
    return 0 if report.passes() else 1


def _cmd_ui_view(args: argparse.Namespace) -> int:
    """渲染一屏视图：文本 / 自包含 HTML / 叠加 PNG（写盘）。"""
    from .ui.protocol import LAYER_ORDER
    from .ui.render import build_view_from_file

    if not args.record.is_file():
        raise SystemExit(f"找不到运行记录：{args.record}")
    view = build_view_from_file(args.record, step=args.step)
    counts = view.severity_counts
    print(
        f"记录 {args.record}：{view.record.summary()}；单步 {view.step}/{view.replay.total_steps}",
        file=sys.stderr,
    )
    print(
        "五层："
        + "、".join(
            f"{layer}=" + ("有" if view.layer_present[layer] else "缺") for layer in LAYER_ORDER
        ),
        file=sys.stderr,
    )
    if view.issues:
        print(
            f"提示 {len(view.issues)} 条（error {counts['error']} / warning {counts['warning']} / "
            f"info {counts['info']}）："
            + "、".join(sorted({entry.code for entry in view.issues})),
            file=sys.stderr,
        )
    for entry in view.issues:
        print(f"  {entry.describe()}", file=sys.stderr)

    if args.html_out is not None:
        args.html_out.parent.mkdir(parents=True, exist_ok=True)
        args.html_out.write_text(view.render_html(), encoding="utf-8")
        print(f"HTML 已写入 {args.html_out}", file=sys.stderr)
    if args.text_out is not None:
        args.text_out.parent.mkdir(parents=True, exist_ok=True)
        args.text_out.write_text(view.render_text(), encoding="utf-8")
        print(f"文本视图已写入 {args.text_out}", file=sys.stderr)
    if args.overlay_out is not None:
        args.overlay_out.parent.mkdir(parents=True, exist_ok=True)
        args.overlay_out.write_bytes(view.overlay_png)
        print(f"叠加图已写入 {args.overlay_out}", file=sys.stderr)

    if args.format == "text":
        sys.stdout.write(view.render_text())
    elif args.format == "html":
        sys.stdout.write(view.render_html())
    if args.fail_on_error and any(entry.severity == "error" for entry in view.issues):
        return 1
    return 0


def _cmd_ui_replay(args: argparse.Namespace) -> int:
    """回放一份记录：打印时间线，并核对「同一记录回放两次结果一致」。"""
    import time

    from .ui.record import parse_record_file
    from .ui.render import build_view
    from .ui.replay import build_replay

    if not args.record.is_file():
        raise SystemExit(f"找不到运行记录：{args.record}")
    started = time.perf_counter()
    record = parse_record_file(args.record)
    parse_ms = (time.perf_counter() - started) * 1000.0
    started = time.perf_counter()
    replay = build_replay(record)
    replay_ms = (time.perf_counter() - started) * 1000.0
    started = time.perf_counter()
    view = build_view(record, step=args.step, record_path=args.record)
    rendered = (view.render_html(), view.render_text(), view.overlay_png)
    render_ms = (time.perf_counter() - started) * 1000.0

    print(f"回放 {args.record}")
    print(
        f"  run_id={record.run_id or '?'} backend={record.backend or '?'} "
        f"事件={len(record.events)} 时间线={len(replay.states)} 帧 问题={len(view.issues)}"
    )
    shown = replay.states if args.step is None else (replay.state_at(args.step),)
    for state in shown[: max(1, args.max_steps)]:
        current = "  当前帧" if state.index == view.step else ""
        print(f"  {state.describe()}{current}")
    if len(shown) > max(1, args.max_steps):
        print(f"  …（还有 {len(shown) - max(1, args.max_steps)} 帧，用 --max-steps 调整）")
    for note in replay.notes:
        print(f"  ! {note}")

    second_record = parse_record_file(args.record)
    second_replay = build_replay(second_record)
    second_view = build_view(second_record, step=args.step, record_path=args.record)
    checks = {
        "HTML": second_view.render_html() == rendered[0],
        "文本": second_view.render_text() == rendered[1],
        "叠加图": second_view.overlay_png == rendered[2],
        "时间线": second_replay.signature() == replay.signature(),
    }
    print("  两次回放：" + " / ".join(f"{key} {'一致' if value else '不一致'}" for key, value in checks.items()))
    print(
        f"  实测（只是参考，不是性能结论）：解析 {parse_ms:.2f} ms，回放 {replay_ms:.2f} ms，"
        f"渲染 {render_ms:.2f} ms"
    )
    return 0 if all(checks.values()) else 1


def _serve_cam_config(args: argparse.Namespace):
    from .serve_cam import ServeCamConfig

    return ServeCamConfig(live_dir=args.live_dir, card_layout=args.card_layout)


def _cmd_serve_cam_serve(args: argparse.Namespace) -> int:
    from .serve_cam.server import banner, serve

    config = _serve_cam_config(args)
    url = f"http://{args.host}:{args.port}/"
    if args.print_url_only:
        print(banner(url, config))
        return 0
    return serve(config, host=args.host, port=args.port, verbose=args.verbose)


def _cmd_serve_cam_page(args: argparse.Namespace) -> int:
    from .serve_cam import render_page

    html = render_page()
    if args.out is None:
        sys.stdout.write(html.decode("utf-8"))
        return 0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(html)
    print(f"页面已写入 {args.out}（{len(html)} 字节，自带 CSS/JS，无外链）")
    return 0


def _cmd_ui_report(args: argparse.Namespace) -> int:
    import json

    from .ui.report import build_report, format_report

    report = build_report(args.dir)
    if not report.fixtures:
        print(f"{args.dir} 里没有运行记录夹具（或清单缺失）")
        if report.manifest_error:
            print(f"清单问题：{report.manifest_error}")
        return 1
    print(format_report(report), end="")
    if args.json is not None:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(report_to_dict(report), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"报告已写入 {args.json}")
    return 0 if report.passes() else 1


def report_to_dict(report) -> dict[str, object]:
    """把 UI 自检报告序列化成 JSON（机器可读，供 CI/PR 附证据）。"""
    from .ui.protocol import UI_VERSION

    return {
        "directory": str(report.directory),
        "ui_version": UI_VERSION,
        "manifest_error": report.manifest_error,
        "wall_ms": round(report.wall_ms, 3),
        "doc_gaps": list(report.doc_gaps),
        "evidence": report.evidence,
        "evidence_problems": list(report.evidence_problems),
        "fixtures": [
            {
                "name": item.name,
                "file": str(item.path),
                "family": item.family,
                "layers": list(item.layers),
                "events": item.events,
                "steps": item.steps,
                "issues": item.issues,
                "issue_codes": list(item.issue_codes),
                "hashes": item.hashes,
                "hash_mismatches": list(item.hash_mismatches),
                "determinism": item.determinism[0],
                "determinism_detail": item.determinism[1],
                "step_probe": item.step_probe[0],
                "error": item.error,
            }
            for item in report.fixtures
        ],
        "acceptance": [
            {"ok": ok, "criterion": criterion, "evidence": evidence}
            for ok, criterion, evidence in report.acceptance()
        ],
    }


def main(argv: Sequence[str] | None = None) -> int:
    """命令行主函数，返回进程退出码。"""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.version:
        print(f"{PROG} {__version__}")
        return 0

    handler = getattr(args, "handler", None)
    if handler is not None:
        return int(handler(args))

    parser.print_help(sys.stdout)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
