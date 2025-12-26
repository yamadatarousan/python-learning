"""
Day28: ログ集計ツール（logsum）

狙い：
- 2つ目の小ツールを作って「共通処理(toolkit) + ツール固有処理」の分け方を固める
- 設定の優先順位（CLI > env > config）を、dirscan と同じノリで使い回せる形にする

このツールがやること（ざっくり）：
- ログファイル（または標準入力）を1行ずつ読む
- 1行が `[INFO] message` みたいな形なら、level=INFO として数える
- levelごとの件数と、よく出る message の TOP N を出す
- 必要なら JSON を stdout / ファイル保存 / HTTP POST できる（toolkit を利用）
"""

from __future__ import annotations

import argparse
import heapq
import json
import logging
import re
import sys
from collections import Counter
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

    ここでは「logsum が受け取る項目（仕様）」だけを列挙する。
    env/configの優先順位や補完は別関数（resolve_effective_args）でやる。
    """
    parser = argparse.ArgumentParser(description="Summarize log lines (count by level, top messages).")

    parser.add_argument(
        "path",
        nargs="?",
        default=None,  # config/envで上書きできるように「未指定(None)」を区別する
        type=Path,
        help="ログファイルのパス（省略時はstdin）。 '-' でもstdin扱い。",
    )

    parser.add_argument(
        "--level",
        type=str,
        default="",
        help="このレベルだけ集計する（例: INFO）。空なら全部。",
    )

    parser.add_argument(
        "--top",
        type=int,
        default=5,
        help="よく出る message の上位N件（default: 5）。0なら出さない。",
    )

    parser.add_argument("--json", action="store_true", help="集計結果をJSON形式で出力する")
    parser.add_argument("--verbose", action="store_true", help="処理中の詳細ログを表示する")

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
      {"path": "app.log", "level": "INFO", "top": 10, "json": true}
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
    - どのキー名を使うか（path / level など）は logsum 固有の仕様なのでここに残す
    """

    def has(name: str) -> bool:
        return name in cfg

    # path（位置引数）：未指定(None)のときだけconfigを使う
    if args.path is None and has("path"):
        args.path = Path(str(cfg["path"]))

    if "--level" not in provided and has("level"):
        args.level = str(cfg["level"])
    if "--top" not in provided and has("top"):
        args.top = int(cfg["top"])
    if "--timeout" not in provided and has("timeout"):
        args.timeout = float(cfg["timeout"])
    if "--post" not in provided and has("post"):
        args.post = str(cfg["post"])
    if "--out" not in provided and has("out"):
        args.out = Path(str(cfg["out"]))

    # store_true のフラグ類は、CLI未指定なら config を反映してよい
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
      LOGSUM_PATH, LOGSUM_LEVEL, LOGSUM_TOP,
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

    if "--level" not in provided:
        v = toolkit.get_env("LOGSUM_LEVEL", env_file)
        if v is not None and v != "":
            args.level = v
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


@dataclass
class Record:
    """
    1行ぶんの「解釈結果」DTO（level + message）。

    ここをDTOにする狙い：
    - 行の解釈（parse_line）と、集計（compute_stats）を切り分けやすくする
    - テストで「この行はどう解釈されるべきか」を確認しやすい
    """

    level: str
    message: str


@dataclass
class Stats:
    """
    集計結果DTO。

    - total_lines: 集計対象にした行数
    - level_counts: level別の件数
    - top_messages: よく出る message の上位（(count, message) の配列）
    """

    total_lines: int
    level_counts: dict[str, int]
    top_messages: list[Tuple[int, str]]


# -------------------------
# 走査・計算（コアロジック）
# -------------------------


_BRACKET_RE = re.compile(r"^\[(?P<level>[A-Za-z]+)\]\s*(?P<msg>.*)$")
_COLON_RE = re.compile(r"^(?P<level>[A-Za-z]+)\s*:\s*(?P<msg>.*)$")


def parse_line(line: str) -> Record | None:
    """
    1行を (level, message) に分解する。

    対応する形：
    - `[INFO] something`
    - `INFO: something`

    それ以外は level=UNKNOWN として扱う（完全に捨てるより「数えた」ほうが原因調査に役立つ）。
    空行は None（集計対象外）にする。
    """
    s = line.strip("\n")
    if s.strip() == "":
        return None

    m = _BRACKET_RE.match(s.strip())
    if m:
        return Record(level=m.group("level").upper(), message=m.group("msg").strip())

    m = _COLON_RE.match(s.strip())
    if m:
        return Record(level=m.group("level").upper(), message=m.group("msg").strip())

    return Record(level="UNKNOWN", message=s.strip())


def iter_records_from_text(lines: Iterable[str]) -> Iterator[Record]:
    """
    テキスト行（Iterable[str]）から Record を順次yieldする。

    狙い：
    - ファイルでもstdinでも「行の列」にしてしまえば同じ処理で流せる
    - list化しない（大きいログでもメモリを食いにくい）
    """
    for line in lines:
        rec = parse_line(line)
        if rec is None:
            continue
        yield rec


def compute_stats(records: Iterable[Record], level_filter: str, top_n: int) -> Stats:
    """
    集計をする（なるべく純粋関数っぽく）。

    - level_filter が指定されているなら、そのlevelだけ数える（例: INFO）
    - top_n は message の頻出上位。0なら出さない。
    """
    level_filter_norm = level_filter.strip().upper()

    level_counter: Counter[str] = Counter()
    msg_counter: Counter[str] = Counter()
    total = 0

    for r in records:
        if level_filter_norm and r.level != level_filter_norm:
            continue

        total += 1
        level_counter[r.level] += 1
        msg_counter[r.message] += 1

    top_messages: list[Tuple[int, str]] = []
    if top_n > 0 and msg_counter:
        # (count, message) を作って nlargest。tie は message の辞書順で安定させる。
        items: list[Tuple[int, str]] = [(c, m) for m, c in msg_counter.items()]
        top_messages = heapq.nlargest(top_n, items, key=lambda t: (t[0], -len(t[1]), t[1]))
        # 表示は count desc, message asc に寄せる（見た目が安定する）
        top_messages = sorted(top_messages, key=lambda t: (-t[0], t[1]))

    return Stats(total_lines=total, level_counts=dict(level_counter), top_messages=top_messages)


# -------------------------
# 出力（I/O境界：stdout / ファイル / HTTP）
# -------------------------


def build_json_payload(path: str, level: str, top_n: int, stats: Stats) -> dict[str, Any]:
    """
    JSON用の辞書を組み立てる（表示形式の責務）。

    ポイント：
    - payload の形（キー名など）は logsum 固有の“出力仕様”
    - なので toolkit ではなく logsum 側が持つ
    """
    return {
        "path": path,
        "level": level,
        "top_n": top_n,
        "total_lines": stats.total_lines,
        "levels": [{"level": k, "count": v} for k, v in sorted(stats.level_counts.items(), key=lambda t: (-t[1], t[0]))],
        "top_messages": [{"count": c, "message": m} for c, m in stats.top_messages],
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

    # stdin の場合は存在チェックしない
    if args.path is None:
        return 0
    if str(args.path) == "-":
        return 0

    p: Path = args.path.expanduser()
    if not p.exists():
        print(f"Error: 指定されたパスが存在しません: {p}", file=sys.stderr)
        return 2
    if not p.is_file():
        print(f"Error: 指定されたパスはファイルではありません: {p}", file=sys.stderr)
        return 2
    return 0


def _open_lines(path: Path | None) -> tuple[str, Iterable[str]]:
    """
    入力を「行の列」にする。

    返り値：
    - 表示用の path 文字列（stdinなら "-"）
    - 行の Iterable[str]
    """
    if path is None:
        return "-", sys.stdin
    if str(path) == "-":
        return "-", sys.stdin
    p = path.expanduser()
    return str(p), p.read_text(encoding="utf-8", errors="replace").splitlines(True)


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

    rc = validate_args(args)
    if rc != 0:
        return rc

    display_path, lines = _open_lines(args.path)
    logger.info("read start: path=%s level=%s top=%d", display_path, args.level, args.top)

    records = iter_records_from_text(lines)
    stats = compute_stats(records, level_filter=args.level, top_n=args.top)
    logger.info("read done: total_lines=%d", stats.total_lines)

    # payloadは --json / --post / --out のどれかで必要
    payload: dict[str, Any] | None = None
    if args.json or args.post or args.out is not None:
        payload = build_json_payload(path=display_path, level=args.level, top_n=args.top, stats=stats)

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
    level_label = args.level.strip().upper() if args.level.strip() else "(all)"
    print(f"path:       {display_path}")
    print(f"level:      {level_label}")
    print(f"total:      {stats.total_lines}")
    print("levels:")
    for lvl, cnt in sorted(stats.level_counts.items(), key=lambda t: (-t[1], t[0])):
        print(f"  {lvl:8s} {cnt}")

    if args.top > 0:
        print(f"top:        {args.top}")
        for cnt, msg in stats.top_messages:
            print(f"{cnt}\t{msg}")

    return 0
