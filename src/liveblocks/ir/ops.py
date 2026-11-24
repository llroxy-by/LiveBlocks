"""P0 的 ``op`` 表（``ir_version`` 0.0.1）：§5 ``Command`` 的卡面怎么变成 §6 的 ``op`` + ``args``。

`docs/PROTOCOL.md` §6 只给了一个 ``step`` 示例（``op: "move"``、``args: {"distance": 1, "unit": "cell"}``）
与四句约定（确定性、``limits`` 必须显式出现、``op`` 只能来自文法定义、未知 ``op`` 是规范化错误），
**没有**定义：

- 卡面 → ``op`` 的映射表（示例里的 ``move`` 与卡面 ``move-forward`` 不是一个字符串）；
- 每条命令收几个参数、参数叫什么名字、单位怎么写（D-016 第 3 条明确把 arity 留给 LB-006）；
- ``LB-I0xx`` 的其它码（§9 只举了 ``LB-I001`` 一个例子）。

本模块把这些写死成机器可读的表，人类可读的同一份在 ``docs/IR.md``：``ir_document_gaps`` 用同一份
表核对文档是否写全，``tests/test_ir_docs.py`` 与 ``liveblocks ir report`` 都会调它，因此「文档与
实现一致」是可回归的。

**保守读法（`docs/DECISIONS.md` D-017）**：

1. ``op`` 直接取 ``Command.name``（也就是卡面本身），因为 §6 原文就是「只允许 ``op`` 取值来自文法
   定义」，而文法定义里的命令名就是卡面（``docs/GRAMMAR.md`` §2）。示例里的 ``move`` 与卡面
   ``move-forward`` 对不上，是 §6 自身的缺口：要让 ``move-back`` 也叫 ``move`` 就必须替它发明
   「负距离」这类语义，超出本单范围，因此**不猜**，只报缺口。
2. 每个 ``op`` 的元数（arity）与参数名按「卡面上写的意思」取最小集：前进/后退一格数、左转/右转
   若干 90°、等待若干 tick，``jump`` / ``pick`` / ``drop`` 不接参数。
3. ``op`` 表必须覆盖 P0 文法里全部命令卡面（``docs/GRAMMAR.md`` §2 的 ``command`` 角色），
   :func:`op_table_gaps` 会把「字典里有、op 表里没有」与「op 表里有、字典里没有」都报出来。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..parser.grammar import ROLE_COMMAND, card_types, faces_with_role

#: IR 版本。改 ``op`` 表、``args`` 形状、``limits`` 语义必须提升它并在 ``docs/DECISIONS.md`` 记一条；
#: ``ir_version`` 与 ``schema_version``（协议对象版本）、``grammar_version``（§5 文法版本）是三条
#: 独立的版本线。
IR_VERSION = "0.0.1"

#: ``docs/IR.md`` 的位置（相对仓库根），``ir_document_gaps`` 的输入是它的正文。
DOC_PATH = "docs/IR.md"

#: 包内 JSON Schema 的文件名（``liveblocks.data`` 里，随 wheel 一起安装）。
SCHEMA_FILENAME = "ir_schema.json"

#: ``limits`` 的缺省值，直接取 §6 示例里那两个数（协议只说「缺省由运行时补齐」，没给数值）。
LIMIT_DEFAULTS: dict[str, int] = {"max_steps": 1000, "timeout_ms": 5000}

#: ``limits`` 的字段名与它们的下界（0 或负数不是「限制」，是禁用，属于配置错误）。
LIMIT_KEYS: tuple[str, ...] = ("max_steps", "timeout_ms")
LIMIT_MINIMUM = 1


@dataclass(frozen=True)
class Param:
    """一个 ``op`` 的数值参数：``args`` 里的 ``name`` 与配套的 ``unit``。"""

    name: str
    unit: str

    def describe(self) -> str:
        return f"{self.name}({self.unit})"


@dataclass(frozen=True)
class OpSpec:
    """卡面 → ``op`` 的登记项：``op``、参数表与元数。

    P0 每个 ``op`` 最多一个数值参数，因此 ``args`` 的形状固定为
    ``{"<参数名>": <整数>, "unit": "<单位>"}``（对齐 §6 示例的 ``{"distance": 1, "unit": "cell"}``）；
    零元 ``op`` 的 ``args`` 是空对象 ``{}``。
    """

    card_face: str
    op: str
    params: tuple[Param, ...] = ()

    @property
    def arity(self) -> int:
        return len(self.params)

    @property
    def unit(self) -> str | None:
        return self.params[0].unit if self.params else None

    def describe(self) -> str:
        if not self.params:
            return f"{self.op}（无参数）"
        return f"{self.op}（{', '.join(param.describe() for param in self.params)}）"


def _move(face: str) -> OpSpec:
    return OpSpec(card_face=face, op=face, params=(Param("distance", "cell"),))


def _turn(face: str) -> OpSpec:
    return OpSpec(card_face=face, op=face, params=(Param("angle", "quarter_turn"),))


def _bare(face: str) -> OpSpec:
    return OpSpec(card_face=face, op=face, params=())


#: P0 的 ``op`` 表（按卡面字典顺序的语义顺序，输出顺序以它为准，保证确定性）。
#:
#: - ``move-forward`` / ``move-back``：``distance``，单位 ``cell``（一格一格地走，§6 示例用的就是
#:   ``cell``）；方向写在 ``op`` 里，不用负距离表达。
#: - ``turn-left`` / ``turn-right``：``angle``，单位 ``quarter_turn``（数字卡都是正整数，1 = 四分之一圈）。
#: - ``wait``：``duration``，单位 ``tick``（虚拟时钟的刻度；tick 与毫秒的关系属于 LB-007 / Q5）。
#: - ``jump`` / ``pick`` / ``drop``：不接参数（元数 0），``args`` 是空对象。
OP_SPECS: tuple[OpSpec, ...] = (
    _move("move-forward"),
    _move("move-back"),
    _turn("turn-left"),
    _turn("turn-right"),
    _bare("jump"),
    _bare("pick"),
    _bare("drop"),
    OpSpec(card_face="wait", op="wait", params=(Param("duration", "tick"),)),
)

#: 卡面 → 登记项（``op`` 与卡面同名字，查表用卡面）。
OP_BY_FACE: dict[str, OpSpec] = {spec.card_face: spec for spec in OP_SPECS}

#: ``op`` → 登记项（``op`` 与卡面同名，查表用 ``op``）。
OP_BY_NAME: dict[str, OpSpec] = {spec.op: spec for spec in OP_SPECS}

#: §9 的规范化诊断区间（``LB-I0xx``）。``LB-I001`` 沿用 §9 示例的语义（未知 ``op``）。
DIAG_UNKNOWN_OP = "LB-I001"  # op 不在 P0 命令卡表里（含文法外的控制流卡）
DIAG_ARITY = "LB-I002"  # 参数个数（arity）与 op 登记不符
DIAG_UNSUPPORTED_STATEMENT = "LB-I003"  # 语句节点不是本实现认识的 Command（§5 的 node 是开放枚举）
DIAG_NON_LITERAL_ARG = "LB-I004"  # 参数不是可直接取值的 Number（§5 的 node 是开放枚举）

#: 诊断码 → （标题，含义）。标题会与 ``docs/IR.md`` 的诊断表逐条核对。
DIAGNOSTICS_BY_CODE: dict[str, tuple[str, str]] = {
    DIAG_UNKNOWN_OP: (
        "未知 op",
        "Command.name 不在 P0 的 op 表里（命令卡表见 docs/IR.md §2）；该语句不进 IR。",
    ),
    DIAG_ARITY: (
        "参数个数不符",
        "Command.args 的个数与该 op 登记的元数不符；该语句不进 IR，不做截断或补零。",
    ),
    DIAG_UNSUPPORTED_STATEMENT: (
        "语句节点不受支持",
        "Program.body 里的节点不是 Command（§5 的 node 是开放枚举）；该语句不进 IR。",
    ),
    DIAG_NON_LITERAL_ARG: (
        "参数不是字面量",
        "Command.args 里出现不是 Number 的节点，取不到整数值；该语句不进 IR。",
    ),
}

#: 规范化诊断码全集（按码值升序）。
IR_CODES = tuple(sorted(DIAGNOSTICS_BY_CODE))

#: ``step_id`` 的形状（密集、按产出顺序从 ``s0`` 开始）。
STEP_ID_PATTERN = re.compile(r"s\d+")


def op_spec(card_face: str) -> OpSpec | None:
    """按卡面（= ``Command.name``）查登记项；不是 P0 的 ``op`` 时返回 ``None``。"""
    if not isinstance(card_face, str):
        return None
    return OP_BY_FACE.get(card_face)


def op_table_gaps() -> list[str]:
    """核对 ``op`` 表与卡片字典是否一致，返回缺口（空列表 = 一致）。

    两边都得对上：P0 文法里的每个命令卡面（``docs/GRAMMAR.md`` §2 的 ``command`` 角色）都必须有
    ``op`` 登记（否则新加的 action 卡会在规范化时报 ``LB-I001``），``op`` 表里也不许留着字典里
    已经没有的卡面。
    """
    gaps: list[str] = []
    command_faces = set(faces_with_role(ROLE_COMMAND))
    registered = set(OP_BY_FACE)
    known_faces = set(card_types())
    for face in sorted(command_faces - registered):
        gaps.append(f"命令卡面 {face} 没有 op 登记")
    for face in sorted(registered - command_faces):
        if face in known_faces:
            gaps.append(f"op 表里的卡面 {face} 在 P0 文法里不是命令角色")
        else:
            gaps.append(f"op 表里的卡面 {face} 不在卡片字典里")
    return gaps


def _normalise(text: str) -> str:
    """把文档正文压成「去行首尾空白 + 行间单个换行」，用于逐字核对代码块。"""
    return "\n".join(line.strip() for line in text.strip().splitlines())


def _op_row_pattern(spec: OpSpec) -> re.Pattern[str]:
    params = "、".join(f"`{param.name}`" for param in spec.params) or "—"
    unit = f"`{spec.params[0].unit}`" if spec.params else "—"
    return re.compile(
        rf"\|\s*`?{re.escape(spec.card_face)}`?\s*"
        rf"\|\s*`?{re.escape(spec.op)}`?\s*"
        rf"\|\s*{re.escape(params)}\s*"
        rf"\|\s*{spec.arity}\s*"
        rf"\|\s*{re.escape(unit)}\s*\|"
    )


def ir_document_gaps(text: str) -> list[str]:
    """核对 ``docs/IR.md`` 是否写全了本模块的定义，返回缺失项（空列表 = 一致）。

    检查五件事：``ir_version``、``op`` 表的每一行（卡面 / op / 参数 / 元数 / 单位）、每个诊断码
    （含标题）、``limits`` 的缺省值与字段名、schema 文件名。
    """
    gaps: list[str] = []
    normalised = _normalise(text)

    if IR_VERSION not in text:
        gaps.append(f"缺少 ir_version {IR_VERSION}")
    if SCHEMA_FILENAME not in text:
        gaps.append(f"缺少 schema 文件名 {SCHEMA_FILENAME}")
    for key, value in sorted(LIMIT_DEFAULTS.items()):
        if not re.search(rf"`{re.escape(key)}`[^|\n]*\|\s*{value}\b", text):
            gaps.append(f"limits 缺省表缺少 {key} = {value}")
    for spec in OP_SPECS:
        if _op_row_pattern(spec).search(text) is None:
            gaps.append(f"op 表缺少一行：{spec.card_face} → {spec.op}（元数 {spec.arity}）")
    for code, (title, _meaning) in sorted(DIAGNOSTICS_BY_CODE.items()):
        if code not in text:
            gaps.append(f"缺少诊断码 {code}")
        elif title not in text:
            gaps.append(f"诊断码 {code} 缺少标题「{title}」")
    if "s0" not in normalised:
        gaps.append("缺少 step_id 形状说明（s0…）")
    return gaps


__all__ = [
    "DIAGNOSTICS_BY_CODE",
    "DIAG_ARITY",
    "DIAG_NON_LITERAL_ARG",
    "DIAG_UNKNOWN_OP",
    "DIAG_UNSUPPORTED_STATEMENT",
    "DOC_PATH",
    "IR_CODES",
    "IR_VERSION",
    "LIMIT_DEFAULTS",
    "LIMIT_KEYS",
    "LIMIT_MINIMUM",
    "OP_BY_FACE",
    "OP_BY_NAME",
    "OP_SPECS",
    "OpSpec",
    "Param",
    "SCHEMA_FILENAME",
    "STEP_ID_PATTERN",
    "ir_document_gaps",
    "op_spec",
    "op_table_gaps",
]
