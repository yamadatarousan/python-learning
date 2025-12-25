"""
Day21: エントリーポイント（薄いラッパー）

狙い：
- import される「実装本体」と、CLI実行の「入口」を分離する
- テストは `dirscan.py` を直接 import して行う（副作用の少ない形）
"""

from __future__ import annotations

import sys

if __name__ == "__main__":
    from dirscan import main

    raise SystemExit(main(sys.argv[1:]))
