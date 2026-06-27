# 楽天RSS LAN Monitor 開発引き継ぎ

最終更新: 2026-06-27 JST

この文書は、別PC・別チャット・別担当者でも現在の開発状況を復元してそのまま作業を継続できるようにまとめたものです。

---

## 1. ツールの目的

マーケットスピード II / 楽天RSSでリアルタイム更新されるExcelを、メインPC上のPythonアプリから定期的に読み取り、ブラウザへ表示するLAN監視ツールです。デイトレ・スイング向けにセクター資金フロー・RVOLなどの補助分析も備えます。

基本フロー:

1. マーケットスピード II と楽天RSSを起動
2. `楽天RSS｜株式銘柄監視用｜軽量版.xlsm` をExcelで開く
3. `start.bat` または `.venv\Scripts\python.exe app.py` でAppを起動
4. AppがExcelの `DB` シートを約5秒ごとにCOM経由で読み取る
5. ブラウザが `/api/snapshot` を約2秒ごとに取得して表示を更新

厳密な瞬時更新ではなく、最大約5秒遅れの準リアルタイム表示です。

---

## 2. ワークスペース

開発フォルダ:

```text
C:\Users\Kaz\PycharmProjects\kabu_rakuten_rss
```

主要ファイル:

```text
app.py                                  Flask/API・Excel収集・SQLite・アラート・ブレイク検知
daily_data.py                           日足CSV取込・当日live仮値保存
templates/index.html                    画面HTML
static/app.js                           描画・フィルター・ソート・スクリーナー・セクター分析
static/style.css                        PC/スマホ用スタイル
monitor.db                              価格履歴・アラート・お気に入り・日足OHLC
classification/classification.db        NEEDS分類 + テーマ分類DB
classification/lookup.py                分類DBロードとセクターフロー集計関数
classification/build_classification.py  分類DB再生成スクリプト
classification/seed_themes.py           テーマ銘柄投入スクリプト
classification/theme_membership_edit.csv テーマ所属の人手確認用CSV
day_stock_data/T*.csv                   日足CSV（Shift-JIS、翌朝配置）
楽天RSS｜株式銘柄監視用｜軽量版.xlsm    現在使用するExcel
start.bat                               起動スクリプト（多重起動停止・venv自動作成含む）
requirements.txt                        Flask 3.1.1 / pywin32 310
README.md                               利用者向け説明
HANDOFF.md                              本文書
CHANGELOG.md                            変更ログ
```

このフォルダは現時点ではGitリポジトリではありません。

---

## 3. 現在の稼働状態（2026-06-27時点）

- App: `0.0.0.0:8765` で待受中
- 取得銘柄数: 最大100銘柄（Excelから）
- `monitor.db` サイズ: 約1GB（price_history 648万行、14日保持）
- daily_ohlc: 25万行（日足CSVの累積）
- アラートルール: 2銘柄
- お気に入り: 7銘柄

ローカルURL:
```text
http://127.0.0.1:8765
```
Tailscale（外部端末）:
```text
http://100.85.144.74:8765
```
Tailscale経由アクセスは動作確認済みです。

---

## 4. スレッド構成

App起動後に3スレッドが動作します:

| スレッド名 | 役割 |
|---|---|
| `excel-collector` | Excelを5秒ごとに読取、state更新、ブレイク検知、履歴保存 |
| `analytics` | rvol/flowを非同期計算して `_analytics_cache` へ格納 |
| Flaskメイン | HTTPリクエスト処理（`threaded=True`） |

**設計原則:** `analytics` スレッドが重いDBクエリ（rvol最大3.5秒、flow×3回）を担当し、`excel-collector` はキャッシュを読むだけにすることで、コレクタのブロッキングを防いでいます。

---

## 5. Excel構成

対象ブック:
```text
楽天RSS｜株式銘柄監視用｜軽量版.xlsm
```

### 設計方針: ①取得はExcel、②加工はPython

ExcelはRSS関数による**生データの受け皿に徹し、派生計算式は持たない**。
派生指標（ボラ・始値比率・前日比率・VWAP乖離率・GU/GD率・各高安差分・特別気配・板不均衡）はすべて `app.py` の `compute_derived()` で計算します。

### DBシート（5秒系統・リアルタイム）

読取範囲: `DB!A1:BK101`（63列）

主要列グループ:
```text
基本:   メモ Code 銘柄名 市場 貸借 ↑↓ 現在値 売買代金(千円) VWAP 前日C
OHLC:   O/O時刻 H/H時刻 L/L時刻
高安:   YH/YH日付 LH/LH日付 YL/YL日付 LL/LL日付
気配:   買い気配 売り気配 特売 特買
詳細時刻: 現在値詳細時刻
歩み:   歩み1〜4 / 歩み1時刻〜4時刻
場中追加: 出来高 前場終値 前場出来高 後場始値 後場高値 後場安値
板:     最良買気配数量 最良売気配数量 買成行数量 売成行数量 OVER/UNDER気配数量
信用:   貸借倍率 逆日歩 信用倍率 信用売残/前週比 信用買残/前週比 回転日数
板複数: 最良買気配数量1〜3 最良売気配数量1〜3
```

Pythonで計算する派生指標（Excelには不在）:
```text
volatility   = (H-L)/O
from_high    = (H-現在値)/O
from_low     = (現在値-L)/O
from_open    = 現在値/O - 1
change_pct   = 現在値/前日C - 1
vwap_gap     = 現在値/VWAP - 1
indicative   = (買い気配+売り気配)/2 （寄前のみ）
gap_pct      = (indicative-前日C)/前日C （寄前のみ・Oが付いたらNone）
year_high_gap   = (YH-現在値)/現在値
listing_high_gap = 同上（差分100%超は除外 = 分割前不正確値対策）
year_low_gap    = (現在値-YL)/現在値
listing_low_gap = (現在値-LL)/現在値
book_imbalance  = (最良買-最良売)/(最良買+最良売)
book_imbalance_3, market_order_imbalance, auction_imbalance も同様
special_quote   = "特売" or "特買" or ""
```

### 銘柄リストシート（60秒系統・静的参照データ）

読取範囲: `銘柄リスト!A1:Q101`

楽天の銘柄リスト画面からコピペしたスナップショット（リアルタイムではない）。
利用列: 発行済み株式数・コード/ティッカー・時価総額(百万円)・PER・PBR・配当利回り・決算発表予定日・業種。
決算発表予定日からは `days_to_earnings` を計算し、0〜1日でバッジ表示。

### Configシート（60秒系統）

読取範囲: `Config!A1:E30`

| Key | 設定名 | 初期値 |
|---|---|---:|
| `upper_price_offset_pct` | 上限価格 | 3.0% |
| `lower_price_offset_pct` | 下限価格 | -3.0% |
| `change_up_pct` | 前日比上限 | 3.0% |
| `change_down_pct` | 前日比下限 | -3.0% |
| `open_up_pct` | 始値比上限 | 3.0% |
| `open_down_pct` | 始値比下限 | -3.0% |
| `vwap_up_pct` | VWAP比上限 | 2.0% |
| `vwap_down_pct` | VWAP比下限 | -2.0% |
| `year_high_gap_pct` | 年初来高値差分 | 3.0% |
| `listing_high_gap_pct` | 上場来高値差分 | 3.0% |
| `year_low_gap_pct` | 年初来安値差分 | 3.0% |
| `listing_low_gap_pct` | 上場来安値差分 | 3.0% |
| `premarket_gu_pct` | 寄前GU上位 | 1.0% |
| `premarket_gd_pct` | 寄前GD下位 | -1.0% |
| `attention_rvol` | 注目:RVOL倍率 | 2.0倍 |
| `attention_turnover_oku` | 注目:最低売買代金 | 10億円 |
| `attention_high_gap_pct` | 注目:高値接近 | 3.0% |

### PTS切替（VBA）

- 06:00以上15:30未満: TSE（コード末尾 `.JNX` なし）
- それ以外: PTS（コード末尾 `.JNX` あり）

手動切替ボタン（Config!G列付近）:
- `自動` → `UseAutomaticSession`
- `東証` → `UseManualTSE`  
- `PTS` → `UseManualPTS`

制御状態は `Config!H3` に永続化（AUTO / MANUAL_TSE / MANUAL_PTS）。Appはコード末尾から市場状態を判定し、時刻想定と不一致なら「要確認」バッジ表示。

---

## 6. 環境変数（デフォルト値）

```text
RAKUTEN_RSS_PORT              = 8765
RAKUTEN_RSS_POLL_SECONDS      = 5       ExcelDB読取間隔（秒）
RAKUTEN_RSS_CONFIG_POLL_SECONDS = 60    Config・銘柄リスト再読取間隔（秒）
RAKUTEN_RSS_HISTORY_SECONDS   = 10      price_history書込間隔（秒）
RAKUTEN_RSS_WORKBOOK          = 楽天RSS｜株式銘柄監視用｜軽量版.xlsm
RAKUTEN_RSS_SHEET             = DB
RAKUTEN_RSS_DATA_RANGE        = A1:BK101
RAKUTEN_RSS_CONFIG_RANGE      = A1:E30
RAKUTEN_RSS_STATIC_SHEET      = 銘柄リスト
RAKUTEN_RSS_STATIC_RANGE      = A1:Q101
RAKUTEN_RSS_RVOL_DAYS         = 10      RVOL計算の過去参照日数
```

---

## 7. SQLite（monitor.db）

テーブル:

| テーブル | 用途 | 保持期間 |
|---|---|---|
| `price_history` | 価格・前日比・VWAP比・売買代金の時系列 | 14日（30分ごとにprune） |
| `alert_rules` | 銘柄ごとのアラート条件 | 永続 |
| `alert_events` | 発生したアラート履歴 | 14日（30分ごとにprune） |
| `breakout_events` | 高値・安値ブレイク履歴 | 14日（30分ごとにprune） |
| `favorites` | お気に入り銘柄 | 永続 |
| `daily_ohlc` | 日足OHLC（CSV確定値 or live仮値） | pruneなし（手動管理） |

price_historyのインデックス:
```text
idx_history_code_time     (code, sampled_at)
idx_history_time          (sampled_at)           pruneのみ使用
idx_history_ms_code_time  (market_session, code, sampled_at)   rvolクエリ最適化用
```

daily_ohlcのsource列:
- `'csv'`  : 翌朝配置のCSVから確定した値。優先度高。
- `'live'` : 場中に楽天RSSから保存した仮値。csvが来たら上書きされる。

**DBサイズ注意:** price_historyは14日で6〜7百万行・約1GB規模になる。定期的な手動VACUUMを推奨（場引け後に実行）:
```sql
VACUUM;
```

---

## 8. 実装済み機能

### 銘柄一覧テーブル

- 最大100銘柄、2秒更新（スナップショットキャッシュで実DBクエリは最小化）
- 表示列: お気に入り★ / コード・銘柄名 / 現在値 / 前日比 / 始値比 / VWAP比 / GU/GD / 高安位置(レンジバー) / 売買代金 / RVOL / 通知スイッチ / 設定ボタン
- バッジ: 高値更新 / 安値更新 / 本日決算 / 明日決算 / 特別気配
- 日本株標準配色（上昇=赤 / 下落=緑）

### タブ一覧

```text
全銘柄
★銘柄
🔥真ブレイク       （スクリーナー）
💎押し目再加速     （スクリーナー）
🍄反転初動         （スクリーナー）
⚡️セリクラ反転    （スクリーナー）
🎯VWAP反発         （スクリーナー）
💛テーマ主役       （スクリーナー）
📕セクター         セクター資金フロービュー
💸Mフロー          推定資金フローランキング
前日比             上限/下限 A/Bトグル
始値比             上限/下限 A/Bトグル
YH/LH差分          年初来/上場来 A/Bトグル
YL/LL差分          年初来/上場来 A/Bトグル
寄前               GU上位/GD上位 A/Bトグル
```

### スクリーナー（6種）

各スクリーナーは `match`（条件フィルタ）と `score`（ソート用スコア）を返す。Config値を閾値として使用。

| ID | 狙い |
|---|---|
| `trend_core` | 売買代金×RVOL×高値接近の複合・順張りブレイク候補 |
| `pullback_resume` | 60分・15分強い銘柄の押し目からの再加速 |
| `reversal_early` | Mフロー流出圏から5分足が先に切り返した反転初動 |
| `selling_climax` | セリクラ後に5分陽転・商い急増のリバウンド候補 |
| `vwap_reclaim` | VWAP付近で5分先行反発・需給改善 |
| `theme_leader` | 強いテーマの上位メンバーで個別指標も強い銘柄 |

### DETAILパネル（右側）

- 直近3時間の価格チャート（price_historyより）
- 現在値・前日比・始値比・VWAP比・GU/GD・RVOL・売買代金
- 15分Δ代金・ボラ・業種・時価総額・PER/PBR・決算予定
- 始値/高値/安値（時刻付き）・高安差分4種

### アラート

- 12条件（上下限価格×2 + 前日比/始値比/VWAP比の上下限×各2 + 高安差分×4）
- 各条件を個別ON/OFF可能・銘柄単位マスタースイッチ
- 5分クールダウン（同一銘柄・同一条件の再通知抑制）
- ブラウザ通知対応（OS設定依存）
- アラート履歴は14日pruneで管理

### ブレイク検知（detect_breakouts）

年初来高値・上場来高値・年初来安値・上場来安値の4種を監視。差分0以下かつアーム済みで発火、0.1%以上離れると再アーム。起動直後は未アームのため誤発火なし。検知時は `breakout_events` と `alert_events` の両方に記録。

### RVOL（Relative Volume）

過去 `RVOL_LOOKBACK_DAYS`（デフォルト10）日の「同時刻時点の累計売買代金」を基準に当日売買代金の倍率を計算。5分ごとに analytics スレッドが計算してキャッシュ。

### 資金フロー分析（Mフロータブ）

```text
日中フロー   = 売買代金 × (前日比×0.5 + 始値比×0.3 + VWAP比×0.2)
15分フロー   = 15分売買代金増分 × 15分騰落率
5分フロー    = 5分売買代金増分 × 5分騰落率
```

各方向（流入/流出）の上位5〜10銘柄をランキング表示。

### セクター資金フロー（セクタータブ）

- TFサブタブ: 5分 / 15分 / 60分 / 日 / 週間
- 場中TF（5m/15m/60m）: 監視銘柄のスナップショット
- 日足TF（day/week）: day_stock_data/*.csv の確定値 + 当日live仮値
- 表示: テーマヒートマップ（squarified treemap） + 資金ローテーション横棒 + NEEDS大分類/中分類横棒
- テーマ集計: 多重ラベル銘柄を weight 付きで重複計上（設計意図）
- big_cat集計: 1銘柄1所属・非重複（保存則あり）
- アコーディオン展開で所属銘柄リスト表示

### 日足データ（daily_ohlc）

- 起動時 + 10分ごとに `day_stock_data/*.csv` をスキャンし未取込日を取込
- ファイル名 `T<YYMMDD>.csv`（Shift-JIS・ヘッダなし10列）
- 同一コードの東証/名証重複は出来高最大を採用（=東証）
- TSE場中は5分ごとに当日4本値をlive仮値で保存（翌朝CSVで上書き）

### スマホ対応

幅700px以下で自動レイアウト切替。390x844相当で横はみ出しなしを確認済み。

---

## 9. 重要な設計判断・落とし穴

### analytics スレッド分離

`compute_rvol_baselines`（旧実装では実測11秒超）と `compute_flow_anchors`×3本（計2秒）をコレクタから分離し、専用スレッドで非同期実行。コレクタはキャッシュ読取のみで、Excelの取得・ブレイク検知・アラートチェックが11秒ブロックされる問題を解消。

### rvolクエリの最適化

旧実装の `ROW_NUMBER() OVER (PARTITION BY code, date ORDER BY sampled_at DESC)` を `MAX(turnover) GROUP BY code, date` に変更。売買代金が日中単調増加する性質を利用して窓関数・一時Bツリーを排除。さらに `(market_session, code, sampled_at)` 複合インデックスを追加して約6秒→3.5秒に短縮。

### スナップショットキャッシュ

`/api/snapshot` は2秒ポーリング×複数クライアントで呼ばれるが、元データは5秒ごとしか変わらない。`_snapshot_cache` で `updated_at` をキーにペイロードをキャッシュ（dirty flagで書込後は強制更新）。アラート・お気に入り・既読操作後は自動無効化。

### snapshotを読み取るAPIのエラー起源

`clean_excel_value()` で `-1,000,000,000` 以下のCOMエラー値を `None` 化。 `api/history/<code>` でも異常負価格を除外。

### Excelビジー対策

`Excel.Ready == False` または計算中なら読取をスキップして最後のスナップショットを保持。COM/RPCエラー時は接続を破棄して最大30秒の指数バックオフで再接続。

### 上場来高値差分の分割前除外

差分が100%超（= 株式分割前の高値）の場合は `None` として除外。`compute_derived()` 内に実装。

### daily_ohlcのsource設計

CSV上書きは常に `ON CONFLICT DO UPDATE SET ... source='csv'`。live書込みは `WHERE daily_ohlc.source != 'csv'` でCSV確定済みを保護。日次/週次フロー集計はsourceフィルタなしで両方参照（当日live含む）。

---

## 10. Excel固まり対策

初期実装の2秒×`UsedRange` 読取からの改善点:

- 読取周期を5秒へ変更
- `UsedRange` ではなく固定範囲 `A1:BK101`
- Config・銘柄リストは60秒ごとに分離
- `Excel.Ready == False` または計算中なら読み取りをスキップ
- COM/RPC拒否時は接続を破棄して再接続
- エラー継続時は最大30秒まで段階的バックオフ
- 読取失敗中も最後に成功したスナップショットをブラウザへ表示

---

## 11. 起動・停止・復旧手順

### 通常起動

1. マーケットスピード II を起動
2. 楽天RSSを利用可能な状態にする
3. Excelブックを開く
4. `start.bat` を実行（多重起動の旧プロセスを自動停止→新規起動）
5. `http://127.0.0.1:8765` を開く

### Appだけ停止

```powershell
Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -match 'python(.exe)?\"? app.py' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```

### App再起動（手動）

```powershell
cd C:\Users\Kaz\PycharmProjects\kabu_rakuten_rss
.\.venv\Scripts\python.exe app.py
```

### API確認

```powershell
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8765/api/snapshot
```

### monitor.db の手動VACUUM（DB肥大化対策・場引け後推奨）

```powershell
cd C:\Users\Kaz\PycharmProjects\kabu_rakuten_rss
.\.venv\Scripts\python.exe -c "import sqlite3; con=sqlite3.connect('monitor.db'); con.execute('VACUUM'); print('done')"
```

---

## 12. 別PCへの移行

最低限コピーするもの:
```text
app.py
daily_data.py
templates\
static\
classification\
day_stock_data\（任意）
start.bat
requirements.txt
楽天RSS｜株式銘柄監視用｜軽量版.xlsm
```

状態も引き継ぐ場合:
```text
monitor.db
```

手順:
1. Windows へ Python 3.12 をインストール
2. 上記一式を同じフォルダ構成で配置
3. マーケットスピード II / 楽天RSS をセットアップ
4. Excelを開く
5. `start.bat` を実行（初回は `.venv` と依存パッケージが自動作成される）

Excel COM と楽天RSS 依存のため、App本体は Windows 前提です。

---

## 13. 現状の既知仕様・今後の候補

現時点の仕様:
- App独自の警告音なし（ブラウザ通知はOS設定依存）
- Flask組込みサーバーを使用（Waitressなど本番向けWSGIは未導入）
- 認証なし・Tailscale内での個人利用を想定
- daily_ohlcのprune未実装（現状25万行・累積するが実害は小）
- price_historyの自動VACUUM未設定（手動VACUUMを推奨）
- GitリポジトリなしCI/CDなし

今後の候補:
- Web Audio APIによるアラート音と音量/ミュート設定
- DETAILチャートの時間軸ラベル・ツールチップ
- Waitness等本番向けWSGIサーバー
- daily_ohlcのprune追加（古い日付を削除）
- Gitリポジトリ化・自動テスト追加
- 推定資金フローの重みをConfig化

---

## 14. 変更履歴

変更の詳細は `CHANGELOG.md` を参照してください。

大まかな経緯:
- 2026-06-07: テーブルdiff更新・タブUI統合（17→9タブ）・HTMLエスケープ適用
- 2026-06-12: Excel軽量化（派生計算Python移管）・RVOL・15分フロー・ブレイク検知・PTS対応
- 2026-06-22〜23: セクターフロー・テーマヒートマップ・日足CSV取込・スクリーナー6種・60分フロー追加
- 2026-06-27: analyticsスレッド分離・rvolクエリ最適化・スナップショットキャッシュ・バグ修正各種

---

## 15. 次のチャットへ渡すプロンプト

```text
このプロジェクトの HANDOFF.md を最初に読み、記載された現状・設計判断・既知の問題を前提として作業を続けてください。
作業前に app.py・daily_data.py・static/app.js・classification/lookup.py の現物を確認し、
ユーザーの既存Excel変更・monitor.db・classification.db を壊さないようにしてください。
```

重要:
- HANDOFF.md だけを盲信せず、作業開始時に現物ファイルと稼働状態を再確認すること
- Excelブックは先にバックアップしてから変更すること
- 既存のユーザーデータや `monitor.db` を削除・初期化しないこと
- 重いDB操作（VACUUM・インデックス追加）は場引け後などの非稼働時間に行うこと
