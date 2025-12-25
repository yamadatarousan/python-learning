"""
Day22: エントリーポイント（ログ集計ツール）

狙い：
- import される「実装本体」と、CLI実行の「入口」を分離する
- テスト/再利用は logscan.py を直接 import して行う
"""

from __future__ import annotations

import sys

if __name__ == "__main__":
    from logscan import main

    raise SystemExit(main(sys.argv[1:]))
