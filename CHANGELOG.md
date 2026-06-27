# 変更ログ

---

## 2026-06-27 — バグ修正・パフォーマンス改善・DB整合

### 背景

コードレビューで検出した以下の5項目を対応した。

---

### 1. analytics スレッド分離（app.py）

**問題:** `compute_rvol_baselines` が `collector_loop` 内で同期実行されており、5分ごとに最大11秒以上コレクタがブロックされていた。ブロック中はExcel読取・ブレイク検知・アラートチェックがすべて停止する。

**対応:**
- `analytics_worker()` 関数を新設（`detect_breakouts` の手前に挿入）
- `rvol_baselines` と `flow_anchors`（15m/5m/60m）の計算をこのスレッドへ移管
- `_analytics_cache` + `_analytics_cache_lock` をグローバルに追加
- `collector_loop` はキャッシュ読取のみに変更（`attach_analytics` はそのまま使用）
- `__main__` で `threading.Thread(target=analytics_worker, ...)` を起動

**効果:** コレクタの11秒ブロックが解消。analytics スレッドはバックグラウンドで独立して動く。

---

### 2. rvolクエリ SQL最適化（app.py）

**問題:** `compute_rvol_baselines` の SQL が `ROW_NUMBER() OVER (PARTITION BY code, date ORDER BY sampled_at DESC)` を使っており、日次売買代金集計のために全行スキャン + 一時Bツリーソートが走っていた。実測11.3秒。

**対応:**
- 窓関数を `MAX(turnover) GROUP BY code, d` に置換
  - 売買代金は日中単調増加するため、末尾値 = 日次最大値 と等価
- `price_history` に複合インデックスを追加（`init_db()` 内）:
  ```sql
  CREATE INDEX IF NOT EXISTS idx_history_ms_code_time
      ON price_history(market_session, code, sampled_at);
  ```

**効果:** クエリ時間 11.3s → 6.8s（SQL変更のみ）→ 3.5s（インデックス追加後）

---

### 3. live仮値の日次フロー集計への反映（app.py / classification/lookup.py）

**問題:** `save_live_ohlc` が `daily_ohlc` に `source='live'` で当日データを保存していたが、読み出し側の `compute_sector_flow_daily`（lookup.py）と `api_daily_dates`（app.py）に `AND source = 'csv'` フィルタがついていたため、live仮値が日次フロー集計に一切反映されていなかった（デッドライト）。

**対応（lookup.py の `compute_sector_flow_daily`）:**
- `dates` クエリの `AND source = 'csv'` を削除
- `today_rows` クエリの `AND source = 'csv'` を削除
- `prev_closes` クエリの `AND source = 'csv'` を削除
- `week rows` クエリの `AND source = 'csv'` を削除
- エラーメッセージを更新（CSVだけでなくRSSでも表示される旨を追記）

**対応（app.py の `api_daily_dates`）:**
- `AND source = 'csv'` フィルタを削除

**対象外（変更しなかった箇所）:**
- `daily_data.py` の `import_new_csvs` 内の `source = 'csv'` チェックはCSV確定済み日付を判定するためのものなので変更しない

---

### 4. alert_events の無制限肥大化を修正（app.py）

**問題:** `alert_events` テーブルのprune処理が未実装で無制限に蓄積し続けていた（`save_history` の定期pruneは `price_history` と `breakout_events` のみ対象）。

**対応（`save_history` 内）:**
```python
connection.execute(
    "DELETE FROM alert_events WHERE occurred_at < datetime('now', 'localtime', '-14 days')"
)
```
- 既存の30分ごとpruneサイクルに相乗りして追加
- 保持期間は `price_history` / `breakout_events` と統一した14日

---

### 5. スナップショットキャッシュ導入（app.py）

**問題:** `/api/snapshot` が2秒間隔×複数クライアントから叩かれるたびに毎回DBクエリが走っていたが、元データは5秒ごとにしか変化しない。

**対応:**
- `_snapshot_cache` + `_snapshot_cache_lock` をグローバルに追加
- `_invalidate_snapshot_cache()` ヘルパーを追加
- `api_snapshot` でキャッシュヒット時はDBクエリをスキップ
- 書込系API（`api_update_alert` / `api_toggle_alert` / `api_toggle_favorite` / `api_acknowledge_alerts`）の末尾で `_invalidate_snapshot_cache()` を呼び出し

**副次修正:**
- `_theme_leaders_cache` に `_theme_leaders_lock` を追加（スレッド競合対策）
- `app.js` の `refresh()` にオーバーラップガード（`_refreshing` フラグ）を追加
- `app.js` の `seenAlertIds` に上限キャップ（500件超で古い300件を削除）を追加

---

### 影響範囲まとめ

| ファイル | 変更内容 |
|---|---|
| `app.py` | analytics_worker追加 / rvolSQL最適化 / インデックス追加 / alert_events prune追加 / スナップショットキャッシュ追加 / theme_leaders_lock追加 / api_daily_dates source filter削除 |
| `classification/lookup.py` | compute_sector_flow_daily の source='csv' フィルタ全削除 / エラーメッセージ更新 |
| `static/app.js` | refresh()オーバーラップガード追加 / seenAlertIds上限キャップ追加 |

---

## 2026-06-22〜23 — セクターフロー・テーマヒートマップ・スクリーナー追加

（詳細は当時の CLAUDE_HANDOFF_SECTOR_FLOW.md 参照）

主な追加機能:
- セクタータブ（5m/15m/60m/day/week サブタブ）
- テーマ別squarified treemapヒートマップ
- NEEDS大分類/中分類 横棒グラフ
- 日足CSV取込（daily_data.py）
- 当日live仮値保存（save_live_ohlc）
- classification.db（NEEDS分類 + テーマ分類）
- スクリーナー追加: reversal_early / selling_climax / vwap_reclaim / theme_leader
- 60分フロー（flow_anchors_60m）追加
- DATA_RANGE拡張: `A1:BK101`（63列）

---

## 2026-06-12 — Excel軽量化・RVOL・15分フロー・ブレイク検知・PTS対応

主な変更:
- Excel派生計算をPythonへ移管（compute_derived）
- RVOL（Relative Volume）実装
- 15分フロー実装
- 年初来/上場来 高安ブレイク検知（detect_breakouts）
- PTS自動切替対応（コード末尾 `.JNX`）
- スクリーナー追加: trend_core / pullback_resume

---

## 2026-06-07 — テーブルdiff更新・タブUI統合

主な変更:
- diff-based DOM更新（buildRowHtml / updateRowInPlace）でフルリビルド排除
- タブ構成を17→9タブに統合
- HTMLエスケープ適用（XSS対策）
- 検索・ソート・フィルター機能統合
