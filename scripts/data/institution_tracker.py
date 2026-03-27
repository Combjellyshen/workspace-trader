#!/usr/bin/env python3
"""
institution_tracker.py — 机构行为追踪（散户最容易忽视但信号最强的数据）

用法:
    python3 institution_tracker.py north_top      # 北向资金持股排行（近5日净买入）
    python3 institution_tracker.py north_history  # 北向资金历史净流入趋势
    python3 institution_tracker.py block_trade    # 大宗交易（折价买入=机构低吸信号）
    python3 institution_tracker.py institute_hold # 机构持仓汇总（最新季报披露）
    python3 institution_tracker.py fund_position  # 基金重仓股（公募重仓=被认可的价值股）
    python3 institution_tracker.py all            # 全套机构行为数据
"""

import sys
import json
import time
import requests
import concurrent.futures
from datetime import datetime, timedelta
import warnings
warnings.filterwarnings('ignore')

STALE_DAYS_THRESHOLD = 7

HEADERS = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://data.eastmoney.com'}


def _with_timeout(fn, timeout=15):
    """Run a function with timeout protection"""
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = executor.submit(fn)
    try:
        return future.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        executor.shutdown(wait=False, cancel_futures=True)
        raise


def get_north_top():
    """北向资金持股排行 — 外资最爱买什么"""
    result = {"timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"), "data": {}}

    url = ('https://datacenter-web.eastmoney.com/api/data/v1/get?'
           'sortColumns=ADD_MARKET_CAP&sortTypes=-1'
           '&pageSize=30&pageNumber=1'
           '&reportName=RPT_MUTUAL_STOCK_NORTHSTA&columns=ALL'
           '&source=WEB&client=WEB')

    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        data = resp.json()
        items = (data.get('result') or {}).get('data') or []

        if items:
            result["data"]["north_holdings"] = {
                "count": len(items),
                "top30": [{
                    "code": i.get("SECURITY_CODE", ""),
                    "name": i.get("SECURITY_NAME_ABBR", ""),
                    "hold_shares": i.get("HOLD_SHARES", ""),
                    "hold_market_cap": i.get("HOLD_MARKET_CAP", ""),
                    "hold_ratio": i.get("HOLD_SHARES_RATIO", ""),
                    "change_rate": i.get("CHANGE_RATE", ""),
                    "close_price": i.get("CLOSE_PRICE", ""),
                    "industry": i.get("INDUSTRY_NAME", ""),
                } for i in items[:30]],
            }
    except Exception as e:
        result["data"]["north_holdings_error"] = str(e)[:80]

    return result


def get_north_history():
    """北向资金历史净流入趋势"""
    result = {"timestamp": datetime.now().strftime("%Y-%m-%d %H:%M")}

    url = ('https://datacenter-web.eastmoney.com/api/data/v1/get?'
           'sortColumns=TRADE_DATE&sortTypes=-1'
           '&pageSize=20&pageNumber=1'
           '&reportName=RPT_MUTUAL_DEAL_HISTORY&columns=ALL'
           '&source=WEB&client=WEB')

    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        data = resp.json()
        items = (data.get('result') or {}).get('data') or []

        if items:
            result["fund_flow_summary"] = {
                "count": len(items),
                "recent_20d": [{
                    "date": i.get("TRADE_DATE", "")[:10],
                    "north_net": i.get("NET_DEAL_AMT", ""),
                    "buy_amt": i.get("BUY_AMT", ""),
                    "sell_amt": i.get("SELL_AMT", ""),
                    "accum_net": i.get("ACCUM_DEAL_AMT", ""),
                } for i in items],
            }
    except Exception as e:
        result["fund_flow_error"] = str(e)[:80]

    return result


def get_block_trade():
    """大宗交易"""
    result = {"timestamp": datetime.now().strftime("%Y-%m-%d %H:%M")}

    url = ('https://datacenter-web.eastmoney.com/api/data/v1/get?'
           'sortColumns=TRADE_DATE&sortTypes=-1'
           '&pageSize=50&pageNumber=1'
           '&reportName=RPT_BLOCKTRADE_STA&columns=ALL'
           '&source=WEB&client=WEB')

    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        data = resp.json()
        items = (data.get('result') or {}).get('data') or []

        if items:
            result["date"] = items[0].get("TRADE_DATE", "")[:10] if items else ""
            result["total_count"] = len(items)
            result["all_trades"] = [{
                "code": i.get("SECUCODE", "").split(".")[0],
                "name": i.get("SECURITY_NAME_ABBR", ""),
                "trade_date": i.get("TRADE_DATE", "")[:10],
                "deal_count": i.get("DEAL_NUM", ""),
                "volume": i.get("VOLUME", ""),
                "deal_amt": i.get("DEAL_AMT", ""),
                "avg_price": i.get("AVERAGE_PRICE", ""),
                "close_price": i.get("CLOSE_PRICE", ""),
                "premium_ratio": i.get("PREMIUM_RATIO", ""),
                "change_rate": i.get("CHANGE_RATE", ""),
            } for i in items[:30]]

            # Top 10 by amount
            try:
                sorted_items = sorted(items, key=lambda x: float(x.get("DEAL_AMT", 0) or 0), reverse=True)
                result["top10_by_amount"] = [{
                    "code": i.get("SECUCODE", "").split(".")[0],
                    "name": i.get("SECURITY_NAME_ABBR", ""),
                    "deal_amt": i.get("DEAL_AMT", ""),
                    "premium_ratio": i.get("PREMIUM_RATIO", ""),
                } for i in sorted_items[:10]]
            except:
                pass
    except Exception as e:
        result["block_trade_error"] = str(e)[:80]

    return result


def get_institute_hold():
    """机构持仓汇总 — 新浪机构持股，参数格式如 '20253'(2025三季报), '20254'(2025年报)"""
    result = {"timestamp": datetime.now().strftime("%Y-%m-%d %H:%M")}

    try:
        import akshare as ak
        import pandas as pd
    except ImportError as e:
        result["error"] = f"缺少依赖: {e}"
        return result

    # 新浪格式：YYYY + 季度编号(1=一季报,2=中报,3=三季报,4=年报)
    quarters = ["20254", "20253", "20252", "20251", "20244"]

    for q in quarters:
        try:
            df = _with_timeout(lambda q=q: ak.stock_institute_hold(symbol=q), timeout=15)
            if not df.empty:
                result["report_period"] = q
                result["description"] = f"{'一季报' if q[-1]=='1' else '中报' if q[-1]=='2' else '三季报' if q[-1]=='3' else '年报'} {q[:4]}年"
                result["total_stocks"] = len(df)
                result["columns"] = df.columns.tolist()
                # 按机构数/持股数量排序
                sort_col = next((c for c in df.columns if '机构' in c or '数量' in c), df.columns[-1])
                try:
                    df_sorted = df.sort_values(sort_col, ascending=False)
                    result["top30_by_institutions"] = df_sorted.head(30).to_dict('records')
                except:
                    result["top30"] = df.head(30).to_dict('records')
                break
        except Exception as e:
            result[f"error_{q}"] = str(e)[:60]

    # 备用：机构推荐评级（不依赖季报日期）
    if "total_stocks" not in result:
        try:
            df2 = _with_timeout(lambda: ak.stock_institute_recommend(symbol="近一月"), timeout=15)
            if not df2.empty:
                result["fallback_recommend"] = {
                    "source": "机构推荐近一月",
                    "count": len(df2),
                    "columns": df2.columns.tolist(),
                    "top20": df2.head(20).to_dict('records'),
                }
        except Exception as e:
            result["recommend_error"] = str(e)[:80]

    return result


def _latest_disclosed_quarters(n=4):
    """动态计算最近 n 个已可能披露的季报日期（YYYYMMDD 格式）
    季报披露规律：Q1(0331)→4月底, Q2(0630)→8月底, Q3(0930)→10月底, Q4(1231)→次年4月底
    """
    today = datetime.now()
    candidates = []
    for year in [today.year, today.year - 1]:
        for q_end, disc_month in [("1231", 4), ("0930", 10), ("0630", 8), ("0331", 4)]:
            disc_year = year + 1 if q_end == "1231" else year
            disc_deadline = datetime(disc_year, disc_month + 1, 1) if disc_month < 12 else datetime(disc_year + 1, 1, 1)
            if today >= disc_deadline:
                candidates.append(f"{year}{q_end}")
        if len(candidates) >= n:
            break
    return candidates[:n]


def get_fund_position():
    """基金重仓股 — 公募基金最新季报重仓股（机构认可的核心资产）"""
    result = {"timestamp": datetime.now().strftime("%Y-%m-%d %H:%M")}
    quarters = _latest_disclosed_quarters(4)

    try:
        import akshare as ak
        import pandas as pd
    except ImportError as e:
        result["error"] = f"缺少依赖: {e}"
        return result

    for q in quarters:
        try:
            df = _with_timeout(lambda q=q: ak.stock_institute_hold_detail(symbol=q), timeout=15)
            if not df.empty:
                result["report_date"] = q
                result["count"] = len(df)
                result["columns"] = df.columns.tolist()
                result["top30"] = df.head(30).to_dict('records')
                return result
        except Exception:
            continue

    # 全部失败后尝试东财基金持仓备用
    result["fund_position_error"] = f"所有季度均无数据: {quarters}"
    try:
        for q in quarters:
            df2 = _with_timeout(lambda q=q: ak.stock_institute_hold(symbol=q), timeout=15)
            if not df2.empty:
                result["institute_hold_fallback"] = {
                    "quarter": q,
                    "top20": df2.head(20).to_dict('records')
                }
                break
    except Exception as e2:
        result["fallback_error"] = str(e2)[:80]

    return result


def _extract_latest_date(payload):
    latest = None
    def walk(obj):
        nonlocal latest
        if isinstance(obj, dict):
            for k, v in obj.items():
                if '日期' in str(k) or str(k).lower().endswith('date'):
                    s = str(v)
                    for fmt in ('%Y-%m-%d', '%Y%m%d'):
                        try:
                            dt = datetime.strptime(s, fmt)
                            if latest is None or dt > latest:
                                latest = dt
                            break
                        except Exception:
                            pass
                walk(v)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)
    walk(payload)
    return latest


def _freshness_info(payload):
    latest = _extract_latest_date(payload)
    if latest is None:
        return {"fresh": None, "latest_date": None, "stale_days": None}
    stale_days = (datetime.now() - latest).days
    return {
        "fresh": stale_days <= STALE_DAYS_THRESHOLD,
        "latest_date": latest.strftime('%Y-%m-%d'),
        "stale_days": stale_days,
    }


def get_all():
    """全套机构行为数据（附带新鲜度判断，陈旧北向榜单降级）"""
    north_top = get_north_top()
    north_history = get_north_history()
    block_trade = get_block_trade()
    institute_hold = get_institute_hold()

    north_top_freshness = _freshness_info(north_top)
    if north_top_freshness.get('fresh') is False:
        north_top['warning'] = f"北向排行样本日期停留在 {north_top_freshness.get('latest_date')}，已降级为低置信参考"
        north_top['usable_as_core_evidence'] = False
    else:
        north_top['usable_as_core_evidence'] = True

    return {
        "report_type": "institution_tracker",
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "freshness": {
            "north_top": north_top_freshness,
            "north_history": _freshness_info(north_history),
            "block_trade": _freshness_info(block_trade),
            "institute_hold": _freshness_info(institute_hold),
        },
        "north_top": north_top,
        "north_history": north_history,
        "block_trade": block_trade,
        "institute_hold": institute_hold,
    }


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"

    dispatch = {
        "north_top": get_north_top,
        "north_history": get_north_history,
        "block_trade": get_block_trade,
        "institute_hold": get_institute_hold,
        "fund_position": get_fund_position,
        "all": get_all,
    }

    fn = dispatch.get(cmd)
    if fn:
        print(json.dumps(fn(), ensure_ascii=False, indent=2, default=str))
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
