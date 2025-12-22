# python-learning

LLMに毎回渡す固定プロンプト

【前提】
- 私は「30日間のPython学習カリキュラム」を進行中
- 次は Day 14
- 目的：Pythonの基礎理解と設計感覚の獲得

以下は君が考えた当初の学習計画
※注意：君のブレで当初の学習計画と実際の学習プロセスがズレている可能性がある

Python学習計画 30日（実務寄り・DTO/CLI中心）
フェーズ1：書ける感覚を戻す（Day 1–7）
目的：Pythonで手が動く安心感
Day 1：環境確認・print・if・for
Day 2：list / dict / set 基本操作
Day 3：関数定義・引数・戻り値
Day 4：Pathlibでファイル操作
Day 5：例外処理（try/except）
Day 6：簡単なCLI（argparse）
Day 7：ミニ課題
ディレクトリ内のファイル数・合計サイズを出す

フェーズ2：Pythonらしさに慣れる（Day 8–15）
目的：コードを短く・安全に
* Day 8：list/dict comprehension
* Day 9：dataclass基礎
* Day10：dataclass + sort / key
* Day11：typing（list[str], Iterable）
* Day12：標準ライブラリ探索（heapq, itertools）
* Day13：ジェネレータ（yield）
* Day14：ログ・stderr出力
* Day15：ミニ課題
    * 今回作った「大きいファイルTOP N」を書き直す
  
フェーズ3：実務感覚（Day 16–25）
目的：現場で「使える」Python
* Day16：仮想環境（venv）
* Day17：requests / httpx でAPI叩く
* Day18：JSON読み書き
* Day19：設定ファイル（env / json）
* Day20：テストの雰囲気（pytest触るだけ）
* Day21：CLI構成整理
* Day22–24：小ツール作成
    * ログ集計 / API結果整形 / CSV処理
* Day25：コード整理・コメント追加

フェーズ4：仕上げ（Day 26–30）
Day26–28：1ツール完成（雑でOK）
Day29：READMEを書く
Day30：振り返り
「Pythonで出来ること」棚卸し

day13までの成果物は以下

"""
Day13: ミニCLIツール（ディレクトリ走査）— 出力パスを相対/絶対で切替（Day12 FIX版の続き）

このプログラムは、指定ディレクトリ以下を再帰的に走査して集計するCLIツール。

Day13のポイント：
- “計算する部分”（entries -> 集計結果）をなるべく純粋関数っぽくする
  * なるべく引数だけで結果が決まる形にして、printやファイル走査と分離する
- I/O（走査・表示・JSON出力）は外側に寄せる
- 出力するパスを --relative で「rootからの相対パス」に切り替えられるようにする

使い方:
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


def human_size(size: int) -> str:
    """バイト数を人間向けの単位に変換する（表示専用）。"""
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    value = float(size)

    for unit in units:
        is_small_enough = value < 1024
        is_last_unit = unit == units[-1]

        if is_small_enough or is_last_unit:
            if unit == "B":
                return f"{int(value)}B"
            return f"{value:.1f}{unit}"

        value /= 1024

    return f"{int(value)}{units[-1]}"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """
    CLI引数を定義して解析する。
    - directory: 省略可能な位置引数（デフォルトはカレント）
    - --mode: 何をcount対象にするか（file/all）
    - --min-size: 小さいものを除外（バイト）
    - --top: サイズが大きい上位N件を表示する
    - --human / --verbose: 表示のスイッチ
    - --json: JSONで出力する（stdoutをJSON専用にする）
    - --relative: 出力パスをrootからの相対パスにする（Day13）
    """
    parser = argparse.ArgumentParser(
        description="Scan a directory recursively, count entries, sum sizes, and optionally show top N by size."
    )

    parser.add_argument(
        "directory",
        nargs="?",
        default=Path("."),
        type=Path,  # 引数ありでも常に Path になる（以前踏んだ地雷ポイント）
        help="Directory to scan (default: current directory).",
    )

    parser.add_argument(
        "--mode",
        choices=["file", "all"],
        default="file",
        help="What to count: file=regular files only (default), all=all non-directory entries (e.g., sockets)",
    )

    parser.add_argument(
        "--top",
        type=int,
        default=0,
        help="Show top N largest entries by st_size (0 = do not show).",
    )

    parser.add_argument(
        "--min-size",
        type=int,
        default=0,
        help="Ignore entries smaller than N bytes (default: 0).",
    )

    parser.add_argument(
        "--human",
        action="store_true",
        help="Show sizes in a human-readable format.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print verbose messages (e.g., skipped paths).",
    )

    parser.add_argument(
        "--json",
        action="store_true",
        help="Output result as JSON.",
    )

    parser.add_argument(
        "--relative",
        action="store_true",
        help="Output paths as relative to the root directory when possible.",
    )

    return parser.parse_args(argv)


def should_count(path: Path, mode: str) -> bool:
    """modeに応じて「このpathをcount対象に含めるか」を判断する。"""
    if mode == "file":
        return path.is_file()
    if mode == "all":
        return not path.is_dir()
    return False


@dataclass(frozen=True)
class Entry:
    """走査結果の「パス + サイズ」を持つDTO。"""
    path: Path
    size: int


@dataclass(frozen=True)
class Stats:
    """
    Day12: 集計結果（計算の出力）をひとまとめにするDTO。
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


def iter_entries(root: Path, mode: str, min_size: int, verbose: bool) -> list[Entry]:
    """
    root以下を走査してEntry一覧を返す（I/O側）。
    - statが取れないものはスキップ（verboseなら理由をstderrに出す）
    - min_size 未満は除外
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

        if size < min_size:
            continue

        entries.append(Entry(path=path, size=size))

    return entries


def find_top_n(entries: list[Entry], n: int) -> list[Entry]:
    """サイズが大きい上位N件を返す。"""
    if n <= 0:
        return []

    top = heapq.nlargest(n, entries, key=lambda e: e.size)
    top.sort(key=lambda e: (-e.size, str(e.path)))  # 表示安定化
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
    return Stats(count=count, total_bytes=total_bytes, top=top_entries)


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
        "top": [{"path": format_path(e.path, root, relative), "size_bytes": e.size} for e in stats.top],
    }


def main(argv: list[str] | None = None) -> int:
    """
    CLIのエントリーポイント。
    - 引数解析
    - 入力検証
    - I/O（走査）
    - 計算（集計）
    - 出力（テキスト or JSON）
    - 終了コード（0=成功, 2=入力エラー）
    """
    args = parse_args(argv)
    root: Path = args.directory.expanduser().resolve()

    if not root.exists():
        print(f"Directory does not exist: {root}", file=sys.stderr)
        return 2
    if not root.is_dir():
        print(f"Not a directory: {root}", file=sys.stderr)
        return 2
    if args.top < 0:
        print("--top must be >= 0", file=sys.stderr)
        return 2
    if args.min_size < 0:
        print("--min-size must be >= 0", file=sys.stderr)
        return 2

    # I/O（走査）：rootからentriesを作る
    entries = iter_entries(root, mode=args.mode, min_size=args.min_size, verbose=args.verbose)

    # 計算（Day12）：entriesから集計結果を作る
    stats = compute_stats(entries, top_n=args.top)

    # JSON出力（stdoutはJSON専用にする）
    if args.json:
        payload = build_json_payload(root, args.mode, args.min_size, args.top, stats, args.relative)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    # 人間向け表示
    display_total = human_size(stats.total_bytes) if args.human else str(stats.total_bytes)
    print(f"directory: {root}")
    print(f"mode:      {args.mode}")
    print(f"min-size:  {args.min_size}")
    print(f"relative:  {args.relative}")
    print(f"count:     {stats.count}")
    print(f"total:     {display_total}")

    if args.top > 0:
        print(f"top:       {args.top}")
        for e in stats.top:
            size_str = human_size(e.size) if args.human else str(e.size)
            print(f"{size_str}\t{format_path(e.path, root, args.relative)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
