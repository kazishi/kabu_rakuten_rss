# -*- coding: utf-8 -*-
"""分類DBのメモリロードとテーマ別資金フロー集計。

app.py から `load_classification()` を起動時に1回だけ呼ぶ。
`compute_sector_flow()` は /api/sector_flow のたびに呼ぶ（100銘柄規模なら十分軽い）。
"""
import sqlite3
from typing import Any


def load_classification(db_path: str) -> dict[str, Any]:
    """DBを全件読み込みメモリdictへ展開する。起動時に1回だけ呼ぶ。

    returns {
      "stocks": {code: {"big_cat", "mid_cat", "small_cat", "name"}},
      "themes": {theme_id: {"name", "category"}},
      "memberships": {code: [{theme_id, weight, role}]},
    }
    """
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row

    stocks = {
        r["code"]: {"name": r["name"], "big_cat": r["big_cat"],
                    "mid_cat": r["mid_cat"], "small_cat": r["small_cat"]}
        for r in con.execute("SELECT code, name, big_cat, mid_cat, small_cat FROM stocks")
    }
    themes = {
        r["theme_id"]: {"name": r["name"], "category": r["category"]}
        for r in con.execute("SELECT theme_id, name, category FROM themes")
    }
    memberships: dict[str, list[dict]] = {}
    for r in con.execute("SELECT code, theme_id, weight, role FROM stock_themes"):
        memberships.setdefault(r["code"], []).append(
            {"theme_id": r["theme_id"], "weight": float(r["weight"]), "role": r["role"]}
        )

    con.close()
    return {"stocks": stocks, "themes": themes, "memberships": memberships}


def _flow_day(s: dict) -> float:
    if "flow_day" in s and s.get("flow_day") is not None:
        return float(s.get("flow_day") or 0.0)
    t = s.get("turnover") or 0.0
    c = s.get("change_pct") or 0.0
    o = s.get("from_open") or 0.0
    v = s.get("vwap_gap") or 0.0
    return t * (c * 0.5 + o * 0.3 + v * 0.2)


def _flow_15m(s: dict) -> float:
    d = s.get("turnover_delta_15m")
    c = s.get("change_15m")
    if d is None or c is None:
        return 0.0
    return d * c


def _flow_5m(s: dict) -> float:
    d = s.get("turnover_delta_5m")
    c = s.get("change_5m")
    if d is None or c is None:
        return 0.0
    return d * c


def _flow_60m(s: dict) -> float:
    d = s.get("turnover_delta_60m")
    c = s.get("change_60m")
    if d is None or c is None:
        return 0.0
    return d * c


_MEMBER_SORT_KEY = {
    "5m": "flow_5m_w", "15m": "flow_15m_w",
    "60m": "flow_60m_w", "day": "flow_day_w", "week": "flow_day_w",
}

_MEMBER_CHANGE_KEY = {
    "5m": "change_5m", "15m": "change_15m", "60m": "change_60m",
    "day": "change_pct", "week": "change_pct",
}


def _empty_result(note: str) -> dict:
    return {"themes": {}, "big_cats": {}, "mid_cats": {}, "sample_note": note}


def compute_sector_flow(
    stocks: list[dict],
    classif: dict,
    sort_tf: str = "15m",
    include_members: bool = False,
    member_limit: int | None = None,
) -> dict:
    """監視銘柄スナップショットからテーマ/NEEDS大分類別の資金フローを集計する。

    テーマ集計: 多重ラベル銘柄は weight 付きで重複計上（意図的）。
    big_cat集計: 1銘柄1所属のため非重複（保存則ビュー）。
    """
    if not classif or not all(k in classif for k in ("memberships", "themes", "stocks")):
        return _empty_result("分類DBが未ロードです。classification/classification.db を確認してください。")

    memberships = classif["memberships"]
    themes_meta = classif["themes"]
    stocks_meta = classif["stocks"]

    theme_acc: dict[str, dict] = {}
    bigcat_acc: dict[str, dict] = {}
    midcat_acc: dict[str, dict] = {}

    for s in stocks:
        code = s.get("code", "")
        turnover = s.get("turnover") or 0.0
        fd = _flow_day(s)
        f15 = _flow_15m(s)
        f5 = _flow_5m(s)
        f60 = _flow_60m(s)
        price = s.get("price") or 0.0
        change = s.get("change_pct")
        is_up = (change or 0.0) > 0

        # テーマ別集計（重み付き）
        for m in memberships.get(code, []):
            tid = m["theme_id"]
            w = m["weight"]
            if tid not in theme_acc:
                meta = themes_meta.get(tid, {})
                theme_acc[tid] = {
                    "name": meta.get("name", tid),
                    "category": meta.get("category", ""),
                    "stock_count": 0,
                    "turnover_sum_raw": 0.0,
                    "turnover_sum_weighted": 0.0,
                    "flow_day_sum_raw": 0.0,
                    "flow_day_sum_weighted": 0.0,
                    "flow_15m_sum_raw": 0.0,
                    "flow_15m_sum_weighted": 0.0,
                    "flow_5m_sum_weighted": 0.0,
                    "flow_60m_sum_weighted": 0.0,
                    "_weight_total": 0.0,
                    "_weight_up": 0.0,
                    "_members": [],
                }
            a = theme_acc[tid]
            a["stock_count"] += 1
            a["turnover_sum_raw"] += turnover
            a["turnover_sum_weighted"] += turnover * w
            a["flow_day_sum_raw"] += fd
            a["flow_day_sum_weighted"] += fd * w
            a["flow_15m_sum_raw"] += f15
            a["flow_15m_sum_weighted"] += f15 * w
            a["flow_5m_sum_weighted"] += f5 * w
            a["flow_60m_sum_weighted"] += f60 * w
            a["_weight_total"] += w
            if is_up:
                a["_weight_up"] += w
            a["_members"].append({
                "code": code,
                "name": s.get("name", ""),
                "flow_15m_w": f15 * w,
                "flow_5m_w": f5 * w,
                "flow_60m_w": f60 * w,
                "flow_day_w": fd * w,
                "turnover": turnover,
                "change_pct": change,
                "change_5m":  s.get("change_5m"),
                "change_15m": s.get("change_15m"),
                "change_60m": s.get("change_60m"),
            })

        # big_cat / mid_cat 集計（非重複）
        cat_info = stocks_meta.get(code, {})
        member_row = {
            "code": code, "name": s.get("name", ""),
            "flow_day": fd, "flow_5m": f5, "flow_15m": f15, "flow_60m": f60,
            "turnover": turnover, "change_pct": change,
            "change_5m": s.get("change_5m"),
            "change_15m": s.get("change_15m"),
            "change_60m": s.get("change_60m"),
        }
        for acc_dict, cat_key in ((bigcat_acc, "big_cat"), (midcat_acc, "mid_cat")):
            cat_val = cat_info.get(cat_key) or "未分類"
            if cat_val not in acc_dict:
                acc_dict[cat_val] = {
                    "turnover_sum": 0.0,
                    "flow_day_sum": 0.0, "flow_5m_sum": 0.0,
                    "flow_15m_sum": 0.0, "flow_60m_sum": 0.0,
                    "stock_count": 0, "_members": [],
                }
            a = acc_dict[cat_val]
            a["turnover_sum"] += turnover
            a["flow_day_sum"] += fd
            a["flow_5m_sum"]  += f5
            a["flow_15m_sum"] += f15
            a["flow_60m_sum"] += f60
            a["stock_count"]  += 1
            a["_members"].append(member_row)

    # 各テーマ: breadth計算 + leaders/laggards。membersは詳細API用に必要な時だけ返す。
    for tid, a in theme_acc.items():
        wt = a["_weight_total"]
        raw = a["_members"]  # 生データ全件
        a["breadth"] = (
            sum(1 for s in raw if (s.get("change_pct") or 0) > 0) / len(raw)
            if raw else 0.0
        )
        a["weighted_breadth"] = a["_weight_up"] / wt if wt > 0 else 0.0
        sk = _MEMBER_SORT_KEY.get(sort_tf, "flow_15m_w")
        ck = _MEMBER_CHANGE_KEY.get(sort_tf, "change_pct")
        sorted_l = sorted(raw, key=lambda x: x[sk], reverse=True)
        members = [
            {"code": x["code"], "name": x["name"], "flow": x[sk],
             "turnover": x["turnover"], "change_pct": x["change_pct"],
             "change_tf": x.get(ck)}
            for x in sorted_l
        ]
        if include_members:
            a["members"] = members if member_limit is None else members[:member_limit]
        a["leaders"] = [{"code": x["code"], "name": x["name"]} for x in sorted_l[:3]]
        a["laggards"] = [{"code": x["code"], "name": x["name"]} for x in reversed(sorted_l[-3:])]
        del a["_weight_total"], a["_weight_up"], a["_members"]

    # 大分類 / 中分類: TF対応フローで降順ソート、change_tf付与
    _cat_flow_key = {
        "5m": "flow_5m", "15m": "flow_15m", "60m": "flow_60m",
        "day": "flow_day", "week": "flow_day",
    }
    bk = _cat_flow_key.get(sort_tf, "flow_day")
    ck = _MEMBER_CHANGE_KEY.get(sort_tf, "change_pct")
    for acc_dict in (bigcat_acc, midcat_acc):
        for b in acc_dict.values():
            members = sorted(
                [{"code": x["code"], "name": x["name"], "flow": x[bk],
                  "turnover": x["turnover"], "change_pct": x["change_pct"],
                  "change_tf": x.get(ck)}
                 for x in b["_members"]],
                key=lambda x: x["flow"], reverse=True,
            )
            if include_members:
                b["members"] = members if member_limit is None else members[:member_limit]
            del b["_members"]

    return {
        "themes": theme_acc,
        "big_cats": bigcat_acc,
        "mid_cats": midcat_acc,
        "sample_note": (
            "監視銘柄サンプル。全市場集計ではありません。"
            "テーマ集計はマルチテーマ銘柄を重み付きで重複計上します。"
        ),
    }


def compute_sector_flow_daily(
    con: sqlite3.Connection,
    classif: dict,
    tf: str,
    include_members: bool = False,
    member_limit: int | None = None,
) -> dict:
    """daily_ohlcから全市場のテーマ/大分類別資金フローを集計する。

    tf='day': 最新取引日の前日比方向 × 当日売買代金
    tf='week': 直近5取引日の騰落率方向 × 5日合計売買代金
    """
    if not classif or not all(k in classif for k in ("memberships", "themes", "stocks")):
        return _empty_result("分類DBが未ロードです。classification/classification.db を確認してください。")

    dates = [r[0] for r in con.execute(
        "SELECT DISTINCT date FROM daily_ohlc ORDER BY date DESC LIMIT 7"
    )]
    if not dates:
        return _empty_result("日足データなし（day_stock_data にCSVを配置するか、楽天RSSを起動してください）")

    stocks_meta = classif["stocks"]
    synthetic: list[dict] = []

    if tf == "day":
        today = dates[0]
        yesterday = dates[1] if len(dates) > 1 else None

        today_rows: dict[str, tuple] = {}
        for r in con.execute(
            "SELECT code, close, volume, open FROM daily_ohlc WHERE date = ?",
            (today,),
        ):
            c, v, o = r[1], r[2] or 0.0, r[3]
            if c and v > 0:
                today_rows[r[0]] = (c, v, o)

        prev_closes: dict[str, float] = {}
        if yesterday:
            for r in con.execute(
                "SELECT code, close FROM daily_ohlc WHERE date = ?",
                (yesterday,),
            ):
                if r[1]:
                    prev_closes[r[0]] = float(r[1])

        for code, (c, v, o) in today_rows.items():
            if code not in stocks_meta:
                continue
            prev_c = prev_closes.get(code)
            change_pct = (c / prev_c - 1) if prev_c and prev_c > 0 else 0.0
            from_open = (c / o - 1) if o and o > 0 else 0.0
            name = stocks_meta.get(code, {}).get("name", code)
            synthetic.append({
                "code": code, "name": name,
                "turnover": c * v / 1000,
                "flow_day": (c * v / 1000) * change_pct,
                "change_pct": change_pct,
                "from_open": from_open,
                "vwap_gap": 0.0,
                "turnover_delta_15m": None, "change_15m": None,
                "turnover_delta_5m": None,  "change_5m": None,
                "turnover_delta_60m": None, "change_60m": None,
            })

        note = (
            f"日足（{today}）全市場 {len(synthetic):,}銘柄"
            " ／ 確定CSVのみ ／ フロー=当日close×volume×前日比"
        )

    else:  # week
        target_dates = dates[:5]
        if len(target_dates) < 2:
            return _empty_result("日足データ不足（2日分未満）")

        ph = ",".join("?" * len(target_dates))
        rows = con.execute(
            f"SELECT code, close, volume, date FROM daily_ohlc"
            f" WHERE date IN ({ph}) ORDER BY code, date",
            target_dates,
        ).fetchall()

        code_data: dict[str, dict] = {}
        for r in rows:
            code, c, v, d = r[0], r[1], r[2] or 0.0, r[3]
            if not c:
                continue
            if code not in code_data:
                code_data[code] = {"days": {}, "total_yen": 0.0}
            code_data[code]["days"][d] = float(c)
            code_data[code]["total_yen"] += c * v

        for code, cd in code_data.items():
            if code not in stocks_meta:
                continue
            sd = sorted(cd["days"])
            if len(sd) < 2:
                continue
            c0, c1 = cd["days"][sd[0]], cd["days"][sd[-1]]
            change_5d = (c1 / c0 - 1) if c0 > 0 else 0.0
            name = stocks_meta.get(code, {}).get("name", code)
            synthetic.append({
                "code": code, "name": name,
                "turnover": cd["total_yen"] / 1000,
                "flow_day": (cd["total_yen"] / 1000) * change_5d,
                "change_pct": change_5d,
                "from_open": 0.0, "vwap_gap": 0.0,
                "turnover_delta_15m": None, "change_15m": None,
                "turnover_delta_5m": None,  "change_5m": None,
                "turnover_delta_60m": None, "change_60m": None,
            })

        d_range = f"{target_dates[-1]}〜{target_dates[0]}"
        note = (
            f"週間（{d_range}）全市場 {len(synthetic):,}銘柄"
            " ／ 確定CSVのみ ／ フロー=5日合計close×volume×5日騰落率"
        )

    result = compute_sector_flow(
        synthetic,
        classif,
        sort_tf="day",
        include_members=include_members,
        member_limit=member_limit,
    )
    result["sample_note"] = note
    return result
