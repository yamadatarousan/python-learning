"""
Day23: ログ集計ツール（logsum）

やること（目的がはっきりした小ツール）：
- 指定ディレクトリ配下のログファイルを集めて、行数とレベル別件数を数える
- ついでに「よく出るメッセージ TOP N」も出す（同じエラーが連発してないかを見る用途）

設計の狙い（Day21の続き）：
- 入力（ファイル読む）と、計算（数える）と、出力（print / json / out）を分ける
- テストでは「計算が合ってる」「出力用データが作れる」を軽く押さえる

対応するログの形（最低限）：
- "[INFO] something" みたいな形式（あなたのツールが出すログと近い）
- "2025-... - name - INFO - something" みたいな形式（Python loggingでよく見る）
  ※どれにも当てはまらない行は level="UNKNOWN" 扱いにする（落とさない）
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator


# -------------------------
# CLIパース（I/O境界：入力）
# -------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """
    CLI引数を定義して、解析結果（args）を返す。

    ここで受け取るもの：
    - directory: 走査対象（省略時はカレント）
    - --pattern: 収集するログファイルのglob（例: "*.log" / "*.txt"）
    - --top: 「よく出るメッセージ」TOP N（0なら出さない）
    - --json: 集計結果をJSONでstdoutに出す
    - --out: 集計結果(JSON)をファイルに保存する
    - --verbose: 進捗ログをstderrに出す
    """
    parser = argparse.ArgumentParser(description="Aggregate log files under a directory.")

    parser.add_argument(
        "directory",
        nargs="?",
        default=Path("."),
        type=Path,
        help="ログを探すディレクトリ（省略時はカレント）",
    )

    parser.add_argument(
        "--pattern",
        type=str,
        default="*.log",
        help="収集するログファイルのglob（デフォルト: *.log）",
    )

    parser.add_argument(
        "--top",
        type=int,
        default=5,
        help="よく出るメッセージ TOP N（0なら出さない）",
    )

    parser.add_argument("--json", action="store_true", help="集計結果をJSON形式で出力する")
    parser.add_argument("--out", type=Path, default=None, help="JSONをファイルに保存する（例: report.json）")
    parser.add_argument("--verbose", action="store_true", help="進捗ログをstderrに出す")

    return parser.parse_args(argv)


# -------------------------
# ログ（I/O境界：出力先がstderr）
# -------------------------

def setup_logger(verbose: bool) -> logging.Logger:
    """
    進捗ログをstderrに出すloggerを構成する。

    仕様として守りたいこと：
    - 集計結果（--json）は stdout に出るので、進捗は stderr に寄せる
    """
    logger = logging.getLogger("logsum")
    logger.setLevel(logging.INFO if verbose else logging.WARNING)
    logger.propagate = False
    logger.handlers.clear()

    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    logger.addHandler(handler)
    return logger


# -------------------------
# データモデル（DTO）
# -------------------------

@dataclass(frozen=True)
class MessageCount:
    """
    「よく出るメッセージ」ランキング用のDTO。

    ここで持ちたい情報：
    - message: 正規化したメッセージ本文（タイムスタンプ等は落として、同じ文言は同一扱い）
    - count: 何回出たか
    """

    message: str
    count: int


@dataclass(frozen=True)
class Summary:
    """
    集計結果のDTO。

    ここで持つもの（人が見ても用途が分かる最低限）：
    - files: 何ファイル読んだか
    - lines: 合計何行見たか
    - levels: レベル別の件数（INFO/WARNING/ERROR/UNKNOWN）
    - top_messages: よく出るメッセージTOP（top=0なら空）
    """

    files: int
    lines: int
    levels: dict[str, int]
    top_messages: list[MessageCount]


# -------------------------
# ログ収集（I/O境界：ファイル読む）
# -------------------------

def iter_log_files(root: Path, pattern: str) -> Iterator[Path]:
    """
    root配下からログファイルを見つけて順に返す。

    仕様：
    - 再帰で探す（rglob）
    - patternはglob（*.log など）
    """
    yield from root.rglob(pattern)


def iter_log_lines(paths: Iterable[Path], logger: logging.Logger) -> Iterator[str]:
    """
    複数ログファイルを読み、行を順に返す。

    仕様として守りたいこと：
    - 読めないファイルがあっても落とさない（運用では壊れたログも混ざる）
    - 文字化け/不正バイトがあっても落とさない（errors="replace"）
    """
    for p in paths:
        try:
            with p.open("r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    yield line.rstrip("\n")
        except OSError as exc:
            logger.info("[skip] %s: %s", p, exc)
            continue


# -------------------------
# 解析・集計（コアロジック）
# -------------------------

_BRACKET_LEVEL = re.compile(r"^\[(?P<level>[A-Z]+)\]\s*(?P<msg>.*)$")
_DASH_LEVEL = re.compile(r"\b(?P<level>DEBUG|INFO|WARNING|ERROR|CRITICAL)\b\s*-\s*(?P<msg>.*)$")


def parse_level_and_message(line: str) -> tuple[str, str]:
    """
    1行のログから (level, message) を取り出す。

    仕様：
    - "[INFO] hello" 形式 → ("INFO", "hello")
    - "... - INFO - hello" 形式 → ("INFO", "hello")
    - どれにも当たらない → ("UNKNOWN", 元の行)

    嬉しいこと：
    - 「パースできない行がある」だけで集計全体が落ちない
    """
    m = _BRACKET_LEVEL.match(line)
    if m:
        level = m.group("level")
        msg = m.group("msg").strip()
        return level, msg

    m = _DASH_LEVEL.search(line)
    if m:
        level = m.group("level")
        msg = m.group("msg").strip()
        return level, msg

    return "UNKNOWN", line.strip()


def normalize_message(msg: str) -> str:
    """
    似た文言を「同じもの」として数えるための簡単な正規化。

    仕様（やりすぎない範囲）：
    - 連続スペースを1つにする
    - 前後の空白を落とす
    """
    msg = msg.strip()
    msg = re.sub(r"\s+", " ", msg)
    return msg


def aggregate(lines: Iterable[str], top_n: int) -> Summary:
    """
    行を受け取って集計結果を作る（計算側）。

    仕様として守りたいこと：
    - レベル別件数を数える
    - top_n > 0 のときだけ、メッセージ頻度を数える
    """
    level_counts: Counter[str] = Counter()
    msg_counts: Counter[str] = Counter()
    total_lines = 0

    for line in lines:
        total_lines += 1
        level, msg = parse_level_and_message(line)
        level_counts[level] += 1

        if top_n > 0:
            msg_counts[normalize_message(msg)] += 1

    top_messages: list[MessageCount] = []
    if top_n > 0:
        for msg, count in msg_counts.most_common(top_n):
            top_messages.append(MessageCount(message=msg, count=count))

    return Summary(
        files=0,  # filesは呼び出し側で埋める（ここは「計算だけ」に寄せたい）
        lines=total_lines,
        levels=dict(level_counts),
        top_messages=top_messages,
    )


# -------------------------
# 出力（I/O境界：stdout / ファイル）
# -------------------------

def build_json_payload(root: Path, pattern: str, summary: Summary) -> dict[str, Any]:
    """
    JSON出力用の辞書を作る（表示形式の責務）。

    仕様：
    - toolsとして使うときに「何をどう集計したか」が後から追える形にする
    """
    return {
        "directory": str(root),
        "pattern": pattern,
        "files": summary.files,
        "lines": summary.lines,
        "levels": summary.levels,
        "top_messages": [{"message": m.message, "count": m.count} for m in summary.top_messages],
    }


def write_json_file(path: Path, payload: dict[str, Any], logger: logging.Logger) -> bool:
    """
    JSON payload をファイルに保存する。

    仕様：
    - 失敗したら False を返す（mainが終了コードに反映する）
    - stdoutは汚さない（json出力と混ざると壊れる）
    """
    try:
        out_path = path.expanduser().resolve()
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        logger.info("payload written to %s", out_path)
        return True
    except Exception as exc:
        logger.error("failed to write payload to %s: %s", path, exc)
        return False


# -------------------------
# 実行フロー（入口を薄く、I/Oをまとめる）
# -------------------------

def main(argv: list[str] | None = None) -> int:
    """
    CLIの実行入口（テストからも呼べる形）。

    流れ（読みやすさ優先）：
    1) args を作る
    2) logger を作る（stderr）
    3) ファイルを集める → 行を読む（I/O）
    4) 集計する（計算）
    5) 出力する（stdout / out）
    """
    args = parse_args(argv)
    logger = setup_logger(args.verbose)

    root: Path = args.directory.expanduser().resolve()
    if not root.exists() or not root.is_dir():
        print(f"Error: directory not found: {root}", file=sys.stderr)
        return 2
    if args.top < 0:
        print(f"Error: --top must be >= 0: {args.top}", file=sys.stderr)
        return 2

    paths = list(iter_log_files(root, args.pattern))
    logger.info("found %d files (pattern=%s)", len(paths), args.pattern)

    lines = iter_log_lines(paths, logger=logger)
    summary = aggregate(lines, top_n=args.top)
    summary = Summary(
        files=len(paths),
        lines=summary.lines,
        levels=summary.levels,
        top_messages=summary.top_messages,
    )

    payload = build_json_payload(root=root, pattern=args.pattern, summary=summary)

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    # 人間向け表示（jsonを要求していないときだけ）
    print(f"directory: {root}")
    print(f"pattern:   {args.pattern}")
    print(f"files:     {summary.files}")
    print(f"lines:     {summary.lines}")
    print("levels:")
    for level in sorted(summary.levels.keys()):
        print(f"  {level}: {summary.levels[level]}")

    if args.top > 0:
        print(f"top_messages: {args.top}")
        for m in summary.top_messages:
            print(f"  {m.count}\t{m.message}")

    if args.out is not None:
        ok = write_json_file(args.out, payload, logger=logger)
        return 0 if ok else 1

    return 0
