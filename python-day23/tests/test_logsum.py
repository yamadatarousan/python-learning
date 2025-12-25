"""
Day23: logsum の最小テスト群。

狙い：
- ログの「読み方（パース）」が壊れてないかを軽く押さえる
- 集計（レベル件数 / TOPメッセージ）が想定どおりか確認する
- --out でJSONファイルが書けること（副作用の確認）を押さえる
"""

from __future__ import annotations

import json
from pathlib import Path

import logsum


def test_parse_level_and_message_bracket_style() -> None:
    # テスト意図： "[INFO] hello" 形式を正しく分解できることを確認する
    # 仕様：先頭が [LEVEL] なら LEVEL と本文に分かれる
    level, msg = logsum.parse_level_and_message("[INFO] hello world")
    assert level == "INFO"
    assert msg == "hello world"


def test_parse_level_and_message_dash_style() -> None:
    # テスト意図： "... - INFO - hello" 形式を正しく分解できることを確認する
    # 仕様：行中に "INFO - " があれば INFO と本文に分かれる（loggingの定番形）
    level, msg = logsum.parse_level_and_message("2025-01-01 - app - INFO - started")
    assert level == "INFO"
    assert msg == "started"


def test_parse_level_and_message_unknown_falls_back() -> None:
    # テスト意図：パースできない行が来ても落ちずに UNKNOWN 扱いになることを確認する
    # 仕様：UNKNOWN のときは「元の行」をメッセージとして残す
    level, msg = logsum.parse_level_and_message("??? something weird")
    assert level == "UNKNOWN"
    assert msg == "??? something weird"


def test_aggregate_counts_levels_and_top_messages() -> None:
    # テスト意図：集計（レベル別件数 / TOPメッセージ）が想定どおりか確認する
    # 仕様：
    # - levelは INFO/ERROR/UNKNOWN などで数える
    # - top_n > 0 のときは、同じメッセージが何回出たかをランキングできる
    lines = [
        "[INFO] ok",
        "[INFO] ok",
        "2025-01-01 - app - ERROR - boom",
        "nonsense line",
    ]
    summary = logsum.aggregate(lines, top_n=2)
    assert summary.lines == 4
    assert summary.levels["INFO"] == 2
    assert summary.levels["ERROR"] == 1
    assert summary.levels["UNKNOWN"] == 1
    assert summary.top_messages[0].message in {"ok", "boom", "nonsense line"}
    assert sum(m.count for m in summary.top_messages) <= 4


def test_main_writes_out_json_file(tmp_path: Path) -> None:
    # テスト意図： --out 指定で JSON ファイルが書けることを確認する（副作用の確認）
    # 仕様：
    # - patternに一致するログを集計し、outにJSONを書き込む
    root = tmp_path / "logs"
    root.mkdir()
    (root / "app.log").write_text("[INFO] ok\n[ERROR] boom\n", encoding="utf-8")

    out_path = tmp_path / "report.json"
    rc = logsum.main([str(root), "--pattern", "*.log", "--top", "1", "--out", str(out_path)])
    assert rc == 0
    assert out_path.exists()

    data = json.loads(out_path.read_text(encoding="utf-8"))
    assert data["directory"] == str(root.resolve())
    assert data["pattern"] == "*.log"
    assert data["files"] == 1
    assert data["lines"] == 2
    assert data["levels"]["INFO"] == 1
    assert data["levels"]["ERROR"] == 1
