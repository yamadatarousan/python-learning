"""
Day26: 小ツール1つ目 - logsum（ログの集計）

何をするツール？
- テキストログを読み、行数と「レベル別件数」を集計する
- ついでに「同じメッセージが何回出たか」の上位N件も出す

狙い：
- “ツール固有の処理” と “共通I/O（logger/.env/bool/POST/JSON保存）” を分ける練習
- streaming（1行ずつ読む）で集計し、巨大ログでも破綻しにくくする

入力フォーマット（--format）：
- bracket: `[INFO] message` みたいな形（合わない行は UNKNOWN 扱い）
- jsonl: 1行1JSON（例：{"level":"INFO","message":"..."})
- raw: 行全体をメッセージとして扱う（レベルは RAW 固定）

設定の優先順位：
  CLI > env（OS環境変数 / --env-file） > config（JSON）
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

import toolkit

LOGGER_NAME = "logsum"


# -------------------------
# CLIパース（I/O境界：入力）
# -------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """
    CLI引数を定義して、解析結果（args）を返す。

    ここでは「logsum が受け取る項目（仕様）」だけを列挙する。
    env/configの優先順位や補完は別関数（resolve_effective_args）でやる。
    """
    parser = argparse.ArgumentParser(description="Summarize log file: count lines, levels, and top messages.")

    parser.add_argument(
        "input",
        nargs="?",
        default=None,  # config/envで上書きできるように「未指定(None)」を区別する
        type=Path,
        help="入力ログファイル（省略時はstdinから読む）",
    )

    parser.add_argument(
        "--format",
        choices=["bracket", "jsonl", "raw"],
        default="bracket",
        help="入力フォーマット（bracket/jsonl/raw）",
    )

    parser.add_argument(
        "--top",
        type=int,
        default=10,
        help="同一メッセージ出現回数の上位N件を出す（default: 10）。0で無効。",
    )

    parser.add_argument("--verbose", action="store_true", help="詳細ログをstderrに出す")
    parser.add_argument("--json", action="store_true", help="集計結果をJSON形式でstdoutに出す")

    parser.add_argument(
        "--post",
        type=str,
        default="",
        help="集計結果のJSONをPOSTするURL（指定しない場合はPOSTしない）",
    )

    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="HTTP POSTのタイムアウト秒数（デフォルト: 10.0秒）",
    )

    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="JSON config file path (e.g., config.json). CLI args override config.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="JSON payload をファイルに保存する（例: report.json）",
    )

    parser.add_argument(
        "--env-file",
        type=Path,
        default=None,
        help="Load environment variables from a .env file before processing (e.g., .env).",
    )

    return parser.parse_args(argv)


# -------------------------
# 設定ファイル（JSON）（I/O境界：入力）
# -------------------------


def load_config(path: Path, logger: logging.Logger) -> dict[str, Any]:
    """
    JSON設定ファイルを読み込む。

    期待する例：
      {"input": "app.log", "format": "bracket", "top": 20, "json": true}
    """
    try:
        text = path.read_text(encoding="utf-8")
        data = json.loads(text)
    except Exception as exc:
        logger.error("config load failed: %s (%s)", path, exc)
        return {}
    if not isinstance(data, dict):
        logger.error("config must be a JSON object: %s", path)
        return {}
    return data


def apply_config(args: argparse.Namespace, cfg: dict[str, Any], provided: set[str], logger: logging.Logger) -> None:
    """
    configの値を args に反映する（ただしCLI指定が優先）。

    ここでの責務：
    - 「未指定の項目だけ」を埋める（provided に入っているものは上書きしない）
    - どのキー名を使うか（input/format など）は logsum 固有の仕様なのでここに残す
    """

    def has(name: str) -> bool:
        return name in cfg

    # input（位置引数）：未指定(None)のときだけconfigを使う
    if args.input is None and has("input"):
        args.input = Path(str(cfg["input"]))

    if "--format" not in provided and has("format"):
        args.format = str(cfg["format"])
    if "--top" not in provided and has("top"):
        args.top = int(cfg["top"])
    if "--timeout" not in provided and has("timeout"):
        args.timeout = float(cfg["timeout"])
    if "--post" not in provided and has("post"):
        args.post = str(cfg["post"])
    if "--out" not in provided and has("out"):
        args.out = Path(str(cfg["out"]))

    # store_true のフラグ類は、CLI未指定なら config を反映してよい
    if "--verbose" not in provided and has("verbose"):
        args.verbose = bool(cfg["verbose"])
    if "--json" not in provided and has("json"):
        args.json = bool(cfg["json"])

    logger.info("config applied (CLI overrides config)")


# -------------------------
# env適用（I/O境界：入力）
# -------------------------


def apply_env(
    args: argparse.Namespace,
    env_file: dict[str, str],
    provided: set[str],
    logger: logging.Logger,
    input_from_cli: bool,
) -> None:
    """
    envの値を args に反映する（ただしCLI指定が優先）。

    仕様として守りたいこと：
    - CLI > env > config（configは先に適用しておく）
    - input（位置引数）は「CLIで渡されたかどうか」を別扱いして上書き事故を防ぐ

    対応する環境変数名（logsum 固有の“名前”なのでここに残す）：
      LOGSUM_INPUT, LOGSUM_FORMAT, LOGSUM_TOP, LOGSUM_JSON, LOGSUM_VERBOSE,
      LOGSUM_POST, LOGSUM_TIMEOUT, LOGSUM_OUT, LOGSUM_CONFIG
    """
    # configパス：CLI未指定かつargs.config未指定のときだけ
    if "--config" not in provided and args.config is None:
        v = toolkit.get_env("LOGSUM_CONFIG", env_file)
        if v:
            args.config = Path(v)

    # input（位置引数）：CLIで渡されたなら env では上書きしない
    if not input_from_cli:
        v = toolkit.get_env("LOGSUM_INPUT", env_file)
        if v:
            args.input = Path(v)

    if "--format" not in provided:
        v = toolkit.get_env("LOGSUM_FORMAT", env_file)
        if v:
            args.format = v

    if "--top" not in provided:
        v = toolkit.get_env("LOGSUM_TOP", env_file)
        if v:
            args.top = int(v)

    if "--timeout" not in provided:
        v = toolkit.get_env("LOGSUM_TIMEOUT", env_file)
        if v:
            args.timeout = float(v)

    if "--post" not in provided:
        v = toolkit.get_env("LOGSUM_POST", env_file)
        if v:
            args.post = v

    if "--out" not in provided:
        v = toolkit.get_env("LOGSUM_OUT", env_file)
        if v:
            args.out = Path(v)

    if "--json" not in provided:
        v = toolkit.get_env("LOGSUM_JSON", env_file)
        if v is not None:
            args.json = toolkit.parse_bool(v)

    if "--verbose" not in provided:
        v = toolkit.get_env("LOGSUM_VERBOSE", env_file)
        if v is not None:
            args.verbose = toolkit.parse_bool(v)

    logger.info("env applied (CLI overrides env)")


# -------------------------
# データモデル（DTO）
# -------------------------


@dataclass(frozen=True)
class MessageCount:
    """
    「同じメッセージが何回出たか」を表すDTO。

    ねらい：
    - dict/tuple のままにせず、意味のある“名前”を付ける
    - 出力整形（JSON/表示）で迷子になりにくくする
    """

    message: str
    count: int


@dataclass(frozen=True)
class LogStats:
    """
    ログ集計の結果DTO。

    含めるもの：
    - total_lines: 全行数
    - by_level: レベル別の件数
    - top_messages: メッセージ頻度の上位N件
    """

    total_lines: int
    by_level: dict[str, int]
    top_messages: list[MessageCount]


# -------------------------
# 解析（コアロジック）
# -------------------------


_BRACKET_RE = re.compile(r"^\[(?P<level>[A-Za-z0-9_]+)\]\s*(?P<msg>.*)$")


def iter_lines(path: Path | None) -> Iterator[str]:
    """
    1行ずつテキストを返すジェネレータ（streaming）。

    仕様：
    - path があればファイルから読む
    - path がなければ stdin から読む
    """
    if path is None:
        for line in sys.stdin:
            yield line
        return

    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            yield line


def parse_record(line: str, fmt: str, logger: logging.Logger) -> tuple[str, str]:
    """
    1行のログを（level, message）に分解する。

    ポイント：
    - 入力フォーマットが壊れていても “落とさない”
    - 落とさない代わりに、レベルを UNKNOWN / PARSE_ERROR 扱いにする
    """
    raw = line.rstrip("\n")
    if fmt == "raw":
        return ("RAW", raw.strip())

    if fmt == "bracket":
        m = _BRACKET_RE.match(raw.strip())
        if not m:
            return ("UNKNOWN", raw.strip())
        level = m.group("level").upper()
        msg = m.group("msg").strip()
        return (level, msg)

    if fmt == "jsonl":
        s = raw.strip()
        if not s:
            return ("EMPTY", "")
        try:
            obj = json.loads(s)
        except Exception as exc:
            logger.info("jsonl parse error: %s", exc)
            return ("PARSE_ERROR", s[:200])
        if not isinstance(obj, dict):
            return ("PARSE_ERROR", s[:200])
        level = str(obj.get("level", "UNKNOWN")).upper()
        msg = obj.get("message", obj.get("msg", ""))
        return (level, str(msg))

    # fmt のchoicesで通常来ないが、保険で UNKNOWN
    return ("UNKNOWN", raw.strip())


def compute_log_stats(lines: Iterable[str], fmt: str, top_n: int, logger: logging.Logger) -> LogStats:
    """
    ログを集計する（なるべく純粋関数っぽく）。

    仕様として守りたいこと：
    - 1行ずつ処理する（streaming）
    - レベル別件数を数える
    - メッセージ頻度の上位N件を出す（top_n<=0なら空）
    """
    total = 0
    by_level: Counter[str] = Counter()
    by_message: Counter[str] = Counter()

    for line in lines:
        total += 1
        level, msg = parse_record(line, fmt, logger)
        by_level[level] += 1
        if msg:
            by_message[msg] += 1

    top_messages: list[MessageCount] = []
    if top_n > 0:
        for msg, cnt in by_message.most_common(top_n):
            top_messages.append(MessageCount(message=msg, count=cnt))

    return LogStats(total_lines=total, by_level=dict(by_level), top_messages=top_messages)


# -------------------------
# 出力（I/O境界：stdout / ファイル / HTTP）
# -------------------------


def build_json_payload(
    input_path: Path | None,
    fmt: str,
    top_n: int,
    stats: LogStats,
) -> dict[str, Any]:
    """
    JSON用の辞書を組み立てる（表示形式の責務）。

    ポイント：
    - payload の形（キー名など）は logsum 固有の“出力仕様”
    - なので toolkit ではなく logsum 側が持つ
    """
    return {
        "input": str(input_path) if input_path is not None else "",
        "format": fmt,
        "top_n": top_n,
        "total_lines": stats.total_lines,
        "by_level": stats.by_level,
        "top_messages": [{"message": m.message, "count": m.count} for m in stats.top_messages],
    }


# -------------------------
# 実行フロー組み立て（入口を薄くする）
# -------------------------


def resolve_effective_args(argv: list[str] | None) -> tuple[argparse.Namespace, logging.Logger]:
    """
    CLI/env/config を統合して「最終的に使う args」を確定する。

    ここを関数に切る狙い：
    - 優先順位（CLI > env > config）の処理を main から分離する
    - テスト時に「設定解決だけ」を独立に確認しやすくする
    """
    args = parse_args(argv)

    # CLIで位置引数(input)が渡されたか（後から判別できないので先に確保）
    input_from_cli = args.input is not None

    provided = toolkit.parse_provided_options(argv)

    # まずはCLIのverboseで暫定loggerを作る（env/configでverboseが変わったら作り直す）
    logger = toolkit.setup_logger(LOGGER_NAME, args.verbose)

    # --env-file の読み込み（OS環境変数より優先されるのは get_env 側の仕様）
    env_file: dict[str, str] = {}
    if args.env_file is not None:
        env_file = toolkit.load_env_file(args.env_file, logger)

    # env から config パスを先に解決しておく（configは最下位なので先に読む必要がある）
    if args.config is None and "--config" not in provided:
        v = toolkit.get_env("LOGSUM_CONFIG", env_file)
        if v:
            args.config = Path(v)

    # config（最下位）を適用
    if args.config is not None:
        cfg = load_config(args.config, logger)
        apply_config(args, cfg, provided, logger)

    # env（中位）を適用
    apply_env(args, env_file, provided, logger, input_from_cli)

    # verbose が env/config で変わりうるので logger を組み直す（ログレベルが反映される）
    logger = toolkit.setup_logger(LOGGER_NAME, args.verbose)
    return args, logger


def validate_args(args: argparse.Namespace, input_path: Path | None) -> int:
    """
    入力検証。失敗したら終了コード（2）を返す。

    ここを関数に切る狙い：
    - main の「流れ」を読みやすくする
    - どこが「入力の正しさ」を担保しているかを明確にする
    """
    if args.top < 0:
        print(f"Error: --top の値は0以上でなければなりません: {args.top}", file=sys.stderr)
        return 2
    if args.timeout <= 0:
        print(f"Error: --timeout の値は0より大きい必要があります: {args.timeout}", file=sys.stderr)
        return 2
    if args.format not in {"bracket", "jsonl", "raw"}:
        print(f"Error: --format が不正です: {args.format}", file=sys.stderr)
        return 2
    if input_path is not None:
        if not input_path.exists():
            print(f"Error: 入力ファイルが存在しません: {input_path}", file=sys.stderr)
            return 2
        if not input_path.is_file():
            print(f"Error: 入力パスがファイルではありません: {input_path}", file=sys.stderr)
            return 2
    return 0


def main(argv: list[str] | None = None) -> int:
    """
    実行入口（テストからも呼べる形）。

    意図：
    - resolve_effective_args（設定解決）
    - validate_args（入力検証）
    - read/compute（実処理）
    - output（副作用）
    を順に並べて、「責務の境界」が読める形にする。
    """
    args, logger = resolve_effective_args(argv)

    input_path: Path | None = None
    if args.input is not None:
        input_path = args.input.expanduser().resolve()

    rc = validate_args(args, input_path)
    if rc != 0:
        return rc

    logger.info("log read start: input=%s format=%s top=%d", input_path, args.format, args.top)
    stats = compute_log_stats(iter_lines(input_path), fmt=args.format, top_n=args.top, logger=logger)
    logger.info("log read done: total_lines=%d", stats.total_lines)

    # payloadは --json / --post / --out のどれかで必要
    payload: dict[str, Any] | None = None
    if args.json or args.post or args.out is not None:
        payload = build_json_payload(input_path=input_path, fmt=args.format, top_n=args.top, stats=stats)

    # --json: stdoutはJSON専用
    if args.json and payload is not None:
        print(json.dumps(payload, ensure_ascii=False, indent=2))

    # --out: payload をファイルに保存（stdoutは汚さない）
    if args.out is not None and payload is not None:
        ok = toolkit.write_json_file(args.out, payload, logger)
        if not ok:
            return 1

    # --post: stderr(log)で結果を報告（stdoutを汚さない）
    if args.post and payload is not None:
        ok = toolkit.post_json(args.post, payload, timeout=args.timeout, logger=logger)
        if not ok:
            return 1

    # --json のときは人間向け表示はしない
    if args.json:
        return 0

    # 人間向け表示（ざっくり状況が分かればOK）
    src = str(input_path) if input_path is not None else "<stdin>"
    print(f"input:     {src}")
    print(f"format:    {args.format}")
    print(f"lines:     {stats.total_lines}")
    print("by_level:")
    for level, cnt in sorted(stats.by_level.items(), key=lambda t: (-t[1], t[0])):
        print(f"  {level}: {cnt}")

    if args.top > 0 and stats.top_messages:
        print(f"top_messages: {args.top}")
        for m in stats.top_messages:
            print(f"  {m.count}\t{m.message}")

    return 0