"""
Day13: ミニCLIツール（ディレクトリ走査）— 出力パスを相対/絶対で切替（Day12 FIX版の続き）

このプログラムは、指定ディレクトリ以下を再帰的に走査して集計するCLIツール。
Day13のポイント：
- “計算する部分”（entries -> 集計結果）をなるべく純粋関数っぽくする
    * なるべく引数だけで結果が決まる形にして、printやファイル走査と分離する
- I/O（走査・表示・JSON出力）は外側に寄せる
- 出力するパスを --relative で「rootからの相対パス」に切り替えられるようにする

使い方：
    python main.py [directory] [--mode file|all] [--min-size N] [--top N] [--human] [--json] [--verbose] [--relative]
"""

from __future__ import annotations

import argparse
import heapq
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
    """
    parser = argparse.ArgumentParser(
        description="指定したディレクトリ以下を走査し、ファイル数と合計サイズを集計します。"
    )

    # 位置引数: directory
    # - nargs="?" により「0個 or 1個」指定できる（省略可能）
    # - default=Path(".") は省略時の値（カレント）
    # - type=Path により、文字列ではなく Path として受け取れる
    parser.add_argument(
        "directory",
        nargs="?",
        default=Path("."),
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
        default=0,
        help="サイズが大きい順に上位N件を表示する（0のときは表示しない）"
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

    return parser.parse_args(argv)

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

def iter_entries(root: Path, mode: str, min_size: int,verbose: bool) -> list[Entry]:
    """
    root以下を走査して、count対象のEntry一覧を返す。
    - statが取れないものはスキップ（verboseなら理由をstderrに出す）
    """
    entries: list[Entry] = []

    for path in root.rglob("*"):
        if not should_count(path, mode):
            continue

        try:
            size = path.stat().st_size
        except OSError as exc:
            if verbose:
                print(f"[skip] {path}: {exc}", file=sys.stderr)
            continue

        # Day11: 最小サイズフィルタ
        if size < min_size:
            continue

        entries.append(Entry(path=path, size=size))

    return entries

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

def compute_stats(entries: list[Entry], top_n: int) -> Stats:
    """
    Day12: 計算部分（なるべく純粋関数っぽく）
    入力：entries（既に走査済みのデータ）
    出力：Stats（count/total/top）
    """
    count = len(entries)
    total_bytes = sum(e.size for e in entries)
    top_entries = find_top_n(entries, top_n)

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

def main(argv: list[str] | None = None) -> int:
    """
    CLIのエントリーポイント。
    - 引数を読む
    - 入力を検証する
    - 走査する
    - 結果を表示して終了コードを返す
    """
    args = parse_args(argv)

    # ~ を展開し、絶対パスへ（ログや比較でブレにくくなる）
    root: Path = args.directory.expanduser().resolve()

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
    
    # I/O（走査）：rootからentriesを作る
    entries = iter_entries(
        root, 
        mode=args.mode, 
        min_size=args.min_size,
        verbose=args.verbose,
    )

    # 計算（Day12）：entriesから集計結果を作る
    stats = compute_stats(entries, top_n=args.top)

    # Day10: --json のときは機械向けにJSONを出す
    if args.json:
        payload = build_json_payload(
            root=root,
            mode=args.mode,
            min_size=args.min_size,
            top_n=args.top,
            stats=stats,
            relative=args.relative,
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
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
    raise SystemExit(main())