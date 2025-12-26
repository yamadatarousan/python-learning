"""
Day25: dirscan を toolkit.py を使う形に整理した版（コメント整理つき）

狙い：
- 「小ツール共通のI/Oまわり（logger/.env/bool/POST/JSON保存）」は toolkit.py に寄せる
- dirscan.py は「dirscan 固有の処理（引数仕様、config仕様、走査、集計、payload構造）」に集中する
- こうすると別ツール（例：ログ集計）を作るときに “I/Oの同じ部分” をコピペせずに済む
"""

from __future__ import annotations

import argparse
import heapq
import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator, Tuple

# Python 3.8 互換: TypeAlias は 3.10+。3.8 では typing_extensions を使う。
try:
    from typing import TypeAlias  # Python 3.10+
except ImportError:  # pragma: no cover
    from typing_extensions import TypeAlias  # Python 3.8/3.9

import toolkit

LOGGER_NAME = "dirscan"


# -------------------------
# CLIパース（I/O境界：入力）
# -------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """
    CLI引数を定義して、解析結果（args）を返す。

    ここでは「dirscan が受け取る項目（仕様）」だけを列挙する。
    env/configの優先順位や補完は別関数（resolve_effective_args）でやる。
    """
    parser = argparse.ArgumentParser(description="List largest files under a directory (recursively).")

    parser.add_argument(
        "directory",
        nargs="?",
        default=None,  # config/envで上書きできるように「未指定(None)」を区別する
        type=Path,
        help="走査対象のディレクトリ（省略時はカレントディレクトリ）",
    )

    parser.add_argument(
        "--mode",
        choices=["file", "all"],
        default="file",
        help="数える対象を指定する（file: 通常ファイルのみ、all: ディレクトリ以外すべて）",
    )

    parser.add_argument(
        "--top",
        type=int,
        default=10,
        help="Show top N largest entries (default: 10). Use 0 to disable listing.",
    )

    parser.add_argument("--human", action="store_true", help="サイズを人間向け表記で表示する")
    parser.add_argument("--verbose", action="store_true", help="走査中の詳細ログを表示する")
    parser.add_argument("--json", action="store_true", help="集計結果をJSON形式で出力する")

    parser.add_argument(
        "--min-size",
        type=int,
        default=0,
        help="指定したサイズ（バイト）以上のエントリのみを集計対象とする",
    )

    parser.add_argument("--relative", action="store_true", help="出力するパスをrootからの相対パスにする")

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
      {"directory": "/tmp", "mode": "all", "top": 20, "min_size": 1024, "relative": true}
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
    - どのキー名を使うか（min_size など）は dirscan 固有の仕様なのでここに残す
    """

    def has(name: str) -> bool:
        return name in cfg

    # directory（位置引数）：未指定(None)のときだけconfigを使う
    if args.directory is None and has("directory"):
        args.directory = Path(str(cfg["directory"]))

    if "--mode" not in provided and has("mode"):
        args.mode = str(cfg["mode"])
    if "--top" not in provided and has("top"):
        args.top = int(cfg["top"])
    if "--min-size" not in provided and has("min_size"):
        args.min_size = int(cfg["min_size"])
    if "--timeout" not in provided and has("timeout"):
        args.timeout = float(cfg["timeout"])
    if "--post" not in provided and has("post"):
        args.post = str(cfg["post"])
    if "--out" not in provided and has("out"):
        args.out = Path(str(cfg["out"]))

    # store_true のフラグ類は、CLI未指定なら config を反映してよい
    if "--human" not in provided and has("human"):
        args.human = bool(cfg["human"])
    if "--verbose" not in provided and has("verbose"):
        args.verbose = bool(cfg["verbose"])
    if "--json" not in provided and has("json"):
        args.json = bool(cfg["json"])
    if "--relative" not in provided and has("relative"):
        args.relative = bool(cfg["relative"])


# -------------------------
# env適用（I/O境界：入力）
# -------------------------


def apply_env(
    args: argparse.Namespace,
    env_file: dict[str, str],
    provided: set[str],
    logger: logging.Logger,
    directory_from_cli: bool,
) -> None:
    """
    envの値を args に反映する（ただしCLI指定が優先）。

    仕様として守りたいこと：
    - CLI > env > config（configは先に適用しておく）
    - directory（位置引数）は「CLIで渡されたかどうか」を別扱いして上書き事故を防ぐ

    対応する環境変数名（dirscan 固有の“名前”なのでここに残す）：
      DIRSCAN_DIRECTORY, DIRSCAN_MODE, DIRSCAN_TOP, DIRSCAN_MIN_SIZE,
      DIRSCAN_HUMAN, DIRSCAN_VERBOSE, DIRSCAN_JSON, DIRSCAN_RELATIVE,
      DIRSCAN_POST, DIRSCAN_TIMEOUT, DIRSCAN_OUT, DIRSCAN_CONFIG
    """
    # configパス：CLI未指定かつargs.config未指定のときだけ
    if "--config" not in provided and args.config is None:
        v = toolkit.get_env("DIRSCAN_CONFIG", env_file)
        if v:
            args.config = Path(v)

    # directory（位置引数）：CLIで渡されたなら env では上書きしない
    if not directory_from_cli:
        v = toolkit.get_env("DIRSCAN_DIRECTORY", env_file)
        if v:
            args.directory = Path(v)

    if "--mode" not in provided:
        v = toolkit.get_env("DIRSCAN_MODE", env_file)
        if v:
            args.mode = v
    if "--top" not in provided:
        v = toolkit.get_env("DIRSCAN_TOP", env_file)
        if v:
            args.top = int(v)
    if "--min-size" not in provided:
        v = toolkit.get_env("DIRSCAN_MIN_SIZE", env_file)
        if v:
            args.min_size = int(v)
    if "--timeout" not in provided:
        v = toolkit.get_env("DIRSCAN_TIMEOUT", env_file)
        if v:
            args.timeout = float(v)
    if "--post" not in provided:
        v = toolkit.get_env("DIRSCAN_POST", env_file)
        if v:
            args.post = v

    # out は env が config を上書きできる必要がある（CLI以外は上書きOK）
    if "--out" not in provided:
        v = toolkit.get_env("DIRSCAN_OUT", env_file)
        if v:
            args.out = Path(v)

    if "--human" not in provided:
        v = toolkit.get_env("DIRSCAN_HUMAN", env_file)
        if v is not None:
            args.human = toolkit.parse_bool(v)
    if "--verbose" not in provided:
        v = toolkit.get_env("DIRSCAN_VERBOSE", env_file)
        if v is not None:
            args.verbose = toolkit.parse_bool(v)
    if "--json" not in provided:
        v = toolkit.get_env("DIRSCAN_JSON", env_file)
        if v is not None:
            args.json = toolkit.parse_bool(v)
    if "--relative" not in provided:
        v = toolkit.get_env("DIRSCAN_RELATIVE", env_file)
        if v is not None:
            args.relative = toolkit.parse_bool(v)

    logger.info("env applied (CLI overrides env)")


# -------------------------
# データモデル（DTO）
# -------------------------


@dataclass(frozen=True)
class Entry:
    """
    走査結果のDTO（パス + サイズ）。

    frozen=True の狙い：
    - 集計中に「途中で書き換わる」事故を防ぐ
    - テストで扱うときに前提が揺れない
    """

    path: Path
    size: int


@dataclass(frozen=True)
class Stats:
    """
    集計結果のDTO（件数 / 合計バイト / topN）。

    ここをDTOにしておくと：
    - 計算部分の戻り値が一つにまとまる
    - 出力形式（表示/JSON）と計算ロジックを分けやすい
    """

    count: int
    total_bytes: int
    top: list[Entry]


# -------------------------
# 走査・計算（コアロジック）
# -------------------------


def should_count(path: Path, mode: str) -> bool:
    """modeに応じて「件数/対象に含めるか」を決める。"""
    if mode == "file":
        return path.is_file()
    if mode == "all":
        return not path.is_dir()
    return False


def format_path(path: Path, root: Path, relative: bool) -> str:
    """出力用のパス文字列を作る（relative=Trueなら可能なら相対パス）。"""
    if not relative:
        return str(path)
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def iter_entries(root: Path, mode: str, min_size: int, logger: logging.Logger) -> Iterator[Entry]:
    """
    root以下を走査してEntryを順次yieldする（I/O側）。

    仕様として守りたいこと：
    - listを作らず streaming で流す（巨大ディレクトリでもメモリを食いにくい）
    - statが取れないものはスキップし、理由はstderrログへ（落とさない）
    - min_size 未満は除外（集計対象を減らす）
    """
    for path in root.rglob("*"):
        if not should_count(path, mode):
            continue

        try:
            size = path.stat().st_size
        except OSError as exc:
            logger.info("[skip] %s: %s", path, exc)
            continue

        if size < min_size:
            continue

        yield Entry(path=path, size=size)


HeapItem: TypeAlias = Tuple[int, str, Entry]


def compute_stats(entries: Iterable[Entry], top_n: int) -> Stats:
    """
    計算部分（なるべく純粋関数っぽく）。

    仕様として守りたいこと：
    - entriesを1回なめて count/total を計算する
    - top_n が小さい想定なので、min-heapで「上位N件だけ」保持する
      → 全件ソートを避ける（N << 全件数 のケースに強い）
    """
    count = 0
    total_bytes = 0
    heap: list[HeapItem] = []

    for e in entries:
        count += 1
        total_bytes += e.size

        if top_n <= 0:
            continue

        # tie-breaker を path で固定しておく（同サイズのときの表示ブレを減らす）
        item: HeapItem = (e.size, str(e.path), e)
        if len(heap) < top_n:
            heapq.heappush(heap, item)
        else:
            if item > heap[0]:
                heapq.heapreplace(heap, item)

    top_entries = [t[2] for t in sorted(heap, key=lambda t: (-t[0], t[1]))]
    return Stats(count=count, total_bytes=total_bytes, top=top_entries)


# -------------------------
# 出力（I/O境界：stdout / ファイル / HTTP）
# -------------------------


def build_json_payload(root: Path, mode: str, min_size: int, top_n: int, stats: Stats, relative: bool) -> dict[str, Any]:
    """
    JSON用の辞書を組み立てる（表示形式の責務）。

    ポイント：
    - payload の形（キー名など）は dirscan 固有の“出力仕様”
    - なので toolkit ではなく dirscan 側が持つ
    """
    return {
        "directory": str(root),
        "mode": mode,
        "min_size": min_size,
        "count": stats.count,
        "total_bytes": stats.total_bytes,
        "top_n": top_n,
        "top": [{"path": format_path(e.path, root, relative), "size_bytes": e.size} for e in stats.top],
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

    # CLIで位置引数(directory)が渡されたか（後から判別できないので先に確保）
    directory_from_cli = args.directory is not None

    provided = toolkit.parse_provided_options(argv)

    # まずはCLIのverboseで暫定loggerを作る（env/configでverboseが変わったら作り直す）
    logger = toolkit.setup_logger(LOGGER_NAME, args.verbose)

    # --env-file の読み込み（OS環境変数より優先されるのは get_env 側の仕様）
    env_file: dict[str, str] = {}
    if args.env_file is not None:
        env_file = toolkit.load_env_file(args.env_file, logger)

    # env から config パスを先に解決しておく（configは最下位なので先に読む必要がある）
    if args.config is None and "--config" not in provided:
        v = toolkit.get_env("DIRSCAN_CONFIG", env_file)
        if v:
            args.config = Path(v)

    # config（最下位）を適用
    if args.config is not None:
        cfg = load_config(args.config, logger)
        apply_config(args, cfg, provided, logger)

    # env（中位）を適用
    apply_env(args, env_file, provided, logger, directory_from_cli)

    # verbose が env/config で変わりうるので logger を組み直す（ログレベルが反映される）
    logger = toolkit.setup_logger(LOGGER_NAME, args.verbose)
    return args, logger


def validate_args(args: argparse.Namespace, root: Path) -> int:
    """
    入力検証。失敗したら終了コード（2）を返す。

    ここを関数に切る狙い：
    - main の「流れ」を読みやすくする
    - どこが「入力の正しさ」を担保しているかを明確にする
    """
    if not root.exists():
        print(f"Error: 指定されたパスが存在しません: {root}", file=sys.stderr)
        return 2
    if not root.is_dir():
        print(f"Error: 指定されたパスはディレクトリではありません: {root}", file=sys.stderr)
        return 2
    if args.top < 0:
        print(f"Error: --top の値は0以上でなければなりません: {args.top}", file=sys.stderr)
        return 2
    if args.min_size < 0:
        print(f"Error: --min-size の値は0以上でなければなりません: {args.min_size}", file=sys.stderr)
        return 2
    if args.timeout <= 0:
        print(f"Error: --timeout の値は0より大きい必要があります: {args.timeout}", file=sys.stderr)
        return 2
    return 0


def main(argv: list[str] | None = None) -> int:
    """
    実行入口（テストからも呼べる形）。

    意図：
    - resolve_effective_args（設定解決）
    - validate_args（入力検証）
    - scan/compute（実処理）
    - output（副作用）
    を順に並べて、「責務の境界」が読める形にする。
    """
    args, logger = resolve_effective_args(argv)

    directory = args.directory if args.directory is not None else Path(".")
    root: Path = directory.expanduser().resolve()

    rc = validate_args(args, root)
    if rc != 0:
        return rc

    logger.info("scan start: root=%s mode=%s min_size=%d top=%d", root, args.mode, args.min_size, args.top)
    entries = iter_entries(root, mode=args.mode, min_size=args.min_size, logger=logger)
    stats = compute_stats(entries, top_n=args.top)
    logger.info("scan done: count=%d total_bytes=%d", stats.count, stats.total_bytes)

    # payloadは --json / --post / --out のどれかで必要
    payload: dict[str, Any] | None = None
    if args.json or args.post or args.out is not None:
        payload = build_json_payload(
            root=root,
            mode=args.mode,
            min_size=args.min_size,
            top_n=args.top,
            stats=stats,
            relative=args.relative,
        )

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

    # 人間向け表示（従来どおり）
    display_total = toolkit.human_size(stats.total_bytes) if args.human else str(stats.total_bytes)
    print(f"directory: {root}")
    print(f"mode:      {args.mode}")
    print(f"min-size:  {args.min_size}")
    print(f"relative:  {args.relative}")
    print(f"count:     {stats.count}")
    print(f"total:     {display_total}")

    if args.top > 0:
        print(f"top:       {args.top}")
        for e in stats.top:
            size_str = toolkit.human_size(e.size) if args.human else str(e.size)
            print(f"{size_str}\t{format_path(e.path, root, args.relative)}")

    return 0
