"""
Day7: ミニCLIツール（ディレクトリ走査）

このプログラムは、指定したディレクトリ以下を再帰的に走査し、
- ファイル数
- 合計ファイルサイズ

を集計して表示するシンプルなCLIツール。

【使い方】
    python main.py [directory]
    python main.py [directory] --human
    python main.py [directory] --verbose

- directory を省略した場合は、カレントディレクトリを対象とする
- --human を付けると、サイズを KB / MB など人間向け表記で表示する
- --verbose を付けると、走査中の詳細ログやスキップ理由を stderr に出力する

【内部構成】
- parse_args(): CLI引数を解析する
- scan_directory(): ディレクトリを再帰的に走査し、件数とサイズを集計する
- main(): 入力検証・処理呼び出し・結果表示をまとめるエントリーポイント

このコードは以下の学習目的を兼ねている：
- argparse による CLI 引数処理
- pathlib.Path を使ったファイル走査
- try/except による安全なエラーハンドリング
- main() から終了コードを返す CLI の基本構造
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
        if value < 1024 or unit == units[-1]:
            return f"{int(value)}B" if unit == "B" else f"{value:.1f}{unit}"
        value /= 1024
    
    # 通常ここには到達しない（保険）
    return f"{int(value)}{units[-1]}"

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """
    CLI引数を定義して、解析結果（args）を返す。
    - directory: 省略可能な位置引数（デフォルトはカレント）
    - --human: サイズを人間向けに表示するスイッチ
    - --verbose: 走査中の詳細ログを出すスイッチ
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

def scan_directory(root: Path, verbose: bool) -> tuple[int, int]:
    """
    ディレクトリ以下を再帰的に走査して、
    (ファイル数, 合計サイズ) を返す。

    - 途中で stat が取れないファイルがあっても落ちない（try/except）
    - verbose=True のときだけ、スキップ理由などのログを出す
    """
    file_count = 0
    total_size = 0

    # rglob("*") で下位ディレクトリも含めて全要素を走査
    for path in root.rglob("*"):
        # 通常ファイルのみを対象にする（ソケット等は除外）
        if not path.is_file():
            continue

        try:
            size = path.stat().st_size # OSに問い合わせるので例外が起き得る
        except OSError as exc:
            # 権限不足など。verboseなら理由も出す
            if verbose:
                print(f"[skip] {path}: {exc}", file=sys.stderr)
            continue

        file_count += 1
        total_size += size

        # verboseなら進捗を軽く出す（出しすぎるとうるさいので控えめ）
        if verbose and file_count % 200 == 0:
            print(f"[info] scanned files: {file_count}", file=sys.stderr)

    return file_count, total_size

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
    file_count, total_size = scan_directory(root, verbose=args.verbose)

    # 表示（humanフラグがあるなら変換）
    display_size = human_size(total_size) if args.human else str(total_size)

    print(f"directory: {root}")
    print(f"files:     {file_count}")
    print(f"total:     {display_size}")

    return 0  # 正常終了

# 直接実行されたときだけ main() を呼ぶ。
# raise SystemExit(main()) にすると、戻り値が終了コードになる。
if __name__ == "__main__":
    raise SystemExit(main())