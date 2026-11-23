"""P0 文法（``grammar_version`` 0.0.1）：**机器可读**的文法定义。

人类可读版本是 ``docs/GRAMMAR.md``，两者必须一致：``grammar_document_gaps`` 用同一份表
核对文档里是否写全了产生式、节点名、诊断码与卡面角色，``tests/test_parser_grammar.py`` 与
``liveblocks parse report`` 都调它，因此「文法定义有独立文档且可被测试引用」是可回归的。

P0 的取法是 ``docs/DECISIONS.md`` D-005 的提案：**语句序列 + 字面参数**，不引入变量、条件、
循环、函数。本模块只回答「一行卡片能组成什么语句」，不做求值、不做执行（LB-006 起的活）。

按卡面在卡片字典（``data/card_dictionary.json``）里的 ``card_type`` 定角色，唯一例外是
``wait``：它虽是 ``control`` 卡，但不引入分支/循环，按本实现的最省事读法当作**普通语句**。
``repeat`` / ``if`` / ``end`` 需要「块」的概念，超出 P0，落到 ``LB-P004`` 而不是静默接受。
"""

from __future__ import annotations

import re
from functools import lru_cache

from ..data import read_json

#: 文法版本。改文法（新增产生式、改节点形状、改诊断码语义）必须提升它并在
#: ``docs/DECISIONS.md`` 记录；``grammar_version`` 与 ``schema_version`` 是两条独立的版本线。
GRAMMAR_VERSION = "0.0.1"

#: §5 的 ``node`` 取值（开放枚举，这里只列本实现的三种）。
NODE_PROGRAM = "Program"
NODE_COMMAND = "Command"
NODE_NUMBER = "Number"
NODE_KINDS = (NODE_PROGRAM, NODE_COMMAND, NODE_NUMBER)

#: ``docs/GRAMMAR.md`` 里逐字出现的产生式（``grammar_document_gaps`` 会核对）。
BNF = """\
Program   ::= Statement*
Statement ::= Command Literal*
Command   ::= command_card
Literal   ::= number_card
"""

#: 卡面角色。``unknown`` 表示卡片字典里查不到（或卡面为空串）。
ROLE_COMMAND = "command"
ROLE_LITERAL = "literal"
ROLE_CONTROL = "control"  # 控制流卡（repeat / if / end）：P0 不支持
ROLE_OPERATOR = "operator"  # 运算符卡：P0 只有字面参数，不做表达式
ROLE_UNKNOWN = "unknown"

ROLES = (ROLE_COMMAND, ROLE_LITERAL, ROLE_CONTROL, ROLE_OPERATOR, ROLE_UNKNOWN)

#: ``card_type`` → 角色（``control`` 再按卡面细分成 command / control）。
ROLE_BY_CARD_TYPE = {
    "action": ROLE_COMMAND,
    "number": ROLE_LITERAL,
    "control": ROLE_CONTROL,
    "operator": ROLE_OPERATOR,
}

#: ``control`` 卡里 P0 当作普通语句的卡面（不引入分支/循环，参数是字面数字）。
CONTROL_FACES_AS_STATEMENT = frozenset({"wait"})

#: 字面数字卡的卡面形状；``number-5`` → 5。P0 只有整数字面量（`docs/GRAMMAR.md` §3）。
NUMBER_FACE_PATTERN = re.compile(r"number-(\d+)")

#: §9 的语法诊断区间（``LB-P0xx``）。``LB-P003`` 沿用 §9 示例的语义。
DIAG_MISSING_COMMAND = "LB-P001"  # 语句缺命令卡（行首是字面卡，没有可执行的命令）
DIAG_BAD_ARGUMENT = "LB-P002"  # 参数位置不是字面卡（命令卡 / 控制卡占了参数位）
DIAG_EMPTY_SLOT = "LB-P003"  # 期望卡片但槽位为空 / 卡面未知（§9 示例语义）
DIAG_CONTROL_UNSUPPORTED = "LB-P004"  # 控制流卡超出 P0 文法（repeat / if / end）
DIAG_OPERATOR_UNSUPPORTED = "LB-P005"  # 运算符卡：P0 只支持字面参数，不做表达式

#: 诊断码 → （标题，含义）。标题会与 ``docs/GRAMMAR.md`` 的诊断表逐条核对。
DIAGNOSTICS_BY_CODE: dict[str, tuple[str, str]] = {
    DIAG_MISSING_COMMAND: (
        "语句缺命令卡",
        "一行（= 一条语句）的第一个槽位不是命令卡，例如以数字卡开头；该行整条丢弃。",
    ),
    DIAG_BAD_ARGUMENT: (
        "参数位置不是字面卡",
        "命令卡之后的槽位出现了命令卡或控制卡；参数位置只接受字面数字卡，该行整条丢弃。",
    ),
    DIAG_EMPTY_SLOT: (
        "槽位为空或卡面未知",
        "槽位的 card_face 为空串，或不在卡片字典里；无法判断它是命令还是参数。",
    ),
    DIAG_CONTROL_UNSUPPORTED: (
        "控制流卡超出 P0 文法",
        "repeat / if / end 需要「块」的概念，P0 只做语句序列；该行整条丢弃而不是静默接受。",
    ),
    DIAG_OPERATOR_UNSUPPORTED: (
        "运算符卡超出 P0 文法",
        "P0 的参数只取字面数字卡；表达式求值不属于本任务范围。",
    ),
}

#: 诊断码全集（按码值升序）。
PARSER_CODES = tuple(sorted(DIAGNOSTICS_BY_CODE))

#: 文档位置（相对仓库根），``grammar_document_gaps`` 的输入就是它的正文。
DOC_PATH = "docs/GRAMMAR.md"


@lru_cache(maxsize=1)
def card_types() -> dict[str, str]:
    """卡片字典里的 ``card_face → card_type``（只读，按字典文件顺序）。"""
    raw = read_json("card_dictionary.json")
    return {
        str(card["card_face"]): str(card["card_type"])
        for card in raw.get("cards", [])
    }


def card_role(card_face: str) -> str:
    """卡面在 P0 文法里的角色（:data:`ROLES` 之一）。"""
    if not isinstance(card_face, str) or not card_face:
        return ROLE_UNKNOWN
    role = ROLE_BY_CARD_TYPE.get(card_types().get(card_face, ""), ROLE_UNKNOWN)
    if role == ROLE_CONTROL and card_face in CONTROL_FACES_AS_STATEMENT:
        return ROLE_COMMAND
    if role == ROLE_LITERAL and literal_value(card_face) is None:
        # 字典里登记成 number，但卡面不是 number-<整数>：当未知卡面处理，比猜一个值安全。
        return ROLE_UNKNOWN
    return role


def literal_value(card_face: str) -> int | None:
    """字面数字卡的整数值；不是 ``number-<整数>`` 形状时返回 ``None``。"""
    if not isinstance(card_face, str):
        return None
    match = NUMBER_FACE_PATTERN.fullmatch(card_face)
    return None if match is None else int(match.group(1))


def faces_with_role(role: str) -> tuple[str, ...]:
    """按当前卡片字典列出某个角色下的全部卡面（排序后返回，保证确定性）。"""
    return tuple(
        sorted(face for face in card_types() if card_role(face) == role)
    )


def _normalise(text: str) -> str:
    """把文档正文压成「去行首尾空白 + 行间单个换行」，用于逐字核对代码块。"""
    return "\n".join(line.strip() for line in text.strip().splitlines())


def grammar_document_gaps(text: str) -> list[str]:
    """核对 ``docs/GRAMMAR.md`` 是否写全了本模块的定义，返回缺失项（空列表 = 一致）。

    检查四件事：文法版本、产生式代码块、三种节点名、每个诊断码（含标题）、
    每个卡面的「卡面 / card_type / 角色」三列。任一缺失都说明文档与实现漂移了。
    """
    gaps: list[str] = []
    normalised = _normalise(text)

    if GRAMMAR_VERSION not in text:
        gaps.append(f"缺少 grammar_version {GRAMMAR_VERSION}")
    if _normalise(BNF) not in normalised:
        gaps.append("缺少与 grammar.BNF 逐字一致的产生式代码块")
    for node in NODE_KINDS:
        if node not in text:
            gaps.append(f"缺少节点名 {node}")
    for code, (title, _meaning) in sorted(DIAGNOSTICS_BY_CODE.items()):
        if code not in text:
            gaps.append(f"缺少诊断码 {code}")
        elif title not in text:
            gaps.append(f"诊断码 {code} 缺少标题「{title}」")
    for face, card_type in sorted(card_types().items()):
        role = card_role(face)
        pattern = (
            rf"\|\s*`?{re.escape(face)}`?\s*"
            rf"\|\s*`?{re.escape(card_type)}`?\s*"
            rf"\|\s*`?{re.escape(role)}`?\s*\|"
        )
        if re.search(pattern, text) is None:
            gaps.append(f"卡面 {face}（{card_type} → {role}）不在角色表里")
    return gaps


__all__ = [
    "BNF",
    "CONTROL_FACES_AS_STATEMENT",
    "DIAGNOSTICS_BY_CODE",
    "DIAG_BAD_ARGUMENT",
    "DIAG_CONTROL_UNSUPPORTED",
    "DIAG_EMPTY_SLOT",
    "DIAG_MISSING_COMMAND",
    "DIAG_OPERATOR_UNSUPPORTED",
    "DOC_PATH",
    "GRAMMAR_VERSION",
    "NODE_COMMAND",
    "NODE_KINDS",
    "NODE_NUMBER",
    "NODE_PROGRAM",
    "NUMBER_FACE_PATTERN",
    "PARSER_CODES",
    "ROLES",
    "ROLE_BY_CARD_TYPE",
    "ROLE_COMMAND",
    "ROLE_CONTROL",
    "ROLE_LITERAL",
    "ROLE_OPERATOR",
    "ROLE_UNKNOWN",
    "card_role",
    "card_types",
    "faces_with_role",
    "grammar_document_gaps",
    "literal_value",
]
