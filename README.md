# dirscan（Day29）

ディレクトリ配下を再帰的に走査して、**合計サイズ**と**サイズ上位N件**を表示する小さなCLIツールです。  
必要なら **JSON出力** / **ファイル保存** / **HTTP POST** もできます。

- 実装本体: `dirscan.py`
- 共通I/O部品: `toolkit.py`
- エントリーポイント: `main.py`
- テスト: `test_dirscan.py`

---

## できること

- 指定ディレクトリ以下を再帰走査（`Path.rglob("*")`）
- 件数（count）と合計バイト（total_bytes）を集計
- サイズが大きい順に上位N件を表示（`heapq`で上位N件だけ保持）
- `--json` で JSON を stdout に出す（人間向け表示はしない）
- `--out` で JSON をファイルに保存（stdoutは汚さない）
- `--post` で JSON をHTTP POST（stdoutは汚さない）
- `--env-file` で `.env` を読み、環境変数として扱う

---

## 前提

- Python 3.8+（3.8でも動くように調整済み）
- HTTP POST を使う場合は `httpx` が必要

---

## インストール（最小）

このリポジトリ内で実行する想定です。

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
```

HTTP POST（`--post`）を使うなら:

```bash
python -m pip install httpx
```

---

## 実行方法

### 1) ふつうに人間向け表示

```bash
python main.py .
```

例（イメージ）:

```
directory: /abs/path/to/project
mode:      file
min-size:  0
relative:  False
count:     123
total:     456789
top:       10
1200	/abs/path/to/project/a.bin
...
```

### 2) サイズを人間向け表示にする

```bash
python main.py . --human
```

### 3) 上位N件を変える

```bash
python main.py . --top 5
```

`--top 0` にすると上位表示を無効化します（集計はする）。

### 4) 相対パス表示にする

```bash
python main.py . --relative
```

### 5) JSONをstdoutに出す

```bash
python main.py . --json
```

`--json` を付けると人間向け表示はしません（stdoutはJSON専用）。

### 6) JSONをファイルに保存する（stdoutは汚さない）

```bash
python main.py . --out report.json
```

### 7) JSONをHTTP POSTする（stdoutは汚さない）

```bash
python main.py . --post https://httpbin.org/post --timeout 10
```

---

## オプション一覧

`dirscan.py` の `parse_args()` で定義している項目です。

- `directory`（位置引数・省略可）  
  走査対象ディレクトリ。省略時は `.`（カレント）

- `--mode {file,all}`  
  - `file`: 通常ファイルのみ  
  - `all`: ディレクトリ以外すべて（ファイル/シンボリックリンク等を含む）

- `--top N`  
  サイズ上位N件を表示（デフォルト 10）。`0` で無効。

- `--min-size BYTES`  
  指定サイズ（バイト）未満は集計対象から除外

- `--relative`  
  出力するパスを `root` からの相対パスにする（できる範囲で）

- `--human`  
  サイズを `1.5KB` のように表示する

- `--verbose`  
  走査中の詳細ログを stderr に出す（stdoutは汚さない）

- `--json`  
  集計結果を JSON で stdout に出す（人間向け表示はしない）

- `--post URL`  
  JSONをPOSTするURL。空文字ならPOSTしない

- `--timeout SECONDS`  
  POST時のタイムアウト秒（デフォルト 10.0）

- `--config PATH`  
  JSON config ファイル。CLIが最優先で、未指定の項目だけを補完する

- `--out PATH`  
  JSON payload をファイルに保存する

- `--env-file PATH`  
  `.env` を読み込んで「環境変数として」扱う

---

## 設定の優先順位（超重要）

最終的に使う値は、この順で強いです。

**CLI > env > config**

- **CLI**: ユーザーがコマンドラインで指定したもの（最強）
- **env**: OS環境変数 + `--env-file` で読み込んだ `.env`
- **config**: JSON設定ファイル（最弱）

`resolve_effective_args()` の中で、この順に適用しています。

### `.env` が必ず勝つルール

`toolkit.get_env()` は、環境変数取得の優先順位を

**env_file（.env） > OS環境変数**

にしています。  
「`--env-file` を指定したら `.env` の値を必ず採用したい」要件のためです。

---

## config（JSON）例

`config.json` の例:

```json
{
  "directory": "/tmp",
  "mode": "file",
  "top": 10,
  "min_size": 1024,
  "relative": true,
  "human": true,
  "json": false,
  "verbose": false,
  "timeout": 10.0,
  "post": "",
  "out": ""
}
```

ポイント:

- config は「未指定の項目を埋める」目的で使います  
  例: `--top 3` をCLIで指定したら、configの `top` は無視されます

---

## `.env` 例

`.env` の例:

```env
# 走査対象
DIRSCAN_DIRECTORY=/tmp

# 上位件数
DIRSCAN_TOP=5

# JSONをstdoutに出す
DIRSCAN_JSON=true

# JSONをファイル保存
DIRSCAN_OUT=report.env.json
```

`.env` のパースは `toolkit.load_env_file()` がやっています（標準ライブラリのみ）。

---

## JSONの形（payload）

`--json` / `--out` / `--post` のどれかを使うと、内部で payload を作ります。  
キーは `dirscan.build_json_payload()` で決めています（dirscan固有仕様）。

例（イメージ）:

```json
{
  "directory": "/abs/path/to/scan",
  "mode": "file",
  "min_size": 0,
  "count": 123,
  "total_bytes": 456789,
  "top_n": 10,
  "top": [
    {"path": "a/b.txt", "size_bytes": 2048}
  ]
}
```

---

## stdout / stderr の使い分け

- **stdout**: 結果の出力（人間向け or JSON）
- **stderr**: 進捗ログや警告、失敗（logger）

この使い分けをすると:

- `--json` で stdout を JSON だけにできる
- パイプ処理（`| jq` など）を邪魔しない

---

## テストの実行

```bash
python -m pip install -U pytest
pytest -q
```

テストは `test_dirscan.py` にまとまっています。  
ポイントは次の2つです。

- `toolkit` の共通関数は `toolkit` を直接テストする
- `dirscan` 側は「dirscan固有の仕様」に寄せてテストする

---

## コード構成メモ（設計の意図）

### `main.py` を薄くする理由

- `dirscan.py` を import してテストしやすくする
- 実装本体と「CLI起動の入口」を分ける  
  （import時に副作用を出しにくくする）

### `toolkit.py` に寄せるもの / 寄せないもの

- 寄せる（どのツールでも同じ意味で使える）  
  例: logger構成、`.env`読み取り、bool変換、JSON保存、HTTP POST、サイズ表示など

- 寄せない（ツール固有の仕様）  
  例: 引数名、環境変数名の命名、configキー名、payload構造、優先順位の細かい例外

---

## ありがちな使い方（例）

### 「調べたいディレクトリを `.env` に固定して、毎回コマンドを短くする」

```bash
python main.py --env-file .env --json | jq .
```

### 「レポートをファイルに保存する」

```bash
python main.py /var/log --top 50 --out report.json
```

---

## ライセンス

学習用サンプル（30日カリキュラム）として作成。
