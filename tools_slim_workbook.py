# -*- coding: utf-8 -*-
"""DBシートから派生計算列を削除し、Configに注目タブ用キーを追加する（実行前にバックアップ）"""
import sys
import io
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

import win32com.client

DERIVED_HEADERS = [
    "特", "ボラ", "H差分", "L差分", "始値比率", "前日比率",
    "VWAP乖離率", "GU/GD率", "暫定気配",
    "YH差分", "LH差分", "YL差分", "LL差分",
]

CONFIG_ROWS = [
    ("attention_rvol", "注目:RVOL倍率", 2, "倍",
     "売買代金が過去日同時刻平均のこの倍率以上で注目タブに表示"),
    ("attention_turnover_oku", "注目:最低売買代金", 10, "億円",
     "この売買代金（億円）以上を注目タブの対象にする"),
    ("attention_high_gap_pct", "注目:高値接近", 3, "%",
     "年初来/上場来高値までこの%以内の銘柄も注目タブに表示"),
]

excel = win32com.client.GetActiveObject("Excel.Application")
wb = None
for b in excel.Workbooks:
    if "軽量版" in b.Name and "BU" not in b.Name:
        wb = b
        break
if wb is None:
    print("対象ブックが見つかりません")
    sys.exit(1)

# 1) バックアップ
stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
backup_path = rf"C:\Users\Kaz\PycharmProjects\kabu_rakuten_rss\楽天RSS｜株式銘柄監視用｜軽量版_before_slim_{stamp}.xlsm"
wb.SaveCopyAs(backup_path)
print("バックアップ作成:", backup_path)

prev_calc = excel.Calculation
prev_screen = excel.ScreenUpdating
excel.Calculation = -4135  # xlCalculationManual
excel.ScreenUpdating = False
try:
    ws = wb.Worksheets("DB")
    tables = [ws.ListObjects(i + 1) for i in range(ws.ListObjects.Count)]
    print("DBシートのテーブル:", [t.Name for t in tables])

    deleted = []
    if tables:
        table = tables[0]
        for header in DERIVED_HEADERS:
            try:
                table.ListColumns(header).Delete()
                deleted.append(header)
            except Exception as exc:
                print(f"  列「{header}」削除スキップ: {exc}")
    else:
        # テーブルでない場合はヘッダー行から列番号を逆順で削除
        used_cols = ws.UsedRange.Columns.Count
        headers = ws.Range(ws.Cells(1, 1), ws.Cells(1, used_cols)).Value[0]
        targets = [(i + 1, h) for i, h in enumerate(headers) if h in DERIVED_HEADERS]
        for col_index, header in sorted(targets, reverse=True):
            ws.Columns(col_index).Delete()
            deleted.append(header)
    print("削除した列:", deleted)

    # 2) Configシートへ3行追加（既存キーがあればスキップ）
    cfg = ws = wb.Worksheets("Config")
    existing = set()
    r = 2
    while cfg.Cells(r, 1).Value not in (None, ""):
        existing.add(str(cfg.Cells(r, 1).Value).strip())
        r += 1
    for key, label, value, unit, desc in CONFIG_ROWS:
        if key in existing:
            print(f"Config「{key}」は既存のためスキップ")
            continue
        cfg.Cells(r, 1).Value = key
        cfg.Cells(r, 2).Value = label
        cfg.Cells(r, 3).Value = value
        cfg.Cells(r, 4).Value = unit
        cfg.Cells(r, 5).Value = desc
        print(f"Config追加: 行{r} {key}={value}{unit}")
        r += 1
finally:
    excel.Calculation = prev_calc
    excel.ScreenUpdating = prev_screen

wb.Save()
print("保存完了")

# 3) 結果確認: DBシートの新ヘッダーを出力
db = wb.Worksheets("DB")
used_cols = db.UsedRange.Columns.Count
headers = db.Range(db.Cells(1, 1), db.Cells(1, used_cols)).Value[0]
print("\n新しいDBヘッダー:")
for i, h in enumerate(headers, 1):
    n, col_letter = i, ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        col_letter = chr(65 + rem) + col_letter
    print(f"  {col_letter:>3}: {h}")

# Code列の銘柄数
codes = [db.Cells(row, 2).Value for row in range(2, 102)]
codes = [c for c in codes if c not in (None, "")]
print(f"\n登録銘柄数: {len(codes)} → {codes}")
