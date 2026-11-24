# 序界（LiveBlocks）S5 规范化 IR — 草案

> **状态：草案（draft）`ir_version` 0.0.1，未冻结。**
> 本文件是 LB-006 的 **IR 规范**：它说明「`docs/PROTOCOL.md` §5 的 `Ast` 怎么规范化成 §6 的 `Ir`」。
> 机器可读的同一份定义在 `src/liveblocks/ir/ops.py`（op 表、`limits` 缺省值、`LB-I0xx` 码表）与
> `src/liveblocks/data/ir_schema.json`（JSON Schema）。两份必须一致——`tests/test_ir_docs.py` 与
> `liveblocks ir report` 会逐项核对（op 表的每一行、诊断码与标题、`limits` 缺省值、schema 文件名），
> 任何一边改了另一边没跟上就会失败。
> IR 改动必须提升 `ir_version` 并记进 `docs/DECISIONS.md`。

## 0. 范围与依据

- **依据**：`docs/ROADMAP.md` LB-006「AST → 确定性 IR，schema 校验，缺省值填充，`limits` 显式化」；
  `docs/DECISIONS.md` D-017（本层的取舍与协议缺口清单）。**不做执行**（LB-007）。
- **输入**：§5 的 `Ast`（LB-005 的产物，每个节点带 `span` 与 `origin`）。
- **输出**：§6 的 `Ir`（`schema_version` + `kind` + `ir_version` + `program{steps, limits}` + `diagnostics`）。
- **方法**：一条 `Command` 折成一步 `step`；`op` 取卡面（§2 的 op 表），参数折成
  `{"<参数名>": <整数>, "unit": "<单位>"}`，来源折成 `{"row", "slot"}`；`limits` 由规范化器填缺省值；
  有问题的语句报一条 `LB-I0xx` 且**不进 IR**。

输入本身不是合法 §5 文档（`kind` 不对、节点缺 `span`/`origin` 等）不是规范化错误：
`normalize_ast_document` 抛 `ValueError`，CLI 以非零码退出，**不**伪装成 `LB-I0xx`。

## 1. 规范化规则（一条 `Command` = 一步）

| 步骤 | 取法 |
| --- | --- |
| `op` | `Command.name` 原样（也就是卡面本身，见 §2） |
| `args` | 按 §2 的参数表命名：一个数值参数 + `unit`；零元 `op` 给空对象 `{}` |
| `source` | `{"row": <span 的起始行>, "slot": <span 的起始槽位>}`，与 §6 示例一致 |
| `step_id` | `s0` / `s1` / …，按**产出的**步骤顺序密集编号（与其它层的 `row_index`/`slot_index` 一样从 0 连续） |
| `limits` | 见 §4，由规范化器填缺省值，调用方可显式覆盖 |
| 顺序 | 就是 `Program.body` 的顺序（= `Layout.rows` 的顺序），不重排 |

`Command.span` 若给了跨行形状（`row_from`/`row_to`），`source.row` 取 `row_from`；LB-005 的解析器
只产出单行 `span`，这条只是兜底。

## 2. op 表（卡面 → `op` / 参数 / 元数 / 单位）

`op` 的取值**只能是**文法定义里的命令卡面（§6 原文「只允许 `op` 取值来自文法定义」），也就是
`docs/GRAMMAR.md` §2 里角色为 `command` 的那些卡面。表里的元数是 **P0 的 IR 语义**（D-016 第 3 条
把 arity 明确留给 LB-006）：参数个数与之不符的语句报 `LB-I002`，**不做截断、不补零**。

| 卡面 `card_face` | `op` | 参数 `args` | 元数 `arity` | 单位 `unit` |
| --- | --- | --- | --- | --- |
| `move-forward` | `move-forward` | `distance` | 1 | `cell` |
| `move-back` | `move-back` | `distance` | 1 | `cell` |
| `turn-left` | `turn-left` | `angle` | 1 | `quarter_turn` |
| `turn-right` | `turn-right` | `angle` | 1 | `quarter_turn` |
| `jump` | `jump` | — | 0 | — |
| `pick` | `pick` | — | 0 | — |
| `drop` | `drop` | — | 0 | — |
| `wait` | `wait` | `duration` | 1 | `tick` |

说明：

- **方向写在 `op` 里**：`move-back` 就是 `op: "move-back"`，不用「负距离」表达，
  因此 `distance` 永远是非负整数。
- **单位只是标签**：`cell`（一格）、`quarter_turn`（四分之一圈）、`tick`（虚拟时钟刻度）都还没有
  物理换算关系，换算属于 LB-007 / Q5（语义模型）。P0 的数值参数是整数（卡片字典里的数字卡只有
  正整数），因此 `args` 里不会出现浮点。
- **零元 `op` 的 `args` 是 `{}`**，不是省略 `args`：字段形状固定，下游不用分情况。
- op 表必须覆盖字典里**全部** `command` 角色卡面：字典新增一张 action 卡后，`op_table_gaps()` 会
  报「命令卡面 X 没有 op 登记」，`liveblocks ir report` 与测试都会失败——必须回来扩表（元数不能猜）。

## 3. `limits`（执行器的硬约束，必须显式）

§6 只说「缺省由运行时补齐，但规范化后必须显式出现」，没给数值；本实现取 §6 示例里那两个数：

| 字段 | 缺省值 | 含义 |
| --- | --- | --- |
| `max_steps` | 1000 | 单次运行允许执行的最大步数（超出属于执行层的 `limit_exceeded`，LB-007） |
| `timeout_ms` | 5000 | 单次运行的挂钟/虚拟时钟上限（毫秒，超出属于执行层的 `timeout`，LB-007） |

- 两个值都必须是 ≥ 1 的整数（0 / 负数不是「限制」，是配置错误：规范化直接抛 `ValueError`）。
- 覆盖入口：`normalize_ast_document(..., max_steps=..., timeout_ms=...)` 与 CLI
  `liveblocks ir normalize --max-steps N --timeout-ms M`；覆盖值照样写进 IR。
- **规范化不负责判「步数超限」**：`len(steps) > max_steps` 是执行层要报的 `ExecResult.status =
  limit_exceeded`（§7.2），本层只把约束显式写下来。

## 4. 确定性

§6 要求「同一 AST 两次规范化结果字节一致（稳定键序、数字格式固定、无时间戳/随机量）」，本实现的做法：

1. 所有对象按固定顺序构造：`schema_version` / `kind` / `ir_version` / `program` /
   `diagnostics`；`steps[]` 里是 `step_id` / `op` / `args` / `source`；`limits` 里是
   `max_steps` / `timeout_ms`。
2. 序列化只走 `Ir.canonical_text()`：`json.dumps(..., ensure_ascii=False, indent=2,
   sort_keys=False)` + 结尾一个换行。**不排序键**（排序会打乱上一条的语义顺序），
   但构造顺序本身是写死的，因此输出仍然逐字节稳定。
3. 数值只有整数：卡片字典里的数字卡只有正整数，`Number.value` 是整数（§5 校验也这么要求），
   所以不存在浮点格式差异。
4. 不含时间戳、随机量、环境信息（`frame_id` 之类的上游标识也不进 IR，见 §8 缺口 2）。
5. 诊断顺序 = 语句顺序；一条语句最多一条诊断（见 §5），因此诊断序列也是确定的。

## 5. 诊断与定位（`LB-I0xx`）

诊断沿用协议 §9 的统一形状 `{code, severity, message, candidate_ids?, span?}`，`severity` 一律
`error`，`span` 指向「第几行的第几个槽位」（与 §5 的 `span` 同一口径）。

**策略：一条语句出问题 = 一条诊断 + 该语句不进 IR。** 沿用 D-016 第 7 条「整行丢弃、只报一条」的
保守策略：不做截断、不补零、不猜默认参数——猜出来的语义下游没法追溯，而原 `Ast` 仍在运行记录里
（§8），诊断的 `span` 能指回去。检查顺序固定（先查节点种类，再查 `op`、参数、元数），因此一条语句
不会同时冒两条诊断。

| 诊断码 | 标题 | 何时报 | 该语句是否进 IR |
| --- | --- | --- | --- |
| `LB-I001` | 未知 op | `Command.name` 不在 §2 的 op 表里（含文法外的控制流卡 `repeat` / `if` / `end`） | 否 |
| `LB-I002` | 参数个数不符 | `Command.args` 的个数与 §2 的元数不符 | 否 |
| `LB-I003` | 语句节点不受支持 | `Program.body` 里的节点不是 `Command`（§5 的 `node` 是开放枚举） | 否 |
| `LB-I004` | 参数不是字面量 | `Command.args` 里出现不是 `Number` 的节点，取不到整数值 | 否 |

补充：

- `LB-I001` 是 §9 已给的示例码（未知 `op`），语义沿用；`LB-I002`…`LB-I004` 由本实现按
  `LB-I0xx` 区间自增（§9 只举了 `LB-I001` 一个例子）。
- **不复制上游诊断**：`Ast.diagnostics`（`LB-P0xx`）留在语法层，`Ir.diagnostics` 只放 `LB-I0xx`；
  一次运行的各层对象都会进运行记录（§8），信息不丢。

## 6. Schema

schema 是**入库的数据文件** `src/liveblocks/data/ir_schema.json`（JSON Schema draft 2020-12，
随 wheel 安装）。校验分两层：

1. **形状**：`liveblocks.ir.schema.validate_document` 按 schema 核（零依赖，只支持 schema 用到的
   那一小撮关键字；schema 里一旦出现不支持的关键字，测试会失败）。schema 只管 §6 明写的结构与
   `LB-I0xx` 的码形，`additionalProperties: true`（§0 要求消费者容忍未知字段）。
2. **语义**：`validate_ir` 再查 schema 表达不了的约束——`op` 必须在 op 表里、`args` 的字段名/单位/
   取值类型必须与 op 表一致、`step_id` 必须密集连续（`s0`、`s1`…）、诊断码必须落在 `LB-I0xx`。

装得上 `jsonschema` 时（`python -m pip install -e '.[dev,schema]'`），`tests/test_ir_protocol.py`
会用它当**独立实现**交叉核对同一批文档（与 LB-003 用 OpenCV 交叉核对同一思路）：本实现与
`Draft202012Validator` 对全部正例/反例结论一致；CI 只装 `.[dev]`，那组用例自动跳过。

## 7. 示例

输入 `fixtures/ir/asts/all-ops.json` 的第一条语句 `move-forward 1`（`Layout` 第 0 行第 0–1 槽位），
规范化后：

```json
{
  "step_id": "s0",
  "op": "move-forward",
  "args": { "distance": 1, "unit": "cell" },
  "source": { "row": 0, "slot": 0 }
}
```

非法样例（`fixtures/ir/asts/unknown-op.json` 第 2 行是一张 `teleport` 卡）：

```json
{
  "code": "LB-I001",
  "severity": "error",
  "message": "行 1 槽位 0 的命令卡 'teleport' 不在 P0 的 op 表里（docs/IR.md §2：op 只能取文法定义里的命令卡面，控制流卡不在 P0）；该语句不进 IR",
  "candidate_ids": ["c-2"],
  "span": { "row": 1, "slot_from": 0, "slot_to": 0 }
}
```

## 8. 明确不做

- 不执行、不做步数与超时的运行时判定（LB-007）。
- 不做常量折叠/表达式求值（P0 只有字面参数，D-005）。
- 不做跨语句优化、不重排步骤（顺序就是 `Program.body` 的顺序）。
- 不改 `docs/PROTOCOL.md`（协议由仓库所有者维护）。

## 9. 已知缺口（只报告，未改 `docs/PROTOCOL.md`）

1. **§6 示例的 `op` 与卡面对不上**：示例写 `op: "move"`，而卡面是 `move-forward`。要让
   `move-back` 也叫 `move`，就必须替它发明「负距离」之类的语义，超出本单范围，因此本实现
   `op` = 卡面（依据 §6 自己那句「`op` 取值来自文法定义」），示例按**示意**处理，等仓库所有者
   决定是否引入「卡面 → op 动词」的映射表（要动就得提升 `ir_version`）。
2. **§6 的 `Ir` 没有指回上游的字段**：`step.source` 只有 `row`/`slot`（示例如此），没有
   `frame_id` / 语法树 id / 卡片 id。本实现**不加**（保守，且 §8 的 `RunHeader` 已经承载帧引用）；
   需要逐卡回溯时看诊断的 `candidate_ids` 或原 `Ast`。
3. **§6 没有定义 `args` 的形状**（示例只给了一个 op 的 `args`）：本实现固定为「至多一个数值参数 +
   `unit`」，零元 `op` 用 `{}`。若将来某个 `op` 要多个带单位的参数，`args` 形状必须重定义。
4. **§6 没有定义元数（arity）**：`docs/GRAMMAR.md`、D-005、D-016 都把它留给 LB-006，本实现按
   卡面语义取最小集（§2）；这属于**新定的语义**，建议并入协议（并在 §2 表旁边注明单位含义）。
5. **§6 说 `limits` 缺省「由运行时补齐」而本层就填了**：本实现在规范化阶段填缺省值（范围项要求
   「`limits` 显式化」），值取示例里的 1000 / 5000；`tick` 与毫秒的换算关系仍未定（Q5）。
6. **§9 只给了 `LB-I001` 一个示例**：`LB-I002`…`LB-I004` 由本实现按区间自增（§5）。
7. **§5 的 `node` 是开放枚举，但 §6 没说未知语句节点怎么办**：本实现报 `LB-I003` 并跳过该语句，
   不猜它的语义（与解析层「未知节点只要求 `span` + `origin`」的降级口径衔接）。
8. **§6 没有 schema 文件**：D-015/D-016 写着「完整的 schema 工具留给 LB-006/LB-009」，本单补了
   `src/liveblocks/data/ir_schema.json`；协议若采用，应与 `ir_version` 一起版本化。

## 10. 命令与校验入口

```bash
# 一份 Ast JSON（协议 §5）→ §6 的 Ir JSON；stdout 可直接管道给下游
liveblocks ir normalize --ast fixtures/ir/asts/all-ops.json

# 显式覆盖 limits：覆盖值会写进 IR
liveblocks ir normalize --ast fixtures/ir/asts/all-ops.json --max-steps 50 --timeout-ms 1200

# 出现 LB-I0xx 时也照常输出 Ir（退出码 0）；加 --fail-on-error 让退出码变 1
liveblocks ir normalize --ast fixtures/ir/asts/unknown-op.json --fail-on-error

# 对 fixtures/ir 全部夹具跑一遍：规范化两次比字节、与 golden 快照逐字节比、跑 schema 校验
liveblocks ir report --dir fixtures/ir
```

夹具与清单（`fixtures/ir/index.json`）的说明见 `fixtures/README.md` §6。
