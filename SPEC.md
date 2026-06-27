# 楽天RSS LAN Monitor — 仕様・設計ドキュメント

> 監査・引き継ぎ用。最終更新 2026-06-23。

---

## 1. プロジェクト概要

**目的**: 楽天証券の Excel アドイン「RSS（リアルタイムスプレッドシート）」から取得した株価を LAN 内で共有し、場中のデイトレ・スイングトレードを支援する Web モニター。

**主要ユースケース**:
- 監視銘柄（最大 100）のリアルタイム株価・指標一覧
- NEEDS 業種 + テーマ別の資金フロー可視化（セクターローテーション把握）
- 価格・RVOL・高値更新などの条件アラート通知

---

## 2. 環境・起動

| 項目 | 内容 |
|---|---|
| OS | Windows 11（Excel COM アクセスが必要なため Windows 限定） |
| Python | `.venv\Scripts\python.exe`（プロジェクト内 venv） |
| 起動 | `python app.py`（ポート: 環境変数 `RAKUTEN_RSS_PORT`, デフォルト `8765`） |
| アクセス | `http://localhost:8765/` または LAN 内 `http://<host-ip>:8765/` |
| 依存 | Flask, openpyxl, pywin32（COM）, SQLite3（標準） |

**Flask は `debug=False`** で起動。`index.html` の変更はサーバー再起動が必要（テンプレートキャッシュ）。`app.js` / `style.css` は `?v=YYYYMMDD-N` バージョン付きで提供されブラウザキャッシュ回避。

---

## 3. アーキテクチャ概観

```
Excel RSS アドイン
  └─ COM (pywin32)
       └─ collector_loop() ─ 5秒ポーリング
              │
              ├─ attach_analytics()   ← RVOL / 資金フローアンカー計算
              ├─ detect_breakouts()   ← 高値更新検出
              ├─ maybe_fire_alerts()  ← 条件アラート発火
              └─ save_history()       ← price_history に永続化
                   └─ MonitorState    ← スナップショットをメモリ保持

Flask (threaded=True)
  ├─ GET /                      → index.html
  ├─ GET /api/snapshot          → MonitorState から JSON
  ├─ GET /api/sector_flow       → compute_sector_flow() (監視銘柄, 5/15/60分)
  ├─ GET /api/sector_flow_daily → compute_sector_flow_daily() (全市場, 日/週)
  └─ PUT/POST /api/alerts/*     → SQLite alert_rules 更新

ブラウザ (app.js)
  └─ 2秒ポーリング /api/snapshot
       └─ renderAll() → renderSector() / renderTable() / renderAnalysis()
```

---

## 4. データソース

### 4-1. リアルタイムデータ（5秒更新）

Excel RSS → COM → `collector_loop()` が `MonitorState.stocks` を上書き。
最大 100 銘柄。監視銘柄は RSS の設定シートで管理。

**キー指標（`attach_analytics` で計算）**:

| フィールド | 計算式 |
|---|---|
| `turnover` | `price × volume`（当日累計、千円） |
| `rvol` | `turnover_now / baseline_same_time`（過去 10 日平均比） |
| `flow_day` | `turnover × (change_pct×0.5 + from_open×0.3 + vwap_gap×0.2)` |
| `turnover_delta_5m` | 5 分前アンカーからの売買代金増分 |
| `turnover_delta_15m` | 15 分前アンカーからの増分 |
| `turnover_delta_60m` | 60 分前アンカーからの増分 |
| `change_5m` / `change_15m` / `change_60m` | 各時間軸の騰落率 |

### 4-2. 日足 CSV（永続 OHLCV）

- `daily_data.py` が `day_stock_data/T*.csv` を読んで `monitor.db:daily_ohlc` に INSERT。
- 起動時 + 10 分ごとに実行。237,233 行（55 日分 × 約 4,300 銘柄）。
- source 優先度: `csv > live`（当日分は live で仮値上書き可）。

### 4-3. 銘柄分類データ

- `銘柄分類.xlsx`（手動管理）→ `build_classification.py` → `classification/classification.db`
- 3,707 銘柄。ETF・指数は含まない（NEEDS 分類対象外のため）。

---

## 5. データベース

### `monitor.db`

| テーブル | 行数（参考） | 用途 |
|---|---|---|
| `price_history` | 6,059,225 | 5 秒スナップの時系列（チャート・RVOL ベースライン用） |
| `daily_ohlc` | 237,233 | 日足 OHLCV 永続化 |
| `alert_rules` | 2 | 銘柄別アラート条件 |
| `alert_events` | 722 | アラート発火履歴 |
| `breakout_events` | 722 | 高値更新イベント |
| `favorites` | 5 | お気に入り銘柄 |

### `classification/classification.db`

| テーブル | 行数 | 内容 |
|---|---|---|
| `stocks` | 3,707 | コード, 名称, market, big_cat, mid_cat, small_cat |
| `themes` | 19 | テーママスター（theme_id, name, category, source） |
| `stock_themes` | 544 | 銘柄⇔テーマ M:N（weight, role, confidence, source） |

---

## 6. 分類システム

### 6-1. NEEDS 業種（2 層）

```
big_cat（大分類, 14 種）
  └─ mid_cat（中分類, ~34 種）
       └─ small_cat（細分類）
```

1 銘柄は必ず 1 つの big_cat に属する（非重複）。

### 6-2. テーマ（多重ラベル）

19 テーマ × 5 カテゴリ。1 銘柄が複数テーマに属ける。

| カテゴリ | テーマ（theme_id） |
|---|---|
| AI・半導体 | semiconductor, semi_equipment, mlcc, memory, datacenter, physical_ai, gemini |
| 部品・素材 | densen, metals, rare_earth |
| 防衛・国策 | defense, cyber_security, space, shipbuilding, aviation, infrastructure |
| エネルギー | perovskite, fusion |
| 金融 | bank_rate |

**weight**: `1.0`（core）/ `0.4`（related）。フロー集計時に重み付き加算。  
**自動付与 (`source='業種自動'`)**: `build_classification.py` の `AUTO` 辞書で small_cat / mid_cat → theme_id マッピング。  
**手動付与 (`source='カリン手動'`)**: `seed_themes.py` または `theme_membership_edit.csv` で追加。

---

## 7. セクター資金フロー機能

> このセッションで実装したメイン機能。

### 7-1. TF サブタブ（5 種）

| TF | ラベル | 方向の定義 | 商いの定義 | 母集団 | API |
|---|---|---|---|---|---|
| 5m | 5分 | 直近5分間騰落率 | 直近5分売買代金増分 | 監視銘柄 | `/api/sector_flow` |
| 15m | 15分 | 直近15分間騰落率 | 直近15分売買代金増分 | 監視銘柄 | `/api/sector_flow` |
| 60m | 60分 | 直近60分間騰落率 | 直近60分売買代金増分 | 監視銘柄 | `/api/sector_flow` |
| day | 日 | 当日前日比 | close×volume | 全市場（3,702銘柄） | `/api/sector_flow_daily?tf=day` |
| week | 週間 | 直近5日騰落率 | 5日合計close×volume | 全市場（日足CSV） | `/api/sector_flow_daily?tf=week` |

**日/週間 TF の 60 秒クライアントキャッシュ** (`_dailyCache` in app.js):  
全市場集計は重いため、2 秒ポーリングで毎回叩かないようキャッシュ。

### 7-2. テーマ資金フロー計算式

```python
flow_day   = turnover × (change_pct×0.5 + from_open×0.3 + vwap_gap×0.2)
flow_15m   = turnover_delta_15m × change_15m
flow_5m    = turnover_delta_5m  × change_5m
flow_60m   = turnover_delta_60m × change_60m
# 週間: flow_day と同形式、change_pct = close_last/close_first - 1
```

テーマへの集計: `flow_XX_sum_weighted = Σ(flow_XX × membership_weight)`

### 7-3. テーマヒートマップ（Treemap）

- **アルゴリズム**: Squarified Treemap（Bruls/Huizing/van Wijk, 2000）を Vanilla JS で実装（外部ライブラリなし）
- **タイル面積**: `turnover_sum_raw`（実売買代金）
- **タイル色**: 赤=流入（上昇）/ 緑=流出（下落）、日本式カラー
- **色強度**: 最大テーマを 100 とした相対強度（`rel = flow / maxAbs × 100`）
- **テキスト**: テーマ名 + 相対強度スコア + 銘柄数（タイルサイズに応じてフォールバック）
- **インタラクション**: タイルクリック → 下のバー一覧でアコーディオン展開と連動（`view.expandedSectors`）

### 7-4. NEEDS 分類バー（大分類 / 中分類切り替え）

- 右上のトグルボタン「大分類 / 中分類」で切り替え（`view.sectorCatLevel`）
- 大分類: 14 カテゴリ / 中分類: 約 34 カテゴリ
- **バー幅・ソート**: TF に対応したフロー sum（`flow_5m_sum`, `flow_15m_sum`, etc.）
- ETF・指数は `stocks_meta` に存在しないため自動除外（全市場 4,265 → 3,702 銘柄）

### 7-5. アコーディオン（所属銘柄一覧）

テーマバー / NEEDS 分類バーをクリックで展開。

| カラム | TF での内容 |
|---|---|
| コード | 証券コード |
| 銘柄 | 銘柄名 |
| **XX分比/前日比/週間比** | **TF に応じて切り替え**（change_tf フィールド） |
| 売買代金 | 当日累計（監視）or close×volume（日足） |

ソート順も TF 対応（5分タブなら `flow_5m_w` 降順）。  
`view.expandedSectors` (Set) で展開状態を保持し、ポーリング再描画でも維持。

---

## 8. API エンドポイント

| Method | Path | 説明 |
|---|---|---|
| GET | `/` | index.html |
| GET | `/api/snapshot` | MonitorState JSON（銘柄一覧・config・アラート） |
| GET | `/api/sector_flow` | 監視銘柄ベース テーマ/NEEDS 集計（5/15/60m） |
| GET | `/api/sector_flow_daily?tf=day\|week` | 全市場 日足ベース テーマ/NEEDS 集計 |
| GET | `/api/history/<code>` | 銘柄の price_history（チャート用） |
| GET | `/api/alerts/<code>/defaults` | アラートデフォルト値取得 |
| PUT | `/api/alerts/<code>` | アラートルール保存 |
| POST | `/api/alerts/<code>/toggle` | アラート有効/無効切り替え |
| POST | `/api/favorites/<code>/toggle` | お気に入り登録/解除 |
| POST | `/api/alerts/acknowledge` | アラート既読 |

---

## 9. フロントエンド設計

### グローバル状態（`view` オブジェクト）

```javascript
view = {
  snapshot,          // 最新 API レスポンス
  selectedCode,      // 詳細パネルで選択中の銘柄
  activeTab,         // "all" | "sector" | "analysis"
  filterValues,      // フィルター設定値
  expandedSectors,   // Set<string> — アコーディオン開閉状態
  sectorTf,          // "5m" | "15m" | "60m" | "day" | "week"
  sectorCatLevel,    // "big" | "mid"
  ...
}
```

### ポーリング

`/api/snapshot` を 2 秒ごとに fetch → `renderAll()` でフル再描画。  
セクタータブのみ別途 `renderSector()` を呼び、日足データは `_dailyCache` でスロットリング。

### カラーパレット（CSS 変数）

```css
--market-up:   #ff6577  /* 上昇・流入 = 赤（日本式） */
--market-down: #46e39b  /* 下落・流出 = 緑（日本式） */
--cyan:        #55d7ff  /* アクセント・選択状態 */
--panel-2:     #0d1b24  /* パネル背景 */
```

---

## 10. 設計上の判断・注意事項

### 意図的な設計

- **テーマ集計でマルチラベルを重複計上する**:  
  半導体銘柄が「半導体」と「AI」の両テーマに属する場合、両方にカウント。これは意図的（テーマ間の資金フロー比較が目的であり、ポートフォリオ配分ではないため）。

- **大分類バーは非重複（保存則）**:  
  NEEDS 業種は 1 銘柄 1 所属なので、大分類・中分類の合計は全銘柄の合計に等しい。

- **日/週間 TF の全市場集計**:  
  監視銘柄 100 件では偏りが大きいため、日足 CSV から全 3,702 銘柄を合成 dict として構築し同じ `compute_sector_flow` を通す。

- **ETF 除外**:  
  `daily_ohlc` に含まれる ETF（1xxx/15xx/2xxA 等）は `classification.db` に存在しないため、日足集計では自動的に対象外。「未分類」が残る場合は Excel で `big_cat` が空欄の銘柄（約 60 件）。

### 制約

- **Windows 専用**: Excel COM（`pywin32`）依存のため macOS/Linux では動かない。
- **監視銘柄上限 100**: RSS の仕様（Excel アドインのセル範囲）。
- **場外は 5m/15m/60m フロー = 0**: `change_Xm` フィールドが 0 になるため正常。場中のみ意味のある値。
- **`vwap_gap` は RSS が直接提供**: 当日 VWAP を RSS から取得するため独自計算なし。

### TODO（未完了・要キュレーション）

- `seed_themes.py` の weight/role/confidence 対応（現スキーマに合わせて更新）
- `theme_membership_edit.csv` キュレーション（三菱重工・川重への造船タグ追加等）
- 小分類（small_cat）を使ったさらに細粒度なビュー
- ヒートマップ: ResizeObserver によるレスポンシブ対応

---

## 11. ファイル構成

```
kabu_rakuten_rss/
├── app.py                        # Flask アプリ本体 (~1,008 行)
├── daily_data.py                 # 日足 CSV → daily_ohlc インポート
├── monitor.db                    # SQLite: 価格履歴・アラート・日足
├── 銘柄分類.xlsx                 # NEEDS 業種マスター（手動管理）
├── classification/
│   ├── build_classification.py   # xlsx → classification.db ビルダー
│   ├── seed_themes.py            # テーマ membership 手動付与
│   ├── lookup.py                 # compute_sector_flow() / daily (~354 行)
│   └── classification.db        # SQLite: stocks / themes / stock_themes
├── static/
│   ├── app.js                    # フロントエンド全体 (~1,037 行)
│   └── style.css                 # ダークテーマ CSS (~1,010 行)
└── templates/
    └── index.html                # Single Page App シェル
```
