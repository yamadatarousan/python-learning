"""
Day27: logsum（ログ要約ツール）

狙い：
- 2つ目の小ツールを作って、toolkit の “共通処理” を実際に使い回す感覚を掴む
- 「I/Oの入口（CLI/env/config）」と「中身（集計）」と「出口（stdout/file/http）」を分けて読める形にする

このツールがやること（ざっくり）：
- ログ行を読み込む（ファイル or stdin）
- 行頭の [INFO] みたいな形式、または JSONL を解釈して level を取り出す
- level ごとの件数と、よく出る message 上位N件を出す
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator, Tuple

import toolkit

LOGGER_NAME = "logsum"


# -------------------------
# CLIパース（I/O境界：入力）
# -------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """
    CLI引数を定義して、解析結果（args）を返す。

    ここでやるのは「logsumの引数の並び（仕様）」を決めることだけ。
    env/config の優先順位や補完は resolve_effective_args でやる。
    """
    parser = argparse.ArgumentParser(description="Summarize logs by level and frequent messages.")

    parser.add_argument(
        "path",
        nargs="?",
        default=None,  # config/envで上書きできるように「未指定(None)」を区別する
        type=Path,
        help="入力ログファイル（省略時はstdin）",
    )

    parser.add_argument(
        "--format",
        choices=["bracket", "jsonl"],
        default="bracket",
        help="入力の形式（bracket: [INFO] msg 形式 / jsonl: 1行1JSON）",
    )

    parser.add_argument(
        "--top",
        type=int,
        default=10,
        help="よく出る message 上位N件を表示する（default: 10）。0なら表示しない。",
    )

    parser.add_argument("--json", action="store_true", help="集計結果をJSON形式で出力する")
    parser.add_argument("--verbose", action="store_true", help="処理中の詳細ログをstderrに出す")

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
        help="Write the JSON payload to a file (e.g., report.json).",
    )
    parser.add_argument(
        "--post",
        type=str,
        default="",
        help="集計結果のJSONをPOSTするURLを指定する（指定しない場合はPOSTしない）",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="HTTP POSTのタイムアウト秒数（デフォルト: 10.0秒）",
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
      {"path": "app.log", "format": "bracket", "top": 20, "json": true}
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
    - キー名（path/format/top/json...）は logsum 固有の仕様なのでここに残す
    """

    def has(name: str) -> bool:
        return name in cfg

    # path（位置引数）：未指定(None)のときだけconfigを使う
    if args.path is None and has("path"):
        args.path = Path(str(cfg["path"]))

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

    # store_true フラグ類は CLI未指定なら config を反映してよい
    if "--json" not in provided and has("json"):
        args.json = bool(cfg["json"])
    if "--verbose" not in provided and has("verbose"):
        args.verbose = bool(cfg["verbose"])

    logger.info("config applied (CLI overrides config)")


# -------------------------
# env適用（I/O境界：入力）
# -------------------------


def apply_env(
    args: argparse.Namespace,
    env_file: dict[str, str],
    provided: set[str],
    logger: logging.Logger,
    path_from_cli: bool,
) -> None:
    """
    envの値を args に反映する（ただしCLI指定が優先）。

    仕様として守りたいこと：
    - CLI > env > config（configは先に適用しておく）
    - path（位置引数）は「CLIで渡されたかどうか」を別扱いして上書き事故を防ぐ

    対応する環境変数名（logsum 固有の“名前”なのでここに残す）：
      LOGSUM_PATH, LOGSUM_FORMAT, LOGSUM_TOP,
      LOGSUM_JSON, LOGSUM_VERBOSE,
      LOGSUM_POST, LOGSUM_TIMEOUT, LOGSUM_OUT, LOGSUM_CONFIG
    """
    # configパス：CLI未指定かつargs.config未指定のときだけ
    if "--config" not in provided and args.config is None:
        v = toolkit.get_env("LOGSUM_CONFIG", env_file)
        if v:
            args.config = Path(v)

    # path（位置引数）：CLIで渡されたなら env では上書きしない
    if not path_from_cli:
        v = toolkit.get_env("LOGSUM_PATH", env_file)
        if v:
            args.path = Path(v)

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
class LogEvent:
    """
    1行から取り出した “最低限の情報” のDTO。

    このツールの都合：
    - 「level と message が取れれば集計できる」
    - 元の行がどんな形式でも、ここに寄せれば下流が単純になる
    """

    level: str
    message: str


@dataclass(frozen=True)
class Summary:
    """
    集計結果のDTO。

    ここをDTOにしておく狙い：
    - 集計ロジック（compute_summary）の戻り値を1つにまとめる
    - 表示（print/JSON）側が “何を持っているか” を読みやすくする
    """

    total_lines: int
    by_level: dict[str, int]
    top_messages: list[Tuple[str, int]]  # (message, count)


# -------------------------
# パース・集計（コアロジック）
# -------------------------


def parse_bracket_line(line: str) -> LogEvent:
    """
    [INFO] hello みたいな形式を想定して LogEvent を作る。

    ルール（ゆるめ）：
    - 行頭が "[" で、"]" が見つかれば level とする
    - それ以外は level=UNKNOWN 扱い
    """
    s = line.strip("\n")
    if s.startswith("[") and "]" in s:
        close = s.find("]")
        level = s[1:close].strip() or "UNKNOWN"
        msg = s[close + 1 :].lstrip()
        return LogEvent(level=level, message=msg)
    return LogEvent(level="UNKNOWN", message=s.strip())


def parse_jsonl_line(line: str, logger: logging.Logger) -> LogEvent | None:
    """
    JSONL（1行1JSON）を想定して LogEvent を作る。

    期待する例：
      {"level": "INFO", "message": "hello"}

    壊れている行は None を返してスキップする（落とさない）。
    """
    s = line.strip()
    if not s:
        return None
    try:
        obj = json.loads(s)
    except Exception as exc:
        logger.info("[skip] json parse failed: %s (%s)", s[:200], exc)
        return None
    if not isinstance(obj, dict):
        logger.info("[skip] json must be object: %s", s[:200])
        return None
    level = str(obj.get("level", "UNKNOWN"))
    message = str(obj.get("message", ""))
    return LogEvent(level=level, message=message)


def iter_events(lines: Iterable[str], fmt: str, logger: logging.Logger) -> Iterator[LogEvent]:
    """
    入力の行（文字列）を LogEvent に変換して流す。

    ここを iterator にする理由：
    - 入口（読み込み）と中身（集計）をつなぐ “変換レイヤ” を分けたい
    - 後で形式が増えても、ここだけ触れば済む
    """
    if fmt == "bracket":
        for line in lines:
            yield parse_bracket_line(line)
        return
    if fmt == "jsonl":
        for line in lines:
            ev = parse_jsonl_line(line, logger)
            if ev is None:
                continue
            yield ev
        return

    # ここには通常来ない（保険）
    for line in lines:
        yield LogEvent(level="UNKNOWN", message=line.strip())


def compute_summary(events: Iterable[LogEvent], top_n: int) -> Summary:
    """
    LogEvent を集計して Summary を返す。

    仕様として守りたいこと：
    - total_lines: 何行（イベント）処理したか
    - by_level: levelごとの件数
    - top_messages: messageの出現回数 上位N件（top_n<=0 なら空）
    """
    total = 0
    by_level: dict[str, int] = {}
    msg_count: dict[str, int] = {}

    for ev in events:
        total += 1
        by_level[ev.level] = by_level.get(ev.level, 0) + 1
        if top_n > 0:
            msg_count[ev.message] = msg_count.get(ev.message, 0) + 1

    top_messages: list[Tuple[str, int]] = []
    if top_n > 0 and msg_count:
        items = sorted(msg_count.items(), key=lambda t: (-t[1], t[0]))
        top_messages = items[:top_n]

    return Summary(total_lines=total, by_level=by_level, top_messages=top_messages)


# -------------------------
# 出力（I/O境界：stdout / ファイル / HTTP）
# -------------------------


def build_json_payload(source: str, fmt: str, top_n: int, summary: Summary) -> dict[str, Any]:
    """
    JSON用の辞書を組み立てる（表示形式の責務）。

    ポイント：
    - payload の形（キー名など）は logsum 固有の“出力仕様”
    - なので toolkit ではなく logsum 側が持つ
    """
    return {
        "source": source,
        "format": fmt,
        "top_n": top_n,
        "total_lines": summary.total_lines,
        "by_level": summary.by_level,
        "top_messages": [{"message": m, "count": c} for (m, c) in summary.top_messages],
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

    # CLIで位置引数(path)が渡されたか（後から判別できないので先に確保）
    path_from_cli = args.path is not None

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
    apply_env(args, env_file, provided, logger, path_from_cli)

    # verbose が env/config で変わりうるので logger を組み直す（ログレベルが反映される）
    logger = toolkit.setup_logger(LOGGER_NAME, args.verbose)
    return args, logger


def validate_args(args: argparse.Namespace) -> int:
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
    if args.path is not None:
        if not args.path.exists():
            print(f"Error: 指定されたパスが存在しません: {args.path}", file=sys.stderr)
            return 2
        if not args.path.is_file():
            print(f"Error: 指定されたパスはファイルではありません: {args.path}", file=sys.stderr)
            return 2
    return 0


def iter_input_lines(path: Path | None) -> Iterator[str]:
    """
    入力行を1行ずつ返す。

    仕様として守りたいこと：
    - path があればファイルから読む
    - path がなければ stdin から読む
    - encoding の問題で落ちないように errors="replace" を使う
    """
    if path is None:
        for line in sys.stdin:
            yield line
        return
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            yield line


def main(argv: list[str] | None = None) -> int:
    """
    実行入口（テストからも呼べる形）。

    意図：
    - resolve_effective_args（設定解決）
    - validate_args（入力検証）
    - parse/compute（実処理）
    - output（副作用）
    を順に並べて、「責務の境界」が読める形にする。
    """
    args, logger = resolve_effective_args(argv)

    rc = validate_args(args)
    if rc != 0:
        return rc

    source = str(args.path.expanduser().resolve()) if args.path is not None else "<stdin>"
    logger.info("logsum start: source=%s format=%s top=%d", source, args.format, args.top)

    lines = iter_input_lines(args.path)
    events = iter_events(lines, fmt=args.format, logger=logger)
    summary = compute_summary(events, top_n=args.top)

    logger.info("logsum done: total_lines=%d", summary.total_lines)

    # payloadは --json / --post / --out のどれかで必要
    payload: dict[str, Any] | None = None
    if args.json or args.post or args.out is not None:
        payload = build_json_payload(source=source, fmt=args.format, top_n=args.top, summary=summary)

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

    # 人間向け表示
    print(f"source:     {source}")
    print(f"format:     {args.format}")
    print(f"total:      {summary.total_lines}")
    print("by_level:")
    for level, count in sorted(summary.by_level.items(), key=lambda t: (-t[1], t[0])):
        print(f"  {level}: {count}")

    if args.top > 0:
        print(f"top:        {args.top}")
        for msg, count in summary.top_messages:
            print(f"{count}\t{msg}")

    return 0
