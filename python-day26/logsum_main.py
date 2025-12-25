"""
Day23: エントリーポイント（薄いラッパー） - logsum

狙い：
- 実装本体(logsum.py)とCLI実行の入口を分離する
- import しただけで集計が走らない（テスト/再利用がしやすい）
"""

from __future__ import annotations

import sys


if __name__ == "__main__":
    from logsum import main

    raise SystemExit(main(sys.argv[1:]))
