# 序界（LiveBlocks）P0 文法 — 草案

> **状态：草案（draft）`grammar_version` 0.0.1，未冻结。**
> 本文件是 LB-005 的**文法定义**：它说明「排列好的卡片」怎么读成 `docs/PROTOCOL.md` §5 的
> `Ast`。机器可读的同一份定义在 `src/liveblocks/parser/grammar.py`，两份必须一致——
> `tests/test_parser_grammar.py` 与 `liveblocks parse report` 会逐项核对（产生式、节点名、
> 诊断码、卡面角色表），任何一边改了另一边没跟上就会失败。
> 文法改动必须提升 `grammar_version` 并记进 `docs/DECISIONS.md`。

## 0. 范围与依据

- **依据**：`docs/ROADMAP.md` LB-005「最小文法（P0 定为「语句序列 + 字面参数」），错误定位到
  行/槽位/卡片」；`docs/DECISIONS.md` D-005（P0 文法取最小集）。**不做求值、不做执行**。
- **输入**：`docs/PROTOCOL.md` §4 的 `Layout`（一行 = 画面上的一排卡，槽位按阅读方向排序）。
- **输出**：§5 的 `Ast`（`Program` / `Command` / `Number` 三种节点），每个节点带 `span`
  （行 + 槽位区间）与 `origin`（来源卡片）。
- **方法**：按卡面在卡片字典（`src/liveblocks/data/card_dictionary.json`）里的 `card_type`
  定角色，唯一例外是 `wait`（见 §2.2）。

## 1. 文法

最小文法（EBNF 风格，`*` 表示零或多次）：

```
Program   ::= Statement*
Statement ::= Command Literal*
Command   ::= command_card
Literal   ::= number_card
```

- `Program`：整个 `Layout`，语句按 `row_index` 升序排列。
- `Statement`：**一行就是一条语句**。第 0 个槽位必须是命令卡，其后一个或多个槽位是字面数字卡
  （参数按槽位顺序排列）。空行不是语句（§4 也不会产出空行）。
- `Command`：一张命令卡，`name` 取它的 `card_face`（例如 `move-forward`）。
- `Literal`：一张字面数字卡，`value` 由卡面推得（`number-5` → `5`）。
- 一个 `Layout` 里没有任何行时，得到一个 `body` 为空的合法 `Program`（空程序）。

示例（两行：`move-forward 1`、`wait 3`）：

```text
行0: [move-forward][number-1]        →  Command{name: "move-forward", args: [Number 1]}
行1: [wait][number-3]                →  Command{name: "wait", args: [Number 3]}
```

## 2. 卡面角色表

| 卡面 `card_face` | `card_type` | P0 角色 `role` | 说明 |
| --- | --- | --- | --- |
| `move-forward` | `action` | `command` | 前进；语句头 |
| `move-back` | `action` | `command` | 后退；语句头 |
| `turn-left` | `action` | `command` | 左转；语句头 |
| `turn-right` | `action` | `command` | 右转；语句头 |
| `jump` | `action` | `command` | 跳跃；语句头 |
| `pick` | `action` | `command` | 拾取；语句头 |
| `drop` | `action` | `command` | 放置；语句头 |
| `wait` | `control` | `command` | 等待；虽是控制类卡，但不引入分支/循环，按普通语句处理（§2.2） |
| `repeat` | `control` | `control` | 需要「块」，P0 不支持 → `LB-P004` |
| `if` | `control` | `control` | 需要「块」与条件，P0 不支持 → `LB-P004` |
| `end` | `control` | `control` | 块结束标记，P0 无块 → `LB-P004` |
| `number-1` | `number` | `literal` | 字面量 1 |
| `number-2` | `number` | `literal` | 字面量 2 |
| `number-3` | `number` | `literal` | 字面量 3 |
| `number-5` | `number` | `literal` | 字面量 5 |
| `operator-add` | `operator` | `operator` | 运算符；P0 只有字面参数，不做表达式 → `LB-P005` |

### 2.1 卡片字典是唯一身份来源

角色按**卡片字典**推导，不写死在解析器里：字典新增一张 `action` / `number` 卡，解析器不需要
改；但 `tests/test_parser_grammar.py` 会要求本文件的角色表把它也列上（否则报「角色表缺卡面」）。

### 2.2 为什么 `wait` 算普通语句

`wait` 的 `card_type` 是 `control`，但它只是在语句序列里占一步（参数是一个字面数字），不引入
分支或循环，因此按 D-005「语句序列 + 字面参数」的最省事读法当作普通语句；`repeat` / `if` /
`end` 需要「块」的概念，超出 P0，报 `LB-P004` 而不是静默接受。

## 3. 语义（P0 的读法）

1. **一行 = 一条语句**：行内槽位顺序就是命令与参数的顺序；`rows` 的顺序就是语句顺序。
2. **参数只取字面数字卡**：P0 没有变量、没有表达式、没有函数调用（D-005）。
3. **不检查参数个数（arity）**：§5 与 D-005 都没有定义每条命令收几个参数，P0 一律接受
   零个或多个字面参数；「这条命令该收几个参数」属于 IR 语义（LB-006）。
4. **不求值**：解析只产出语法树，不判断「`move-forward` 走几格」这类语义。
5. **不复制上游诊断**：`Layout.diagnostics`（`LB-L0xx`）属于排列层，`Ast.diagnostics` 只放
   `LB-P0xx`。一次运行的所有层对象都会进运行记录（协议 §8），因此不会丢信息。

## 4. AST 形状（§5 的补充约定）

§5 给了字段与一个示例，但没写死下面这些细节，本实现按「最不意外」的读法补齐（`docs/DECISIONS.md`
D-016 记了缺口清单）：

| 约定 | 取法 |
| --- | --- |
| `span` 区间 | **闭区间**：`slot_from` / `slot_to` 都指向真实槽位（`0-2` 表示 0、1、2 三个槽位） |
| 单行节点 `span` | `{"row": r, "slot_from": a, "slot_to": b}`，与 §5 示例一致 |
| 跨行节点 `span` | `Program` 覆盖整个 `Layout`，改写 `{"row_from": r0, "row_to": r1, "slot_from": a, "slot_to": b}`（加法式，§5 只有一个单行示例） |
| 单卡节点 `origin` | `{"candidate_id": "...", "card_face": "..."}`，与 §5 示例一致 |
| 跨卡节点 `origin` | `Program` 用 `{"candidate_ids": [...], "card_faces": [...]}`（加法式）；空程序是两个空数组 |
| `Ast.frame_id` | 加法式字段：把语法树指回它来自哪一帧（§5 示例里没有，但对 LB-008/LB-009 的回溯必需） |
| `grammar_version` | 与本文件同版本：`0.0.1` |
| `node` 取值 | 开放枚举（§5 原文）：本实现产出 `Program` / `Command` / `Number`，校验器遇到未知节点名只要求 `span` + `origin` |

示例（`liveblocks parse ast --layout fixtures/ast/valid-basic.json` 的真实输出，切掉外层
`Ast` 包装只留 `root`；三条语句分别是 `move-forward 1`、`turn-left 2 3`、`jump`）：

```json
{
  "node": "Program",
  "span": { "row_from": 0, "row_to": 2, "slot_from": 0, "slot_to": 2 },
  "body": [
    {
      "node": "Command",
      "span": { "row": 0, "slot_from": 0, "slot_to": 1 },
      "name": "move-forward",
      "args": [
        { "node": "Number", "span": { "row": 0, "slot_from": 1, "slot_to": 1 }, "value": 1,
          "origin": { "candidate_id": "c-1", "card_face": "number-1" } }
      ],
      "origin": { "candidate_id": "c-0", "card_face": "move-forward" }
    },
    {
      "node": "Command",
      "span": { "row": 1, "slot_from": 0, "slot_to": 2 },
      "name": "turn-left",
      "args": [
        { "node": "Number", "span": { "row": 1, "slot_from": 1, "slot_to": 1 }, "value": 2,
          "origin": { "candidate_id": "c-3", "card_face": "number-2" } },
        { "node": "Number", "span": { "row": 1, "slot_from": 2, "slot_to": 2 }, "value": 3,
          "origin": { "candidate_id": "c-4", "card_face": "number-3" } }
      ],
      "origin": { "candidate_id": "c-2", "card_face": "turn-left" }
    },
    {
      "node": "Command",
      "span": { "row": 2, "slot_from": 0, "slot_to": 0 },
      "name": "jump",
      "args": [],
      "origin": { "candidate_id": "c-5", "card_face": "jump" }
    }
  ],
  "origin": {
    "candidate_ids": ["c-0", "c-1", "c-2", "c-3", "c-4", "c-5"],
    "card_faces": ["move-forward", "number-1", "turn-left", "number-2", "number-3", "jump"]
  }
}
```

诊断示例（`liveblocks parse ast --layout fixtures/ast/from-multi-row-cards.json`，一行报一条、
`span` 指槽位、`origin` 指卡片）：

```json
{
  "code": "LB-P002",
  "severity": "error",
  "message": "行 0 槽位 2 是命令卡 'jump'，参数位置只接受字面数字卡（一行一条语句）；该行整条丢弃",
  "candidate_ids": ["c-0"],
  "span": { "row": 0, "slot_from": 2, "slot_to": 2 },
  "origin": { "candidate_id": "c-0", "card_face": "jump" }
}
```

## 5. 诊断与定位（`LB-P0xx`）

诊断沿用协议 §9 的统一形状 `{code, severity, message, candidate_ids?, span?}`，并按本任务要求
**一律带上 `span` 与 `origin`**：`span` 指向「第几行的第几个槽位」，`origin` 指向「哪张卡」。

**策略：出错整行丢弃，只报一条诊断。** 一行里任何槽位违约，就把这条语句整条丢掉，诊断只定位到
**第一个**违约的槽位；同一行不再产生第二条诊断，避免一张错卡引发一串级联误报。其余行照常解析，
因此非法输入也会得到**部分 AST**（`body` 里只有解析成功的语句）而不是异常。

| 诊断码 | 标题 | 何时报 | `span` 指向 |
| --- | --- | --- | --- |
| `LB-P001` | 语句缺命令卡 | 行首是字面卡（如数字卡开头），或行没有任何槽位 | 行首槽位 |
| `LB-P002` | 参数位置不是字面卡 | 命令卡之后的槽位出现了命令卡（含 `wait`） | 那个槽位 |
| `LB-P003` | 槽位为空或卡面未知 | `card_face` 为空串，或不在卡片字典里（沿用 §9「期望参数卡但槽位为空」的语义并扩展） | 那个槽位 |
| `LB-P004` | 控制流卡超出 P0 文法 | `repeat` / `if` / `end` 出现在语句位置或参数位置 | 那张卡 |
| `LB-P005` | 运算符卡超出 P0 文法 | `operator-*` 出现在语句位置或参数位置 | 那张卡 |

补充：

- `severity` 一律是 `error`；`candidate_ids` 里放被点名的候选 id（`origin` 里还有卡面）。
- **输入本身不是合法 `Layout`**（缺字段、`kind` 不对等）不是语法错误：`parse_layout_document`
  抛 `ValueError`，CLI 以非零码退出，不产出 `LB-P0xx`。语法层不为上游的数据错误背锅。
- 诊断按行 → 槽位的自然顺序排列，同一夹具重复运行结果字节一致（确定性）。

## 6. 明确不做

- 不求值、不执行（LB-007）。
- 不做 AST → IR 的规范化、缺省值填充与 `op` 校验（LB-006）。
- 不支持变量、表达式、条件、循环、函数（D-005 的 P0 范围）。
- 不做界面展示（LB-008）。

## 7. 已知缺口（只报告，未改 `docs/PROTOCOL.md`）

1. **§5 的示例自相矛盾**：示例里 `Program` 的 `span` 是 `{row:0, slot_from:0, slot_to:2}`，但同一
   示例的 `rows[0].slots` 只有 2 个槽位（0..1）。本实现按闭区间取「最后一个槽位下标」。
2. **§5 没定义跨行节点的 `span`**：`Program` 必然是跨行的，本实现加了 `row_from` / `row_to`。
3. **§5 没定义多卡节点的 `origin`**：示例只给了单卡形状，本实现给 `Program` 用 `candidate_ids` /
   `card_faces` 数组。
4. **§5 的示例节点缺 `origin`**：正文要求「每个节点都带 `span` 与 `origin`」，但示例的 `Number`
   节点只有 `span`。本实现一律输出 `origin`，校验器也要求它有。
5. **参数个数（arity）没有出处**：§5 与 §9 都没有说每条命令收几个参数，P0 不做这个判断（留给
   LB-006）。如果后续要冻结 arity，应提升 `grammar_version`。
6. **§9 只给了 `LB-P003` 一个示例**，「`LB-P0xx` 的完整码表」由本文件定义；`LB-P003` 的语义
   从「期望参数卡但槽位为空」扩展到「卡面为空/未知」。
7. **§4 的 `ttb_ltr` 下「一行」在画面上是一列**（D-015 对 §4 的解释）：本层只按 `rows` /
   `slots` 的顺序读，与几何无关，因此不受影响；但如果将来把「一行」理解成严格的画面横排，
   解析层的顺序语义也要一起改。
8. **字面量只有整数**：字典里只有 `number-1/2/3/5`，因此 `Number.value` 是整数；小数、负数、
   字符串字面量都没有卡片可表达。

## 8. 命令与校验入口

```bash
# 一份 Layout JSON → 协议 §5 的 Ast JSON
liveblocks parse ast --layout fixtures/ast/valid-basic.json

# 出现 LB-P0xx 时也照常输出 Ast；加 --fail-on-error 可让退出码变 1（便于 CI 卡住非法样例）
liveblocks parse ast --layout fixtures/ast/invalid-number-first.json --fail-on-error

# 对 fixtures/ast 全部夹具跑一遍，打印逐条结果与验收对照（退出码 0 = 三条验收全过）
liveblocks parse report --dir fixtures/ast
```

夹具与清单（`fixtures/ast/index.json`）的说明见 `fixtures/README.md` §5。
