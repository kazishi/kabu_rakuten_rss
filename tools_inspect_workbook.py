# -*- coding: utf-8 -*-
"""起動中のExcelからワークブック構成を読み取り専用で調査する"""
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

import win32com.client

excel = win32com.client.GetActiveObject("Excel.Application")
wb = None
for b in excel.Workbooks:
    if "軽量版" in b.Name and "BU" not in b.Name:
        wb = b
        break
if wb is None:
    print("対象ブックが見つかりません。開いているブック:")
    for b in excel.Workbooks:
        print(" -", b.Name)
    sys.exit(1)

print("ブック:", wb.Name)
print("シート一覧:")
for ws in wb.Worksheets:
    used = ws.UsedRange
    print(f"  - {ws.Name}: UsedRange={used.Address}")

for sheet_name in ["DB", "Config", "銘柄リスト"]:
    try:
        ws = wb.Worksheets(sheet_name)
    except Exception:
        print(f"\n=== {sheet_name}: シートなし ===")
        continue
    used = ws.UsedRange
    rows = used.Rows.Count
    cols = used.Columns.Count
    print(f"\n=== {sheet_name} ({used.Address}, {rows}行 x {cols}列) ===")
    # ヘッダー行
    header = ws.Range(ws.Cells(1, 1), ws.Cells(1, cols)).Value
    if header:
        header = header[0]
        for i, h in enumerate(header, 1):
            col_letter = ""
            n = i
            while n > 0:
                n, r = divmod(n - 1, 26)
                col_letter = chr(65 + r) + col_letter
            # 2行目の数式と値
            cell2 = ws.Cells(2, i)
            formula = cell2.Formula
            value = cell2.Value
            fstr = str(formula)[:90] if formula else ""
            vstr = str(value)[:30] if value is not None else ""
            print(f"  {col_letter:>3} | {str(h)[:24]:<24} | f: {fstr:<90} | v: {vstr}")
    # データ行数（A列で数える）
    last_row = ws.Cells(ws.Rows.Count, 1).End(-4162).Row  # xlUp
    print(f"  A列最終行: {last_row}")
