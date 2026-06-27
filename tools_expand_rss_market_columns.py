# -*- coding: utf-8 -*-
"""DB sheet に RssMarket の追加列を差し込む。

既存の A:AB 列はそのまま維持し、AC 列以降へ将来のフィルターで使いたい
歩み・板・信用需給系の項目を追加する。
"""

from __future__ import annotations

import os
from pathlib import Path

import pythoncom
import win32com.client


ROOT = Path(__file__).resolve().parent
TARGET_NAME = "楽天RSS｜株式銘柄監視用｜軽量版.xlsm"
SHEET_NAME = "DB"
TABLE_NAME = "テーブル163"
MAX_ROWS = 101


ADDITIONAL_FIELDS: list[tuple[str, str, float]] = [
    ("現在値詳細時刻", "現在値詳細時刻", 12.0),
    ("歩み1", "歩み1", 11.0),
    ("歩み2", "歩み2", 11.0),
    ("歩み3", "歩み3", 11.0),
    ("歩み4", "歩み4", 11.0),
    ("歩み1時刻", "歩み1詳細時刻", 12.0),
    ("歩み2時刻", "歩み2詳細時刻", 12.0),
    ("歩み3時刻", "歩み3詳細時刻", 12.0),
    ("歩み4時刻", "歩み4詳細時刻", 12.0),
    ("出来高", "出来高", 11.0),
    ("前場終値", "前場終値", 11.0),
    ("前場出来高", "前場出来高", 11.0),
    ("後場始値", "後場始値", 11.0),
    ("後場高値", "後場高値", 11.0),
    ("後場安値", "後場安値", 11.0),
    ("最良買気配数量", "最良買気配数量", 13.0),
    ("最良売気配数量", "最良売気配数量", 13.0),
    ("買成行数量", "買成行数量", 11.0),
    ("売成行数量", "売成行数量", 11.0),
    ("OVER気配数量", "OVER気配数量", 11.0),
    ("UNDER気配数量", "UNDER気配数量", 11.0),
    ("貸借倍率", "貸借倍率", 11.0),
    ("逆日歩", "逆日歩", 11.0),
    ("信用倍率", "信用倍率", 11.0),
    ("信用売残", "信用売残", 11.0),
    ("信用売残前週比", "信用売残前週比", 13.0),
    ("信用買残", "信用買残", 11.0),
    ("信用買残前週比", "信用買残前週比", 13.0),
    ("回転日数", "回転日数", 11.0),
    ("最良買気配数量1", "最良買気配数量1", 13.0),
    ("最良売気配数量1", "最良売気配数量1", 13.0),
    ("最良買気配数量2", "最良買気配数量2", 13.0),
    ("最良売気配数量2", "最良売気配数量2", 13.0),
    ("最良買気配数量3", "最良買気配数量3", 13.0),
    ("最良売気配数量3", "最良売気配数量3", 13.0),
]


def find_workbook_path() -> Path:
    exact = ROOT / TARGET_NAME
    if exact.exists():
        return exact
    candidates = sorted(
        path
        for path in ROOT.glob("*.xlsm")
        if not path.name.startswith("~$")
        and "before_" not in path.name
        and "recovery" not in path.name
        and "BU" not in path.name
        and "disk_before" not in path.name
    )
    if not candidates:
        raise FileNotFoundError("対象の xlsm が見つかりません")
    return min(candidates, key=lambda p: len(p.name))


def main() -> None:
    pythoncom.CoInitialize()
    excel = None
    workbook = None
    worksheet = None
    table = None
    target_path = find_workbook_path()
    try:
        excel = win32com.client.Dispatch("Excel.Application")
        excel.Visible = False
        excel.DisplayAlerts = False

        workbook = excel.Workbooks.Open(str(target_path))
        worksheet = workbook.Worksheets(SHEET_NAME)
        table = worksheet.ListObjects(TABLE_NAME)

        start_col = table.Range.Columns.Count + 1  # AC から追記
        for offset, (header, rss_field, width) in enumerate(ADDITIONAL_FIELDS):
            col = start_col + offset
            worksheet.Cells(1, col).Value = header
            rng = worksheet.Range(worksheet.Cells(2, col), worksheet.Cells(MAX_ROWS, col))
            rng.FormulaR1C1 = f'=RssMarket(RC2,"{rss_field}")'
            worksheet.Columns(col).ColumnWidth = width

        last_col = start_col + len(ADDITIONAL_FIELDS) - 1
        table.Resize(worksheet.Range(worksheet.Cells(1, 1), worksheet.Cells(MAX_ROWS, last_col)))
        workbook.Save()
        print(f"Updated workbook: {target_path.name}")
        print(f"Extended table to column {last_col}")
    finally:
        if workbook is not None:
            workbook.Close(False)
        if excel is not None:
            excel.Quit()


if __name__ == "__main__":
    main()
