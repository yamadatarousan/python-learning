"""
Day28: logsum のテスト。

狙い：
- logsum 側の「logsum 固有の仕様」をテストする
- 共通処品（toolkit）は、logsum/dirscan から呼ばれる形で自然にカバーされる
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import logsum
import toolkit


def test_parse_line_understands_bracket_and_colon() -> None:
    # テスト意図：ログ1行の解釈ルールが想定どおりか確認する
    # 仕様：
    # - "[INFO] x" -> level=INFO, message="x"
    # - "WARN: y"  -> level=WARN, message="y"
    # - 空行は None
    r = logsum.parse_line("[info] hello\n")
    assert r is not None
    assert r.level == "INFO"
    assert r.message == "hello"

    r = logsum.parse_line("warn:  something\n")
    assert r is not None
    assert r.level == "WARN"
    assert r.message == "something"

    assert logsum.parse_line("\n") is None


def test_compute_stats_filters_by_level_and_builds_top_messages() -> None:
    # テスト意図：集計ロジック（levelフィルタ、top_n）が効いているか確認する
    records = [
        logsum.Record(level="INFO", message="a"),
        logsum.Record(level="INFO", message="a"),
        logsum.Record(level="WARN", message="b"),
        logsum.Record(level="INFO", message="c"),
    ]

    stats = logsum.compute_stats(records, level_filter="INFO", top_n=2)
    assert stats.total_lines == 3
    assert stats.level_counts["INFO"] == 3
    assert ("WARN" not in stats.level_counts) or (stats.level_counts.get("WARN", 0) == 0)
    assert stats.top_messages[0] == (2, "a")


def test_apply_env_respects_cli_overrides_and_path_from_cli(tmp_path: Path) -> None:
    # テスト意図：優先順位（CLI > env）と「位置引数(path)の特例」を確認する
    cli_path = tmp_path / "from_cli.log"
    cli_path.write_text("[INFO] x\n", encoding="utf-8")

    args = logsum.parse_args([str(cli_path), "--top", "3"])
    provided = toolkit.parse_provided_options([str(cli_path), "--top", "3"])
    logger = toolkit.setup_logger("test", False)

    env_file = {
        "LOGSUM_PATH": str(tmp_path / "from_env.log"),
        "LOGSUM_TOP": "2",
        "LOGSUM_JSON": "true",
        "LOGSUM_OUT": str(tmp_path / "out.json"),
    }

    logsum.apply_env(
        args=args,
        env_file=env_file,
        provided=provided,
        logger=logger,
        path_from_cli=True,
    )

    # CLIで渡した path と top は env で上書きされない
    assert args.path == cli_path
    assert args.top == 3
    # CLI未指定の json/out は env が効く
    assert args.json is True
    assert args.out == (tmp_path / "out.json")


def test_main_writes_out_file_when_env_file_sets_json_and_out(tmp_path: Path) -> None:
    # テスト意図：「env-file で json/out を有効化すると、payload が out に保存される」ことを確認する
    log_path = tmp_path / "app.log"
    log_path.write_text(
        "\n".join(
            [
                "[INFO] hello",
                "[INFO] hello",
                "[WARN] boom",
                "unknown format line",
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    out_path = tmp_path / "report.env.json"
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
                f"LOGSUM_PATH={log_path}",
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
    assert data["path"] == str(log_path)
    assert data["top_n"] == 2
    assert data["total_lines"] == 4  # 空行は除外

    # level集計が入っていること（INFO/WARN/UNKNOWN が出る）
    levels = {d["level"]: d["count"] for d in data["levels"]}
    assert levels["INFO"] == 2
    assert levels["WARN"] == 1
    assert levels["UNKNOWN"] == 1
