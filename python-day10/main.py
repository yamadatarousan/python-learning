"""
Day10: ミニCLIツール（ディレクトリ走査）— Day9の続き（JSON出力追加）

このプログラムは、指定ディレクトリ以下を再帰的に走査して、
- count: 対象エントリ数
- total: 対象エントリの st_size 合計
- top N: サイズが大きい順の上位N件（任意）
を出力するCLIツール。

Day10の追加点：
- --json を付けると、集計結果をJSONで出力する（機械処理向け）
  * verboseログは従来どおりstderr（JSONを壊さないため）

使い方:
    python main.py [directory]
    python main.py [directory] --top 10
    python main.py [directory] --top 10 --json
    python main.py [directory] --mode all --json
"""

from __future__ import annotations

import argparse
import heapq
import json
import sys
from dataclasses import dataclass
from pathlib import Path

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

def iter_entries(root: Path, mode: str, verbose: bool) -> list[Entry]:
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

def scan_directory(root: Path, verbose: bool, mode: str, top_n: int) -> tuple[int, int, list[Entry] | None]:
    """
    Day7/8のscanをDay9で拡張：
    - Entry一覧を作る
    - count/totalを計算する
    - 必要なら top N も計算して返す
    """
    entries = iter_entries(root, mode=mode, verbose=verbose)

    count = len(entries)
    total_size = sum(entry.size for entry in entries)

    top_entries = find_top_n(entries, top_n)
    return count, total_size, top_entries

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
    
    # 走査
    count, total_size, top_entries = scan_directory(
        root, verbose=args.verbose, mode=args.mode, top_n=args.top
    )

    # 表示（humanフラグがあるなら変換）
    display_total = human_size(total_size) if args.human else str(total_size)

    # Day10: --json のときは機械向けにJSONを出す
    if args.json:
        payload = {
            "directory": str(root),
            "mode": args.mode,
            "count": count,
            "total_bytes": total_size,
            "top_n": args.top,
            "top": [
                {"path": str(e.path), "size_bytes": e.size}
                for e in top_entries
            ]
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0  # 正常終了

    # それ以外は従来どおり、人間向け表示
    print(f"directory: {root}")
    print(f"mode:      {args.mode}")
    print(f"count:     {count}")
    print(f"total:     {display_total}")

    # Day9: top N を表示（要求がある時だけ）
    if args.top > 0:
        print(f"top:       {args.top}")

        for e in top_entries:
            size_str = human_size(e.size) if args.human else str(e.size)

            # root配下なら相対パスで見やすくする（失敗したら絶対パスのまま）
            try:
                rel_path = e.path.relative_to(root)
            except ValueError:
                rel_path = e.path

            print(f"{size_str}\t{rel_path}")

    return 0  # 正常終了

# 直接実行されたときだけ main() を呼ぶ。
# raise SystemExit(main()) にすると、戻り値が終了コードになる。
if __name__ == "__main__":
    raise SystemExit(main())