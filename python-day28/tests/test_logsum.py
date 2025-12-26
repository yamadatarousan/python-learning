"""
Day27: logsum のテスト

狙い：
- logsum 固有の仕様（ログの解釈と集計）が守られているか確認する
- env-file で json/out を有効化したときに、ファイルに JSON が書けることを確認する
"""

from __future__ import annotations

import json
from pathlib import Path

import logsum


def test_parse_bracket_line_extracts_level_and_message() -> None:
    # テスト意図：[LEVEL] message 形式から level/message が取れることを確認する
    ev = logsum.parse_bracket_line("[INFO] hello world\n")
    assert ev.level == "INFO"
    assert ev.message == "hello world"


def test_parse_bracket_line_falls_back_to_unknown() -> None:
    # テスト意図：想定外の形式は UNKNOWN 扱いになることを確認する
    ev = logsum.parse_bracket_line("no bracket here\n")
    assert ev.level == "UNKNOWN"
    assert ev.message == "no bracket here"


def test_compute_summary_counts_levels_and_top_messages() -> None:
    # テスト意図：levelごとの件数と message の上位N件が想定通りになるか確認する
    events = [
        logsum.LogEvent(level="INFO", message="a"),
        logsum.LogEvent(level="INFO", message="a"),
        logsum.LogEvent(level="WARN", message="b"),
        logsum.LogEvent(level="INFO", message="c"),
    ]
    summary = logsum.compute_summary(events, top_n=2)

    assert summary.total_lines == 4
    assert summary.by_level["INFO"] == 3
    assert summary.by_level["WARN"] == 1
    assert summary.top_messages[0] == ("a", 2)


def test_main_writes_out_file_when_env_file_sets_json_and_out(tmp_path: Path) -> None:
    # テスト意図：「env-file で json/out を有効化すると、stdout が JSON になりつつ out にも保存される」ことを確認する
    log_path = tmp_path / "app.log"
    log_path.write_text(
        "\n".join(
            [
                "[INFO] hello",
                "[INFO] hello",
                "[WARN] boom",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    out_path = tmp_path / "report.logsum.json"
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
                f"LOGSUM_PATH={log_path}",
                "LOGSUM_FORMAT=bracket",
                "LOGSUM_TOP=2",
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
    assert data["source"] == str(log_path.resolve())
    assert data["total_lines"] == 3
    assert data["by_level"]["INFO"] == 2
    assert data["by_level"]["WARN"] == 1
