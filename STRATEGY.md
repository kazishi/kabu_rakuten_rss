# Case 1 実装作戦書 — セクター/テーマ資金フロー統合

Codexとの議論用。現状把握 → 設計判断事項 → 実装計画の順。

---

## 現在のシステム構造

### バックエンド `app.py` 932行

```
ExcelCollector スレッド（5秒ポーリング）
  └─ COM経由でExcel読み取り → 最大100銘柄
  └─ parse_stock_values()  : FIELD_MAP でPython dictへ
  └─ compute_derived()     : change_pct / from_open / vwap_gap / volatility etc.
  └─ attach_analytics()    : rvol / turnover_delta_15m / turnover_delta_5m / change_15m / change_5m
  └─ state.stocks に格納（threading.Lock）

Flask API
  GET /api/snapshot  → state.stocks 全フィールド + monitor.db（アラート/ブレイク等）
  GET /api/history/<code> → price_history テーブル
```

### 既存の資金フロースコア定義（現在は app.js 側で計算）

```
flow_day  = turnover × (change_pct×0.5 + from_open×0.3 + vwap_gap×0.2)
flow_15m  = turnover_delta_15m × change_15m
flow_5m   = turnover_delta_5m  × change_5m
```

### 既存DB `monitor.db`

| テーブル | 用途 |
|---|---|
| `price_history` | (sampled_at, code, price, change_pct, vwap_gap, turnover, market_session)。10秒間隔、14日保持 |
| `alert_rules` | ユーザー設定アラート閾値 |
| `alert_events` | アラート発火履歴 |
| `breakout_events` | 高値/安値ブレイク検知 |
| `favorites` | お気に入り銘柄 |

### フロントエンド

- `templates/index.html` 247行 + `static/app.js` + `static/style.css`
- タブ2枚: **モニター**（銘柄テーブル）/ **分析**（5分/15分/日中の個別銘柄資金フローランキング）

---

## 今セッションで完成した資産

### `classification/classification.db` (SQLite)

```sql
stocks(code PK, name, market, big_cat, mid_cat, small_cat)  -- 3707銘柄、日経NEEDS3層
themes(theme_id PK, name, category, source, note)           -- 19テーマ
stock_themes(code, theme_id, source)                        -- M:N 542リンク
-- indexes: idx_st_theme(theme_id), idx_stocks_mid, idx_stocks_small
```

**テーマ一覧（19件）**

| category | theme_id | 表示名 | 付与方法 |
|---|---|---|---|
| AI・半導体 | semiconductor | 半導体 | 業種自動 |
| AI・半導体 | semi_equipment | 半導体製造装置 | 業種自動 |
| AI・半導体 | mlcc | MLCC・電子部品 | 業種自動 |
| AI・半導体 | memory | メモリー半導体 | Web手動 |
| AI・半導体 | datacenter | データセンター | Web手動 |
| AI・半導体 | physical_ai | フィジカルAI・ロボット | Web手動 |
| AI・半導体 | gemini | ジェミニ関連 | Web手動 |
| 部品・素材 | densen | 電線・ケーブル | 業種自動 |
| 部品・素材 | metals | 金・銀・銅（非鉄メタル）| 業種自動 |
| 部品・素材 | rare_earth | レアアース | Web手動 |
| 防衛・国策 | defense | 防衛 | 業種自動（銃器/総合重機）|
| 防衛・国策 | cyber_security | サイバーセキュリティ | Web手動 |
| 防衛・国策 | space | 宇宙 | Web手動 |
| 防衛・国策 | shipbuilding | 造船 | 業種自動（造船中分類）|
| 防衛・国策 | aviation | 空運・航空 | 業種自動 |
| 防衛・国策 | infrastructure | 国土強靭化・建設 | 業種自動 |
| エネルギー | perovskite | ペロブスカイト太陽電池 | Web手動 |
| エネルギー | fusion | 核融合発電 | Web手動 |
| 金融 | bank_rate | 金利上昇メリット（銀行）| 業種自動 |

**重要な設計前提**
- `big_cat`（大分類15種）は1銘柄1所属 → 保存則が成立
- `stock_themes` は多重ラベル → 三菱重工(7011) = 防衛/宇宙/核融合/レアアース の4タグ同時保持
- 既知の穴: 三菱重工・川重は日経で「総合重機」のため `shipbuilding` タグが自動付与されない（手動キュレーション事項）

---

## Case 1 の実装計画

### 追加するファイル・変更箇所

| ファイル | 種別 | 内容 |
|---|---|---|
| `classification/lookup.py` | 新規 | DBロードと集計ロジックをappから分離 |
| `app.py` | 変更 | `load_classification()` 呼び出し + `/api/sector_flow` エンドポイント追加 |
| `templates/index.html` | 変更 | SECTORタブと3ビューのHTML骨格 |
| `static/app.js` | 変更 | SECTORタブのレンダリングロジック |
| `static/style.css` | 変更 | ヒートタイル・棒グラフ用スタイル |

---

### `classification/lookup.py` の設計

```python
# 起動時に1回呼ぶ
def load_classif(db_path) -> dict[str, dict]:
    """
    returns {code: {"big_cat": str, "mid_cat": str, "small_cat": str, "themes": [theme_id, ...]}}
    """

# /api/sector_flow から呼ぶ
def compute_sector_flow(stocks: list[dict], classif: dict) -> dict:
    """
    returns {
      "themes": {
        theme_id: {
          "name": str,
          "category": str,
          "stock_count": int,       # 監視銘柄中の該当数
          "turnover_sum": float,    # 万円
          "flow_day_sum": float,    # flow_day の合計
          "delta_15m_sum": float,   # turnover_delta_15m の合計
          "breadth": float,         # 上昇銘柄数 / 全銘柄数 (0~1)
        }
      },
      "big_cats": {
        big_cat: {
          "turnover_sum": float,
          "flow_day_sum": float,
          "stock_count": int,
        }
      },
      "sample_note": "監視100銘柄サンプル（全市場ではありません）"
    }
    """
```

**flow_day の Python 側再実装**（app.js と同定義）
```python
def flow_day(s):
    t = s.get("turnover") or 0
    c = s.get("change_pct") or 0
    o = s.get("from_open") or 0
    v = s.get("vwap_gap") or 0
    return t * (c * 0.5 + o * 0.3 + v * 0.2)
```

---

### `/api/sector_flow` エンドポイント

```python
@app.get("/api/sector_flow")
def api_sector_flow():
    snap = state.snapshot()
    data = compute_sector_flow(snap["stocks"], _classif)
    return jsonify(data)
```

- 呼び出しのたびにその場で集計（100銘柄×19テーマ = 軽量、<1ms）
- キャッシュ不要

---

### UIの3ビュー構成

#### ビュー1: 資金ローテーション（横棒グラフ）

```
テーマ名    [←流出 ─────0─────── 流入→]  breadth
防衛        ████████████          78%
半導体              ████████      45%
造船               ██             33%
```

- 軸 = `delta_15m_sum`（15分資金増分の合計、符号付き）
- 棒の長さ = 相対スケール（最大値=100%）
- 右端 = breadth（監視内の上昇銘柄割合）
- テーマクリック → 該当銘柄リスト展開

#### ビュー2: テーマヒート（タイルグリッド）

```
┌─────────┐ ┌───────┐ ┌──────────┐
│  防衛   │ │ 半導体│ │  造船    │
│  ██████ │ │  ████ │ │  ███     │
│  78%▲  │ │  45%▲ │ │  33%▼   │
└─────────┘ └───────┘ └──────────┘
```

- タイルの**面積** = `turnover_sum`（売買代金の比）
- タイルの**色** = `flow_day_sum > 0` → 緑系 / `< 0` → 赤系（輝度は絶対値）
- タイル内**テキスト** = テーマ名 + breadth%
- category（AI・半導体 / 部品・素材 / 防衛・国策 / エネルギー / 金融）で段組み

#### ビュー3: 資金配分（大分類バー）

```
製造業    ████████████████  42%
情報通信  ████████          22%
金融      █████             13%
...
```

- `big_cat` の `turnover_sum` を積み上げバーまたはパーセントバーで表示
- 全体合計 = 監視銘柄の総売買代金（保存則ビュー）
- 「保存則: 各バーの合計 = 監視銘柄の総売買代金」の注記

---

## 設計判断事項（Codexに議論してほしいポイント）

### 1. 分類データのロードタイミング

| 案 | 内容 | トレードオフ |
|---|---|---|
| **A（推奨）** | 起動時1回 (`init_db()` の後) | シンプル。classification.dbは場中不変が前提 |
| B | `/api/sector_flow` 初回呼び出し時にレイジーロード | 起動高速化。再ロードフック（将来のDB更新）が必要 |

### 2. 集計の実行タイミング

| 案 | 内容 | トレードオフ |
|---|---|---|
| **A（推奨）** | GETのたびにその場で計算 | コード最小。100銘柄の集計は<1ms |
| B | collector_loopで30秒ごとにキャッシュ | 複雑化。メリットなし（UIポーリングが30秒以下なら差がない）|

### 3. 多重ラベル銘柄の扱い

- テーマビュー: 三菱重工の売買代金が防衛/宇宙/核融合/レアアース の**全テーマに計上**される（意図的）
- big_cat配分は非重複（保存則）
- **UIへの注記が必要**: 「マルチテーマ銘柄は複数テーマに重複計上されます」

### 4. classification.db の接続方法

| 案 | 内容 |
|---|---|
| **A（推奨）** | 起動時に全件メモリ展開（dict）。SQLiteコネクションは起動後クローズ |
| B | `ATTACH DATABASE` で monitor.db に結合してSQLで集計 |

A推奨の理由: 3707銘柄 × 542リンク はメモリ<1MB。場中にDBファイルを開き続けるリスク回避。

### 5. MVP（最初に実装する1ビュー）

| 案 | 実装コスト | 情報密度 | 推奨度 |
|---|---|---|---|
| **資金ローテーション（横棒）** | 低 | 中（15分の方向性） | ★★★ |
| テーマヒート（タイル） | 中 | 高（面積+色+breadth） | ★★ |
| 資金配分（大分類バー）| 低 | 低（参考情報）| ★ |

→ **資金ローテーションから始めて、テーマヒートを次ステップ**にするのが現実的。

---

## Case 2 概要（今回は着手しない）

`monitor.db` に `daily_ohlc(date, code, open, high, low, close, volume)` を追加。
`day_stock_data/T*.csv`（Shift-JIS、ヘッダなし）をインポートするスクリプトを別途作成。

指標候補: 日次RVOL / OBV傾き / CMF / MA並び / ATR / RSI / 25日高値ブレイク。
合成「需給スコア」を銘柄テーブルの行バッジで表示。

**Case 1×Case 2 の掛け合わせ**: 点灯テーマ内の需給スコア上位が最終的なスクリーニング。

---

## 付録: 関連ファイル

| パス | 役割 |
|---|---|
| `app.py` | Flaskアプリ本体（932行） |
| `monitor.db` | 場中DBメイン |
| `classification/classification.db` | 分類DB（場中読み取り専用） |
| `classification/build_classification.py` | 銘柄分類.xlsx → DB構築（再実行可） |
| `classification/seed_themes.py` | Webソースのテーマ銘柄を検証ゲート付きで投入 |
| `classification/theme_membership_edit.csv` | 手キュレーション用エクスポート（542行） |
| `銘柄分類.xlsx` | 日経NEEDS手作業シート（3707銘柄） |
| `day_stock_data/T<YYMMDD>.csv` | 日足データ（Shift-JIS、約4300コード/日） |
