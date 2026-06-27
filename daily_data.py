# -*- coding: utf-8 -*-
"""日足データ（T*.csv）の取込と、場中の当日4本値（live仮値）の永続化。

設計（ユーザー仕様）:
  ① 場中(TSE)〜引け後: 楽天RSSの当日4本値を source='live' で保存（仮の日足）。
  ② 翌朝(起動時&定期): day_stock_data/T<YYMMDD>.csv を source='csv' で上書き（確定値）。
  ③ CSV読込エラー: 上書きせず①のlive値をそのまま継続。
  ④ 朝6時の再起動: daily_ohlc は monitor.db（SQLite=ディスク永続）なので記憶は途切れない。

CSV形式（Shift-JIS, ヘッダ無し10列）:
  日付, 内部コード, 市場コード(10/11/13/30), "コード 銘柄名", 始値,高値,安値,終値, 出来高, 市場ラベル
  - 証券コードは col[3] の接頭辞（col[1]は指数のみ特殊値）
  - 同一コードが東証と名証で重複する → 出来高最大の行を採用（=東証）

集計時の「売買代金」は close×volume で概算する想定（CSVに代金列が無いため）。
live保存時は出来高を turnover(売買代金千円)×1000/close で概算し、close×volume が
場中の売買代金と整合するようにしておく。
"""
import csv
import glob
import os
import re
import sqlite3
from datetime import datetime


def ensure_daily_table(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS daily_ohlc (
            date TEXT NOT NULL,
            code TEXT NOT NULL,
            open REAL, high REAL, low REAL, close REAL, volume REAL,
            source TEXT NOT NULL DEFAULT 'csv',
            PRIMARY KEY (date, code)
        );
        CREATE INDEX IF NOT EXISTS idx_daily_code ON daily_ohlc(code, date);
        CREATE INDEX IF NOT EXISTS idx_daily_date ON daily_ohlc(date);
        """
    )


def _norm_date(s: str) -> str | None:
    try:
        return datetime.strptime((s or "").strip(), "%Y/%m/%d").strftime("%Y-%m-%d")
    except ValueError:
        return None


def _f(v) -> float | None:
    try:
        return float(str(v).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def parse_csv(path: str) -> tuple[str | None, dict[str, tuple]]:
    """CSV1ファイルをパース。重複コードは出来高最大を採用。

    returns (date_str, {code: (open, high, low, close, volume)})
    """
    best: dict[str, tuple] = {}  # code -> (volume, o, h, l, c)
    date_str: str | None = None
    with open(path, encoding="shift_jis", errors="replace", newline="") as f:
        for r in csv.reader(f):
            if len(r) < 9:
                continue
            d = _norm_date(r[0])
            if not d:
                continue
            date_str = d
            tok = (r[3] or "").split()
            if not tok:
                continue
            code = tok[0]
            o, h, l, c, vol = _f(r[4]), _f(r[5]), _f(r[6]), _f(r[7]), _f(r[8])
            if c is None or vol is None:
                continue
            if code not in best or vol > best[code][0]:
                best[code] = (vol, o, h, l, c)
    ohlc = {code: (o, h, l, c, vol) for code, (vol, o, h, l, c) in best.items()}
    return date_str, ohlc


def import_csv_file(con: sqlite3.Connection, path: str) -> tuple[int, str | None]:
    """1ファイルを source='csv' で取込（liveより優先＝無条件上書き）。"""
    date_str, ohlc = parse_csv(path)
    if not date_str or not ohlc:
        return 0, None
    rows = [
        (date_str, code, o, h, l, c, vol)
        for code, (o, h, l, c, vol) in ohlc.items()
    ]
    con.executemany(
        """
        INSERT INTO daily_ohlc (date, code, open, high, low, close, volume, source)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'csv')
        ON CONFLICT(date, code) DO UPDATE SET
            open=excluded.open, high=excluded.high, low=excluded.low,
            close=excluded.close, volume=excluded.volume, source='csv'
        """,
        rows,
    )
    con.commit()
    return len(rows), date_str


def import_new_csvs(con: sqlite3.Connection, folder: str) -> list[tuple[str, int]]:
    """フォルダ内CSVのうち、まだ source='csv' で取込んでいない日付を取り込む。

    ファイル名 T<YYMMDD>.csv から日付を推定し、既取込ならファイルを開かずskip（高速）。
    returns [(date, 行数), ...]
    """
    ensure_daily_table(con)
    existing = {
        r[0] for r in con.execute("SELECT DISTINCT date FROM daily_ohlc WHERE source='csv'")
    }
    imported: list[tuple[str, int]] = []
    for path in sorted(glob.glob(os.path.join(folder, "T*.csv"))):
        m = re.search(r"T(\d{6})\.csv$", os.path.basename(path))
        if m:
            yymmdd = m.group(1)
            guess = f"20{yymmdd[:2]}-{yymmdd[2:4]}-{yymmdd[4:6]}"
            if guess in existing:
                continue
        try:
            n, d = import_csv_file(con, path)
        except Exception:
            # ③ 破損等は無視（liveがあればliveを継続）
            continue
        if d:
            imported.append((d, n))
            existing.add(d)
    return imported


def save_live_ohlc(con: sqlite3.Connection, stocks: list[dict], date_str: str) -> int:
    """場中の楽天RSS四本値を source='live' で保存。csv確定分は上書きしない。

    出来高は turnover(売買代金千円)×1000/close で概算（close×volume が場中の代金と整合）。
    """
    ensure_daily_table(con)
    rows = []
    for s in stocks:
        code = s.get("code")
        close = s.get("price")
        if not code or not close:
            continue
        turnover = s.get("turnover")  # 千円
        volume = (turnover * 1000.0 / close) if (turnover and close) else None
        rows.append((
            date_str, code, s.get("open"), s.get("high"), s.get("low"), close, volume,
        ))
    if not rows:
        return 0
    con.executemany(
        """
        INSERT INTO daily_ohlc (date, code, open, high, low, close, volume, source)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'live')
        ON CONFLICT(date, code) DO UPDATE SET
            open=excluded.open, high=excluded.high, low=excluded.low,
            close=excluded.close, volume=excluded.volume
        WHERE daily_ohlc.source != 'csv'
        """,
        rows,
    )
    con.commit()
    return len(rows)
