#!/usr/bin/env python3
"""
A股深度数据采集器 — 直连免费 API（无需积分）
数据源：新浪财经 + 东方财富 datacenter-web + Tushare（免费部分）
"""
import json
import sys
import os
from datetime import datetime
from pathlib import Path

_HERE = Path(__file__).resolve().parents[2]
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from scripts.utils.common import http_get as _get, load_config, load_watchlist, SINA_HEADERS  # noqa: E402

EM_HEADERS = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://data.eastmoney.com'}


# ============================================================
# 1. 实时行情 — 新浪 Level-1
# ============================================================
def realtime_quotes(codes):
    """新浪实时报价，codes=['sh000001','sz399001','sh560710']"""
    url = f'https://hq.sinajs.cn/list={",".join(codes)}'
    raw = _get(url, SINA_HEADERS, encoding='gbk')
    results = []
    for line in raw.strip().split('\n'):
        if '=' not in line: continue
        var, val = line.split('=', 1)
        code = var.split('_')[-1]
        fields = val.strip('";\n').split(',')
        if len(fields) < 32: continue
        results.append({
            'code': code,
            'name': fields[0],
            'open': float(fields[1] or 0),
            'pre_close': float(fields[2] or 0),
            'close': float(fields[3] or 0),
            'high': float(fields[4] or 0),
            'low': float(fields[5] or 0),
            'volume': float(fields[8] or 0),
            'amount': float(fields[9] or 0),
            'date': fields[30],
            'time': fields[31],
        })
        if results[-1]['pre_close'] > 0:
            results[-1]['pct_chg'] = round((results[-1]['close'] - results[-1]['pre_close']) / results[-1]['pre_close'] * 100, 2)
    return results


# ============================================================
# 2. 板块资金流 — 新浪
# ============================================================
def sector_money_flow(fenlei=0, num=30):
    """
    板块资金流排名
    fenlei: 0=行业, 1=概念
    """
    url = f'https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/MoneyFlow.ssl_bkzj_bk?page=1&num={num}&sort=netamount&asc=0&fenlei={fenlei}'
    raw = _get(url, SINA_HEADERS, encoding='gbk')
    data = json.loads(raw)
    results = []
    for item in data:
        net = float(item.get('netamount', 0))
        results.append({
            'name': item.get('name', ''),
            'category': item.get('category', ''),
            'net_inflow': round(net / 1e8, 2),  # 亿元
            'net_inflow_raw': net,
            'inflow': float(item.get('inamount', 0)),
            'outflow': float(item.get('outamount', 0)),
            'avg_pct': item.get('avg_changeratio', ''),
        })
    return results


# ============================================================
# 3. 个股资金流 TOP — 新浪
# ============================================================
def stock_money_flow_top(num=30):
    """个股主力净流入排名"""
    url = f'https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/MoneyFlow.ssl_bkzj_ssggzj?page=1&num={num}&sort=netamount&asc=0'
    raw = _get(url, SINA_HEADERS, encoding='gbk')
    data = json.loads(raw)
    results = []
    for item in data:
        net = float(item.get('netamount', 0))
        results.append({
            'symbol': item.get('symbol', ''),
            'name': item.get('name', ''),
            'net_inflow': round(net / 1e8, 2),
        })
    return results


# ============================================================
# 4. 龙虎榜 — 东方财富 datacenter-web
# ============================================================
def dragon_tiger(trade_date=None):
    """龙虎榜数据"""
    if not trade_date:
        trade_date = datetime.now().strftime('%Y-%m-%d')
    url = f"https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPT_DAILYBILLBOARD_DETAILSNEW&columns=ALL&filter=(TRADE_DATE='{trade_date}')&pageSize=50&sortColumns=BILLBOARD_DEAL_AMT&sortTypes=-1"
    raw = _get(url, EM_HEADERS)
    data = json.loads(raw)
    if not data.get('result') or not data['result'].get('data'):
        return []
    results = []
    for item in data['result']['data']:
        results.append({
            'code': item.get('SECURITY_CODE', ''),
            'name': item.get('SECURITY_NAME_ABBR', ''),
            'close': item.get('CLOSE_PRICE', 0),
            'pct_chg': item.get('CHANGE_RATE', 0),
            'deal_amount': item.get('BILLBOARD_DEAL_AMT', 0),
            'buy_amount': item.get('BILLBOARD_BUY_AMT', 0),
            'sell_amount': item.get('BILLBOARD_SELL_AMT', 0),
            'net': round(((item.get('BILLBOARD_BUY_AMT') or 0) - (item.get('BILLBOARD_SELL_AMT') or 0)) / 1e8, 2),
            'reason': item.get('EXPLAIN', ''),
            'accum_amount': item.get('DEAL_AMOUNT_RATIO', 0),
        })
    return results


# ============================================================
# 5. 北向资金 — Tushare（免费可用）
# ============================================================
def northbound_flow(days=10):
    """北向资金（沪深港通）"""
    try:
        import tushare as ts
        token = load_config().get('tushare_token', '')
        if not token:
            return {"error": "no tushare token"}
        ts.set_token(token)
        pro = ts.pro_api()
        from datetime import timedelta
        end = datetime.now().strftime('%Y%m%d')
        start = (datetime.now() - timedelta(days=days + 5)).strftime('%Y%m%d')
        df = pro.moneyflow_hsgt(start_date=start, end_date=end)
        if df.empty:
            return {"data": []}
        records = df.head(days).to_dict('records')
        for r in records:
            for k in ['hgt', 'sgt', 'north_money', 'south_money', 'ggt_ss', 'ggt_sz']:
                try:
                    r[k] = float(r.get(k) or 0)
                except:
                    pass
        return {"data": records}
    except Exception as e:
        return {"error": str(e)}


# ============================================================
# 6. 同花顺热度 — Tushare（每天2次限制）
# ============================================================
def ths_hot_ranking(trade_date=None):
    """同花顺热度排名"""
    try:
        import tushare as ts
        token = load_config().get('tushare_token', '')
        if not token:
            return {"error": "no tushare token"}
        ts.set_token(token)
        pro = ts.pro_api()
        if not trade_date:
            trade_date = datetime.now().strftime('%Y%m%d')
        df = pro.ths_hot(trade_date=trade_date)
        if df.empty:
            return {}
        hot_stocks = df[df['data_type'] == '热股'].drop_duplicates('ts_name').head(15)
        hot_concept = df[df['data_type'] == '概念板块'].drop_duplicates('ts_name').head(10)
        hot_industry = df[df['data_type'] == '行业板块'].drop_duplicates('ts_name').head(10)
        return {
            'hot_stocks': hot_stocks[['ts_code', 'ts_name', 'pct_change', 'hot', 'concept', 'rank_reason']].to_dict('records'),
            'hot_concepts': hot_concept[['ts_name', 'pct_change', 'hot']].to_dict('records'),
            'hot_industries': hot_industry[['ts_name', 'pct_change', 'hot']].to_dict('records'),
        }
    except Exception as e:
        return {"error": str(e)}


# ============================================================
# 综合快照
# ============================================================
def full_snapshot(trade_date=None):
    """全维度数据快照"""
    result = {'snapshot_time': datetime.now().isoformat()}

    # 实时行情
    try:
        indices = realtime_quotes(['sh000001', 'sz399001', 'sz399006', 'sh000688', 'sh000300'])
        result['indices'] = indices
    except Exception as e:
        result['indices_error'] = str(e)

    # 行业资金流
    try:
        result['industry_flow'] = sector_money_flow(fenlei=0, num=20)
    except Exception as e:
        result['industry_flow_error'] = str(e)

    # 概念资金流
    try:
        result['concept_flow'] = sector_money_flow(fenlei=1, num=20)
    except Exception as e:
        result['concept_flow_error'] = str(e)

    # 个股资金流 TOP
    try:
        result['stock_flow_top'] = stock_money_flow_top(num=20)
    except Exception as e:
        result['stock_flow_top_error'] = str(e)

    # 龙虎榜
    try:
        result['dragon_tiger'] = dragon_tiger(trade_date)
    except Exception as e:
        result['dragon_error'] = str(e)

    # 北向资金
    try:
        result['northbound'] = northbound_flow(10)
    except Exception as e:
        result['north_error'] = str(e)

    # 自选股行情
    try:
        stocks = load_watchlist()
        if not stocks:
            stocks = []
        if stocks:
            codes = []
            for s in stocks:
                c = s['code']
                prefix = 'sh' if c.startswith(('6', '5')) else 'sz'
                codes.append(prefix + c)
            result['watchlist_realtime'] = realtime_quotes(codes)
    except Exception as e:
        result['watchlist_error'] = str(e)

    return result


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else "snapshot"

    if cmd == "snapshot":
        data = full_snapshot()
    elif cmd == "industry":
        data = sector_money_flow(fenlei=0, num=30)
    elif cmd == "concept":
        data = sector_money_flow(fenlei=1, num=30)
    elif cmd == "stock_flow":
        data = stock_money_flow_top(num=30)
    elif cmd == "dragon":
        date = sys.argv[2] if len(sys.argv) > 2 else None
        data = dragon_tiger(date)
    elif cmd == "north":
        data = northbound_flow()
    elif cmd == "hot":
        date = sys.argv[2] if len(sys.argv) > 2 else None
        data = ths_hot_ranking(date)
    elif cmd == "quote":
        codes = sys.argv[2:]
        data = realtime_quotes(codes)
    else:
        print(f"Usage: {sys.argv[0]} [snapshot|industry|concept|stock_flow|dragon|north|hot|quote <codes...>]", file=sys.stderr)
        sys.exit(1)

    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
