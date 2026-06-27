# -*- coding: utf-8 -*-
"""銘柄分類.xlsx（日経NEEDS手作業シート）から分類DBを構築する。

出力: classification/classification.db (SQLite)
  stocks(code, name, market, big_cat, mid_cat, small_cat)  -- 日経NEEDS業種（1銘柄1行）
  themes(theme_id, name, category, source, note)            -- テーマ・マスター
  stock_themes(code, theme_id, source)                      -- 多重ラベル（M:N）

業種から機械的に導出できるテーマは自動付与（source='業種自動'）。
業種で表現できない物語系テーマ（メモリ/データセンター/防衛SW等）は
テーマ行だけ作成し、membershipは手動キュレーション（source='カリン手動'）で後追い。

再実行で安全に作り直せる（DROP→再生成）。手動付与したstock_themesは
source列で区別できるので、別ファイルへ退避してマージする運用を想定。
"""
import os
import sqlite3
import openpyxl

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
XLSX = os.path.join(ROOT, "銘柄分類.xlsx")
DB = os.path.join(HERE, "classification.db")


def s(v):
    if v is None:
        return None
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    t = str(v).strip()
    return t or None


def load_stocks():
    wb = openpyxl.load_workbook(XLSX, read_only=True, data_only=True)
    rows = list(wb.active.iter_rows(values_only=True))
    out = []
    for r in rows[1:]:
        code = s(r[0])
        if not code:
            continue
        out.append((code, s(r[1]), s(r[2]), s(r[3]), s(r[4]), s(r[5])))
    return out


# テーマ・マスター: (theme_id, 表示名, グループ, source)
THEMES = [
    # --- AI・半導体系 ---
    ("semiconductor", "半導体", "AI・半導体", "業種自動"),
    ("semi_equipment", "半導体製造装置", "AI・半導体", "業種自動"),
    ("mlcc", "MLCC・電子部品", "AI・半導体", "業種自動"),
    ("memory", "メモリー半導体", "AI・半導体", "カリン手動"),
    ("datacenter", "データセンター", "AI・半導体", "カリン手動"),
    ("physical_ai", "フィジカルAI・ロボット", "AI・半導体", "カリン手動"),
    ("gemini", "ジェミニ関連", "AI・半導体", "カリン手動"),
    # --- 部品・素材系 ---
    ("densen", "電線・ケーブル", "部品・素材", "業種自動"),
    ("metals", "金・銀・銅（非鉄メタル）", "部品・素材", "業種自動"),
    ("rare_earth", "レアアース", "部品・素材", "カリン手動"),
    # --- 防衛・国策系 ---
    ("defense", "防衛", "防衛・国策", "業種自動"),  # 部分自動（銃器・兵器/総合重機）
    ("cyber_security", "サイバーセキュリティ", "防衛・国策", "カリン手動"),
    ("space", "宇宙", "防衛・国策", "カリン手動"),
    ("shipbuilding", "造船", "防衛・国策", "業種自動"),
    ("aviation", "空運・航空", "防衛・国策", "業種自動"),
    ("infrastructure", "国土強靭化・建設", "防衛・国策", "業種自動"),
    # --- エネルギー系 ---
    ("perovskite", "ペロブスカイト太陽電池", "エネルギー", "カリン手動"),
    ("fusion", "核融合発電", "エネルギー", "カリン手動"),
    # --- 金融系 ---
    ("bank_rate", "金利上昇メリット（銀行）", "金融", "業種自動"),
]


# 業種自動マッピング規則:  theme_id -> (level, set_of_values)
#   level='small' は 細分類、'mid' は 中分類 で一致判定
AUTO = {
    "semiconductor": ("small", {
        "半導体（集積回路・半導体素子）", "シリコン・シリコンウエハー", "フォトマスク",
        "半導体・液晶製造装置", "電子部品製造装置", "電気機能材料", "ＬＥＤ・半導体レーザー",
    }),
    "semi_equipment": ("small", {
        "半導体・液晶製造装置", "電子部品製造装置", "太陽電池製造装置",
    }),
    "mlcc": ("small", {"電子受動部品（コンデンサー・コイル）"}),
    "densen": ("small", {"電線・ケーブル", "鋼線・ワイヤー"}),
    "metals": ("small", {"非鉄金属開発・精錬", "貴金属回収・精錬", "伸銅・銅鋼"}),
    "defense": ("small", {"銃器・兵器・軍用品", "総合重機"}),
    "shipbuilding": ("mid", {"造船"}),
    "aviation": ("mid", {"空運"}),
    "infrastructure": ("mid", {"建設・土木", "建設資材・設備"}),
    "bank_rate": ("mid", {"銀行"}),
}


def build():
    stocks = load_stocks()
    if os.path.exists(DB):
        os.remove(DB)
    con = sqlite3.connect(DB)
    con.executescript(
        """
        CREATE TABLE stocks (
            code TEXT PRIMARY KEY, name TEXT, market TEXT,
            big_cat TEXT, mid_cat TEXT, small_cat TEXT
        );
        CREATE TABLE themes (
            theme_id TEXT PRIMARY KEY, name TEXT, category TEXT,
            source TEXT, note TEXT
        );
        CREATE TABLE stock_themes (
            code TEXT, theme_id TEXT,
            weight REAL NOT NULL DEFAULT 1.0,
            role TEXT NOT NULL DEFAULT 'core',
            confidence TEXT NOT NULL DEFAULT 'auto',
            source TEXT,
            PRIMARY KEY (code, theme_id)
        );
        CREATE INDEX idx_st_theme ON stock_themes(theme_id);
        CREATE INDEX idx_stocks_mid ON stocks(mid_cat);
        CREATE INDEX idx_stocks_small ON stocks(small_cat);
        """
    )
    con.executemany("INSERT INTO stocks VALUES (?,?,?,?,?,?)", stocks)
    con.executemany(
        "INSERT INTO themes (theme_id,name,category,source) VALUES (?,?,?,?)", THEMES
    )

    # 業種自動マッピング
    links = []
    for code, name, market, big, mid, small in stocks:
        for theme_id, (level, values) in AUTO.items():
            key = small if level == "small" else mid
            if key in values:
                src = f"業種:{key}"
                links.append((code, theme_id, 1.0, "core", "auto", src))
    con.executemany(
        "INSERT OR IGNORE INTO stock_themes VALUES (?,?,?,?,?,?)", links
    )
    con.commit()

    # サマリ
    print(f"stocks: {len(stocks)}")
    print(f"themes: {len(THEMES)}")
    print(f"auto stock_themes: {len(links)}")
    print("\n=== テーマ別 自動付与件数 ===")
    for tid, name, cat, source in [(t[0], t[1], t[2], t[3]) for t in THEMES]:
        n = con.execute(
            "SELECT COUNT(*) FROM stock_themes WHERE theme_id=?", (tid,)
        ).fetchone()[0]
        flag = "" if source == "業種自動" else "  (要キュレーション)"
        print(f"  {n:4d}  {name}{flag}")
    con.close()
    print(f"\nDB: {DB}")


if __name__ == "__main__":
    build()
