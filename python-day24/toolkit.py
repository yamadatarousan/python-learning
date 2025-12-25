"""
Day24: 小ツール共通の「I/Oまわり」部品集（toolkit）

狙い：
- いろんな小ツールで毎回出てくる「だいたい同じ処理」をまとめる
  例：logger構成、.env読み取り、bool変換、JSON保存、HTTP POST
- 各ツール本体は「そのツール固有の処理」に集中できるようにする

注意：
- ここに入れるのは「どのツールでも同じ意味で使えるもの」だけ
- ツール固有の優先順位・引数名・payload構造は各ツール側で持つ
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any


def parse_provided_options(argv: list[str] | None) -> set[str]:
    """
    どの --option が CLI で明示されたかを判定する。

    目的：
    - config/env が「既定値」を埋めるのはOK
    - ただし「ユーザーがCLIで明示した値」は上書きしない（= CLI優先を守る）
    """
    if argv is None:
        return set()
    provided: set[str] = set()
    for token in argv:
        if token.startswith("--"):
            provided.add(token.split("=", 1)[0])
    return provided


def parse_bool(value: str) -> bool:
    """
    env用のboolパース（.env / 環境変数は文字列なので明示変換が必要）。

    true: 1, true, yes, y, on
    false: 0, false, no, n, off
    """
    v = value.strip().lower()
    if v in {"1", "true", "yes", "y", "on"}:
        return True
    if v in {"0", "false", "no", "n", "off"}:
        return False
    return bool(v)


def load_env_file(path: Path, logger: logging.Logger) -> dict[str, str]:
    """
    .env 形式（KEY=VALUE）を読む。標準ライブラリのみで実装。

    対応範囲（運用で遭遇しがちな最低限）：
    - 空行/コメント(#...)は無視する
    - `export KEY=VALUE` を許容する（シェル由来の書き方）
    - 値の前後のクォート（' "）は剥がす（"x" -> x）
    - `=` を含まない行は無視する（壊れた行で落とさない）
    """
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as exc:
        logger.error("env file load failed: %s (%s)", path, exc)
        return {}

    env: dict[str, str] = {}
    for row in text.splitlines():
        line = row.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip()
        if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
            val = val[1:-1]
        if key:
            env[key] = val
    return env


def get_env(name: str, env_file: dict[str, str]) -> str | None:
    """
    環境変数取得。

    優先順位：
    - `--env-file` を明示した場合に「.envが必ず勝つ」要件を満たすため、
      env_file（.env） > OS環境変数 とする。
    """
    v = env_file.get(name)
    if v is not None and v != "":
        return v
    v = os.getenv(name)
    if v is not None and v != "":
        return v
    return None


def setup_logger(name: str, verbose: bool) -> logging.Logger:
    """
    ログをstderrに出すためのloggerを構成する。

    設計意図：
    - stdoutは「結果の出力」で使いたい（JSONなど）
    - なので進捗/警告/失敗はstderrへ寄せる
    """
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO if verbose else logging.WARNING)
    logger.propagate = False

    logger.handlers.clear()

    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    logger.addHandler(handler)
    return logger


def write_json_file(path: Path, payload: dict[str, Any], logger: logging.Logger) -> bool:
    """
    JSON payload をファイルに保存する（stdoutは汚さない）。

    仕様：
    - 失敗したら logger.error を出して False を返す
    - 成功したら True
    """
    try:
        out_path = path.expanduser().resolve()
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        logger.info("payload written to %s", out_path)
        return True
    except Exception as exc:
        logger.error("failed to write payload to %s: %s", path, exc)
        return False


def post_json(url: str, payload: dict[str, Any], timeout: float, logger: logging.Logger) -> bool:
    """
    payload を JSON として POST する（I/O）。

    仕様：
    - stdoutは汚さない（JSON出力の邪魔をしない）
    - 成否やエラーはstderrログへ
    """
    try:
        import httpx
    except ImportError:
        logger.error("httpx モジュールが見つかりません。HTTP POST を実行できません。")
        return False

    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(url, json=payload)
        logger.info("POST %s -> %d", url, resp.status_code)
        if resp.status_code >= 400:
            logger.warning("response body (truncated): %s", resp.text[:200])
            return False
        return True
    except Exception as exc:
        logger.error("HTTP POST エラー: %s", exc)
        return False