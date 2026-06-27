# Claude Code 引継書: セクター/テーマ資金フロー MVP

## 目的

場中のデイトレ/スイング判断を強化するため、監視銘柄に対して「どのテーマに資金が入っているか/抜けているか」を可視化する。

最初のMVPは日足指標までは入れず、既存の場中データと `classification/classification.db` を使ってテーマ別資金フローを表示する。

## 現在ある資産

- `app.py`: Flaskアプリ本体。Excel RSSから監視銘柄を取得し、`state.stocks` に保持している。
- `static/app.js`: 現在の分析タブで個別銘柄の資金フローを計算/表示している。
- `monitor.db`: 場中価格履歴、アラート、ブレイク等のDB。
- `classification/classification.db`: NEEDS分類 + テーマ分類DB。
- `classification/build_classification.py`: `銘柄分類.xlsx` から分類DBを再生成するスクリプト。
- `classification/seed_themes.py`: Web由来のテーマ銘柄を投入するスクリプト。
- `classification/theme_membership_edit.csv`: テーマ所属の人手確認用CSV。
- `STRATEGY.md`: 先行検討メモ。文字化けして見える場合はUTF-8で読むこと。

`classification.db` の現状:

```sql
stocks(code PK, name, market, big_cat, mid_cat, small_cat)
themes(theme_id PK, name, category, source, note)
stock_themes(code, theme_id, source)
```

現状件数:

```text
stocks: 3707
themes: 19
stock_themes: 542
```

## 採用する設計方針

Claude Code側の `STRATEGY.md` 案をMVPの土台として採用する。ただし、多重テーマ銘柄の扱いは補強する。

重要な方針:

- NEEDS大分類/中分類/小分類は「企業の本籍」として使う。
- テーマ分類は「相場での呼ばれ方」として使う。
- `big_cat` 集計は非重複。保存則が成立する参考ビュー。
- `theme` 集計は多重ラベル。1銘柄が複数テーマに寄与してよい。
- ただしテーマ寄与は原則 `weight` で加重する。

## 必須修正: stock_themes に重みを持たせる

現状の `stock_themes` は `code, theme_id, source` のみで、三菱重工のような銘柄が複数テーマに丸ごと重複計上される。

これは「テーマ反応の確認」には使えるが、「資金流入出の比較」では過大表示になりやすい。

`stock_themes` は以下へ拡張する。

```sql
stock_themes(
  code TEXT,
  theme_id TEXT,
  weight REAL DEFAULT 1.0,
  role TEXT DEFAULT 'core',
  confidence TEXT DEFAULT 'auto',
  source TEXT,
  PRIMARY KEY (code, theme_id)
)
```

推奨する `role -> weight` の初期変換:

```text
core    -> 1.00
strong  -> 0.70
related -> 0.40
watch   -> 0.20
```

意味:

```text
core:
  そのテーマそのもの。テーマ買いの主役になりやすい。

strong:
  事業寄与や市場認知が強く、テーマ物色でかなり見られる。

related:
  関連性はあるが、本業全体では一部。連想買い枠。

watch:
  材料、思惑、一部製品、過去テーマ。過大評価しない。
```

例:

```text
5803 フジクラ
  densen       1.00 core
  datacenter   0.70 strong
  defense      0.20 watch

6981 村田製作所
  mlcc         1.00 core
  semiconductor 0.40 related

7011 三菱重工業
  defense      0.85 strong
  space        0.55 related
  fusion       0.35 related
  rare_earth   0.15 watch
```

実装上はまず `role` を入れ、`weight` は自動補完でよい。既存の自動付与分は一旦 `core/1.0`、Web手動分は一旦 `related/0.4` でもよい。違和感が出る銘柄だけ後から編集する。

## 重みの正規化について

銘柄ごとのテーマ重み合計を 1.0 に正規化しない。

理由:

- 三菱重工、川崎重工、フジクラのように、本当に複数テーマで物色される銘柄は複数テーマに影響してよい。
- ただし薄い関連テーマは `0.2` や `0.4` に落として、過大表示を抑える。
- UIには「テーマ集計はマルチテーマ銘柄を重み付きで重複計上」と注記する。

## 集計スコアの注意点

既存の資金フロー定義:

```text
flow_day  = turnover * (change_pct * 0.5 + from_open * 0.3 + vwap_gap * 0.2)
flow_15m  = turnover_delta_15m * change_15m
flow_5m   = turnover_delta_5m  * change_5m
```

重要:

`delta_15m_sum` だけをテーマの流入/流出軸にしないこと。

`turnover_delta_15m` は売買代金増分であり、方向性の符号を表しにくい。テーマの流入/流出を見る主軸は `flow_15m_sum` にする。

推奨するテーマ集計項目:

```text
stock_count:
  監視銘柄中の該当数

turnover_sum_raw:
  該当銘柄の売買代金を単純合計

turnover_sum_weighted:
  turnover * weight の合計

flow_day_sum_raw:
  flow_day の単純合計

flow_day_sum_weighted:
  flow_day * weight の合計

flow_15m_sum_raw:
  flow_15m の単純合計

flow_15m_sum_weighted:
  flow_15m * weight の合計

flow_5m_sum_weighted:
  flow_5m * weight の合計

breadth:
  上昇銘柄数 / 該当銘柄数

weighted_breadth:
  change_pct > 0 の weight 合計 / weight 合計

leaders:
  weighted flow_15m または weighted flow_day の上位銘柄

laggards:
  weighted flow_15m または weighted flow_day の下位銘柄
```

UIのランキングやヒート表示は、基本的に `*_weighted` を使う。

## 実装対象ファイル

### 1. `classification/build_classification.py`

`stock_themes` スキーマを拡張する。

既存の自動付与では以下を初期値にする。

```text
role = 'core'
weight = 1.0
confidence = 'auto'
```

### 2. `classification/seed_themes.py`

Web手動投入分に `role/weight/confidence` を付ける。

初期値は以下でよい。

```text
role = 'related'
weight = 0.4
confidence = 'curated'
```

明らかにコアなものだけ個別に `core/strong` へ上げる。

### 3. `classification/theme_membership_edit.csv`

エクスポート列に `role, weight, confidence` を追加する。

人間が後から調整しやすいようにする。

### 4. `classification/lookup.py` 新規

DBロードとテーマ集計を `app.py` から分離する。

必要関数:

```python
def load_classification(db_path: str) -> dict:
    """起動時に1回だけDBを読み、メモリ上のdictにする。"""

def compute_sector_flow(stocks: list[dict], classif: dict) -> dict:
    """監視銘柄スナップショットからテーマ/NEEDS分類別の資金フローを集計する。"""
```

返却イメージ:

```python
{
    "themes": {
        "defense": {
            "name": "防衛",
            "category": "防衛・国策",
            "stock_count": 8,
            "turnover_sum_weighted": 123456.0,
            "flow_day_sum_weighted": 9876.0,
            "flow_15m_sum_weighted": 1234.0,
            "flow_5m_sum_weighted": 321.0,
            "breadth": 0.75,
            "weighted_breadth": 0.68,
            "leaders": [...],
            "laggards": [...]
        }
    },
    "big_cats": {
        "製造業": {
            "turnover_sum": 123456.0,
            "flow_day_sum": 9876.0,
            "stock_count": 40
        }
    },
    "sample_note": "監視銘柄サンプル。全市場集計ではありません。テーマ集計はマルチテーマ銘柄を重み付きで重複計上します。"
}
```

### 5. `app.py`

起動時に分類DBをロードする。

```python
_classification = load_classification(CLASSIFICATION_DB_PATH)
```

API追加:

```python
@app.get("/api/sector_flow")
def api_sector_flow():
    snap = state.snapshot()
    data = compute_sector_flow(snap["stocks"], _classification)
    return jsonify(data)
```

集計はGETごとに実行でよい。監視100銘柄程度なら十分軽い。

### 6. `templates/index.html`

`SECTOR` または `テーマ` タブを追加する。

MVPは横棒グラフビューのみでよい。

### 7. `static/app.js`

`/api/sector_flow` を取得して表示する。

MVP表示:

- テーマ名
- 15分フロー横棒: `flow_15m_sum_weighted`
- 日中フロー補助値: `flow_day_sum_weighted`
- breadth / weighted_breadth
- leaders 上位3件

### 8. `static/style.css`

横棒グラフ、流入/流出カラー、テーマリストのスタイルを追加する。

## MVP画面

最初に作る画面は「資金ローテーション横棒」だけでよい。

```text
テーマ名        15分フロー                     breadth   leaders
防衛            ████████████ 流入              78%       7011, 6208, 7013
半導体          ██████ 流入                    45%       6857, 8035, 6146
造船            ███ 流出                       33%       7012, 7003
```

バー軸:

```text
flow_15m_sum_weighted
```

色:

```text
positive -> 流入
negative -> 流出
```

ソート:

```text
abs(flow_15m_sum_weighted) desc
```

## 今回はやらないこと

以下は後工程でよい。

- 日足CSVのインポート。
- RSI/ATR/OBV/CMFなどのテクニカル指標。
- テーマヒートマップのタイル面積表示。
- 過去テーマ連動率による weight 自動補正。
- 全市場ベースのセクター資金フロー。

日足連携の最終構想:

```text
テーマ点灯 = 場中の weighted flow_15m が強いテーマ
銘柄候補   = 点灯テーマ内で日足需給スコアが高い銘柄
```

## 実装時の落とし穴

- `delta_15m_sum` を流入/流出の主軸にしない。方向性は `flow_15m` を使う。
- 多重テーマを完全に排除しない。複数テーマにまたがる銘柄はこのツールの重要対象。
- 一方で、全テーマに `1.0` で重複計上しない。`weight` を使う。
- `big_cat` と `theme` の集計を混ぜない。`big_cat` は保存則あり、`theme` は重複あり。
- 分類DBは場中不変として起動時ロードでよい。
- SQLiteコネクションを場中に開きっぱなしにしない。ロード後はdictで持つ。
- UIには「監視銘柄サンプルであり全市場集計ではない」と明記する。

## 完了条件

MVP完了の条件:

- `classification.db` に `weight/role/confidence` が入っている。
- `/api/sector_flow` がJSONを返す。
- テーマ別に `flow_15m_sum_weighted` が集計される。
- 画面にテーマ別の流入/流出横棒が表示される。
- リーダー/ラガード銘柄が最低限確認できる。
- 既存のモニター/分析タブが壊れていない。

## 後工程でCodexにレビューしてほしいポイント

- `weight` の初期値が過大/過小でないか。
- `flow_15m_sum_weighted` の定義が既存JSの資金フローと整合しているか。
- `app.py` に集計ロジックが肥大化していないか。
- UIが「全市場のセクター別資金流入」と誤解されない表現になっているか。
- leaders/laggardsが大型株に偏りすぎる場合、正規化指標を追加すべきか。

