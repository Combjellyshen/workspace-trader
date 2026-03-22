#!/usr/bin/env python3
"""
Tushare Pro A股数据 — 基于免费积分可用接口
可用：daily/weekly, moneyflow_hsgt(北向), ths_hot(同花顺热度), stock_basic
"""
import tushare as ts
import json
import sys
import os
from pathlib import Path
from datetime import datetime, timedelta

_HERE = Path(__file__).resolve().parents[2]
import sys as _sys
if str(_HERE) not in _sys.path:
    _sys.path.insert(0, str(_HERE))

from scripts.utils.common import load_config, load_watchlist, WORKSPACE_ROOT  # noqa: E402

def init_pro():
    try:
        token = load_config().get('tushare_token', '')
    except Exception as e:
        print(f"load_config failed, falling back to env: {e}", file=sys.stderr)
        token = os.environ.get('TUSHARE_TOKEN', '')
    if not token:
        print(json.dumps({"error": "未配置 Tushare Token"}))
        sys.exit(1)
    ts.set_token(token)
    return ts.pro_api()


def northbound(pro, days=10):
    """北向资金（沪深港通）近N天"""
    end = datetime.now().strftime('%Y%m%d')
    start = (datetime.now() - timedelta(days=days+5)).strftime('%Y%m%d')
    result = {}
    try:
        df = pro.moneyflow_hsgt(start_date=start, end_date=end)
        if not df.empty:
            records = df.head(days).to_dict('records')
            # 计算净流入
            for r in records:
                try:
                    r['north_money'] = float(r.get('north_money') or 0)
                    r['hgt'] = float(r.get('hgt') or 0)
                    r['sgt'] = float(r.get('sgt') or 0)
                except:
                    pass
            result['data'] = records
            result['summary'] = {
                'latest_date': records[0]['trade_date'],
                'latest_north': records[0].get('north_money'),
                'trend': 'inflow' if len(records) >= 2 and (records[0].get('north_money', 0) > records[1].get('north_money', 0)) else 'outflow',
            }
    except Exception as e:
        result['error'] = str(e)
    return result


def ths_hot(pro, trade_date=None):
    """同花顺热度排名 — 热股/概念/行业"""
    if not trade_date:
        trade_date = datetime.now().strftime('%Y%m%d')
    result = {}
    try:
        df = pro.ths_hot(trade_date=trade_date)
        if not df.empty:
            # 按类型分组
            hot_stocks = df[df['data_type'] == '热股'].drop_duplicates('ts_name').head(15)
            hot_concept = df[df['data_type'] == '概念板块'].drop_duplicates('ts_name').head(10)
            hot_industry = df[df['data_type'] == '行业板块'].drop_duplicates('ts_name').head(10)
            hot_hk = df[df['data_type'] == '港股'].drop_duplicates('ts_name').head(5)
            hot_us = df[df['data_type'] == '美股'].drop_duplicates('ts_name').head(5)
            hot_futures = df[df['data_type'] == '期货'].drop_duplicates('ts_name').head(5)

            result['hot_stocks'] = hot_stocks[['ts_code', 'ts_name', 'rank', 'pct_change', 'current_price', 'hot', 'concept', 'rank_reason']].to_dict('records')
            result['hot_concepts'] = hot_concept[['ts_name', 'rank', 'pct_change', 'hot']].to_dict('records')
            result['hot_industries'] = hot_industry[['ts_name', 'rank', 'pct_change', 'hot']].to_dict('records')
            result['hot_hk'] = hot_hk[['ts_name', 'rank', 'pct_change', 'hot']].to_dict('records')
            result['hot_us'] = hot_us[['ts_name', 'rank', 'pct_change', 'hot']].to_dict('records')
            result['hot_futures'] = hot_futures[['ts_name', 'rank', 'pct_change', 'hot']].to_dict('records')
    except Exception as e:
        result['error'] = str(e)
    return result


def stock_kline(pro, ts_code, days=60):
    """个股K线数据"""
    end = datetime.now().strftime('%Y%m%d')
    start = (datetime.now() - timedelta(days=days+30)).strftime('%Y%m%d')
    result = {'ts_code': ts_code}

    try:
        daily = pro.daily(ts_code=ts_code, start_date=start, end_date=end)
        if not daily.empty:
            result['daily'] = daily.head(days).to_dict('records')

            # 简单技术指标计算
            closes = daily['close'].tolist()[::-1]  # 时间正序
            if len(closes) >= 20:
                ma5 = sum(closes[-5:]) / 5
                ma10 = sum(closes[-10:]) / 10
                ma20 = sum(closes[-20:]) / 20
                result['indicators'] = {
                    'latest_close': closes[-1],
                    'ma5': round(ma5, 3),
                    'ma10': round(ma10, 3),
                    'ma20': round(ma20, 3),
                    'above_ma5': closes[-1] > ma5,
                    'above_ma10': closes[-1] > ma10,
                    'above_ma20': closes[-1] > ma20,
                    'trend': 'bullish' if ma5 > ma10 > ma20 else ('bearish' if ma5 < ma10 < ma20 else 'mixed'),
                    'vol_avg5': round(sum(daily['vol'].tolist()[:5]) / 5, 0),
                    'vol_latest': daily['vol'].tolist()[0],
                }
    except Exception as e:
        result['error'] = str(e)

    return result


def full_snapshot(pro, trade_date=None):
    """综合快照：北向 + 热度 + 自选股K线"""
    result = {
        'snapshot_time': datetime.utcnow().isoformat() + 'Z',
    }

    # 北向资金
    result['northbound'] = northbound(pro)

    # 同花顺热度
    result['ths_hot'] = ths_hot(pro, trade_date)

    # 自选股K线
    try:
        stocks = load_watchlist()
        if stocks:
            import time
            watchlist_data = []
            for s in stocks:
                code = s['code']
                # tushare 需要后缀
                if code.startswith('6') or code.startswith('5'):
                    ts_code = code + '.SH'
                else:
                    ts_code = code + '.SZ'
                try:
                    kline = stock_kline(pro, ts_code)
                    kline['name'] = s.get('name', '')
                    watchlist_data.append(kline)
                    time.sleep(0.3)  # 频率限制
                except Exception as e:
                    watchlist_data.append({'ts_code': ts_code, 'error': str(e)})
            result['watchlist'] = watchlist_data
    except Exception as e:
        result['watchlist_error'] = str(e)

    return result


if __name__ == '__main__':
    pro = init_pro()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "snapshot"

    if cmd == "snapshot":
        data = full_snapshot(pro)
    elif cmd == "north":
        days = int(sys.argv[2]) if len(sys.argv) > 2 else 10
        data = northbound(pro, days)
    elif cmd == "hot":
        date = sys.argv[2] if len(sys.argv) > 2 else None
        data = ths_hot(pro, date)
    elif cmd == "kline":
        code = sys.argv[2] if len(sys.argv) > 2 else ""
        data = stock_kline(pro, code)
    else:
        print(f"Usage: {sys.argv[0]} [snapshot|north|hot|kline <code>]", file=sys.stderr)
        sys.exit(1)

    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
