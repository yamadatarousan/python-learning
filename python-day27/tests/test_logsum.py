"""
Day26: logsum のテスト

狙い：
- logsum 固有の仕様（format解析、集計、優先順位）を確認する
- 共通I/O部品（toolkit）は既存テストで担保されている前提で、ここでは “使い方” に寄せる
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import logsum
import toolkit


def test_compute_log_stats_bracket_counts_levels_and_top_messages() -> None:
    # テスト意図：bracket形式のレベル集計と top_messages が想定どおりか確認する
    lines = [
        "[INFO] hello\n",
        "[INFO] hello\n",
        "[WARN] oh\n",
        "broken line\n",
    ]
    logger = toolkit.setup_logger("test", False)
    stats = logsum.compute_log_stats(lines, fmt="bracket", top_n=2, logger=logger)

    assert stats.total_lines == 4
    assert stats.by_level["INFO"] == 2
    assert stats.by_level["WARN"] == 1
    assert stats.by_level["UNKNOWN"] == 1
    assert stats.top_messages[0].message == "hello"
    assert stats.top_messages[0].count == 2


def test_compute_log_stats_jsonl_parses_message_keys() -> None:
    # テスト意図：jsonl形式で message/msg のどちらでも読めることを確認する
    lines = [
        '{"level":"info","message":"A"}\n',
        '{"level":"info","msg":"B"}\n',
        '{"level":"warn","message":"A"}\n',
    ]
    logger = toolkit.setup_logger("test", False)
    stats = logsum.compute_log_stats(lines, fmt="jsonl", top_n=10, logger=logger)

    assert stats.total_lines == 3
    assert stats.by_level["INFO"] == 2
    assert stats.by_level["WARN"] == 1

    # A は2回、B は1回
    top = {m.message: m.count for m in stats.top_messages}
    assert top["A"] == 2
    assert top["B"] == 1


def test_main_writes_out_file_when_env_file_sets_json_and_out(tmp_path: Path) -> None:
    # テスト意図：env-file で json/out を有効化すると out に保存されることを確認する
    # 仕様：
    # - LOGSUM_JSON=true なら JSON を出力する（= payload が作られる）
    # - LOGSUM_OUT があれば、そのパスに payload を書き込む（toolkit.write_json_file 経由）
    log_path = tmp_path / "app.log"
    log_path.write_text("[INFO] hello\n[WARN] oh\n", encoding="utf-8")

    out_path = tmp_path / "report.env.json"
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
                f"LOGSUM_INPUT={log_path}",
                "LOGSUM_FORMAT=bracket",
                "LOGSUM_TOP=1",
                "LOGSUM_JSON=true",
                f"LOGSUM_OUT={out_path}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    rc = logsum.main(["--env-file", str(env_path)])
    assert rc == 0
    assert out_path.exists()

    data = json.loads(out_path.read_text(encoding="utf-8"))
    assert data["format"] == "bracket"
    assert data["top_n"] == 1
    assert data["total_lines"] == 2


def test_validate_args_rejects_missing_input_file(tmp_path: Path) -> None:
    # テスト意図：存在しない入力ファイルを指定したら終了コード2になることを確認する
    missing = tmp_path / "missing.log"
    args = logsum.parse_args([str(missing)])
    rc = logsum.validate_args(args, missing)
    assert rc == 2
