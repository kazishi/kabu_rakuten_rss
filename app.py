from __future__ import annotations

import atexit
import os
import socket
import sqlite3
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pythoncom
import pywintypes
import win32com.client
from flask import Flask, jsonify, render_template, request

from classification.lookup import compute_sector_flow, compute_sector_flow_daily, load_classification

import daily_data


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "monitor.db"
CLASSIFICATION_DB_PATH = BASE_DIR / "classification" / "classification.db"
WORKBOOK_NAME = os.environ.get(
    "RAKUTEN_RSS_WORKBOOK", "楽天RSS｜株式銘柄監視用｜軽量版.xlsm"
)
SHEET_NAME = os.environ.get("RAKUTEN_RSS_SHEET", "DB")
CONFIG_SHEET = "Config"
STATIC_SHEET = os.environ.get("RAKUTEN_RSS_STATIC_SHEET", "銘柄リスト")
DATA_RANGE = os.environ.get("RAKUTEN_RSS_DATA_RANGE", "A1:BK101")
CONFIG_RANGE = os.environ.get("RAKUTEN_RSS_CONFIG_RANGE", "A1:E30")
STATIC_RANGE = os.environ.get("RAKUTEN_RSS_STATIC_RANGE", "A1:Q101")
POLL_SECONDS = float(os.environ.get("RAKUTEN_RSS_POLL_SECONDS", "5"))
CONFIG_POLL_SECONDS = float(os.environ.get("RAKUTEN_RSS_CONFIG_POLL_SECONDS", "60"))
HISTORY_SECONDS = float(os.environ.get("RAKUTEN_RSS_HISTORY_SECONDS", "10"))
PORT = int(os.environ.get("RAKUTEN_RSS_PORT", "8765"))
MAX_RETRY_SECONDS = 30.0

RVOL_LOOKBACK_DAYS = int(os.environ.get("RAKUTEN_RSS_RVOL_DAYS", "10"))
RVOL_REFRESH_SECONDS = 300.0
FLOW_WINDOW_MINUTES = 15
FLOW_WINDOW_MINUTES_5 = 5
FLOW_WINDOW_MINUTES_60 = 60
FLOW_REFRESH_SECONDS = 30.0
PRUNE_INTERVAL_SECONDS = 1800.0
BREAK_REARM_GAP = 0.001  # 高安値から0.1%離れたら次のブレイク検知を再アーム

DAY_DATA_DIR = BASE_DIR / "day_stock_data"
DAILY_IMPORT_SECONDS = 600.0   # 日足CSVフォルダのscan間隔（新着があれば確定値で取込）
LIVE_SAVE_SECONDS = 300.0      # 当日4本値のlive仮値を保存する間隔（TSEのみ）

RPC_CALL_REJECTED = -2147418111
RPC_SERVER_UNAVAILABLE = -2147023174

EXCEL_EPOCH = datetime(1899, 12, 30)

# DBシートはRSS生データのみ。派生指標はすべてPython側で計算する。
FIELD_MAP = {
    "メモ": "memo", "Code": "code", "銘柄名": "name", "市場": "market",
    "貸借": "margin_type", "↑↓": "tick",
    "現在値": "price", "売買代金(千円）": "turnover",
    "VWAP": "vwap", "前日C": "previous_close",
    "O": "open", "O時刻": "open_time",
    "H": "high", "H時刻": "high_time",
    "L": "low", "L時刻": "low_time",
    "YH": "year_high", "YH日付": "year_high_date",
    "LH": "listing_high", "LH日付": "listing_high_date",
    "YL": "year_low", "YL日付": "year_low_date",
    "LL": "listing_low", "LL日付": "listing_low_date",
    "買い気配": "bid", "売り気配": "ask",
    "特売": "special_sell", "特買": "special_buy",
}

TIME_FIELDS = ("open_time", "high_time", "low_time")
DATE_FIELDS = ("year_high_date", "listing_high_date", "year_low_date", "listing_low_date")
FIELD_MAP.update({
    "現在値詳細時刻": "current_detail_time",
    "歩み1": "last_trade_1",
    "歩み2": "last_trade_2",
    "歩み3": "last_trade_3",
    "歩み4": "last_trade_4",
    "歩み1時刻": "last_trade_time_1",
    "歩み2時刻": "last_trade_time_2",
    "歩み3時刻": "last_trade_time_3",
    "歩み4時刻": "last_trade_time_4",
    "出来高": "volume",
    "前場終値": "morning_close",
    "前場出来高": "morning_volume",
    "後場始値": "afternoon_open",
    "後場高値": "afternoon_high",
    "後場安値": "afternoon_low",
    "最良買気配数量": "best_bid_size",
    "最良売気配数量": "best_ask_size",
    "買成行数量": "market_buy_qty",
    "売成行数量": "market_sell_qty",
    "OVER気配数量": "over_qty",
    "UNDER気配数量": "under_qty",
    "貸借倍率": "loan_margin_ratio",
    "逆日歩": "reverse_yield",
    "信用倍率": "margin_ratio",
    "信用売残": "margin_sell_balance",
    "信用売残前週比": "margin_sell_balance_wow",
    "信用買残": "margin_buy_balance",
    "信用買残前週比": "margin_buy_balance_wow",
    "回転日数": "rotation_days",
    "最良買気配数量1": "bid_size_1",
    "最良売気配数量1": "ask_size_1",
    "最良買気配数量2": "bid_size_2",
    "最良売気配数量2": "ask_size_2",
    "最良買気配数量3": "bid_size_3",
    "最良売気配数量3": "ask_size_3",
})
TIME_FIELDS = TIME_FIELDS + (
    "current_detail_time",
    "last_trade_time_1", "last_trade_time_2", "last_trade_time_3", "last_trade_time_4",
)

# 銘柄リストシート（楽天からの静的コピー。場中は変化しない参照情報）
STATIC_FIELD_MAP = {
    "コード/ティッカー": "code",
    "発行済み株式数": "shares_million",
    "時価総額": "market_cap_million",
    "PER": "per",
    "PBR": "pbr",
    "配当利回り": "dividend_yield",
    "決算発表予定日": "earnings_raw",
    "業種": "sector",
}

DEFAULT_CONFIG = {
    "upper_price_offset_pct": 3.0,
    "lower_price_offset_pct": -3.0,
    "change_up_pct": 3.0,
    "change_down_pct": -3.0,
    "open_up_pct": 3.0,
    "open_down_pct": -3.0,
    "vwap_up_pct": 2.0,
    "vwap_down_pct": -2.0,
    "year_high_gap_pct": 3.0,
    "listing_high_gap_pct": 3.0,
    "year_low_gap_pct": 3.0,
    "listing_low_gap_pct": 3.0,
    "premarket_gu_pct": 1.0,
    "premarket_gd_pct": -1.0,
    "attention_rvol": 2.0,
    "attention_turnover_oku": 10.0,
    "attention_high_gap_pct": 3.0,
}

RULE_FIELDS = [
    "upper_price", "lower_price", "change_up_pct", "change_down_pct",
    "open_up_pct", "open_down_pct", "vwap_up_pct", "vwap_down_pct",
    "year_high_gap_pct", "listing_high_gap_pct",
    "year_low_gap_pct", "listing_low_gap_pct",
]

RULE_LABELS = {
    "upper_price": "上限価格", "lower_price": "下限価格",
    "change_up_pct": "前日比上限", "change_down_pct": "前日比下限",
    "open_up_pct": "当日始値比上限", "open_down_pct": "当日始値比下限",
    "vwap_up_pct": "VWAP比上限", "vwap_down_pct": "VWAP比下限",
    "year_high_gap_pct": "年初来高値差分",
    "listing_high_gap_pct": "上場来高値差分",
    "year_low_gap_pct": "年初来安値差分",
    "listing_low_gap_pct": "上場来安値差分",
}

# (差分フィールド, イベント種別, 表示ラベル)
BREAK_KINDS = [
    ("year_high_gap", "year_high", "年初来高値ブレイク"),
    ("listing_high_gap", "listing_high", "上場来高値ブレイク"),
    ("year_low_gap", "year_low", "年初来安値ブレイク"),
    ("listing_low_gap", "listing_low", "上場来安値ブレイク"),
]

app = Flask(__name__)

_classification: dict = {}

# テーマ集計はスナップショット(updated_at)が変わったときだけ再計算する。
# /api/snapshot は2秒間隔でポーリングされるが、元データはPOLL_SECONDS毎しか変わらない。
_theme_leaders_lock = threading.Lock()
_theme_leaders_cache: dict[str, Any] = {"key": None, "focus": [], "leaders": {}}

# /api/snapshot のpayload を updated_at 単位でキャッシュ（複数クライアント×2s ポーリングで
# 毎回DBクエリを走らせないため）。書込み系エンドポイント呼び出し後は dirty=True で無効化。
_snapshot_cache_lock = threading.Lock()
_snapshot_cache: dict[str, Any] = {"key": None, "payload": None, "dirty": True}

# analytics_worker が計算した rvol/flow 結果をコレクタスレッドへ渡す共有キャッシュ。
# コレクタは読むだけ（非ブロッキング）で、重いDBクエリはバックグラウンドスレッドに移譲。
_analytics_cache_lock = threading.Lock()
_analytics_cache: dict[str, Any] = {
    "rvol_baselines": {},
    "flow_anchors": {},
    "flow_anchors_5m": {},
    "flow_anchors_60m": {},
}


def _load_classification_safe() -> None:
    global _classification
    if CLASSIFICATION_DB_PATH.exists():
        try:
            _classification = load_classification(str(CLASSIFICATION_DB_PATH))
        except Exception as exc:
            print(f"[warn] classification DB load failed: {exc}")


def db_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def ensure_column(connection: sqlite3.Connection, table: str, name: str, definition: str) -> None:
    columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
    if name not in columns:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def init_db() -> None:
    with db_connection() as connection:
        # WALはDBファイルに永続するので起動時に1回設定すれば足りる。
        # コレクタ(書込)とFlask(読込,threaded=True)の同時アクセスでのロック衝突を防ぐ。
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS price_history (
                sampled_at TEXT NOT NULL, code TEXT NOT NULL, price REAL,
                change_pct REAL, vwap_gap REAL, turnover REAL
            );
            CREATE INDEX IF NOT EXISTS idx_history_code_time
                ON price_history(code, sampled_at);
            CREATE INDEX IF NOT EXISTS idx_history_time
                ON price_history(sampled_at);
            CREATE INDEX IF NOT EXISTS idx_history_ms_code_time
                ON price_history(market_session, code, sampled_at);
            CREATE TABLE IF NOT EXISTS alert_rules (
                code TEXT PRIMARY KEY,
                upper_price REAL, lower_price REAL,
                change_up_pct REAL, change_down_pct REAL,
                enabled INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS alert_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                occurred_at TEXT NOT NULL, code TEXT NOT NULL, name TEXT,
                kind TEXT NOT NULL, message TEXT NOT NULL, value REAL,
                acknowledged INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS favorites (
                code TEXT PRIMARY KEY, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS breakout_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                occurred_at TEXT NOT NULL, code TEXT NOT NULL, name TEXT,
                kind TEXT NOT NULL, price REAL
            );
            CREATE INDEX IF NOT EXISTS idx_breakout_time
                ON breakout_events(occurred_at);
            """
        )
        for field in RULE_FIELDS:
            ensure_column(connection, "alert_rules", field, "REAL")
            ensure_column(connection, "alert_rules", f"{field}_on", "INTEGER NOT NULL DEFAULT 1")
        ensure_column(connection, "price_history", "market_session", "TEXT")
        connection.execute(
            "UPDATE price_history SET market_session = 'TSE' WHERE market_session IS NULL"
        )
        daily_data.ensure_daily_table(connection)


def normalize_code(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    s = str(value).strip()
    if "." in s:
        base, suffix = s.rsplit(".", 1)
        if suffix.isalpha() and len(base) > 0:
            # e.g. "9984.JNX" → "9984"
            return base
        if base.isdigit() and not suffix.strip("0"):
            # e.g. "9984.0" or "9984.00" → "9984"
            # COM returns float codes as strings; strip the decimal part
            return base
    return s


def as_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def clean_excel_value(value: Any) -> Any:
    # Excel/COM can expose worksheet error values as large negative integers.
    if isinstance(value, (int, float)) and value <= -1_000_000_000:
        return None
    return value


def format_excel_date(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, str):
        return value.strip() or None
    serial = as_float(value)
    if serial is None or serial <= 0:
        return None
    return (EXCEL_EPOCH + timedelta(days=serial)).strftime("%Y/%m/%d")


def format_excel_time(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, str):
        return value.strip() or None
    serial = as_float(value)
    if serial is None:
        return None
    seconds = int(round((serial % 1) * 86400))
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def safe_div(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


def signed_balance_ratio(positive: float | None, negative: float | None) -> float | None:
    if positive is None or negative is None:
        return None
    total = positive + negative
    if total == 0:
        return None
    return (positive - negative) / total


def compute_derived(stock: dict[str, Any]) -> None:
    """RSS生データから派生指標を計算する（旧Excel数式と同じ定義）。"""
    price = as_float(stock.get("price"))
    open_ = as_float(stock.get("open"))
    high = as_float(stock.get("high"))
    low = as_float(stock.get("low"))
    prev_close = as_float(stock.get("previous_close"))
    vwap = as_float(stock.get("vwap"))
    bid = as_float(stock.get("bid"))
    ask = as_float(stock.get("ask"))

    # ボラ =(H-L)/O、H差分 =(H-現在値)/O、L差分 =(現在値-L)/O
    stock["volatility"] = safe_div(high - low, open_) if high is not None and low is not None else None
    stock["from_high"] = safe_div(high - price, open_) if high is not None and price is not None else None
    stock["from_low"] = safe_div(price - low, open_) if low is not None and price is not None else None

    # 始値比率・前日比率・VWAP乖離率
    ratio = safe_div(price, open_)
    stock["from_open"] = ratio - 1 if ratio is not None else None
    ratio = safe_div(price, prev_close)
    stock["change_pct"] = ratio - 1 if ratio is not None else None
    ratio = safe_div(price, vwap)
    stock["vwap_gap"] = ratio - 1 if ratio is not None else None

    # 暫定気配（最良売買気配の平均）と寄前GU/GD率（始値が付くまで）
    indicative = (bid + ask) / 2 if bid is not None and ask is not None else None
    stock["indicative"] = indicative
    stock["gap_pct"] = (
        safe_div(indicative - prev_close, prev_close)
        if not open_ and indicative is not None and prev_close is not None
        else None
    )

    # 特別気配（"S"フラグ）
    special_sell = str(stock.get("special_sell") or "").strip()
    special_buy = str(stock.get("special_buy") or "").strip()
    stock["special_quote"] = "特売" if special_sell == "S" else "特買" if special_buy == "S" else ""

    # 高値・安値までの差分。上場来高値は株式分割前の不正確値対策で100%超を除外（旧Excel式と同じ）
    best_bid_size = as_float(stock.get("best_bid_size"))
    best_ask_size = as_float(stock.get("best_ask_size"))
    bid_size_3 = sum(as_float(stock.get(key)) or 0 for key in ("bid_size_1", "bid_size_2", "bid_size_3"))
    ask_size_3 = sum(as_float(stock.get(key)) or 0 for key in ("ask_size_1", "ask_size_2", "ask_size_3"))
    market_buy_qty = as_float(stock.get("market_buy_qty"))
    market_sell_qty = as_float(stock.get("market_sell_qty"))
    under_qty = as_float(stock.get("under_qty"))
    over_qty = as_float(stock.get("over_qty"))

    stock["book_imbalance"] = signed_balance_ratio(best_bid_size, best_ask_size)
    stock["book_imbalance_3"] = signed_balance_ratio(
        bid_size_3 if bid_size_3 > 0 else None,
        ask_size_3 if ask_size_3 > 0 else None,
    )
    stock["market_order_imbalance"] = signed_balance_ratio(market_buy_qty, market_sell_qty)
    stock["auction_imbalance"] = signed_balance_ratio(under_qty, over_qty)

    year_high = as_float(stock.get("year_high"))
    listing_high = as_float(stock.get("listing_high"))
    year_low = as_float(stock.get("year_low"))
    listing_low = as_float(stock.get("listing_low"))
    stock["year_high_gap"] = safe_div(year_high - price, price) if year_high is not None and price else None
    listing_gap = safe_div(listing_high - price, price) if listing_high is not None and price else None
    stock["listing_high_gap"] = listing_gap if listing_gap is None or listing_gap <= 1 else None
    stock["year_low_gap"] = safe_div(price - year_low, price) if year_low is not None and price else None
    stock["listing_low_gap"] = safe_div(price - listing_low, price) if listing_low is not None and price else None


class MonitorState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.stocks: list[dict[str, Any]] = []
        self.config = dict(DEFAULT_CONFIG)
        self.baseline_prices: dict[str, float] = {}
        self.updated_at: str | None = None
        self.error: str | None = "Excelからの初回読取を待っています"
        self.running = True
        self.last_history_at = 0.0
        self.last_prune_at = 0.0
        self.recent_fires: dict[tuple[str, str], float] = {}

    def snapshot(self) -> dict[str, Any]:
        now = datetime.now()
        t = now.hour * 60 + now.minute
        expected_session = "TSE" if 6 * 60 <= t < 15 * 60 + 30 else "PTS"
        with self.lock:
            detected_modes = {
                stock.get("market_mode")
                for stock in self.stocks
                if stock.get("market_mode") in ("TSE", "PTS")
            }
            market_session = detected_modes.pop() if len(detected_modes) == 1 else None
            return {
                "stocks": list(self.stocks),
                "config": dict(self.config),
                "baseline_prices": dict(self.baseline_prices),
                "updated_at": self.updated_at,
                "error": self.error,
                "workbook": WORKBOOK_NAME,
                "sheet": SHEET_NAME,
                "market_session": market_session or expected_session,
                "detected_session": market_session,
                "expected_session": expected_session,
                "session_mismatch": market_session is not None and market_session != expected_session,
            }


state = MonitorState()


class ExcelBusyError(RuntimeError):
    pass


def get_workbook(excel):
    for index in range(1, excel.Workbooks.Count + 1):
        candidate = excel.Workbooks.Item(index)
        if str(candidate.Name).casefold() == WORKBOOK_NAME.casefold():
            return candidate
    raise RuntimeError(f"Excelで「{WORKBOOK_NAME}」を開いてください")


def get_workbook_from_rot():
    """ROT から開いている Workbook を直接探す。"""
    rot = pythoncom.GetRunningObjectTable()
    enum = rot.EnumRunning()
    bind_ctx = pythoncom.CreateBindCtx(0)
    target = WORKBOOK_NAME.casefold()

    while True:
        monikers = enum.Next(1)
        if not monikers:
            break
        moniker = monikers[0]
        try:
            display_name = moniker.GetDisplayName(bind_ctx, None)
        except pythoncom.com_error:
            continue
        if not display_name.lower().endswith((".xls", ".xlsx", ".xlsm", ".xlsb")):
            continue
        try:
            obj = rot.GetObject(moniker)
            disp = obj.QueryInterface(pythoncom.IID_IDispatch)
            workbook = win32com.client.Dispatch(disp)
            if str(workbook.Name).casefold() == target:
                return workbook.Application, workbook
        except Exception:
            continue
    raise RuntimeError(f"Excelで「{WORKBOOK_NAME}」を開いてください")


def connect_excel_workbook():
    """既存の Excel から対象ブックへ接続する。"""
    try:
        excel = win32com.client.GetActiveObject("Excel.Application")
        workbook = get_workbook(excel)
        return excel, workbook
    except Exception:
        return get_workbook_from_rot()


def excel_is_ready(excel) -> bool:
    return bool(excel.Ready) and int(excel.CalculationState) == 0


def parse_stock_values(values: Any) -> list[dict[str, Any]]:
    if not values or len(values) < 2:
        return []
    headers = [str(value).strip() if value is not None else "" for value in values[0]]
    stocks = []
    for excel_row, row in enumerate(values[1:], start=2):
        raw = {
            headers[i]: clean_excel_value(row[i])
            for i in range(len(row))
            if headers[i]
        }
        if raw.get("Code") in (None, ""):
            continue
        stock = {FIELD_MAP.get(key, key): value for key, value in raw.items()}
        source_code = str(raw["Code"]).strip()
        stock["source_code"] = source_code
        stock["market_mode"] = "PTS" if source_code.upper().endswith(".JNX") else "TSE"
        stock["code"] = normalize_code(source_code)
        stock["excel_row"] = excel_row
        for field in TIME_FIELDS:
            stock[field] = format_excel_time(stock.get(field))
        for field in DATE_FIELDS:
            stock[field] = format_excel_date(stock.get(field))
        compute_derived(stock)
        stocks.append(stock)
    return stocks


def parse_config_values(values: Any) -> dict[str, float]:
    config = dict(DEFAULT_CONFIG)
    if values:
        for row in values[1:]:
            if row[0] and as_float(row[2]) is not None:
                config[str(row[0]).strip()] = float(row[2])
    return config


def parse_static_values(values: Any) -> dict[str, dict[str, Any]]:
    """銘柄リストシート（静的参照データ）をコード→情報のマップへ変換する。"""
    if not values or len(values) < 2:
        return {}
    headers = [str(value).strip() if value is not None else "" for value in values[0]]
    result: dict[str, dict[str, Any]] = {}
    for row in values[1:]:
        raw = {
            headers[i]: clean_excel_value(row[i])
            for i in range(len(row))
            if headers[i]
        }
        code_raw = raw.get("コード/ティッカー")
        if code_raw in (None, ""):
            continue
        code = normalize_code(code_raw)
        result[code] = {
            "sector": str(raw.get("業種") or "").strip() or None,
            "market_cap_million": as_float(raw.get("時価総額")),
            "shares_million": as_float(raw.get("発行済み株式数")),
            "per": as_float(raw.get("PER")),
            "pbr": as_float(raw.get("PBR")),
            "dividend_yield": as_float(raw.get("配当利回り")),
            "earnings_date": format_excel_date(raw.get("決算発表予定日")),
        }
    return result


def read_excel_data(
    excel,
    workbook,
    config: dict[str, float],
    read_config: bool,
) -> tuple[list[dict[str, Any]], dict[str, float], dict[str, dict[str, Any]] | None]:
    if not excel_is_ready(excel):
        raise ExcelBusyError("Excelを操作中または計算中のため読取を一時停止しています")

    values = workbook.Worksheets(SHEET_NAME).Range(DATA_RANGE).Value2
    stocks = parse_stock_values(values)
    static_info: dict[str, dict[str, Any]] | None = None
    if read_config:
        try:
            config_values = workbook.Worksheets(CONFIG_SHEET).Range(CONFIG_RANGE).Value2
            config = parse_config_values(config_values)
        except pywintypes.com_error:
            raise
        except Exception:
            config = dict(config)
        try:
            static_values = workbook.Worksheets(STATIC_SHEET).Range(STATIC_RANGE).Value2
            static_info = parse_static_values(static_values)
        except pywintypes.com_error:
            raise
        except Exception:
            static_info = None
    return stocks, config, static_info


def merge_static_info(stocks: list[dict[str, Any]], static_info: dict[str, dict[str, Any]]) -> None:
    today = date.today()
    for stock in stocks:
        info = static_info.get(stock["code"])
        if not info:
            continue
        stock.update(info)
        days = None
        if info.get("earnings_date"):
            try:
                earnings = datetime.strptime(info["earnings_date"], "%Y/%m/%d").date()
                days = (earnings - today).days
            except ValueError:
                days = None
        stock["days_to_earnings"] = days


def compute_rvol_baselines(codes: list[str], market_session: str) -> dict[str, float]:
    """過去N日の「同時刻時点の累計売買代金」の平均を銘柄ごとに求める。

    各日「現在時刻と同じ時刻までの最終行」を取り、turnover>0の日だけ平均する。
    全銘柄をwindow関数で1クエリに集約（旧: 銘柄×日数のN+1クエリ）。
    """
    if not codes:
        return {}
    time_str = datetime.now().strftime("%H:%M:%S")
    placeholders = ",".join("?" for _ in codes)
    with db_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT code, AVG(last_turnover) AS baseline
            FROM (
                SELECT code, date(sampled_at) AS d, MAX(turnover) AS last_turnover
                FROM price_history
                WHERE market_session = ?
                  AND code IN ({placeholders})
                  AND sampled_at >= date('now', 'localtime', '-{RVOL_LOOKBACK_DAYS} days') || ' 00:00:00'
                  AND sampled_at <  date('now', 'localtime') || ' 00:00:00'
                  AND substr(sampled_at, 12, 8) <= ?
                GROUP BY code, d
            ) WHERE last_turnover > 0
            GROUP BY code
            """,
            (market_session, *codes, time_str),
        ).fetchall()
    return {row["code"]: float(row["baseline"]) for row in rows if row["baseline"]}


def compute_flow_anchors(codes: list[str], market_session: str, minutes: int) -> dict[str, dict[str, float]]:
    """指定分前時点（当日内）の価格・累計売買代金を銘柄ごとに取得する。

    全銘柄をwindow関数で1クエリに集約（旧: 銘柄ごとのN+1クエリ）。
    """
    if not codes:
        return {}
    placeholders = ",".join("?" for _ in codes)
    anchors: dict[str, dict[str, float]] = {}
    with db_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT code, price, turnover FROM (
                SELECT code, price, turnover,
                       ROW_NUMBER() OVER (PARTITION BY code ORDER BY sampled_at DESC) AS rn
                FROM price_history
                WHERE market_session = ?
                  AND code IN ({placeholders})
                  AND sampled_at <= datetime('now', 'localtime', '-{minutes} minutes')
                  AND sampled_at >= date('now', 'localtime') || ' 00:00:00'
            ) WHERE rn = 1
            """,
            (market_session, *codes),
        ).fetchall()
    for row in rows:
        if (row["price"] or 0) > 0:
            anchors[row["code"]] = {
                "price": float(row["price"]),
                "turnover": float(row["turnover"] or 0),
            }
    return anchors


def attach_analytics(
    stocks: list[dict[str, Any]],
    rvol_baselines: dict[str, float],
    flow_anchors: dict[str, dict[str, float]],
    flow_anchors_5m: dict[str, dict[str, float]],
    flow_anchors_60m: dict[str, dict[str, float]] | None = None,
) -> None:
    for stock in stocks:
        code = stock["code"]
        turnover = as_float(stock.get("turnover"))
        price = as_float(stock.get("price"))

        baseline = rvol_baselines.get(code)
        stock["rvol"] = (
            turnover / baseline
            if turnover is not None and baseline and baseline > 0
            else None
        )

        anchor = flow_anchors.get(code)
        if anchor and turnover is not None and price:
            delta = turnover - anchor["turnover"]
            stock["turnover_delta_15m"] = delta if delta >= 0 else None
            stock["change_15m"] = price / anchor["price"] - 1 if anchor["price"] else None
        else:
            stock["turnover_delta_15m"] = None
            stock["change_15m"] = None

        anchor5 = flow_anchors_5m.get(code)
        if anchor5 and turnover is not None and price:
            delta5 = turnover - anchor5["turnover"]
            stock["turnover_delta_5m"] = delta5 if delta5 >= 0 else None
            stock["change_5m"] = price / anchor5["price"] - 1 if anchor5["price"] else None
        else:
            stock["turnover_delta_5m"] = None
            stock["change_5m"] = None

        anchor60 = (flow_anchors_60m or {}).get(code)
        if anchor60 and turnover is not None and price:
            delta60 = turnover - anchor60["turnover"]
            stock["turnover_delta_60m"] = delta60 if delta60 >= 0 else None
            stock["change_60m"] = price / anchor60["price"] - 1 if anchor60["price"] else None
        else:
            stock["turnover_delta_60m"] = None
            stock["change_60m"] = None


def analytics_worker() -> None:
    """rvol/flow計算を専用スレッドで非同期実行。コレクタスレッドのブロッキングを防ぐ。

    state.stocks から codes/session を読み、重いDBクエリをバックグラウンドで実行して
    _analytics_cache へ格納する。コレクタはキャッシュを読むだけ（非ブロッキング）。
    """
    last_rvol_at = 0.0
    last_flow_at = 0.0
    while state.running:
        try:
            with state.lock:
                stocks_snap = list(state.stocks)
            if stocks_snap:
                codes = [s["code"] for s in stocks_snap]
                market_session = stocks_snap[0].get("market_mode", "TSE")
                now = time.monotonic()
                if now - last_rvol_at >= RVOL_REFRESH_SECONDS:
                    baselines = compute_rvol_baselines(codes, market_session)
                    with _analytics_cache_lock:
                        _analytics_cache["rvol_baselines"] = baselines
                    last_rvol_at = now
                if now - last_flow_at >= FLOW_REFRESH_SECONDS:
                    fa15 = compute_flow_anchors(codes, market_session, FLOW_WINDOW_MINUTES)
                    fa5  = compute_flow_anchors(codes, market_session, FLOW_WINDOW_MINUTES_5)
                    fa60 = compute_flow_anchors(codes, market_session, FLOW_WINDOW_MINUTES_60)
                    with _analytics_cache_lock:
                        _analytics_cache["flow_anchors"]     = fa15
                        _analytics_cache["flow_anchors_5m"]  = fa5
                        _analytics_cache["flow_anchors_60m"] = fa60
                    last_flow_at = now
        except Exception as exc:
            print(f"[analytics] エラー: {exc}", flush=True)
        time.sleep(5)


def detect_breakouts(stocks: list[dict[str, Any]], armed: dict[tuple[str, str], bool]) -> None:
    """高値・安値の更新瞬間を検知してイベント記録＋通知する。

    差分が0以下（=高安値に到達）でアーム済みなら発火。0.1%以上離れたら再アーム。
    起動直後はアームされていないため、既に高値張り付きの銘柄では誤発火しない。
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    breakout_rows = []
    alert_rows = []
    for stock in stocks:
        price = as_float(stock.get("price"))
        if not price:
            continue
        for gap_field, kind, label in BREAK_KINDS:
            gap = as_float(stock.get(gap_field))
            if gap is None:
                continue
            key = (stock["code"], kind)
            if gap >= BREAK_REARM_GAP:
                armed[key] = True
            elif gap <= 0 and armed.get(key):
                armed[key] = False
                breakout_rows.append((timestamp, stock["code"], stock.get("name"), kind, price))
                alert_rows.append((
                    timestamp, stock["code"], stock.get("name"), f"break_{kind}",
                    f"{label}: {price:,.1f}円", price,
                ))
    if breakout_rows:
        with db_connection() as connection:
            connection.executemany(
                """INSERT INTO breakout_events (occurred_at, code, name, kind, price)
                   VALUES (?, ?, ?, ?, ?)""",
                breakout_rows,
            )
            connection.executemany(
                """INSERT INTO alert_events
                   (occurred_at, code, name, kind, message, value)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                alert_rows,
            )


def load_rules(enabled_only: bool = False) -> dict[str, sqlite3.Row]:
    sql = "SELECT * FROM alert_rules"
    if enabled_only:
        sql += " WHERE enabled = 1"
    with db_connection() as connection:
        rows = connection.execute(sql).fetchall()
    return {row["code"]: row for row in rows}


def default_rule(code: str) -> dict[str, Any]:
    snapshot = state.snapshot()
    stock = next((item for item in snapshot["stocks"] if item["code"] == code), None)
    price = as_float(stock.get("price")) if stock else None
    config = snapshot["config"]
    result: dict[str, Any] = {"code": code, "enabled": 1}
    result["upper_price"] = price * (1 + config["upper_price_offset_pct"] / 100) if price else None
    result["lower_price"] = price * (1 + config["lower_price_offset_pct"] / 100) if price else None
    for field in RULE_FIELDS[2:]:
        result[field] = config[field]
    for field in RULE_FIELDS:
        result[f"{field}_on"] = 1
    return result


def rule_checks(stock: dict[str, Any], rule: sqlite3.Row | dict[str, Any]):
    return [
        ("upper_price", stock.get("price"), lambda v, limit: v >= limit),
        ("lower_price", stock.get("price"), lambda v, limit: v <= limit),
        ("change_up_pct", stock.get("change_pct"), lambda v, limit: v * 100 >= limit),
        ("change_down_pct", stock.get("change_pct"), lambda v, limit: v * 100 <= limit),
        ("open_up_pct", stock.get("from_open"), lambda v, limit: v * 100 >= limit),
        ("open_down_pct", stock.get("from_open"), lambda v, limit: v * 100 <= limit),
        ("vwap_up_pct", stock.get("vwap_gap"), lambda v, limit: v * 100 >= limit),
        ("vwap_down_pct", stock.get("vwap_gap"), lambda v, limit: v * 100 <= limit),
        ("year_high_gap_pct", stock.get("year_high_gap"), lambda v, limit: v * 100 <= limit),
        ("listing_high_gap_pct", stock.get("listing_high_gap"), lambda v, limit: v * 100 <= limit),
        ("year_low_gap_pct", stock.get("year_low_gap"), lambda v, limit: v * 100 <= limit),
        ("listing_low_gap_pct", stock.get("listing_low_gap"), lambda v, limit: v * 100 <= limit),
    ]


def maybe_fire_alerts(stocks: list[dict[str, Any]]) -> None:
    rules = load_rules(enabled_only=True)
    now = time.time()
    # Prune entries older than the 5-minute cooldown to keep the dict bounded
    state.recent_fires = {k: v for k, v in state.recent_fires.items() if now - v < 300}
    pending = []
    for stock in stocks:
        rule = rules.get(stock["code"])
        if rule is None:
            continue
        for field, raw_value, predicate in rule_checks(stock, rule):
            if not rule[f"{field}_on"]:
                continue
            value, limit = as_float(raw_value), as_float(rule[field])
            if value is None or limit is None or not predicate(value, limit):
                continue
            key = (stock["code"], field)
            if now - state.recent_fires.get(key, 0) < 300:
                continue
            state.recent_fires[key] = now
            display_value = value if field in ("upper_price", "lower_price") else value * 100
            unit = "円" if field in ("upper_price", "lower_price") else "%"
            pending.append((
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                stock["code"], stock.get("name"), field,
                f"{RULE_LABELS[field]}: 現在 {display_value:,.2f}{unit} / 設定 {limit:,.2f}{unit}",
                display_value,
            ))
    if pending:
        with db_connection() as connection:
            connection.executemany(
                """INSERT INTO alert_events
                   (occurred_at, code, name, kind, message, value)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                pending,
            )


def save_history(stocks: list[dict[str, Any]], timestamp: str) -> None:
    now = time.time()
    if now - state.last_history_at < HISTORY_SECONDS:
        return
    state.last_history_at = now
    # Skip stocks with no price data (PTS-unlisted stocks return 0 or None)
    rows = [(
        timestamp, stock["code"], stock.get("price"), stock.get("change_pct"),
        stock.get("vwap_gap"), stock.get("turnover"), stock.get("market_mode")
    ) for stock in stocks if (stock.get("price") or 0) > 0]
    with db_connection() as connection:
        connection.executemany(
            """INSERT INTO price_history
               (sampled_at, code, price, change_pct, vwap_gap, turnover, market_session)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
        # 全行スキャンになるため毎回ではなく30分間隔でプルーンする
        if now - state.last_prune_at >= PRUNE_INTERVAL_SECONDS:
            state.last_prune_at = now
            connection.execute(
                "DELETE FROM price_history WHERE sampled_at < datetime('now', 'localtime', '-14 days')"
            )
            connection.execute(
                "DELETE FROM breakout_events WHERE occurred_at < datetime('now', 'localtime', '-14 days')"
            )
            connection.execute(
                "DELETE FROM alert_events WHERE occurred_at < datetime('now', 'localtime', '-14 days')"
            )


def collector_loop() -> None:
    pythoncom.CoInitialize()
    excel = None
    workbook = None
    config = dict(DEFAULT_CONFIG)
    static_info: dict[str, dict[str, Any]] = {}
    breakout_armed: dict[tuple[str, str], bool] = {}
    last_config_at = 0.0
    last_daily_import_at = 0.0
    last_live_save_at = 0.0
    retry_seconds = POLL_SECONDS
    try:
        while state.running:
            try:
                if excel is None or workbook is None:
                    excel, workbook = connect_excel_workbook()

                now = time.monotonic()
                read_config = now - last_config_at >= CONFIG_POLL_SECONDS
                stocks, config, new_static = read_excel_data(excel, workbook, config, read_config)
                if read_config:
                    last_config_at = now
                    if new_static:
                        static_info = new_static

                merge_static_info(stocks, static_info)

                market_session = stocks[0].get("market_mode", "TSE") if stocks else "TSE"
                with _analytics_cache_lock:
                    rvol_b = _analytics_cache["rvol_baselines"]
                    fa15   = _analytics_cache["flow_anchors"]
                    fa5    = _analytics_cache["flow_anchors_5m"]
                    fa60   = _analytics_cache["flow_anchors_60m"]
                attach_analytics(stocks, rvol_b, fa15, fa5, fa60)
                detect_breakouts(stocks, breakout_armed)

                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                with state.lock:
                    for stock in stocks:
                        price = as_float(stock.get("price"))
                        if price and stock["code"] not in state.baseline_prices:
                            state.baseline_prices[stock["code"]] = price
                    state.stocks = stocks
                    state.config = config
                    state.updated_at = timestamp
                    state.error = None
                maybe_fire_alerts(stocks)
                save_history(stocks, timestamp)

                # 日足CSV取込（起動直後＋定期）。新しい日付があれば確定値で上書きする
                if last_daily_import_at == 0.0 or now - last_daily_import_at >= DAILY_IMPORT_SECONDS:
                    last_daily_import_at = now
                    try:
                        with db_connection() as conn:
                            imported = daily_data.import_new_csvs(conn, str(DAY_DATA_DIR))
                        if imported:
                            print(f"[daily] CSV取込 {len(imported)}日分（最新 {imported[-1][0]}）", flush=True)
                    except Exception as exc:
                        print(f"[daily] CSV取込エラー: {exc}", flush=True)

                # 当日4本値のlive仮値を保存（TSEのみ・定期）。翌朝CSVが来たら上書きされる
                if stocks and market_session == "TSE" and now - last_live_save_at >= LIVE_SAVE_SECONDS:
                    last_live_save_at = now
                    try:
                        with db_connection() as conn:
                            daily_data.save_live_ohlc(
                                conn, stocks, datetime.now().strftime("%Y-%m-%d")
                            )
                    except Exception as exc:
                        print(f"[daily] live保存エラー: {exc}", flush=True)

                retry_seconds = POLL_SECONDS
                time.sleep(POLL_SECONDS)
            except ExcelBusyError:
                # Keep the last successful snapshot while the user edits Excel.
                time.sleep(POLL_SECONDS)
            except pywintypes.com_error as exc:
                hresult = exc.hresult
                excel = None
                workbook = None
                if hresult in (RPC_CALL_REJECTED, RPC_SERVER_UNAVAILABLE):
                    message = "Excelが応答待ちです。最後に取得した値を表示しています"
                else:
                    message = f"Excel接続エラー: {exc}"
                with state.lock:
                    state.error = message
                time.sleep(retry_seconds)
                retry_seconds = min(max(POLL_SECONDS, retry_seconds * 2), MAX_RETRY_SECONDS)
            except Exception as exc:
                excel = None
                workbook = None
                with state.lock:
                    state.error = str(exc)
                time.sleep(retry_seconds)
                retry_seconds = min(max(POLL_SECONDS, retry_seconds * 2), MAX_RETRY_SECONDS)
    finally:
        workbook = None
        excel = None
        pythoncom.CoUninitialize()


def save_rule(code: str, data: dict[str, Any]) -> None:
    columns = ["code", *RULE_FIELDS, *[f"{field}_on" for field in RULE_FIELDS], "enabled"]
    values = [code]
    values.extend(as_float(data.get(field)) for field in RULE_FIELDS)
    values.extend(1 if data.get(f"{field}_on", True) else 0 for field in RULE_FIELDS)
    values.append(1 if data.get("enabled", True) else 0)
    update = ", ".join(f"{column}=excluded.{column}" for column in columns[1:])
    placeholders = ", ".join("?" for _ in columns)
    with db_connection() as connection:
        connection.execute(
            f"""INSERT INTO alert_rules ({", ".join(columns)})
                VALUES ({placeholders})
                ON CONFLICT(code) DO UPDATE SET {update}""",
            values,
        )


def build_theme_leaders(stocks: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    if not _classification:
        return [], {}

    data = compute_sector_flow(stocks, _classification, sort_tf="15m", include_members=True, member_limit=8)
    themes = []
    for theme_id, theme in (data.get("themes") or {}).items():
        flow_15m = float(theme.get("flow_15m_sum_weighted") or 0.0)
        if flow_15m <= 0:
            continue
        themes.append({
            "theme_id": theme_id,
            "name": theme.get("name", theme_id),
            "flow_15m": flow_15m,
            "breadth": float(theme.get("weighted_breadth") or 0.0),
            "turnover": float(theme.get("turnover_sum_weighted") or 0.0),
            "members": theme.get("members") or [],
        })
    themes.sort(key=lambda item: (item["flow_15m"], item["breadth"], item["turnover"]), reverse=True)
    focus = themes[:3]

    leaders_by_code: dict[str, dict[str, Any]] = {}
    for rank, theme in enumerate(focus, start=1):
        for member in theme["members"][:5]:
            code = member.get("code")
            if not code:
                continue
            flow = float(member.get("flow") or 0.0)
            prev = leaders_by_code.get(code)
            if prev is None or flow > prev["member_flow_15m"]:
                leaders_by_code[code] = {
                    "theme_id": theme["theme_id"],
                    "theme_name": theme["name"],
                    "theme_rank": rank,
                    "theme_flow_15m": theme["flow_15m"],
                    "member_flow_15m": flow,
                    "theme_breadth": theme["breadth"],
                }

    for theme in focus:
        theme.pop("members", None)
    return focus, leaders_by_code


def _invalidate_snapshot_cache() -> None:
    with _snapshot_cache_lock:
        _snapshot_cache["dirty"] = True


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/snapshot")
def api_snapshot():
    snap = state.snapshot()
    cache_key = snap["updated_at"]

    with _snapshot_cache_lock:
        if (
            not _snapshot_cache["dirty"]
            and _snapshot_cache["key"] == cache_key
            and _snapshot_cache["payload"] is not None
        ):
            return jsonify(_snapshot_cache["payload"])

    with db_connection() as connection:
        rules = connection.execute("SELECT * FROM alert_rules").fetchall()
        favorites = connection.execute("SELECT code FROM favorites").fetchall()
        alerts = connection.execute(
            "SELECT * FROM alert_events ORDER BY id DESC LIMIT 30"
        ).fetchall()
        breaks = connection.execute(
            """SELECT code, kind, COUNT(*) AS count, MAX(occurred_at) AS last_at
               FROM breakout_events
               WHERE occurred_at >= date('now', 'localtime') || ' 00:00:00'
               GROUP BY code, kind"""
        ).fetchall()

    payload = snap
    payload["rules"] = {row["code"]: dict(row) for row in rules}
    payload["favorites"] = [row["code"] for row in favorites]
    payload["alerts"] = [dict(row) for row in alerts]
    breaks_map: dict[str, dict[str, Any]] = {}
    for row in breaks:
        breaks_map.setdefault(row["code"], {})[row["kind"]] = {
            "count": row["count"], "last_at": row["last_at"],
        }
    payload["breaks"] = breaks_map

    with _theme_leaders_lock:
        if _theme_leaders_cache["key"] != cache_key:
            focus, leaders = build_theme_leaders(payload["stocks"])
            _theme_leaders_cache.update(key=cache_key, focus=focus, leaders=leaders)
        payload["theme_focus"] = _theme_leaders_cache["focus"]
        payload["theme_leaders"] = _theme_leaders_cache["leaders"]

    with _snapshot_cache_lock:
        _snapshot_cache["key"] = cache_key
        _snapshot_cache["payload"] = payload
        _snapshot_cache["dirty"] = False

    return jsonify(payload)


@app.get("/api/alerts/<code>/defaults")
def api_alert_defaults(code: str):
    return jsonify(default_rule(code))


@app.put("/api/alerts/<code>")
def api_update_alert(code: str):
    save_rule(code, request.get_json(force=True))
    _invalidate_snapshot_cache()
    return jsonify({"ok": True})


@app.post("/api/alerts/<code>/toggle")
def api_toggle_alert(code: str):
    with db_connection() as connection:
        row = connection.execute(
            "SELECT enabled FROM alert_rules WHERE code = ?", (code,)
        ).fetchone()
        if row is not None:
            enabled = not bool(row["enabled"])
            connection.execute(
                "UPDATE alert_rules SET enabled = ? WHERE code = ?",
                (1 if enabled else 0, code),
            )
    if row is None:
        save_rule(code, default_rule(code))
        _invalidate_snapshot_cache()
        return jsonify({"ok": True, "enabled": True})
    _invalidate_snapshot_cache()
    return jsonify({"ok": True, "enabled": enabled})


@app.post("/api/favorites/<code>/toggle")
def api_toggle_favorite(code: str):
    with db_connection() as connection:
        exists = connection.execute(
            "SELECT 1 FROM favorites WHERE code = ?", (code,)
        ).fetchone()
        if exists:
            connection.execute("DELETE FROM favorites WHERE code = ?", (code,))
            active = False
        else:
            connection.execute(
                "INSERT INTO favorites(code, created_at) VALUES (?, ?)",
                (code, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
            )
            active = True
    _invalidate_snapshot_cache()
    return jsonify({"ok": True, "active": active})


@app.get("/api/history/<code>")
def api_history(code: str):
    minutes = min(max(request.args.get("minutes", 120, type=int), 5), 1440)
    with db_connection() as connection:
        rows = connection.execute(
            """SELECT sampled_at, price, change_pct, vwap_gap, turnover
               FROM price_history
               WHERE code = ?
                 AND sampled_at >= datetime('now', 'localtime', ?)
                 AND (price IS NULL OR price > -1000000000)
               ORDER BY sampled_at""",
            (code, f"-{minutes} minutes"),
        ).fetchall()
    return jsonify([dict(row) for row in rows])


@app.get("/api/sector_flow")
def api_sector_flow():
    tf = request.args.get("tf", "15m")
    if tf not in ("5m", "15m", "60m"):
        return jsonify({"error": "tf must be '5m', '15m', or '60m'"}), 400
    snap = state.snapshot()
    data = compute_sector_flow(snap["stocks"], _classification, sort_tf=tf, include_members=False)
    return jsonify(data)


@app.get("/api/daily_dates")
def api_daily_dates():
    with db_connection() as con:
        dates = [r[0] for r in con.execute(
            "SELECT DISTINCT date FROM daily_ohlc ORDER BY date DESC LIMIT 7"
        )]
    if not dates:
        return jsonify({"day": None, "week_start": None, "week_end": None})
    return jsonify({
        "day": dates[0],
        "week_end": dates[0],
        "week_start": dates[min(4, len(dates) - 1)],
    })


@app.get("/api/sector_flow_daily")
def api_sector_flow_daily():
    tf = request.args.get("tf", "day")
    if tf not in ("day", "week"):
        return jsonify({"error": "tf must be 'day' or 'week'"}), 400
    with db_connection() as con:
        data = compute_sector_flow_daily(con, _classification, tf, include_members=False)
    return jsonify(data)


@app.get("/api/sector_flow_members")
def api_sector_flow_members():
    source = request.args.get("source", "intraday")
    tf = request.args.get("tf", "15m")
    kind = request.args.get("kind", "theme")
    key = request.args.get("id", "")
    limit = min(max(request.args.get("limit", 200, type=int), 1), 1000)
    if kind not in ("theme", "big", "mid"):
        return jsonify({"error": "kind must be 'theme', 'big', or 'mid'"}), 400

    if source == "daily":
        if tf not in ("day", "week"):
            return jsonify({"error": "daily tf must be 'day' or 'week'"}), 400
        with db_connection() as con:
            data = compute_sector_flow_daily(
                con, _classification, tf, include_members=True, member_limit=limit
            )
    else:
        if tf not in ("5m", "15m", "60m"):
            return jsonify({"error": "intraday tf must be '5m', '15m', or '60m'"}), 400
        snap = state.snapshot()
        data = compute_sector_flow(
            snap["stocks"], _classification, sort_tf=tf, include_members=True, member_limit=limit
        )

    bucket = {
        "theme": data.get("themes", {}),
        "big": data.get("big_cats", {}),
        "mid": data.get("mid_cats", {}),
    }[kind]
    item = bucket.get(key)
    if item is None:
        return jsonify({"members": [], "sample_note": data.get("sample_note", "")})
    return jsonify({
        "members": item.get("members", []),
        "sample_note": data.get("sample_note", ""),
    })


@app.post("/api/alerts/acknowledge")
def api_acknowledge_alerts():
    with db_connection() as connection:
        connection.execute("UPDATE alert_events SET acknowledged = 1")
    _invalidate_snapshot_cache()
    return jsonify({"ok": True})


def local_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        return "このPCのIPアドレス"


def ensure_port_available() -> None:
    """Collector起動前に多重起動を検知する。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("0.0.0.0", PORT))
        except OSError as exc:
            raise SystemExit(
                f"ポート{PORT}は使用中です。楽天RSS LAN Monitorは既に起動しています。"
            ) from exc


def shutdown() -> None:
    state.running = False


if __name__ == "__main__":
    ensure_port_available()
    init_db()
    _load_classification_safe()
    atexit.register(shutdown)
    threading.Thread(target=collector_loop, name="excel-collector", daemon=True).start()
    threading.Thread(target=analytics_worker, name="analytics", daemon=True).start()
    print(f"メインPC: http://127.0.0.1:{PORT}")
    print(f"別PC:     http://{local_ip()}:{PORT}")
    app.run(host="0.0.0.0", port=PORT, debug=False, threaded=True)
