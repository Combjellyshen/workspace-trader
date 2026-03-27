#!/usr/bin/env python3
"""
intraday_alert.py — 盘中异动检测引擎
纯数据计算，不调用模型，消耗接近0 token。

用法:
  python3 scripts/analysis/intraday_alert.py check      # 计算当前异动评分，输出JSON
  python3 scripts/analysis/intraday_alert.py snapshot   # 存快照到 data/intraday/YYYY-MM-DD-HHMM.json
"""

import sys
import json
import os
import glob
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

_HERE = Path(__file__).resolve().parents[2]
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from scripts.utils.common import safe_float, load_watchlist, WORKSPACE_ROOT  # noqa: E402

# ─── 路径配置 ─────────────────────────────────────────────────────────────────
WORKSPACE = WORKSPACE_ROOT
WATCHLIST_PATH = WORKSPACE / "watchlist.json"
INTRADAY_DIR = WORKSPACE / "data" / "intraday"
SH_TZ = ZoneInfo("Asia/Shanghai")

# ─── 工具函数 ────────────────────────────────────────────────────────────────

def now_shanghai() -> datetime:
    return datetime.now(SH_TZ)


def warn(msg: str):
    print(f"[intraday_alert] {msg}", file=sys.stderr)


def infer_market(code: str) -> str:
    code = str(code)
    if code.startswith(("6", "9", "5")):
        return "sh"
    if code.startswith(("8", "4")):
        return "bj"
    return "sz"


def is_etf(item: dict) -> bool:
    """判断 watchlist 条目是否为 ETF"""
    ptype = (item.get("type") or "").lower()
    code = str(item.get("code", ""))
    name = item.get("name", "")
    return (
        ptype == "etf"
        or code.startswith(("51", "56", "58", "15", "16"))
        or "etf" in name.lower()
    )


def pick_col(columns, exact_names=None, contains=None, exclude=None):
    """按优先级挑列，优先精确匹配，避免把振幅/5分钟涨跌幅误当成涨跌幅。"""
    exact_names = exact_names or []
    contains = contains or []
    exclude = exclude or []

    cols = list(columns)
    for name in exact_names:
        if name in cols:
            return name

    for col in cols:
        if all(token in col for token in contains) and not any(token in col for token in exclude):
            return col

    return None


# ─── ETF 数据获取 ─────────────────────────────────────────────────────────

_etf_daily_cache = None


def _fetch_etf_daily():
    """
    获取 ETF 日度数据（fund_etf_fund_daily_em），返回 {code: {...}} 字典。
    含净值、市价、增长率、折价率。同一次 check 内缓存，避免重复调用。
    """
    global _etf_daily_cache
    if _etf_daily_cache is not None:
        return _etf_daily_cache

    _etf_daily_cache = {}
    try:
        import akshare as ak
        df = ak.fund_etf_fund_daily_em()
        if df is None or df.empty:
            return _etf_daily_cache

        code_col = pick_col(df.columns, exact_names=["基金代码"], contains=["代码"])
        if not code_col:
            return _etf_daily_cache

        # 列名含日期前缀，动态适配
        # 注意：有两组净值列（最新日期 + 前一日期），用于计算 T-1 收盘价
        nav_cols = sorted([c for c in df.columns if '单位净值' in c], reverse=True)
        nav_col = nav_cols[0] if nav_cols else None       # 最新净值（T-1 日）
        prev_nav_col = nav_cols[1] if len(nav_cols) > 1 else None  # 前一日净值（T-2 日）
        discount_col = None
        price_col = None
        for col in df.columns:
            if "折价率" in col:
                discount_col = col
            elif col == "市价":
                price_col = col

        for _, row in df.iterrows():
            code = str(row[code_col]).zfill(6)
            nav = safe_float(row.get(nav_col, 0)) if nav_col else 0.0
            prev_nav = safe_float(row.get(prev_nav_col, 0)) if prev_nav_col else 0.0
            discount_raw = str(row.get(discount_col, "0")) if discount_col else "0"
            discount_pct = safe_float(discount_raw.replace("%", ""))
            market_price = safe_float(row.get(price_col, 0)) if price_col else 0.0

            # ── 关键修复：用市价 vs 最新净值算今日真实涨跌幅 ──
            # fund_etf_fund_daily_em 的"增长率"是 T-1 净值 vs T-2 净值（昨天的变化），
            # 不是今天的。真正的今日涨跌幅应为：市价 vs T-1 日净值（近似昨收）。
            if nav > 0 and market_price > 0:
                change_pct = round((market_price - nav) / nav * 100, 2)
            elif prev_nav > 0 and nav > 0:
                # fallback：如果市价不可用，用 T-1 vs T-2 净值（即"增长率"列的含义）
                change_pct = round((nav - prev_nav) / prev_nav * 100, 2)
            else:
                change_pct = 0.0

            _etf_daily_cache[code] = {
                "nav": nav,
                "market_price": market_price,
                "change_pct": change_pct,
                "discount_pct": discount_pct,
                "nav_date": nav_col.split('-单位净值')[0] if nav_col and '-单位净值' in nav_col else "",
            }
    except Exception as e:
        warn(f"ETF daily data unavailable: {e}")

    return _etf_daily_cache


# ─── 维度1：自选股价格异动 ───────────────────────────────────────────────────

def check_price_alerts(watchlist):
    """
    用 ak.stock_zh_a_spot_em() 获取全市场实时行情，筛出自选股，检测涨跌幅。
    返回 (alerts, stocks_snapshot)
    """
    alerts = []
    stocks_snapshot = []

    try:
        # 优先使用 HTTP push2（绕过 HTTPS 封锁），fallback 到 akshare
        from scripts.utils.common import fetch_a_stock_spot
        spot_data = fetch_a_stock_spot(top=5000)
        if spot_data:
            import pandas as pd
            df = pd.DataFrame(spot_data)
        else:
            import akshare as ak
            df = ak.stock_zh_a_spot_em()

        # 建立自选股 code → name 映射
        watchlist_codes = {s["code"]: s["name"] for s in watchlist}

        # 精确适配列名
        code_col = pick_col(df.columns, exact_names=["代码", "证券代码"], contains=["代码"])
        name_col = pick_col(df.columns, exact_names=["名称", "证券简称"], contains=["名称"])
        pct_col = pick_col(df.columns, exact_names=["涨跌幅"], contains=["涨跌", "幅"], exclude=["5分钟", "3日", "5日", "10日", "20日", "60日", "年初", "振幅"])
        price_col = pick_col(df.columns, exact_names=["最新价", "当前价", "现价"], contains=["价"], exclude=["最高", "最低", "今开", "昨收", "参考"])
        pre_close_col = pick_col(df.columns, exact_names=["昨收", "昨日收盘", "前收盘"], contains=["昨收"])

        if not code_col:
            return alerts, stocks_snapshot

        # 筛出自选股
        df_watch = df[df[code_col].astype(str).str.zfill(6).isin(watchlist_codes.keys())]

        for _, row in df_watch.iterrows():
            code = str(row[code_col]).zfill(6)
            name = watchlist_codes.get(code, row.get(name_col, "") if name_col else "")
            price = safe_float(row.get(price_col, 0)) if price_col else 0.0
            pct = safe_float(row.get(pct_col, 0)) if pct_col else 0.0
            pre_close = safe_float(row.get(pre_close_col, 0)) if pre_close_col else 0.0

            # 若涨跌幅列异常，但价格/昨收可用，则重新计算
            if pre_close > 0:
                calc_pct = round((price - pre_close) / pre_close * 100, 2)
                if abs(pct - calc_pct) > 3.0:
                    pct = calc_pct

            stock_info = {
                "code": code,
                "name": name,
                "price": price,
                "change_pct": round(pct, 2),
                "main_flow_yi": 0.0  # 后续由维度4填充
            }
            stocks_snapshot.append(stock_info)

            # 判断异动
            level = None
            detail = ""
            if pct > 5:
                level = "red"
                detail = f"涨幅 +{pct:.1f}%，强势拉升"
            elif pct > 3:
                level = "yellow"
                detail = f"涨幅 +{pct:.1f}%，突破前高"
            elif pct < -5:
                level = "red"
                detail = f"跌幅 {pct:.1f}%，大幅下跌"
            elif pct < -3:
                level = "yellow"
                detail = f"跌幅 {pct:.1f}%，跌势明显"

            if level:
                alerts.append({
                    "type": "price",
                    "code": code,
                    "name": name,
                    "level": level,
                    "detail": detail,
                    "action_hint": "关注量能是否配合" if pct > 0 else "关注是否跌破支撑位"
                })

        # 补全未出现在 A 股行情中的自选股（含 ETF）
        returned_codes = {s["code"] for s in stocks_snapshot}
        missing = [s for s in watchlist if s["code"] not in returned_codes]

        if missing:
            etf_data = _fetch_etf_daily()
            for s in missing:
                code = s["code"]
                if code in etf_data:
                    ed = etf_data[code]
                    price = ed["market_price"]
                    pct = ed["change_pct"]
                    snap = {
                        "code": code,
                        "name": s["name"],
                        "price": price,
                        "change_pct": round(pct, 2),
                        "main_flow_yi": 0.0,
                        "is_etf": True,
                        "nav": ed["nav"],
                        "discount_pct": ed["discount_pct"],
                    }
                    stocks_snapshot.append(snap)

                    # ETF 也按同样的涨跌幅阈值触发价格异动
                    level = None
                    detail = ""
                    if pct > 5:
                        level = "red"
                        detail = f"ETF涨幅 +{pct:.1f}%，强势拉升"
                    elif pct > 3:
                        level = "yellow"
                        detail = f"ETF涨幅 +{pct:.1f}%，涨势明显"
                    elif pct < -5:
                        level = "red"
                        detail = f"ETF跌幅 {pct:.1f}%，大幅下跌"
                    elif pct < -3:
                        level = "yellow"
                        detail = f"ETF跌幅 {pct:.1f}%，跌势明显"

                    if level:
                        alerts.append({
                            "type": "price",
                            "code": code,
                            "name": s["name"],
                            "level": level,
                            "detail": detail,
                            "action_hint": "关注量能是否配合" if pct > 0 else "关注是否跌破支撑位"
                        })
                else:
                    stocks_snapshot.append({
                        "code": code,
                        "name": s["name"],
                        "price": 0.0,
                        "change_pct": 0.0,
                        "main_flow_yi": 0.0
                    })

    except Exception as e:
        warn(f"price alerts fallback to placeholders: {e}")
        # A 股接口失败，仍尝试为 ETF 拉取真实数据
        etf_data = _fetch_etf_daily()
        for s in watchlist:
            code = s["code"]
            if is_etf(s) and code in etf_data:
                ed = etf_data[code]
                stocks_snapshot.append({
                    "code": code,
                    "name": s["name"],
                    "price": ed["market_price"],
                    "change_pct": round(ed["change_pct"], 2),
                    "main_flow_yi": 0.0,
                    "is_etf": True,
                    "nav": ed["nav"],
                    "discount_pct": ed["discount_pct"],
                })
            else:
                stocks_snapshot.append({
                    "code": code,
                    "name": s["name"],
                    "price": 0.0,
                    "change_pct": 0.0,
                    "main_flow_yi": 0.0
                })

    return alerts, stocks_snapshot


# ─── 维度2：大盘异动 ─────────────────────────────────────────────────────────

def check_index_alerts():
    """
    获取上证指数涨跌幅，检测大盘异动。
    返回 (alerts, sh_index_snapshot)
    """
    alerts = []
    sh_snapshot = {"price": 0.0, "change_pct": 0.0, "status": "ok"}

    try:
        # 优先使用 HTTP push2 获取指数
        from scripts.utils.common import fetch_index_spot
        idx_data = fetch_index_spot()
        sh_found = False
        if idx_data:
            for ix in idx_data:
                if ix.get('代码') == '000001':
                    pct = ix.get('涨跌幅', 0)
                    price = ix.get('最新价', 0)
                    sh_snapshot = {"price": round(price, 2), "change_pct": round(pct, 2), "status": "ok"}
                    sh_found = True
                    if pct < -2.5:
                        alerts.append({"type": "index", "code": "000001", "name": "上证指数", "level": "red",
                                       "detail": f"沪指大跌 {pct:.2f}%，市场恐慌", "action_hint": "考虑降低仓位，等待企稳"})
                    elif pct < -1.5:
                        alerts.append({"type": "index", "code": "000001", "name": "上证指数", "level": "yellow",
                                       "detail": f"沪指下跌 {pct:.2f}%，注意风险", "action_hint": "关注是否跌破关键支撑"})
                    elif pct > 2.0:
                        alerts.append({"type": "index", "code": "000001", "name": "上证指数", "level": "yellow",
                                       "detail": f"沪指大涨 +{pct:.2f}%，关注追高风险", "action_hint": "强势信号，但注意高位风险"})
                    break
        if sh_found:
            return alerts, sh_snapshot

        # fallback: akshare
        import akshare as ak
        df = ak.stock_zh_index_spot_em(symbol="上证系列指数")
        code_col = None
        pct_col = None
        price_col = None
        for col in df.columns:
            if "代码" in col:
                code_col = col
            elif "涨跌幅" in col:
                pct_col = col
            elif "最新价" in col or "当前价" in col:
                price_col = col

        sh_row = None
        if code_col:
            sh_rows = df[df[code_col] == "000001"]
            if not sh_rows.empty:
                sh_row = sh_rows.iloc[0]

        if sh_row is not None and pct_col:
            pct = safe_float(sh_row.get(pct_col, 0))
            price = safe_float(sh_row.get(price_col, 0)) if price_col else 0.0
            sh_snapshot = {"price": round(price, 2), "change_pct": round(pct, 2), "status": "ok"}

            if pct < -2.5:
                alerts.append({
                    "type": "index",
                    "code": "000001",
                    "name": "上证指数",
                    "level": "red",
                    "detail": f"沪指大跌 {pct:.2f}%，市场恐慌",
                    "action_hint": "考虑降低仓位，等待企稳"
                })
            elif pct < -1.5:
                alerts.append({
                    "type": "index",
                    "code": "000001",
                    "name": "上证指数",
                    "level": "yellow",
                    "detail": f"沪指下跌 {pct:.2f}%，注意风险",
                    "action_hint": "关注是否跌破关键支撑"
                })
            elif pct > 2.0:
                alerts.append({
                    "type": "index",
                    "code": "000001",
                    "name": "上证指数",
                    "level": "yellow",
                    "detail": f"沪指大涨 +{pct:.2f}%，关注追高风险",
                    "action_hint": "强势信号，但注意高位风险"
                })
        else:
            sh_snapshot["status"] = "market_closed"

    except Exception as e:
        warn(f"index alerts unavailable: {e}")
        sh_snapshot["status"] = "market_closed"

    return alerts, sh_snapshot


# ─── 维度3：北向资金 ─────────────────────────────────────────────────────────

def check_north_flow():
    """
    获取北向资金净流入，检测北向异动。
    返回 (alerts, north_flow_yi, north_status)
    """
    alerts = []
    north_flow_yi = 0.0
    north_status = "ok"

    try:
        import akshare as ak

        # 获取北向资金汇总（沪股通+深股通）
        df = ak.stock_hsgt_fund_flow_summary_em()
        if df is None or df.empty:
            north_status = "unavailable"
            return alerts, north_flow_yi, north_status

        # 合并沪股通+深股通的成交净买额
        north_rows = df[df['资金方向'] == '北向']
        if north_rows.empty:
            north_status = "unavailable"
            return alerts, north_flow_yi, north_status

        val = safe_float(north_rows['成交净买额'].sum())
        # 单位为亿
        if abs(val) > 1e4:
            val = val / 1e8
        north_flow_yi = round(val, 2)

        if north_flow_yi < -150:
            alerts.append({
                "type": "north_flow",
                "code": "",
                "name": "北向资金",
                "level": "red",
                "detail": f"北向净流出 {abs(north_flow_yi):.1f}亿，外资大幅撤退",
                "action_hint": "外资大幅出逃，谨慎"
            })
        elif north_flow_yi < -80:
            alerts.append({
                "type": "north_flow",
                "code": "",
                "name": "北向资金",
                "level": "yellow",
                "detail": f"北向净流出 {abs(north_flow_yi):.1f}亿，外资持续流出",
                "action_hint": "关注外资动向"
            })
        elif north_flow_yi > 80:
            alerts.append({
                "type": "north_flow",
                "code": "",
                "name": "北向资金",
                "level": "yellow",
                "detail": f"北向净流入 +{north_flow_yi:.1f}亿，外资积极布局",
                "action_hint": "外资净流入偏强，偏多信号"
            })

    except Exception as e:
        warn(f"north flow unavailable: {e}")
        north_status = "unavailable"

    return alerts, north_flow_yi, north_status


# ─── 维度4：主力资金 ─────────────────────────────────────────────────────────

def check_main_flow(watchlist, stocks_snapshot):
    """
    获取自选股主力资金（大单净流入），检测主力异动。
    stocks_snapshot 会被原地更新 main_flow_yi 字段。
    返回 alerts
    """
    alerts = []

    try:
        import akshare as ak

        # 建立 code → snapshot 索引
        snap_map = {s["code"]: s for s in stocks_snapshot}

        for stock in watchlist:
            code = stock["code"]
            name = stock["name"]

            # ETF 跳过主力资金（由 check_etf_alerts 维度处理）
            if is_etf(stock):
                continue

            # 判断市场
            market = infer_market(code)

            try:
                df = ak.stock_individual_fund_flow(stock=code, market=market)
                if df is None or df.empty:
                    continue

                # 列名适配
                date_col = None
                main_col = None
                for col in df.columns:
                    if "日期" in col or col.lower() == "date":
                        date_col = col
                    if main_col is None and "主力" in col and "净" in col:
                        main_col = col
                    elif main_col is None and "主力净流入" in col:
                        main_col = col
                if main_col is None:
                    for col in df.columns:
                        if "净额" in col or "净流入" in col:
                            main_col = col
                            break

                if not main_col:
                    continue

                # ── 关键修复：校验最后一行的日期是否为今天 ──
                latest = df.iloc[-1]
                today_str = now_shanghai().strftime("%Y-%m-%d")
                is_today = False
                data_date = ""

                if date_col:
                    data_date = str(latest.get(date_col, ""))[:10]
                    is_today = data_date == today_str

                val = safe_float(latest.get(main_col, 0))
                if abs(val) > 1000:
                    val = val / 1e8
                main_flow_yi = round(val, 4)

                # 更新 snapshot（标注数据日期）
                if code in snap_map:
                    snap_map[code]["main_flow_yi"] = main_flow_yi
                    snap_map[code]["main_flow_date"] = data_date
                    snap_map[code]["main_flow_is_today"] = is_today

                # 异动判断（5000万 = 0.5亿）
                # 仅今日数据触发异动；非今日数据降级为 info 级别，不计入 alert_score
                if is_today:
                    if main_flow_yi > 0.5:
                        alerts.append({
                            "type": "main_flow",
                            "code": code,
                            "name": name,
                            "level": "yellow",
                            "detail": f"主力净流入 +{main_flow_yi:.2f}亿（今日盘中）",
                            "action_hint": "主力吸筹信号",
                            "signal": "吸筹"
                        })
                    elif main_flow_yi < -0.5:
                        alerts.append({
                            "type": "main_flow",
                            "code": code,
                            "name": name,
                            "level": "yellow",
                            "detail": f"主力净流出 {main_flow_yi:.2f}亿（今日盘中）",
                            "action_hint": "主力出货信号，注意风险",
                            "signal": "出货"
                        })
                else:
                    # 非今日数据：记录但不触发异动（避免旧快照误报）
                    if abs(main_flow_yi) > 0.5:
                        warn(f"{code} 主力资金数据为 {data_date}（非今日），"
                             f"净额 {main_flow_yi:.2f}亿，不计入盘中异动")

            except Exception as e:
                warn(f"main flow unavailable for {code}: {e}")
                # 单只股票失败，跳过继续
                continue

    except Exception as e:
        warn(f"main flow checks unavailable: {e}")

    return alerts


# ─── 维度4b：ETF 折溢价异动 ──────────────────────────────────────────────────

# 阈值：主题型 ETF 折溢价波动相对大，1.5% 已算显著
ETF_DISCOUNT_YELLOW = 1.5   # 折/溢价绝对值 ≥ 1.5% → yellow
ETF_DISCOUNT_RED = 3.0      # 折/溢价绝对值 ≥ 3.0% → red


def check_etf_alerts(watchlist, stocks_snapshot):
    """
    对 watchlist 中的 ETF 检测折溢价异动。
    利用 _fetch_etf_daily() 缓存数据，同时回填 snapshot 中的折溢价字段。
    返回 alerts
    """
    alerts = []
    etf_items = [s for s in watchlist if is_etf(s)]
    if not etf_items:
        return alerts

    etf_data = _fetch_etf_daily()
    snap_map = {s["code"]: s for s in stocks_snapshot}

    for item in etf_items:
        code = item["code"]
        name = item["name"]
        ed = etf_data.get(code)
        if not ed:
            continue

        discount = ed["discount_pct"]

        # 回填 snapshot
        if code in snap_map:
            snap_map[code]["discount_pct"] = discount
            snap_map[code]["nav"] = ed["nav"]

        # 折溢价异动判断
        abs_disc = abs(discount)
        if abs_disc >= ETF_DISCOUNT_RED:
            direction = "溢价" if discount > 0 else "折价"
            alerts.append({
                "type": "etf_discount",
                "code": code,
                "name": name,
                "level": "red",
                "detail": f"ETF{direction} {abs_disc:.2f}%，净值{ed['nav']:.4f} vs 市价{ed['market_price']:.4f}",
                "action_hint": f"{'高溢价追高风险大' if discount > 0 else '深度折价，关注赎回压力'}",
            })
        elif abs_disc >= ETF_DISCOUNT_YELLOW:
            direction = "溢价" if discount > 0 else "折价"
            alerts.append({
                "type": "etf_discount",
                "code": code,
                "name": name,
                "level": "yellow",
                "detail": f"ETF{direction} {abs_disc:.2f}%，净值{ed['nav']:.4f} vs 市价{ed['market_price']:.4f}",
                "action_hint": f"{'溢价偏高，注意回落风险' if discount > 0 else '折价偏大，观察是否企稳'}",
            })

    return alerts


# ─── 维度5：快照对比 ─────────────────────────────────────────────────────────

def check_snapshot_diff(stocks_snapshot):
    """
    读取 data/intraday/ 下今天最近一次快照，与当前对比。
    返回 alerts
    """
    alerts = []

    today_str = now_shanghai().strftime("%Y-%m-%d")
    pattern = str(INTRADAY_DIR / f"{today_str}-*.json")
    files = sorted(glob.glob(pattern))

    if not files:
        return alerts

    latest_file = files[-1]
    try:
        with open(latest_file, "r", encoding="utf-8") as f:
            prev = json.load(f)

        prev_stocks = {s["code"]: s for s in prev.get("snapshot", {}).get("stocks", [])}
        curr_stocks = {s["code"]: s for s in stocks_snapshot}

        for code, curr in curr_stocks.items():
            if code not in prev_stocks:
                continue
            prev_s = prev_stocks[code]

            curr_pct = curr.get("change_pct", 0)
            prev_pct = prev_s.get("change_pct", 0)
            delta = curr_pct - prev_pct

            if abs(delta) >= 2.0:
                direction = "上涨" if delta > 0 else "下跌"
                alerts.append({
                    "type": "snapshot_diff",
                    "code": code,
                    "name": curr.get("name", code),
                    "level": "yellow",
                    "detail": f"盘中{direction}变化 {delta:+.1f}%（前次快照 {prev_pct:+.1f}% → 当前 {curr_pct:+.1f}%）",
                    "action_hint": f"短期{direction}幅度较大，注意节奏"
                })

            # 主力资金方向反转
            curr_flow = curr.get("main_flow_yi", 0)
            prev_flow = prev_s.get("main_flow_yi", 0)
            if prev_flow != 0 and curr_flow != 0:
                if (prev_flow > 0.1 and curr_flow < -0.1) or (prev_flow < -0.1 and curr_flow > 0.1):
                    alerts.append({
                        "type": "snapshot_diff",
                        "code": code,
                        "name": curr.get("name", code),
                        "level": "yellow",
                        "detail": f"主力资金方向反转（前次 {prev_flow:+.2f}亿 → 当前 {curr_flow:+.2f}亿）",
                        "action_hint": "资金方向反转，需重新评估"
                    })

    except Exception as e:
        warn(f"snapshot diff unavailable: {e}")

    return alerts


# ─── 计算 alert_score ────────────────────────────────────────────────────────

def calc_score(alerts):
    score = 0
    for a in alerts:
        if a.get("level") == "red":
            score += 2
        elif a.get("level") == "yellow":
            score += 1
    return score


# ─── 生成 summary ────────────────────────────────────────────────────────────

def gen_summary(alerts, alert_score, sh_snapshot, stocks_snapshot, north_flow_yi, north_status):
    if alert_score == 0:
        sh_pct = sh_snapshot.get("change_pct", 0)
        sh_str = f"+{sh_pct:.2f}%" if sh_pct >= 0 else f"{sh_pct:.2f}%"
        return f"无异动。上证指数 {sh_str}，自选股整体平稳。"

    parts = []
    for a in alerts:
        if a["type"] in ("price", "main_flow", "etf_discount"):
            name = a.get("name", a.get("code", ""))
            parts.append(f"{name}{a['detail']}")
        elif a["type"] == "index":
            parts.append(a["detail"])
        elif a["type"] == "north_flow":
            parts.append(a["detail"])
        elif a["type"] == "snapshot_diff":
            name = a.get("name", a.get("code", ""))
            parts.append(f"{name}{a['detail']}")

    summary = "；".join(parts[:3])  # 最多取前3条
    if len(alerts) > 3:
        summary += f"；另有 {len(alerts)-3} 项异动。"
    summary += f" alert_score={alert_score}。"
    return summary


# ─── check 主逻辑 ────────────────────────────────────────────────────────────

def run_check():
    global _etf_daily_cache
    _etf_daily_cache = None  # 每次 check 重置缓存

    now_str = now_shanghai().strftime("%Y-%m-%d %H:%M")
    watchlist = load_watchlist()

    if not watchlist:
        return {
            "time": now_str,
            "alert_score": 0,
            "has_alert": False,
            "alerts": [],
            "snapshot": {
                "sh_index": {},
                "stocks": [],
                "north_flow_yi": None,
                "north_status": "no_watchlist"
            },
            "summary": "当前 watchlist 为空，跳过盘中异动检测。"
        }

    all_alerts = []

    # 维度1：价格
    price_alerts, stocks_snapshot = check_price_alerts(watchlist)
    all_alerts.extend(price_alerts)

    # 维度2：大盘
    index_alerts, sh_snapshot = check_index_alerts()
    all_alerts.extend(index_alerts)

    # 维度3：北向
    north_alerts, north_flow_yi, north_status = check_north_flow()
    all_alerts.extend(north_alerts)

    # 维度4：主力资金（原地更新 stocks_snapshot 里的 main_flow_yi，跳过 ETF）
    flow_alerts = check_main_flow(watchlist, stocks_snapshot)
    all_alerts.extend(flow_alerts)

    # 维度4b：ETF 折溢价（原地回填 snapshot 中的 discount_pct/nav）
    etf_alerts = check_etf_alerts(watchlist, stocks_snapshot)
    all_alerts.extend(etf_alerts)

    # 维度5：快照对比
    diff_alerts = check_snapshot_diff(stocks_snapshot)
    all_alerts.extend(diff_alerts)

    # 计算评分
    alert_score = calc_score(all_alerts)

    # 生成 summary
    summary = gen_summary(all_alerts, alert_score, sh_snapshot, stocks_snapshot, north_flow_yi, north_status)

    result = {
        "time": now_str,
        "alert_score": alert_score,
        "has_alert": alert_score >= 1,
        "alerts": all_alerts,
        "snapshot": {
            "sh_index": sh_snapshot,
            "stocks": stocks_snapshot,
            "north_flow_yi": north_flow_yi,
            "north_status": north_status
        },
        "summary": summary
    }

    return result


# ─── snapshot 命令 ───────────────────────────────────────────────────────────

def run_snapshot():
    result = run_check()

    INTRADAY_DIR.mkdir(parents=True, exist_ok=True)
    ts = now_shanghai().strftime("%Y-%m-%d-%H%M")
    out_path = INTRADAY_DIR / f"{ts}.json"

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    result["_saved_to"] = str(out_path)
    return result


# ─── 入口 ────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 intraday_alert.py check|snapshot", file=sys.stderr)
        sys.exit(1)

    cmd = sys.argv[1].lower()

    if cmd == "check":
        result = run_check()
        print(json.dumps(result, ensure_ascii=False, indent=2))

    elif cmd == "snapshot":
        result = run_snapshot()
        print(json.dumps(result, ensure_ascii=False, indent=2))

    else:
        print(f"Unknown command: {cmd}. Use 'check' or 'snapshot'.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
