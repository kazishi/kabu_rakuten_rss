# -*- coding: utf-8 -*-
"""detect_breakouts のヒステリシス動作を一時DBで検証する"""
import sys
import io
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

import app as m

tmp = Path(tempfile.mkdtemp()) / "test.db"
m.DB_PATH = tmp
m.init_db()

stock = {"code": "9999", "name": "テスト", "price": 100.0,
         "year_high_gap": 0.05, "listing_high_gap": None,
         "year_low_gap": 0.5, "listing_low_gap": 0.5}
armed = {}

def fired():
    with m.db_connection() as c:
        return c.execute("SELECT COUNT(*) AS n FROM breakout_events").fetchone()["n"]

m.detect_breakouts([stock], armed)            # gap 5% → アームのみ
assert fired() == 0, "アーム段階で発火してはいけない"

stock["year_high_gap"] = 0.0
m.detect_breakouts([stock], armed)            # 高値到達 → 発火
assert fired() == 1, f"1回目のブレイクで発火するべき: {fired()}"

m.detect_breakouts([stock], armed)            # 張り付き継続 → 再発火しない
m.detect_breakouts([stock], armed)
assert fired() == 1, f"張り付き中に再発火してはいけない: {fired()}"

stock["year_high_gap"] = 0.0005
m.detect_breakouts([stock], armed)            # 0.05%押し → 再アームしない
stock["year_high_gap"] = 0.0
m.detect_breakouts([stock], armed)
assert fired() == 1, f"0.1%未満の押しで再発火してはいけない: {fired()}"

stock["year_high_gap"] = 0.002
m.detect_breakouts([stock], armed)            # 0.2%押し → 再アーム
stock["year_high_gap"] = -0.001
m.detect_breakouts([stock], armed)            # 再ブレイク → 2回目発火
assert fired() == 2, f"再アーム後のブレイクで発火するべき: {fired()}"

# 安値側
stock["year_low_gap"] = 0.0
m.detect_breakouts([stock], armed)            # 安値はまだ非アーム（初期0.5でアーム済み）→発火
assert fired() == 3, f"安値ブレイクも検知するべき: {fired()}"

with m.db_connection() as c:
    rows = c.execute("SELECT kind, price FROM breakout_events").fetchall()
    alerts = c.execute("SELECT kind, message FROM alert_events").fetchall()
print("breakout_events:", [(r["kind"], r["price"]) for r in rows])
print("alert_events:", [(r["kind"], r["message"]) for r in alerts])
print("\n全テスト合格")
