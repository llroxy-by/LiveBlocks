#!/usr/bin/env python3
"""生成 / 校验 ``fixtures/runs/`` 里的 §8 运行记录（JSONL）。

LB-008 的界面消费「一次运行的 JSONL 记录」，但**落盘的运行记录属于 LB-009**。
所以这里只做一件本单范围内的事：把已有各层**真跑一遍**，把它们的产物按 §8 的顺序
拼成记录，用来给调试视图当输入。三层来源都写清在 ``fixtures/runs/index.json`` 里：

- ``sample-clipped`` / ``sample-multi-row``：**真实全链路**（识别 → 排列 → 语法 → IR → 模拟执行），
  逐层用 ``time.perf_counter`` 实测耗时写进 ``RunHeader.timings_ms``；
- ``sample-all-ops``：**只有执行层**的局部记录（LB-006 的 golden ``Ir`` 真跑一遍），
  用来演示多步回放，同时是「缺层提示」的真实形态；
- ``corrupt/*``：故意写坏的记录（坏 JSON、缺字段、乱序、重复、坏帧引用、坏事件流），
  用来证明界面「给可见提示而不是崩溃」。

用法：

.. code-block:: bash

   python scripts/build_run_records.py --out fixtures/runs        # 重新生成（含 index.json）
   python scripts/build_run_records.py --out fixtures/runs --check # 只校验，不写盘

``--check`` 会用当前各层代码重跑一遍，逐字段比对层文档（``timings_ms`` 除外——它是实测耗时，
不同机器/不同次运行必然不同），因此上游层一改就会立刻失败。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from liveblocks.ir import normalize_document  # noqa: E402
from liveblocks.layout import layout_from_card_list  # noqa: E402
from liveblocks.parser import parse_layout_document  # noqa: E402
from liveblocks.runtime import SimExecutor, ir_from_document  # noqa: E402
from liveblocks.vision import recognize_path  # noqa: E402

SCHEMA_VERSION = "0.0.1"
UI_VERSION = "0.0.1"

#: 真实全链路样例：场景 → 记录名。
CHAIN_SAMPLES = (
    ("clipped", "sample-clipped", "裁切场景的两张卡里有一张能构成完整语句：1 步 / 3 条事件"),
    ("multi-row", "sample-multi-row", "两行六卡：排列有两行，语法层报 LB-P002 / LB-P004，IR 因此 0 步"),
)

#: 只有执行层的局部记录：Ir 来源 + 记录名。
EXEC_ONLY_SAMPLES = (("all-ops", "sample-all-ops", "LB-006 的 all-ops golden Ir（8 步 / 17 条事件）真跑一遍"),)


def _jsonl_line(document: dict[str, Any]) -> str:
    return json.dumps(document, ensure_ascii=False)


def _ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000.0, 3)


def build_chain_lines(scene: str) -> tuple[list[dict[str, Any]], dict[str, float], dict[str, Any]]:
    """跑一遍真实全链路，返回 ``(按 §8 顺序的文档列表, timings_ms, 统计)``。"""
    image = REPO_ROOT / "fixtures" / "scenes" / f"{scene}.png"
    run_id = f"run-{scene}"
    timings: dict[str, float] = {}

    started = time.perf_counter()
    recognition = recognize_path(image, frame_id=f"f-{scene}", source="synthetic")
    timings["vision"] = _ms(started)

    frame = recognition.frame.to_dict()
    frame["image_ref"] = f"fixtures/scenes/{scene}.png"

    started = time.perf_counter()
    layout = layout_from_card_list(recognition.cards)
    timings["layout"] = _ms(started)

    started = time.perf_counter()
    ast = parse_layout_document(layout.to_dict())
    timings["parser"] = _ms(started)

    started = time.perf_counter()
    ir = normalize_document(ast.to_dict())
    timings["ir"] = _ms(started)

    started = time.perf_counter()
    ir_doc = ir.to_dict()
    run = SimExecutor(run_id=run_id).run(ir_from_document(ir_doc))
    timings["exec"] = _ms(started)

    markers = recognition.markers.to_dict()
    markers["frame_id"] = frame["frame_id"]
    cards = recognition.cards.to_dict()
    cards["frame_id"] = frame["frame_id"]
    layout_doc = layout.to_dict()
    layout_doc["frame_id"] = frame["frame_id"]
    ast_doc = ast.to_dict()
    ast_doc["frame_id"] = frame["frame_id"]

    documents: list[dict[str, Any]] = [markers, cards, layout_doc, ast_doc, ir_doc]
    documents.extend(json.loads(line) for line in run.events_text().splitlines() if line.strip())
    documents.append(json.loads(run.result_text()))

    stats = {
        "markers": len(markers["markers"]),
        "cards": len(cards["cards"]),
        "rows": len(layout_doc["rows"]),
        "steps": len(ir_doc["program"]["steps"]),
        "events": len(run.events),
        "status": run.status,
    }
    return documents, timings, stats


def build_exec_only_lines(name: str) -> tuple[list[dict[str, Any]], dict[str, float], dict[str, Any]]:
    """只有执行层的局部记录：``fixtures/ir/golden/<name>.json`` 真跑一遍。"""
    ir_doc = json.loads((REPO_ROOT / "fixtures" / "ir" / "golden" / f"{name}.json").read_text(encoding="utf-8"))
    run_id = f"run-{name}"
    timings: dict[str, float] = {}
    started = time.perf_counter()
    run = SimExecutor(run_id=run_id).run(ir_from_document(ir_doc))
    timings["exec"] = _ms(started)
    documents: list[dict[str, Any]] = [ir_doc]
    documents.extend(json.loads(line) for line in run.events_text().splitlines() if line.strip())
    documents.append(json.loads(run.result_text()))
    stats = {
        "markers": 0,
        "cards": 0,
        "rows": 0,
        "steps": len(ir_doc["program"]["steps"]),
        "events": len(run.events),
        "status": run.status,
    }
    return documents, timings, stats


def header(
    *,
    run_id: str,
    scene: str,
    frame: dict[str, Any] | None,
    timings: dict[str, float],
    sources: tuple[str, ...],
) -> dict[str, Any]:
    document: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "kind": "RunHeader",
        "run_id": run_id,
        "backend": "sim",
        "created_with": {
            "name": "liveblocks",
            "package_version": SCHEMA_VERSION,
            "ui_version": UI_VERSION,
            "layer_sources": list(sources),
        },
        "scene": scene,
    }
    if frame is not None:
        document["frame"] = frame
    document["timings_ms"] = dict(timings)
    return document


def write_record(path: Path, lines: list[dict[str, Any]]) -> None:
    path.write_text("\n".join(_jsonl_line(line) for line in lines) + "\n", encoding="utf-8")


# --- 损坏记录（故意写坏，用来证明「给提示而不是崩溃」） -------------------------


def _corrupt_documents() -> dict[str, list[str]]:
    """构造六份损坏记录（静态文本，逐字节可复现）。"""

    card = {
        "schema_version": SCHEMA_VERSION,
        "kind": "CardCandidateList",
        "frame_id": "f-corrupt",
        "cards": [
            {"candidate_id": "c-0", "card_id": "C01", "card_type": "action", "card_face": "move-forward",
             "center_px": [120, 140], "confidence": 0.9, "status": "ok"},
            {"candidate_id": "c-1", "card_id": "C02", "card_type": "action", "card_face": "move-back",
             "confidence": 0.8, "status": "ok"},
        ],
    }
    layout = {
        "schema_version": SCHEMA_VERSION,
        "kind": "Layout",
        "frame_id": "f-corrupt",
        "reading_direction": "ltr_ttb",
        "rows": [{"row_index": 0, "slots": [{"slot_index": 0, "candidate_id": "c-0",
                                             "card_face": "move-forward", "x_px": 120, "y_px": 140}]}],
    }
    ast = {
        "schema_version": SCHEMA_VERSION,
        "kind": "Ast",
        "frame_id": "f-corrupt",
        "grammar_version": SCHEMA_VERSION,
        "root": {"node": "Program", "span": {"row_from": 0, "row_to": 0, "slot_from": 0, "slot_to": 0},
                 "body": []},
    }
    ir = {
        "schema_version": SCHEMA_VERSION,
        "kind": "Ir",
        "ir_version": SCHEMA_VERSION,
        "program": {"steps": [], "limits": {"max_steps": 1000, "timeout_ms": 5000}},
    }
    event = {
        "schema_version": SCHEMA_VERSION,
        "kind": "ExecEvent",
        "run_id": "run-corrupt",
        "seq": 1,
        "at_ms": 0.0,
        "step_id": "",
        "phase": "log",
        "backend": "sim",
        "detail": {"message": "run started"},
    }
    result = {
        "schema_version": SCHEMA_VERSION,
        "kind": "ExecResult",
        "run_id": "run-corrupt",
        "status": "ok",
        "steps_executed": 0,
        "duration_ms": 0.0,
        "backend": "sim",
        "diagnostics": [],
    }
    good_header = {
        "schema_version": SCHEMA_VERSION,
        "kind": "RunHeader",
        "run_id": "run-corrupt",
        "backend": "sim",
        "frame": {"frame_id": "f-corrupt", "image_ref": "fixtures/scenes/clipped.png",
                  "width": 640, "height": 480, "source": "synthetic"},
        "timings_ms": {"vision": 1.5, "layout": 0.4, "parser": 0.2, "ir": 0.2, "exec": 0.1},
    }

    def line(document: dict[str, Any]) -> str:
        return _jsonl_line(document)

    out: dict[str, list[str]] = {}

    # 1) 坏 JSON：第 4 行截断
    bad = [line(good_header), line(card), line(layout)]
    bad.append('{"schema_version": "0.0.1", "kind": "Ast", "root": {"node": "Prog')
    bad.append(line(ast))
    bad.append(line(ir))
    bad.append(line(event))
    bad.append(line(result))
    out["bad-json.jsonl"] = bad

    # 2) 缺字段：header 缺 run_id/backend/timings，卡片缺 center_px，事件缺 seq / phase
    header_missing = {
        "schema_version": SCHEMA_VERSION,
        "kind": "RunHeader",
        "frame": {"frame_id": "f-corrupt", "image_ref": "fixtures/scenes/clipped.png", "width": 640, "height": 480},
    }
    event_broken = {
        "schema_version": SCHEMA_VERSION,
        "kind": "ExecEvent",
        "run_id": "run-corrupt",
        "at_ms": 0.0,
        "step_id": "",
        "backend": "sim",
        "detail": {},
    }
    out["missing-fields.jsonl"] = [
        line(header_missing),
        line(card),
        line(layout),
        line(ast),
        line(ir),
        line(event_broken),
        line({"schema_version": SCHEMA_VERSION, "kind": "ExecResult", "duration_ms": 0.0}),
    ]

    # 3) 没有 RunHeader：第一条就是 Layout
    out["no-header.jsonl"] = [line(layout), line(ast), line(ir), line(result)]

    # 4) 未知 kind + 重复层 + 乱序：Ast 跑到 Layout 前面，Layout 出现两次
    mystery = {"schema_version": SCHEMA_VERSION, "kind": "MysteryLayer", "payload": {"why": "not in §8"}}
    out["unknown-kind.jsonl"] = [
        line(good_header),
        line(card),
        line(ast),
        line(layout),
        line(layout),
        line(mystery),
        line(ir),
        line(event),
        line(result),
    ]

    # 5) 坏帧引用：image_ref 指向不存在的文件
    bad_frame_header = dict(good_header)
    bad_frame_header["frame"] = {
        "frame_id": "f-corrupt",
        "image_ref": "fixtures/scenes/does-not-exist.png",
        "width": 640,
        "height": 480,
    }
    out["bad-frame.jsonl"] = [
        line(bad_frame_header), line(card), line(layout), line(ast), line(ir), line(event), line(result)
    ]

    # 6) 坏事件流：seq 不递增、at_ms 回退、phase 缺失
    def ev(seq: int, at_ms: float, phase: str | None, step_id: str) -> dict[str, Any]:
        document: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "kind": "ExecEvent",
            "run_id": "run-corrupt",
            "seq": seq,
            "at_ms": at_ms,
            "step_id": step_id,
            "backend": "sim",
            "detail": {"op": "move-forward"},
        }
        if phase is not None:
            document["phase"] = phase
        return document

    out["bad-events.jsonl"] = [
        line(good_header),
        line(card),
        line(layout),
        line(ast),
        line(ir),
        line(ev(1, 0.0, "log", "")),
        line(ev(2, 20.0, "start", "s0")),
        line(ev(1, 10.0, None, "s0")),
        line(result),
    ]
    return out


CORRUPT_NOTES = {
    "bad-json.jsonl": (
        "第 4 行是截断的 JSON",
        "整行丢弃并报 LB-U001，其余层照常渲染（不是整份记录失败）",
        ["LB-U001"],
    ),
    "missing-fields.jsonl": (
        "header 缺 run_id/backend/timings_ms，卡片缺 center_px，事件缺 seq/phase，result 缺 status",
        "逐个字段报 LB-U003 / LB-U009，缺的条目跳过，界面显示占位",
        ["LB-U003", "LB-U009"],
    ),
    "no-header.jsonl": (
        "首行是 Layout，没有 RunHeader",
        "报 LB-U006 且继续解析后面的层（缺 vision / ast 层另有 LB-U004）",
        ["LB-U004", "LB-U006"],
    ),
    "unknown-kind.jsonl": (
        "出现 §8 没有的 kind，Ast 早于 Layout，Layout 重复一次",
        "报 LB-U002 / LB-U005，未知行忽略、重复层只保留第一份",
        ["LB-U002", "LB-U005"],
    ),
    "bad-frame.jsonl": (
        "RunHeader.frame.image_ref 指向不存在的文件",
        "报 LB-U007，叠加面板显示占位图，其余四层照常",
        ["LB-U007"],
    ),
    "bad-events.jsonl": (
        "事件 seq 不递增、at_ms 回退、phase 缺失",
        "报 LB-U008 / LB-U003，事件按出现顺序展示，不排序、不修补",
        ["LB-U008", "LB-U003"],
    ),
}


def write_corrupt(directory: Path) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    written = []
    for name, lines in sorted(_corrupt_documents().items()):
        path = directory / name
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        written.append(path)
    return written


def generate(out_dir: Path) -> dict[str, Any]:
    """重新生成样例记录、损坏记录与 ``index.json`` 的骨架（哈希由 ``finish_manifest`` 补）。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    entries: list[dict[str, Any]] = []
    for scene, name, note in CHAIN_SAMPLES:
        documents, timings, stats = build_chain_lines(scene)
        frame = {
            "frame_id": f"f-{scene}",
            "image_ref": f"fixtures/scenes/{scene}.png",
            "width": 640,
            "height": 480,
            "source": "synthetic",
        }
        lines = [header(run_id=f"run-{scene}", scene=scene, frame=frame, timings=timings,
                        sources=("vision", "layout", "parser", "ir", "exec"))]
        lines.extend(documents)
        path = out_dir / f"{name}.jsonl"
        write_record(path, lines)
        entries.append(
            {
                "name": name,
                "file": path.name,
                "family": "sample",
                "source": "真跑出来的：scripts/build_run_records.py（识别 → 排列 → 语法 → IR → 模拟执行）",
                "note": note,
                "stats": stats,
            }
        )
        print(f"写入 {path}（{len(lines)} 行，事件 {stats['events']}，步数 {stats['steps']}）")

    for ir_name, name, note in EXEC_ONLY_SAMPLES:
        documents, timings, stats = build_exec_only_lines(ir_name)
        lines = [
            header(
                run_id=f"run-{ir_name}",
                scene="",
                frame=None,
                timings=timings,
                sources=("exec",),
            )
        ]
        lines.extend(documents)
        path = out_dir / f"{name}.jsonl"
        write_record(path, lines)
        entries.append(
            {
                "name": name,
                "file": path.name,
                "family": "partial",
                "source": f"真跑出来的：fixtures/ir/golden/{ir_name}.json 交给模拟执行器（只有执行层）",
                "note": note,
                "stats": stats,
            }
        )
        print(f"写入 {path}（{len(lines)} 行，事件 {stats['events']}，步数 {stats['steps']}）")

    written = write_corrupt(out_dir / "corrupt")
    for path in written:
        note, expectation, codes = CORRUPT_NOTES[path.name]
        entries.append(
            {
                "name": f"corrupt-{path.stem}",
                "file": f"corrupt/{path.name}",
                "family": "corrupt",
                "source": "手写：故意写坏的记录，用证明「给可见提示而不是崩溃」",
                "note": f"{note} → {expectation}",
            }
        )
        print(f"写入 {path}（损坏夹具，期望问题码 {', '.join(codes)}）")

    provisional = {
        "schema_version": SCHEMA_VERSION,
        "kind": "RunRecordIndex",
        "ui_version": UI_VERSION,
        "generator": {"name": "liveblocks.ui", "package_version": SCHEMA_VERSION},
        "fixtures": entries,
    }
    (out_dir / "index.json").write_text(
        json.dumps(provisional, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return provisional


def finish_manifest(out_dir: Path) -> dict[str, Any]:
    """渲染全部夹具，把期望值与产物哈希写进 ``index.json``。"""
    from liveblocks.ui.report import render_fixture

    manifest_path = out_dir / "index.json"
    entries = json.loads(manifest_path.read_text(encoding="utf-8"))["fixtures"]
    for entry in entries:
        path = out_dir / entry["file"]
        fixture = render_fixture(path, name=entry["name"], family=entry["family"])
        if fixture.error:
            raise SystemExit(f"{entry['name']} 渲染失败：{fixture.error}")
        expect: dict[str, Any] = {
            "layers": list(fixture.layers),
            "events": fixture.events,
            "steps": fixture.steps,
            "issue_codes": list(fixture.issue_codes),
            "hashes": dict(fixture.hashes),
        }
        entry["expect"] = expect
    document = {
        "schema_version": SCHEMA_VERSION,
        "kind": "RunRecordIndex",
        "ui_version": UI_VERSION,
        "generator": {"name": "liveblocks.ui", "package_version": SCHEMA_VERSION},
        "fixtures": entries,
    }
    manifest_path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"写入清单 {manifest_path}（{len(entries)} 份夹具）")
    return document


def check(out_dir: Path) -> int:
    """用当前代码重跑链路，逐字段比对层文档（``timings_ms`` 除外）。"""
    failures: list[str] = []
    for scene, name, _note in CHAIN_SAMPLES:
        path = out_dir / f"{name}.jsonl"
        if not path.is_file():
            failures.append(f"{path} 不存在")
            continue
        expected, _timings, _stats = build_chain_lines(scene)
        recorded = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        body = [document for document in recorded if document.get("kind") != "RunHeader"]
        if len(body) != len(expected):
            failures.append(f"{name}: 行数 {len(body)} != 重跑的 {len(expected)}")
            continue
        for index, (recorded_doc, fresh_doc) in enumerate(zip(body, expected)):
            if recorded_doc != fresh_doc:
                failures.append(f"{name}: 第 {index + 2} 行的 {recorded_doc.get('kind')} 与重跑结果不同")
    for ir_name, name, _note in EXEC_ONLY_SAMPLES:
        path = out_dir / f"{name}.jsonl"
        if not path.is_file():
            failures.append(f"{path} 不存在")
            continue
        expected, _timings, _stats = build_exec_only_lines(ir_name)
        recorded = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        body = [document for document in recorded if document.get("kind") != "RunHeader"]
        if body != expected:
            failures.append(f"{name}: 层文档与重跑结果不同")
    corrupt_dir = out_dir / "corrupt"
    for name, lines in sorted(_corrupt_documents().items()):
        path = corrupt_dir / name
        if not path.is_file():
            failures.append(f"{path} 不存在")
            continue
        if path.read_text(encoding="utf-8") != "\n".join(lines) + "\n":
            failures.append(f"{name}: 损坏夹具内容与脚本生成的不同（有人手改过？）")
    if failures:
        print("校验失败：")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("校验通过：样例记录的层文档与当前各层代码一致，损坏夹具与脚本一致")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成 / 校验 fixtures/runs 的运行记录")
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "fixtures" / "runs")
    parser.add_argument("--check", action="store_true", help="只校验，不写盘")
    args = parser.parse_args(argv)
    if args.check:
        return check(args.out)
    generate(args.out)
    finish_manifest(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
