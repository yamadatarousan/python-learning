"""
Day20: pytest を「雰囲気だけ」触るための最小テスト群。

狙い：
- いきなり「全部を網羅」しない
- 代わりに、壊れやすい設定まわり（CLI / env / config の優先順位）と、
  期待した副作用（--out でファイルが書ける）を押さえる
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import main as dirscan

def test_parse_bool_truthy_and_falsey() -> None:
    # テスト意図：env 文字列を bool に解釈するルールが想定どおりか確認する
    # 仕様：true系（1/true/yes/on）→ True、false系（0/false/no/off）→ False
    assert dirscan.parse_bool("1") is True
    assert dirscan.parse_bool("true") is True
    assert dirscan.parse_bool("YES") is True
    assert dirscan.parse_bool("on") is True

    assert dirscan.parse_bool("0") is False
    assert dirscan.parse_bool("false") is False
    assert dirscan.parse_bool("No") is False
    assert dirscan.parse_bool("off") is False

def test_load_env_file_parses_key_value_and_ignores_comments(tmp_path: Path) -> None:
    # テスト意図：.env パーサの「最低限の互換性」を確認する
    # 仕様：空行/コメントは無視、export を許容、クォートを剥がす、KEY=VALUE のみ読む
    """
    具体的には「よくある .env の書き方で、壊れずに“使える値”が取れる」こと。
    このテストだと最低限＝だいたい次の仕様を指してる：
    KEY=VALUE の基本形を読み取れる
    空行・# で始まるコメント行は無視する（＝余計なものを設定として拾わない）
    export KEY=VALUE も受け付ける（シェル由来の書き方でも動く）
    値の前後の ' / " を剥がして取り出せる（クォート付きでも期待通りの値になる）
    つまり「運用で遭遇しがちな範囲をカバーして、想定外でコケない」って意味。
    """
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
                "# comment",
                "",
                "export DIRSCAN_TOP=2",
                "DIRSCAN_JSON=true",
                "DIRSCAN_OUT='out.json'",
                "NO_EQUAL_SIGN",
            ]
        ),
        encoding="utf-8",
    )

    logger = dirscan.setup_logger(False)
    env = dirscan.load_env_file(env_path, logger)

    assert env["DIRSCAN_TOP"] == "2"
    assert env["DIRSCAN_JSON"] == "true"
    assert env["DIRSCAN_OUT"] == "out.json"
    assert "NO_EQUAL_SIGN" not in env

def test_get_env_prefers_env_file_over_os_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # テスト意図：優先順位の仕様（--env-file を明示したら .env が勝つ）を確認する
    # 仕様：env_file に値があれば OS 環境変数より優先して採用する
    monkeypatch.setenv("DIRSCAN_TOP", "999")
    env_file = {"DIRSCAN_TOP": "2"}

    assert dirscan.get_env("DIRSCAN_TOP", env_file) == "2"

def test_apply_env_respects_cli_overrides_and_directory_from_cli(tmp_path: Path) -> None:
    # テスト意図：優先順位（CLI > env）と「位置引数(directory)の特例」を確認する
    # 仕様：
    # - CLI で --top が指定されているなら env の TOP は上書きしない
    # - 位置引数 directory が CLI で渡されているなら env の DIRECTORY で上書きしない
    args = dirscan.parse_args(["/from/cli", "--top", "3"])
    provided = dirscan.parse_provided_options(["/from/cli", "--top", "3"])
    logger = dirscan.setup_logger(False)

    env_file = {
        "DIRSCAN_DIRECTORY": str(tmp_path),
        "DIRSCAN_TOP": "2",
        "DIRSCAN_JSON": "true",
        "DIRSCAN_OUT": str(tmp_path / "out.json"),
    }

    dirscan.apply_env(
        args=args,
        env_file=env_file,
        provided=provided,
        logger=logger,
        directory_from_cli=True,
    )

    assert str(args.directory) == "/from/cli"
    assert args.top == 3
    assert args.json is True
    assert args.out == (tmp_path / "out.json")

def test_apply_config_only_fills_when_not_provided(tmp_path: Path) -> None:
    # テスト意図：config は「未指定の項目だけ」補完することを確認する
    # 仕様：provided に含まれるオプションは config が上書きしない
    args = dirscan.parse_args(["--top", "3"])
    provided = dirscan.parse_provided_options(["--top", "3"])
    logger = dirscan.setup_logger(False)

    cfg = {
        "top": 999,  # CLI が勝つので反映されないはず
        "min_size": 123,  # CLI未指定なので反映されるはず
        "relative": True,  # CLI未指定なので反映されるはず
    }
    dirscan.apply_config(args, cfg, provided, logger)

    assert args.top == 3
    assert args.min_size == 123
    assert args.relative is True

def test_build_json_payload_uses_relative_paths_when_requested(tmp_path: Path) -> None:
    # テスト意図：JSON payload の path 表示仕様（relative）が守られているか確認する
    # 仕様：relative=True なら root からの相対パスを出す
    root = tmp_path
    p = root / "a" / "b.txt"
    p.parent.mkdir(parents=True)
    p.write_text("hello", encoding="utf-8")

    stats = dirscan.Stats(
        count=1,
        total_bytes=p.stat().st_size,
        top=[dirscan.Entry(path=p, size=p.stat().st_size)],
    )
    payload = dirscan.build_json_payload(
        root=root,
        mode="file",
        min_size=0,
        top_n=1,
        stats=stats,
        relative=True,
    )

    assert payload["top"][0]["path"] == "a/b.txt"

def test_main_writes_out_file_when_env_file_sets_json_and_out(tmp_path: Path) -> None:
    # テスト意図：「env-file で json/out を有効化すると、stdout が JSON になりつつ out にも保存される」ことを確認する
    # 仕様：
    # - DIRSCAN_JSON=true なら JSON を出力する（= payload が作られる）
    # - DIRSCAN_OUT があれば、そのパスに payload を書き込む
    scan_root = tmp_path / "scan"
    scan_root.mkdir()
    (scan_root / "x.txt").write_text("x" * 10, encoding="utf-8")

    out_path = tmp_path / "report.env.json"
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
                f"DIRSCAN_DIRECTORY={scan_root}",
                "DIRSCAN_TOP=1",
                "DIRSCAN_JSON=true",
                f"DIRSCAN_OUT={out_path}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    rc = dirscan.main(["--env-file", str(env_path)])
    assert rc == 0
    assert out_path.exists()

    data = json.loads(out_path.read_text(encoding="utf-8"))
    assert data["directory"] == str(scan_root.resolve())
    assert data["top_n"] == 1
    assert data["count"] >= 1