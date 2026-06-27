# -*- coding: utf-8 -*-
"""物語系テーマの構成銘柄をWebソース由来のコアで投入する（ハイブリッドの自動側）。

source='カリン手動web' で stock_themes へ追加。
全候補は「検証ゲート」を通す:
  - コードが stocks に実在するか
  - シートの銘柄名と候補名がゆるく一致するか（不一致は投入せず flag 出力）
出力: classification/theme_membership_edit.csv（人手キュレーション用）

build_classification.py を実行した後に走らせること（再実行は安全: 同sourceを入れ替え）。
"""
import csv
import os
import re
import sqlite3

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "classification.db")
EXPORT = os.path.join(HERE, "theme_membership_edit.csv")
SRC = "カリン手動web"
DEFAULT_ROLE = "related"
DEFAULT_WEIGHT = 0.4
DEFAULT_CONFIDENCE = "curated"

# 明らかにコアな (code, theme_id) は weight を上げる
OVERRIDES: dict[tuple[str, str], tuple[float, str]] = {
    ("285A",  "memory"):         (1.0, "core"),
    ("3778",  "datacenter"):     (1.0, "core"),
    ("6954",  "physical_ai"):    (0.7, "strong"),
    ("6861",  "physical_ai"):    (0.7, "strong"),
    ("6506",  "physical_ai"):    (0.7, "strong"),
    ("4704",  "cyber_security"): (1.0, "core"),
    ("4417",  "cyber_security"): (1.0, "core"),
    ("4398",  "cyber_security"): (1.0, "core"),
    ("4493",  "cyber_security"): (1.0, "core"),
    ("4494",  "cyber_security"): (1.0, "core"),
    ("4082",  "rare_earth"):     (1.0, "core"),
    ("5724",  "rare_earth"):     (1.0, "core"),
    ("7011",  "defense"):        (0.7, "strong"),
    ("7013",  "defense"):        (0.7, "strong"),
    ("6503",  "defense"):        (0.7, "strong"),
    ("5631",  "defense"):        (1.0, "core"),
    ("6208",  "defense"):        (1.0, "core"),
    ("4274",  "defense"):        (1.0, "core"),
    ("7011",  "space"):          (0.7, "strong"),
    ("7012",  "space"):          (0.7, "strong"),
    ("7013",  "space"):          (0.7, "strong"),
    ("7711",  "fusion"):         (1.0, "core"),
    ("5803",  "fusion"):         (0.7, "strong"),
    ("4204",  "perovskite"):     (1.0, "core"),
    ("4107",  "perovskite"):     (1.0, "core"),
}

# Webソース（株探/みんかぶ/各証券/かりん 等の検索結果）から拾った (コード, 名称ヒント)
SEED = {
    "memory": [("285A", "キオクシア"), ("6871", "日本マイクロニクス"), ("7731", "ニコン")],
    "datacenter": [
        ("9433", "KDDI"), ("6701", "日本電気"), ("6702", "富士通"), ("6501", "日立"),
        ("4307", "野村総研"), ("3778", "さくらインターネット"), ("1951", "エクシオ"),
        ("1801", "大成建設"),
    ],
    "physical_ai": [
        ("6501", "日立"), ("6702", "富士通"), ("6954", "ファナック"), ("6861", "キーエンス"),
        ("6506", "安川電機"), ("6981", "村田"), ("6758", "ソニー"), ("6433", "ヒーハイスト"),
        ("3741", "セック"), ("3132", "マクニカ"),
    ],
    "gemini": [
        ("6702", "富士通"), ("6701", "日本電気"), ("6954", "ファナック"), ("9432", "NTT"),
        ("9613", "ＮＴＴデータ"), ("6506", "安川電機"), ("7046", "ＴＤＳＥ"),
    ],
    "rare_earth": [
        ("6330", "東洋エンジニアリング"), ("6269", "三井海洋開発"), ("1662", "石油資源開発"),
        ("7011", "三菱重工"), ("8015", "豊田通商"), ("2768", "双日"), ("4082", "第一稀元素"),
        ("5711", "三菱マテリアル"), ("4063", "信越化学"), ("5724", "アサカ理研"),
        ("3556", "リネットジャパン"), ("5713", "住友金属鉱山"), ("5714", "ＤＯＷＡ"),
        ("7456", "松田産業"),
    ],
    "space": [("7011", "三菱重工"), ("7012", "川崎重工"), ("7013", "ＩＨＩ"), ("6503", "三菱電機")],
    "cyber_security": [
        ("6701", "日本電気"), ("6702", "富士通"), ("9432", "NTT"), ("4704", "トレンドマイクロ"),
        ("4417", "グローバルセキュリティ"), ("4398", "ブロードバンドセキュリティ"),
        ("4709", "ＩＤ"), ("4493", "サイバーセキュリティクラウド"), ("4475", "ＨＥＮＮＧＥ"),
        ("4494", "バリオセキュア"), ("4258", "網屋"),
    ],
    "perovskite": [
        ("4204", "積水化学"), ("6752", "パナソニック"), ("7751", "キヤノン"), ("7752", "リコー"),
        ("4107", "伊勢化学"), ("6245", "ヒラノテクシード"), ("4963", "星光ＰＭＣ"),
        ("4362", "日本精化"), ("6804", "ホシデン"),
    ],
    "fusion": [
        ("6501", "日立"), ("7011", "三菱重工"), ("7711", "助川電気"), ("5541", "大平洋金属"),
        ("4205", "日本ゼオン"), ("5803", "フジクラ"), ("5801", "古河電気"),
    ],
    "defense": [
        ("7013", "ＩＨＩ"), ("6503", "三菱電機"), ("6701", "日本電気"), ("4274", "細谷火工"),
        ("5631", "日本製鋼所"), ("6208", "石川製作所"), ("7721", "東京計器"), ("8226", "理経"),
    ],
}

KANA = str.maketrans("ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ", "ABCDEFGHIJKLMNOPQRSTUVWXYZ")


def norm(s):
    s = (s or "").translate(KANA).upper()
    return re.sub(r"[\s　・（）\(\)ＨＤホールディングスグループ]", "", s)


def name_ok(hint, sheet_name):
    a, b = norm(hint), norm(sheet_name)
    if not a or not b:
        return False
    core = a[:2]
    return core in b or b[:2] in a or a in b or b in a


def main():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    stocks = {r["code"]: r["name"] for r in con.execute("SELECT code,name FROM stocks")}
    tnames = {r["theme_id"]: r["name"] for r in con.execute("SELECT theme_id,name FROM themes")}

    # 既存の web seed を一旦消して入れ直し（冪等）
    con.execute("DELETE FROM stock_themes WHERE source=?", (SRC,))

    inserted, notfound, flagged = [], [], []
    for theme_id, items in SEED.items():
        for code, hint in items:
            if code not in stocks:
                notfound.append((theme_id, code, hint))
                continue
            if not name_ok(hint, stocks[code]):
                flagged.append((theme_id, code, hint, stocks[code]))
                continue
            w, role = OVERRIDES.get((code, theme_id), (DEFAULT_WEIGHT, DEFAULT_ROLE))
            inserted.append((code, theme_id, w, role, DEFAULT_CONFIDENCE, SRC))
    con.executemany("INSERT OR IGNORE INTO stock_themes VALUES (?,?,?,?,?,?)", inserted)
    # 既に業種自動で入っている銘柄にも、人手overrideのweight/roleを反映する。
    con.executemany(
        """
        UPDATE stock_themes
        SET weight = ?, role = ?, confidence = ?
        WHERE code = ? AND theme_id = ?
        """,
        [(w, role, DEFAULT_CONFIDENCE, code, theme_id)
         for (code, theme_id), (w, role) in OVERRIDES.items()],
    )
    con.commit()

    print(f"投入(検証OK): {len(inserted)}  / 名称不一致でスキップ: {len(flagged)}  / コード不在: {len(notfound)}")
    if flagged:
        print("\n[名称不一致=要確認・未投入]")
        for t, c, h, sn in flagged:
            print(f"  {t:14} {c:>5}  hint='{h}'  sheet='{sn}'")
    if notfound:
        print("\n[コードがシートに無い=未投入]")
        for t, c, h in notfound:
            print(f"  {t:14} {c:>5}  '{h}'")

    print("\n=== テーマ別 合計件数（業種自動+web手動）===")
    for r in con.execute(
        """SELECT t.theme_id,t.name,t.category,COUNT(st.code) n
           FROM themes t LEFT JOIN stock_themes st ON st.theme_id=t.theme_id
           GROUP BY t.theme_id ORDER BY t.category,t.name"""
    ):
        print(f"  {r['n']:4d}  [{r['category']}] {r['name']}")

    # 編集用エクスポート
    rows = con.execute(
        """SELECT st.code, s.name, s.big_cat, s.mid_cat, s.small_cat,
                  st.theme_id, t.name theme_name,
                  st.weight, st.role, st.confidence, st.source
           FROM stock_themes st
           JOIN stocks s ON s.code=st.code
           JOIN themes t ON t.theme_id=st.theme_id
           ORDER BY t.category, t.name, st.code"""
    ).fetchall()
    with open(EXPORT, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["code", "name", "big_cat", "mid_cat", "small_cat",
                    "theme_id", "theme_name", "weight", "role", "confidence", "source"])
        for r in rows:
            w.writerow([r[k] for k in r.keys()])
    print(f"\n編集用CSV: {EXPORT}  ({len(rows)}行)")
    con.close()


if __name__ == "__main__":
    main()
