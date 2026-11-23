"""CLI 测试：synth 子命令的列出 / 生成 / 校验行为。"""

from __future__ import annotations

import json

import pytest

from liveblocks import cli
from liveblocks.cli import DEFAULT_SEED


def test_version_still_works(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--version"]) == 0
    assert capsys.readouterr().out.strip().startswith("liveblocks ")


def test_synth_list(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["synth", "list"]) == 0
    out = capsys.readouterr().out
    assert "DICT_4X4_50" in out
    for scene in ("normal", "slight-rotation", "overlap", "clipped", "low-light", "multi-row"):
        assert scene in out
    assert "C01" in out


def test_synth_generate_single_scene(tmp_path, capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = cli.main(
        ["synth", "generate", "--scene", "normal", "--seed", "7", "--out", str(tmp_path)]
    )
    assert exit_code == 0
    image = tmp_path / "normal.png"
    truth = tmp_path / "normal.json"
    assert image.is_file() and truth.is_file()
    payload = json.loads(truth.read_text(encoding="utf-8"))
    assert payload["seed"] == 7
    assert payload["scene_id"] == "normal"
    assert image.read_bytes().startswith(b"\x89PNG")
    assert "生成 normal" in capsys.readouterr().out


def test_synth_generate_multiple_scenes(tmp_path) -> None:
    exit_code = cli.main(
        [
            "synth",
            "generate",
            "--scene",
            "normal,clipped",
            "--seed",
            str(DEFAULT_SEED),
            "--out",
            str(tmp_path),
        ]
    )
    assert exit_code == 0
    assert (tmp_path / "normal.png").is_file()
    assert (tmp_path / "clipped.png").is_file()
    # 没有生成全部场景时不应该写索引
    assert not (tmp_path / "index.json").exists()


def test_synth_generate_all_writes_index(tmp_path) -> None:
    assert cli.main(["synth", "generate", "--scene", "all", "--out", str(tmp_path)]) == 0
    index = json.loads((tmp_path / "index.json").read_text(encoding="utf-8"))
    assert index["seed"] == DEFAULT_SEED
    assert len(index["scenes"]) >= 5


def test_synth_generate_is_reproducible_across_runs(tmp_path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    args = ["synth", "generate", "--scene", "overlap", "--seed", "123", "--out"]
    assert cli.main([*args, str(first)]) == 0
    assert cli.main([*args, str(second)]) == 0
    assert (first / "overlap.png").read_bytes() == (second / "overlap.png").read_bytes()
    assert (first / "overlap.json").read_text(encoding="utf-8") == (
        second / "overlap.json"
    ).read_text(encoding="utf-8")


def test_synth_generate_rejects_unknown_scene(tmp_path) -> None:
    with pytest.raises(SystemExit):
        cli.main(["synth", "generate", "--scene", "nope", "--out", str(tmp_path)])


def test_synth_verify_fixtures(capsys: pytest.CaptureFixture[str]) -> None:
    from pathlib import Path

    fixtures = Path(__file__).resolve().parents[1] / "fixtures" / "scenes"
    assert cli.main(["synth", "verify", "--dir", str(fixtures)]) == 0
    out = capsys.readouterr().out
    assert "OK" in out
    assert "完全一致" in out


def test_synth_verify_reports_missing_directory(tmp_path, capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = cli.main(["synth", "verify", "--dir", str(tmp_path)])
    assert exit_code == 1
    assert "FAIL" in capsys.readouterr().out
