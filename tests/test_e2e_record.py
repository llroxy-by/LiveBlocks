"""§8 运行记录的写与读（LB-009 的生产侧），以及「记录能被 LB-008 的视图直接打开」。

与 ``tests/test_ui_record.py``（消费侧的容错解析）互补：这里断言**写出去的东西**是
「一行一个对象、首行 RunHeader、按层序排好、末尾一个换行」，并且 `liveblocks.ui` 读同一份
文件时**一条问题都不报**——跨层的兼容性用真实产物证明，不靠口头约定。
"""

from __future__ import annotations

import json
from pathlib import Path

from liveblocks.e2e.protocol import (
    CODE_BAD_LINE,
    KIND_RUN_HEADER,
    REQUIRED_KINDS,
    STAGES,
)
from liveblocks.e2e.record import (
    canonical_json,
    fingerprint,
    jsonl_line,
    read_documents,
    record_text,
    sha256_text,
    short_hash,
    write_record,
)


def test_record_text_is_one_object_per_line_with_trailing_newline() -> None:
    documents = [{"a": 1}, {"b": [1, 2]}]
    text = record_text(documents)
    assert text == '{"a": 1}\n{"b": [1, 2]}\n'
    assert text.endswith("\n")
    assert len(text.splitlines()) == 2


def test_jsonl_line_keeps_insertion_order() -> None:
    document = {"z": 1, "a": 2}
    assert jsonl_line(document) == '{"z": 1, "a": 2}'


def test_canonical_json_sorts_keys_and_drops_whitespace() -> None:
    assert canonical_json({"z": 1, "a": [2, 3]}) == '{"a":[2,3],"z":1}'


def test_write_record_creates_parents_and_round_trips(tmp_path: Path) -> None:
    path = write_record(tmp_path / "deep" / "dir" / "record.jsonl", [{"kind": "X"}])
    assert path.is_file()
    assert path.read_text(encoding="utf-8") == '{"kind": "X"}\n'
    documents, issues = read_documents(path)
    assert documents == [{"kind": "X"}]
    assert issues == []


def test_read_documents_skips_blank_lines(tmp_path: Path) -> None:
    path = tmp_path / "r.jsonl"
    path.write_text('{"a": 1}\n\n{"b": 2}\n', encoding="utf-8")
    documents, issues = read_documents(path)
    assert documents == [{"a": 1}, {"b": 2}]
    assert issues == []


def test_read_documents_reports_bad_json_lines(tmp_path: Path) -> None:
    path = tmp_path / "r.jsonl"
    path.write_text('{"a": 1}\n{"b": \n[1, 2]\n', encoding="utf-8")
    documents, issues = read_documents(path)
    assert documents == [{"a": 1}]
    assert [(item.code, item.line) for item in issues] == [
        (CODE_BAD_LINE, 2),
        (CODE_BAD_LINE, 3),
    ]


def test_fingerprint_is_independent_of_key_order_and_whitespace() -> None:
    left = [{"a": 1, "b": {"c": 2}}, {"d": 3}]
    right = [{"b": {"c": 2}, "a": 1}, {"d": 3}]
    assert fingerprint(left) == fingerprint(right)
    assert fingerprint(left) == sha256_text(canonical_json(left[0]) + "\n" + canonical_json(left[1]) + "\n")


def test_fingerprint_changes_with_content_and_is_stable_for_empty() -> None:
    assert fingerprint([{"a": 1}]) != fingerprint([{"a": 2}])
    assert fingerprint([]) == fingerprint(())
    assert len(fingerprint([])) == 64


def test_short_hash_keeps_short_values_and_truncates_long_ones() -> None:
    assert short_hash("abc") == "abc"
    assert short_hash("a" * 64) == "a" * 12


# --- 真实产物的形状（用一次真链路的结果） -------------------------------------


def texts_order_ok(documents: list[dict]) -> bool:
    """层序检查：``KIND_RANK`` 单调不减（事件流可以重复）。"""
    from liveblocks.e2e.protocol import KIND_RANK

    ranks = [KIND_RANK[document["kind"]] for document in documents[1:]]
    return ranks == sorted(ranks)


def test_written_record_starts_with_run_header_and_has_every_layer_kind(e2e_normal_run) -> None:
    run = e2e_normal_run
    text = Path(run.record_path).read_text(encoding="utf-8")
    documents = [json.loads(line) for line in text.splitlines() if line.strip()]
    assert documents[0]["kind"] == KIND_RUN_HEADER
    assert documents[0]["run_id"] == "run-e2e-normal"
    kinds = {document["kind"] for document in documents[1:]}
    for kind in REQUIRED_KINDS:
        assert kind in kinds
    assert texts_order_ok(documents)


def test_record_timings_cover_all_six_stages(e2e_normal_run) -> None:
    header = e2e_normal_run.header
    assert set(header["timings_ms"]) == set(STAGES)
    assert all(isinstance(value, float) or isinstance(value, int) for value in header["timings_ms"].values())
    assert all(value >= 0 for value in header["timings_ms"].values())


def test_record_image_ref_points_to_a_real_file(e2e_normal_run) -> None:
    header = e2e_normal_run.header
    image_ref = header["frame"]["image_ref"]
    assert (Path(e2e_normal_run.record_path).parent / image_ref).is_file()
    assert (Path(e2e_normal_run.record_path).parent / header["generation"]["ground_truth_ref"]).is_file()


def test_lb008_view_consumes_the_record_without_complaints(e2e_normal_run) -> None:
    """跨层验收：LB-008 的视图读 LB-009 产出的记录，一条问题都不报。"""
    from liveblocks.ui.protocol import LAYER_ORDER
    from liveblocks.ui.record import parse_record_file

    record = parse_record_file(e2e_normal_run.record_path)
    assert [item.describe() for item in record.issues] == []
    assert record.run_id == "run-e2e-normal"
    assert record.backend == "sim"
    assert record.missing_layers == ()
    assert set(record.timings) == {"vision", "layout", "parser", "ir", "exec"}
    assert record.present_kinds, "记录里应当有层对象"


def test_lb008_view_renders_all_five_panels(e2e_normal_run) -> None:
    from liveblocks.ui.protocol import LAYER_TITLES
    from liveblocks.ui.render import build_view_from_file

    view = build_view_from_file(e2e_normal_run.record_path)
    assert all(view.layer_present[layer] for layer in ("vision", "layout", "ast", "ir", "exec"))
    html = view.render_html()
    for title in LAYER_TITLES.values():
        assert title in html


def test_record_does_not_carry_wall_clock_outside_timings(e2e_normal_run) -> None:
    """基准比对的前提：除 ``timings_ms`` 外记录里没有随机器变化的字段。"""
    header = dict(e2e_normal_run.header)
    header.pop("timings_ms")
    text = json.dumps(header, ensure_ascii=False, sort_keys=True)
    assert "wall_ms" not in text
    assert "elapsed" not in text
    assert "2026-" not in text  # 不写时间戳

