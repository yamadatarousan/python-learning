"""
Day22: 小ツール作成（ログ集計 / 整形）

このDayの狙い：
- 「入力（ログ）→ 集計（純粋計算）→ 出力（stdout / jsonファイル）」の流れを作る
- 仕事でよくある “雑ログ” を「落ちずに」集計できるようにする
- I/O境界（ファイル/標準入力）と計算部分（Counterで集計）を分ける

対応するログ行の想定（“最低限”の現場互換）：
- 例1: 2025-12-25 12:34:56 [INFO] something happened
- 例2: [ERROR] failed to connect
- 例3: just a message
  → timestamp/level が欠けていても落とさず、UNKNOWN 扱いで集計する
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
from typing import Any, Iterator, TextIO


# -------------------------
# ログ行のパース（副作用なし）
# -------------------------

# テストしやすいように正規表現はトップレベルに置く（定数として扱う）
_LOG_RE = re.compile(
    r"^(?:(?P<ts>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})\s+)?"
    r"(?:\[(?P<level>[A-Z]+)\]\s+)?"
    r"(?P<msg>.*)$"
)


@dataclass(frozen=True)
class LogEntry:
    """
    1行分のログをパースしたDTO。

    ここで保持するのは「集計に必要な最低限」に絞る：
    - ts: タイムスタンプ（なければ None）
    - level: ログレベル（なければ "UNKNOWN"）
    - message: 本文（空でもよい。落とさない）
    """

    ts: str | None
    level: str
    message: str


def parse_log_line(line: str) -> LogEntry:
    """
    ログ1行をパースして LogEntry にする。

    仕様として守りたいこと：
    - どんな行が来ても例外で落ちない（= “雑ログ” 耐性）
    - level が取れない場合は UNKNOWN に寄せる
    - message は “残り全部” を取る（内容に依存しない）
    """
    s = line.rstrip("\n")
    m = _LOG_RE.match(s)
    if not m:
        # 正規表現が想定外でも落とさず、行全体を message にする
        return LogEntry(ts=None, level="UNKNOWN", message=s)

    ts = m.group("ts")
    level = m.group("level") or "UNKNOWN"
    msg = m.group("msg") or ""
    return LogEntry(ts=ts, level=level, message=msg)


# -------------------------
# 入力（I/O境界）
# -------------------------

def iter_log_entries(fp: TextIO) -> Iterator[LogEntry]:
    """
    ファイル（または stdin）からログを1行ずつ読み、LogEntry を順次 yield する。

    仕様として守りたいこと：
    - ストリーミングで処理する（巨大ログでも “読み切ってから” にならない）
    - 空行はスキップ（集計ノイズを減らす）
    """
    for raw in fp:
        if not raw.strip():
            continue
        yield parse_log_line(raw)


# -------------------------
# 集計（できるだけ純粋関数）
# -------------------------

@dataclass(frozen=True)
class MessageCount:
    """
    “同一メッセージが何回出たか” を表すDTO。

    仕様としてここにまとめる意図：
    - 出力（JSON/表示）で扱いやすい
    - 将来「正規化（trim/マスク）」などを入れる時も変更点が局所化しやすい
    """

    message: str
    count: int


@dataclass(frozen=True)
class LogReport:
    """
    集計結果のDTO。

    ここにまとめる意図：
    - main の流れを “組み立てるだけ” にしやすい
    - 出力形式（JSON/人間向け）を分離しやすい
    """

    total_lines: int
    level_counts: dict[str, int]
    top_messages: list[MessageCount]


def compute_report(entries: Iterator[LogEntry], top_n_messages: int) -> LogReport:
    """
    ログを集計して LogReport を返す。

    仕様として守りたいこと：
    - total_lines は “空行除外後の行数”
    - level_counts はレベル別件数
    - top_messages は “出現回数が多い message” を上位N件
      ※ message の比較はそのまま。整形/正規化はこの段階ではしない
    """
    total = 0
    levels: Counter[str] = Counter()
    messages: Counter[str] = Counter()

    for e in entries:
        total += 1
        levels[e.level] += 1
        messages[e.message] += 1

    # Counter.most_common は (要素, 件数) を頻度順で返す
    top: list[MessageCount] = [
        MessageCount(message=msg, count=cnt) for msg, cnt in messages.most_common(max(0, top_n_messages))
    ]
    return LogReport(total_lines=total, level_counts=dict(levels), top_messages=top)


def build_json_payload(report: LogReport) -> dict[str, Any]:
    """
    JSON出力用の辞書を組み立てる（表示形式の責務）。

    仕様として守りたいこと：
    - JSONが “安定して” 読める形（dict/list/str/int のみ）
    - DTOの内部表現に依存しすぎない（将来の変更耐性）
    """
    return {
        "total_lines": report.total_lines,
        "levels": report.level_counts,
        "top_messages": [{"message": t.message, "count": t.count} for t in report.top_messages],
    }


# -------------------------
# CLI / ログ（I/O境界）
# -------------------------

def setup_logger(verbose: bool) -> logging.Logger:
    """
    stderr にログを出す logger を作る。

    設計意図：
    - stdout は “結果（特に --json）” に使いたい
    - 進捗/診断は stderr へ寄せる
    """
    logger = logging.getLogger("logscan")
    logger.setLevel(logging.INFO if verbose else logging.WARNING)
    logger.propagate = False

    logger.handlers.clear()
    h = logging.StreamHandler(stream=sys.stderr)
    h.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    logger.addHandler(h)
    return logger


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    """
    CLI引数を定義して解析する。

    仕様としてここで決めること：
    - どこからログを読むか（ファイル or stdin）
    - どう出力するか（人間向け or JSON）
    - JSONをファイル保存するか（--out）
    """
    p = argparse.ArgumentParser(description="Aggregate simple logs (level counts + top messages).")
    p.add_argument(
        "logfile",
        nargs="?",
        default=None,
        type=Path,
        help="ログファイルのパス（省略時は標準入力から読む）",
    )
    p.add_argument(
        "--top-messages",
        type=int,
        default=10,
        help="頻出メッセージの上位N件を出す（default: 10）",
    )
    p.add_argument("--json", action="store_true", help="結果をJSONでstdoutに出す")
    p.add_argument("--out", type=Path, default=None, help="JSON payload をファイルに保存する")
    p.add_argument("--verbose", action="store_true", help="詳細ログをstderrに出す")
    return p.parse_args(argv)


def validate_args(args: argparse.Namespace) -> int:
    """
    入力検証。失敗したら終了コード 2 を返す。

    仕様として確認するもの：
    - --top-messages は負数禁止（0はOK = 出さない）
    - logfile が指定されているなら存在チェック（読み取りできないケースを早めに弾く）
    """
    if args.top_messages < 0:
        print(f"Error: --top-messages は0以上でなければなりません: {args.top_messages}", file=sys.stderr)
        return 2
    if args.logfile is not None:
        if not args.logfile.exists():
            print(f"Error: 指定されたログファイルが存在しません: {args.logfile}", file=sys.stderr)
            return 2
        if not args.logfile.is_file():
            print(f"Error: 指定されたパスはファイルではありません: {args.logfile}", file=sys.stderr)
            return 2
    return 0


def write_payload_to_file(out_path: Path, payload: dict[str, Any], logger: logging.Logger) -> bool:
    """
    JSON payload をファイルに書く。

    仕様として守りたいこと：
    - stdout を汚さない（ファイル出力は副作用としてここに閉じ込める）
    - 失敗は logger に残して False を返す（呼び出し側で終了コード制御）
    """
    try:
        path = out_path.expanduser().resolve()
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        logger.info("payload written to %s", path)
        return True
    except Exception as exc:
        logger.error("failed to write payload to %s: %s", out_path, exc)
        return False


def main(argv: list[str] | None = None) -> int:
    """
    CLI実行の入口（テストからも呼べる形）。

    実行フロー（責務の境界が見える形）：
    1) parse_args（入力）
    2) validate_args（入力検証）
    3) iter_log_entries → compute_report（集計）
    4) build_json_payload（出力形式）
    5) stdout / --out（副作用）
    """
    args = parse_args(argv)
    logger = setup_logger(args.verbose)

    rc = validate_args(args)
    if rc != 0:
        return rc

    # 入力元を決める（ファイル or stdin）
    if args.logfile is None:
        logger.info("read from stdin")
        fp: TextIO = sys.stdin
        entries = iter_log_entries(fp)
        report = compute_report(entries, top_n_messages=args.top_messages)
    else:
        logger.info("read from file: %s", args.logfile)
        try:
            with args.logfile.open("r", encoding="utf-8", errors="replace") as fp2:
                report = compute_report(iter_log_entries(fp2), top_n_messages=args.top_messages)
        except Exception as exc:
            logger.error("failed to read logfile: %s (%s)", args.logfile, exc)
            return 1

    payload = build_json_payload(report)

    # stdout
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        # 人間向け表示（“読める” を優先）
        print(f"total_lines: {report.total_lines}")
        print("levels:")
        for level, cnt in sorted(report.level_counts.items(), key=lambda kv: (-kv[1], kv[0])):
            print(f"  {level}: {cnt}")
        print(f"top_messages: {len(report.top_messages)}")
        for t in report.top_messages:
            print(f"  {t.count}\t{t.message}")

    # --out（JSONを書きたいので、payload は常に使う）
    if args.out is not None:
        ok = write_payload_to_file(args.out, payload, logger)
        if not ok:
            return 1

    return 0
