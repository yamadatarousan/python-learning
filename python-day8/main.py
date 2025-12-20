"""
Day8: ミニCLIツール（ディレクトリ走査）— Day7の続き（仕様を明示）

このプログラムは、指定したディレクトリ以下を再帰的に走査し、
- 件数（count）
- 合計サイズ（sum of st_size）
を集計して表示するシンプルなCLIツール。

Day7からの改善点（Day8のテーマ）：
- 「何を数えるか？」をコード中の if ではなく、CLI引数として明示する
  --mode file : 通常ファイルのみ（デフォルト）
  --mode all  : ディレクトリ以外すべて（ソケット等も含む）

使い方:
    python main.py [directory]
    python main.py [directory] --human
    python main.py [directory] --verbose
    python main.py [directory] --mode file
    python main.py [directory] --mode all
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# このプログラムで学習してほしいこと（Day7の狙い）
# - argparseで「位置引数 + フラグ」を扱う
# - Pathでディレクトリを走査する
# - try/exceptで「落ちないCLI」を作る
# - main()が終了コード(int)を返す作法を体に入れる

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

def scan_directory(root: Path, verbose: bool, mode: str) -> tuple[int, int]:
    """
    ディレクトリ以下を再帰的に走査して、
    (件数, 合計サイズ) を返す。

    - 「何を数えるか」は should_count() に委譲（Day8のポイント）
    - 途中で stat が取れないパスがあっても落ちない（try/except）
    - verbose=True のときだけ、スキップ理由などのログを出す
    """
    count = 0
    total_size = 0

    # rglob("*") で下位ディレクトリも含めて全要素を走査
    for path in root.rglob("*"):
        # 通常ファイルのみを対象にする（ソケット等は除外）
        if not should_count(path, mode):
            continue

        try:
            size = path.stat().st_size # OSに問い合わせるので例外が起き得る
        except OSError as exc:
            # 権限不足など。verboseなら理由も出す
            if verbose:
                print(f"[skip] {path}: {exc}", file=sys.stderr)
            continue

        count += 1
        total_size += size

        # verboseなら進捗を軽く出す（出しすぎるとうるさいので控えめ）
        if verbose and count % 200 == 0:
            print(f"[info] counted entries: {count}", file=sys.stderr)

    return count, total_size

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
    
    # 走査
    count, total_size = scan_directory(root, verbose=args.verbose, mode=args.mode)

    # 表示（humanフラグがあるなら変換）
    display_size = human_size(total_size) if args.human else str(total_size)

    print(f"directory: {root}")
    print(f"mode:      {args.mode}")
    print(f"count:     {count}")
    print(f"total:     {display_size}")

    return 0  # 正常終了

# 直接実行されたときだけ main() を呼ぶ。
# raise SystemExit(main()) にすると、戻り値が終了コードになる。
if __name__ == "__main__":
    raise SystemExit(main())