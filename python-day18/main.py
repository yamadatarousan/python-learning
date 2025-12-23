"""
Day17: ミニCLIツール（大きいファイルTOP N）— HTTP POSTで結果送信（Day15の続き）

このプログラムは、指定ディレクトリ以下を再帰的に走査して、
「サイズが大きい順の TOP N」を表示/JSON出力するCLIツール。

+Day17のポイント：
- httpx を使って外部APIにPOSTする（I/O追加）
- stdoutのJSONを壊さないため、HTTPの成否ログは logging(stderr) 側へ寄せる
- 計算部分（topN保持）は Day15 のまま（ストリーミング）

使い方：
    python main.py [directory] [--min-size N] [--top N] [--human] [--json] [--verbose] [--relative]
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

# このプログラムで学習してほしいこと（Day9の狙い）
# - argparse: 位置引数 + フラグ + choices + 数値引数（--top）を扱う
# - Path: 再帰走査（rglob）と stat を使う
# - try/except: 取れないstatがあっても落ちないCLIを作る
# - 「仕様（何を数えるか）」を mode として外に出す（Day8）
# - 「上位N件」の抽出で heapq.nlargest を使う（Day9）

def human_size(size: int) -> str:
    """バイト数を人間向け表記に変換する"""
    units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB']
    value = float(size)

    for unit in units:
        is_small_enough = value < 1024
        is_last_unit = unit == units[-1]
        if is_small_enough or is_last_unit:
            if unit == "B":
                return f"{int(value)}B"
            return f"{value:.1f}{unit}"
        
        # 次の単位へ（B→KB→MB...）
        value /= 1024
    
    # 通常ここには到達しない（保険）
    return f"{int(value)}{units[-1]}"

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """
    CLI引数を定義して、解析結果（args）を返す。
    - directory: 省略可能な位置引数（デフォルトはカレント）
    - --human: サイズを人間向けに表示するスイッチ
    - --verbose: 走査中の詳細ログを出すスイッチ
    - --mode: 何を「数える」対象とするか（Day8で追加）
    - --relative: 出力パスをrootからの相対パスにする（Day13）
    - --post: JSON payloadをHTTP POSTで送る（Day17）
    """
    parser = argparse.ArgumentParser(description="List largest files under a directory (recursively).")

    # 位置引数: directory
    # - nargs="?" により「0個 or 1個」指定できる（省略可能）
    # - default=Path(".") は省略時の値（カレント）
    # - type=Path により、文字列ではなく Path として受け取れる
    parser.add_argument(
        "directory",
        nargs="?",
        default=None,  # Day18: configで上書きできるように「未指定」を区別する
        type=Path,
        help="走査対象のディレクトリ（省略時はカレントディレクトリ）"
    )

    # Day8追加: 「何を数えるか」を仕様として明示する
    # - file: 通常ファイルのみ（一般的な“ファイル数”の期待に近い）
    # - all:  ディレクトリ以外を全部（ソケット等も件数に含める）
    parser.add_argument(
        "--mode",
        choices=["file", "all"],
        default="file",
        help="数える対象を指定する（file: 通常ファイルのみ、all: ディレクトリ以外すべて）"
    )

    # Day9: 「上位N件」を表示する数値引数
    # - 0 のときは表示しない（デフォルト）
    parser.add_argument(
        "--top",
        type=int,
        default=10,
        help="Show top N largest entries (default: 10). Use 0 to disable listing.",
    )

    # フラグ: 指定されたら True、指定されなければ False
    parser.add_argument(
        "--human",
        action="store_true",
        help="サイズを人間向け表記で表示する"
    )
    
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="走査中の詳細ログを表示する"
    )

    # Day10追加: JSONで出力する（ログはstderrのまま）
    parser.add_argument(
        "--json",
        action="store_true",
        help="集計結果をJSON形式で出力する"
    )

    # Day11追加: 最小サイズ（バイト）でフィルタ
    parser.add_argument(
        "--min-size",
        type=int,
        default=0,
        help="指定したサイズ（バイト）以上のエントリのみを集計対象とする"
    )

    parser.add_argument(
        "--relative",
        action="store_true",
        help="出力するパスをrootからの相対パスにする"
    )

    # Day17: HTTP POST
    parser.add_argument(
        "--post",
        type=str,
        default="",
        help="集計結果のJSONをPOSTするURLを指定する（指定しない場合はPOSTしない）"
    )

    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="HTTP POSTのタイムアウト秒数（デフォルト: 10.0秒）"
    )

    # Day18: JSON設定を読む / JSON結果をファイルに書く
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

    return parser.parse_args(argv)

def parse_provided_options(argv: list[str]) -> set[str]:
    """
    Day18: どのオプションがCLIで明示されたかを判定する。
    これにより「既定値」なのか「ユーザー指定」なのかを区別し、
    configの値で上書きしてよいか判断できる。
    """
    if argv is None:
        return set()
    provided: set[str] = set()
    for token in argv:
        if token.startswith("--"):
            provided.add(token.split("=", 1)[0])
    return provided

def load_config(path: Path, logger: logging.Logger) -> dict[str, Any]:
    """
    Day18: JSON設定ファイルを読み込む。
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
    Day18: configの値を args に反映する（ただしCLI指定が優先）。
    - CLIで明示されたオプションは上書きしない
    """
    def has(name: str) -> bool:
        return name in cfg
    
    # directory（位置引数）：未指定(None)のときだけconfigを使う
    if args.directory is None and has("directory"):
        args.directory = Path(str(cfg["directory"]))
    
    # mode / top / min_size / timeout / post / out
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

    # フラグ類（store_true）：CLIで付いてないときは config で True/False を反映してOK
    if "--human" not in provided and has("human"):
        args.human = bool(cfg["human"])
    if "--verbose" not in provided and has("verbose"):
        args.verbose = bool(cfg["verbose"])
    if "--json" not in provided and has("json"):
        args.json = bool(cfg["json"])
    if "--relative" not in provided and has("relative"):
        args.relative = bool(cfg["relative"])

def setup_logger(verbose: bool) -> logging.Logger:
    """
    Day14: ログをstderrに出すためのloggerを構成する。
    - verbose=False: WARNING以上
    - verbose=True : INFO以上
    """
    logger = logging.getLogger("dirscan")
    logger.setLevel(logging.INFO if verbose else logging.WARNING)
    logger.propagate = False  # ルートロガーに伝播させない

    logger.handlers.clear()  # 既存のハンドラをクリア

    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    logger.addHandler(handler)
    return logger

def should_count(path: Path, mode: str) -> bool:
    """
    Day8追加: modeに応じて「このpathを件数に含めるか」を判断する。

    - mode="file": 通常ファイルのみ（Path.is_file()）
      * ソケット等は含めない
    - mode="all": ディレクトリ以外は含める（not Path.is_dir()）
      * ソケット等も含む
    """
    if mode == "file":
        return path.is_file()
    
    if mode == "all":
        return not path.is_dir()

    return False  # 保険（通常ここには来ない）

@dataclass(frozen=True)
class Entry:
    """
    Day9: 走査結果の「パス + サイズ」を持つDTO。
    - frozen=True にして不変（扱いが楽）
    """
    path: Path
    size: int

@dataclass(frozen=True)
class Stats:
    """
    Day12: 集計結果（計算の出力）をひとまとめにするDTO。
    - entries は（必要なら）top表示/JSON用に使う
    """
    count: int
    total_bytes: int
    top: list[Entry]

def format_path(path: Path, root: Path, relative: bool) -> str:
    """
    Day13: 出力用のパス文字列を作る。
    - relative=True のとき、可能なら root からの相対パスにする
    - 失敗したら（別ドライブ等）絶対パスのまま
    """
    if not relative:
        return str(path)
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)    

def iter_entries(root: Path, mode: str, min_size: int, logger: logging.Logger) -> Iterator[Entry]:
    """
    root以下を走査してEntryを順次yieldする（I/O側）。
    - listを作らずstreamingで流す（Day15）
    - statが取れないものはスキップ（loggerでstderrに理由を出す）
    - min_size 未満は除外
    """
    for path in root.rglob("*"):
        if not should_count(path, mode):
            continue

        try:
            size = path.stat().st_size
        except OSError as exc:
            # Day14: stderrログは logging に統一
            logger.info("[skip] %s: %s", path, exc)
            continue

        # Day11: 最小サイズフィルタ
        if size < min_size:
            continue

        yield Entry(path=path, size=size)

def find_top_n(entries: list[Entry], n: int) -> list[Entry]:
    """
    Day9: サイズが大きい上位N件を返す。
    - heapq.nlargest は「全部ソート」より軽いことが多い（Nが小さい想定）
    - 同サイズが多いと順序が不安定になりやすいので、最後に整列し直す
    """
    if n <= 0:
        return []

    top = heapq.nlargest(n, entries, key=lambda e: e.size)
    # サイズ順に安定化ソート（サイズが同じならパス順）
    top.sort(key=lambda e: (-e.size, str(e.path)))
    return top

def compute_stats(entries: Iterable[Entry], top_n: int) -> Stats:
    """
    Day12: 計算部分（なるべく純粋関数っぽく）
    入力：entries（既に走査済みのデータ）
    出力：Stats（count/total/top）
    """
    count = 0
    total_bytes = 0
    heap: list[Tuple[int, str, Entry]] = []

    for e in entries:
        count += 1
        total_bytes += e.size

        # Day15: 上位N件をストリーミングで保持する
        if top_n <= 0:
            continue

        item = (e.size, str(e.path), e)  # タプルで保持（安定化ソート用にパスも入れる）
        if len(heap) < top_n:
            heapq.heappush(heap, item)
        else:
            if item > heap[0]:
                heapq.heapreplace(heap, item)
    
    top_entries = [t[2] for t in sorted(heap, key=lambda t: (-t[0], t[1]))]

    return Stats(
        count=count,
        total_bytes=total_bytes,
        top=top_entries,
    )

def build_json_payload(root: Path, mode: str, min_size: int, top_n: int, stats: Stats, relative: bool) -> dict[str, Any]:
    """
    Day12: JSON用の辞書を組み立てる（表示形式の責務）。
    """
    return {
        "directory": str(root),
        "mode": mode,
        "min_size": min_size,
        "count": stats.count,
        "total_bytes": stats.total_bytes,
        "top_n": top_n,
        "top": [
            {"path": format_path(e.path, root, relative), "size_bytes": e.size}
            for e in stats.top
        ]
    }

def post_payload(url: str, payload: dict[str, Any], timeout: float, logger: logging.Logger) -> bool:
    """
    Day17: payloadをJSONとしてPOSTする（I/O）。
    - 成功したら True、失敗したら False
    - stdoutは汚さず、ログはstderrへ
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

def main(argv: list[str] | None = None) -> int:
    """
    CLIのエントリーポイント。
    - 引数を読む
    - 入力を検証する
    - 走査する
    - 結果を表示して終了コードを返す
    """
    args = parse_args(argv)
    logger = setup_logger(args.verbose)

    # Day18: config（JSON）を読み込んで、未指定のCLI引数を補完する
    provided = parse_provided_options(argv)
    if args.config is not None:
        cfg = load_config(args.config, logger)
        apply_config(args, cfg, provided, logger)
    
    # directory の最終確定（未指定ならカレント）
    directory = args.directory if args.directory is not None else Path(".")
    root: Path = directory.expanduser().resolve()

    # 入力検証：存在するか？ディレクトリか？
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

    # Day18: --out が指定されていたら payload をファイルに保存（stdoutは汚さない）
    if args.out is not None and payload is not None:
        try:
            out_path = args.out.expanduser().resolve()
            out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            logger.info("payload written to %s", out_path)
        except Exception as exc:
            logger.error("failed to write payload to %s: %s", out_path, exc)
            return 1  # 書き込み失敗

    # --post: stderr(log)で結果を報告（stdoutを汚さない）
    if args.post and payload is not None:
        ok = post_payload(args.post, payload, timeout=args.timeout, logger=logger)
        if not ok:
            return 1  # POST失敗
        
    # --json のときは人間向け表示はしない
    if args.json:
        return 0  # 正常終了
    
    # 表示（humanフラグがあるなら変換）
    display_total = human_size(stats.total_bytes) if args.human else str(stats.total_bytes)
    # それ以外は従来どおり、人間向け表示
    print(f"directory: {root}")
    print(f"mode:      {args.mode}")
    print(f"min-size:  {args.min_size}")
    print(f"relative:  {args.relative}")
    print(f"count:     {stats.count}")
    print(f"total:     {display_total}")

    # Day9: top N を表示（要求がある時だけ）
    if args.top > 0:
        print(f"top:       {args.top}")
        for e in stats.top:
            size_str = human_size(e.size) if args.human else str(e.size)
            print(f"{size_str}\t{format_path(e.path, root, args.relative)}")

    return 0  # 正常終了

# 直接実行されたときだけ main() を呼ぶ。
# raise SystemExit(main()) にすると、戻り値が終了コードになる。
if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))