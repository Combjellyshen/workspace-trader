#!/usr/bin/env python3
"""
主力意图分析器

数据源：东方财富 datacenter-web + 新浪
功能：
  flow <code>       — 个股主力/大单/中单/小单资金流向
  tick <code>        — 分时大单成交（识别主力吸筹/出货）
  chip <code>        — 筹码分布概要（获利比例、集中度）
  holder <code>      — 机构持仓变动（十大流通股东）
  analysis <code>    — 综合主力意图分析
  batch              — 自选股批量分析
"""
import json
import sys
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

_HERE = Path(__file__).resolve().parents[2]
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from scripts.utils.common import http_get as _get, load_watchlist  # noqa: E402

HEADERS = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://data.eastmoney.com'}


def _market_prefix(code):
    """返回东财 secid 格式"""
    if code.startswith('6') or code.startswith('5'):
        return f'1.{code}'
    else:
        return f'0.{code}'


def _fetch_daily_flow_datacenter(code):
    """个股多日主力资金流 via datacenter-web（海外可用）"""
    suffix = 'SH' if code.startswith('6') or code.startswith('5') else 'SZ'
    url = (f'https://datacenter-web.eastmoney.com/api/data/v1/get?'
           f'sortColumns=TRADE_DATE&sortTypes=-1&pageSize=10&pageNumber=1'
           f'&reportName=RPT_INDIVIDUAL_FUND_FLOW&columns=ALL'
           f'&filter=(SECURITY_CODE%3D%22{code}%22)&source=WEB&client=WEB')
    raw = _get(url)
    data = json.loads(raw)
    items = (data.get('result') or {}).get('data') or []
    return items


def _parse_datacenter_daily_flow(items):
    """将 datacenter-web 资金流数据转换为 daily_flow 格式"""
    daily_flow = []
    for item in items:
        main_net = (item.get('MAIN_NET_INFLOW') or 0) / 1e8
        super_big = (item.get('SUPER_LARGE_NET_INFLOW') or 0) / 1e8
        big = (item.get('LARGE_NET_INFLOW') or 0) / 1e8
        mid = (item.get('MEDIUM_NET_INFLOW') or 0) / 1e8
        small = (item.get('SMALL_NET_INFLOW') or 0) / 1e8
        trade_date = (item.get('TRADE_DATE') or '')[:10]
        daily_flow.append({
            'date': trade_date,
            'main_net_inflow': round(main_net, 2),
            'small_net': round(small, 2),
            'mid_net': round(mid, 2),
            'big_net': round(big, 2),
            'super_big_net': round(super_big, 2),
        })
    # datacenter-web 返回降序，反转为升序（与 push2his 一致）
    daily_flow.reverse()
    return daily_flow


# ============================================================
# 1. 个股资金流向（大单/超大单/中单/小单）
# ============================================================
def stock_money_flow(code):
    """个股资金流向 — 主力/超大/大/中/小单"""
    secid = _market_prefix(code)

    # 当日分时资金流（push2，海外可能不可用）
    url = (f'http://push2.eastmoney.com/api/qt/stock/fflow/kline/get?'
           f'secid={secid}&fields1=f1,f2,f3&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63&'
           f'klt=1&lmt=0&ut=b2884a393a59ad64002292a3e90d46a5')

    result = {'code': code, 'timestamp': datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()}

    try:
        raw = _get(url)
        data = json.loads(raw)
        klines = (data.get('data') or {}).get('klines') or []

        if klines:
            # 最新一条
            latest = klines[-1].split(',')
            # f51=时间 f52=主力净流入 f53=小单净流入 f54=中单净流入 f55=大单净流入 f56=超大单净流入
            result['intraday_flow'] = {
                'time': latest[0] if len(latest) > 0 else '',
                'main_net': float(latest[1]) / 1e4 if len(latest) > 1 else 0,  # 万→亿
                'small_net': float(latest[2]) / 1e4 if len(latest) > 2 else 0,
                'mid_net': float(latest[3]) / 1e4 if len(latest) > 3 else 0,
                'big_net': float(latest[4]) / 1e4 if len(latest) > 4 else 0,
                'super_big_net': float(latest[5]) / 1e4 if len(latest) > 5 else 0,
            }

            # 全天资金流趋势
            flow_trend = []
            for k in klines[-10:]:  # 最近10个时间点
                parts = k.split(',')
                if len(parts) >= 6:
                    flow_trend.append({
                        'time': parts[0],
                        'main_net': round(float(parts[1]) / 1e4, 2),
                    })
            result['flow_trend'] = flow_trend
        else:
            result['intraday_note'] = 'push2分时数据不可用（海外限制）'
    except Exception as e:
        result['intraday_note'] = f'push2分时数据不可用: {e}'

    # 多日主力资金流 — 先尝试 push2his，失败则回退 datacenter-web
    url2 = (f'http://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get?'
            f'secid={secid}&fields1=f1,f2,f3&fields2=f51,f52,f53,f54,f55,f56,f57&'
            f'klt=101&lmt=10&ut=b2884a393a59ad64002292a3e90d46a5')
    daily_flow = []
    try:
        raw = _get(url2)
        data = json.loads(raw)
        klines = (data.get('data') or {}).get('klines') or []

        for k in klines:
            parts = k.split(',')
            if len(parts) >= 7:
                daily_flow.append({
                    'date': parts[0],
                    'main_net_inflow': round(float(parts[1]) / 1e8, 2),  # 亿
                    'small_net': round(float(parts[2]) / 1e8, 2),
                    'mid_net': round(float(parts[3]) / 1e8, 2),
                    'big_net': round(float(parts[4]) / 1e8, 2),
                    'super_big_net': round(float(parts[5]) / 1e8, 2),
                })
    except Exception:
        pass  # push2his 失败，下面用 datacenter-web 回退

    # 回退：datacenter-web（海外可用）
    if not daily_flow:
        try:
            items = _fetch_daily_flow_datacenter(code)
            daily_flow = _parse_datacenter_daily_flow(items)
            if daily_flow:
                result['daily_flow_source'] = 'datacenter-web'
        except Exception as e:
            result['daily_flow_error'] = f'datacenter-web也失败: {e}'

    if daily_flow:
        result['daily_flow'] = daily_flow

        # 连续流入/流出天数
        consecutive = 0
        direction = 'in' if daily_flow[-1]['main_net_inflow'] > 0 else 'out'
        for d in reversed(daily_flow):
            if (direction == 'in' and d['main_net_inflow'] > 0) or \
               (direction == 'out' and d['main_net_inflow'] < 0):
                consecutive += 1
            else:
                break
        result['consecutive'] = {
            'direction': '连续流入' if direction == 'in' else '连续流出',
            'days': consecutive,
            'total': round(sum(d['main_net_inflow'] for d in daily_flow[-consecutive:]), 2),
        }

    return result


# ============================================================
# 2. 分时大单成交（识别异动）
# ============================================================
def _tick_from_datacenter(code):
    """通过 datacenter-web 获取当日资金流向作为 tick 替代"""
    suffix = 'SH' if code.startswith('6') or code.startswith('5') else 'SZ'
    url_dc = (f'https://datacenter-web.eastmoney.com/api/data/v1/get?'
              f'sortColumns=TRADE_DATE&sortTypes=-1&pageSize=1&pageNumber=1'
              f'&reportName=RPT_INDIVIDUAL_FUND_FLOW&columns=ALL'
              f'&filter=(SECURITY_CODE%3D%22{code}%22)&source=WEB&client=WEB')
    raw = _get(url_dc)
    data = json.loads(raw)
    items = (data.get('result') or {}).get('data') or []
    if not items:
        return None
    item = items[0]
    summary = {
        'buy_big': round((item.get('LARGE_INFLOW') or 0) / 1e8, 2),
        'sell_big': round((item.get('LARGE_OUTFLOW') or 0) / 1e8, 2),
        'buy_super': round((item.get('SUPER_LARGE_INFLOW') or 0) / 1e8, 2),
        'sell_super': round((item.get('SUPER_LARGE_OUTFLOW') or 0) / 1e8, 2),
        'buy_mid': round((item.get('MEDIUM_INFLOW') or 0) / 1e8, 2),
        'sell_mid': round((item.get('MEDIUM_OUTFLOW') or 0) / 1e8, 2),
    }
    return summary


def tick_analysis(code):
    """分时成交明细 — 大单识别"""
    secid = _market_prefix(code)

    result = {'code': code, 'timestamp': datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()}

    # 先尝试 push2ex
    url = (f'http://push2ex.eastmoney.com/getStockFFlow?'
           f'secid={secid}&ut=b2884a393a59ad64002292a3e90d46a5&'
           f'fields=f1,f2,f3,f4,f5,f6,f7&dession=')

    summary = None
    try:
        raw = _get(url)
        data = json.loads(raw)
        d = data.get('data') or {}

        if d and any(d.get(f'f{i}') is not None for i in range(1, 7)):
            summary = {
                'buy_big': round(d.get('f1', 0) / 1e8, 2),  # 大单买入（亿）
                'sell_big': round(d.get('f2', 0) / 1e8, 2),  # 大单卖出
                'buy_super': round(d.get('f3', 0) / 1e8, 2),  # 超大单买入
                'sell_super': round(d.get('f4', 0) / 1e8, 2),  # 超大单卖出
                'buy_mid': round(d.get('f5', 0) / 1e8, 2),
                'sell_mid': round(d.get('f6', 0) / 1e8, 2),
            }
    except Exception:
        pass  # push2ex 失败，下面回退 datacenter-web

    # 回退：datacenter-web（海外可用）
    if summary is None:
        try:
            summary = _tick_from_datacenter(code)
            if summary:
                result['tick_source'] = 'datacenter-web'
        except Exception as e:
            result['tick_error'] = f'push2ex和datacenter-web均失败: {e}'
            return result

    if summary is None:
        result['tick_error'] = '无法获取资金流数据'
        return result

    result['summary'] = summary
    s = summary
    big_net = round(s['buy_big'] - s['sell_big'] + s['buy_super'] - s['sell_super'], 2)
    result['big_net_total'] = big_net

    # 主力意图初判
    if big_net > 0.5:
        result['tick_signal'] = '🟢 主力净买入明显'
    elif big_net > 0:
        result['tick_signal'] = '🟡 主力小幅净买入'
    elif big_net > -0.5:
        result['tick_signal'] = '🟡 主力小幅净卖出'
    else:
        result['tick_signal'] = '🔴 主力净卖出明显'

    return result


# ============================================================
# 3. 筹码分布概要
# ============================================================
def chip_distribution(code):
    """筹码分布 — 获利比例 + 集中度（push2his，海外可能不可用）"""
    secid = _market_prefix(code)

    result = {'code': code, 'timestamp': datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()}

    # 东财筹码分布接口（push2his，海外常不可用）
    url = (f'http://push2his.eastmoney.com/api/qt/stock/fflow/chip/get?'
           f'secid={secid}&ut=b2884a393a59ad64002292a3e90d46a5&'
           f'fields1=f1,f2,f3&fields2=f51,f52,f53,f54,f55,f56,f57,f58')

    try:
        raw = _get(url)
        data = json.loads(raw)
        d = (data.get('data') or {})

        if d:
            result['chip'] = {
                'profit_ratio': d.get('benefitPart', 'N/A'),  # 获利比例
                'avg_cost': d.get('avgCost', 'N/A'),  # 平均成本
                'ninety_cost_low': d.get('costLow90', 'N/A'),  # 90%筹码集中在
                'ninety_cost_high': d.get('costHigh90', 'N/A'),
                'seventy_cost_low': d.get('costLow70', 'N/A'),  # 70%筹码集中在
                'seventy_cost_high': d.get('costHigh70', 'N/A'),
            }
        else:
            result['chip_note'] = 'push2his不可用（海外限制），已用K线自算替代'
            result.update(_chip_fallback_kline(code))
    except Exception as e:
        result['chip_note'] = f'push2his不可用（{e}），已用K线自算替代'
        result.update(_chip_fallback_kline(code))

    return result


def _chip_fallback_kline(code, days=60):
    """用akshare K线数据自建筹码分布（push2his不可用时的替代）。"""
    try:
        import akshare as ak
        import numpy as np
        from datetime import timedelta

        end = datetime.now(ZoneInfo("Asia/Shanghai")).strftime('%Y%m%d')
        start = (datetime.now(ZoneInfo("Asia/Shanghai")) - timedelta(days=days + 30)).strftime('%Y%m%d')

        if code.startswith(('5', '15', '16', '56', '58')):
            df = ak.fund_etf_hist_em(symbol=code, period='daily', start_date=start, end_date=end, adjust='qfq')
        else:
            df = ak.stock_zh_a_hist(symbol=code, period='daily', start_date=start, end_date=end, adjust='qfq')

        if df is None or len(df) < 10:
            return {'chip_fallback_error': '历史数据不足'}

        df = df.tail(days).reset_index(drop=True)
        col_map = {'开盘': 'open', '收盘': 'close', '最高': 'high', '最低': 'low', '换手率': 'turnover'}
        df = df.rename(columns=col_map)

        price_min = float(df['low'].min()) * 0.95
        price_max = float(df['high'].max()) * 1.05
        bins = 100
        price_levels = np.linspace(price_min, price_max, bins + 1)
        chip_dist = np.zeros(bins)

        for _, row in df.iterrows():
            low, high = float(row['low']), float(row['high'])
            turnover = float(row['turnover']) / 100.0
            turnover = min(max(turnover, 0.001), 0.999)
            chip_dist *= (1 - turnover)
            for i in range(bins):
                bl, bh = price_levels[i], price_levels[i + 1]
                overlap = max(0, min(high, bh) - max(low, bl))
                if overlap > 0 and (high - low) > 0:
                    chip_dist[i] += turnover * overlap / (high - low)

        total = chip_dist.sum()
        if total > 0:
            chip_dist /= total

        current_price = float(df['close'].iloc[-1])
        profitable = sum(chip_dist[i] for i in range(bins)
                         if (price_levels[i] + price_levels[i + 1]) / 2 <= current_price)

        return {
            'chip': {
                'profit_ratio': round(float(profitable) * 100, 2),
                'avg_cost': round(float(sum(chip_dist[i] * (price_levels[i] + price_levels[i + 1]) / 2
                                            for i in range(bins))), 2),
                'source': 'kline_self_calc',
            },
        }
    except Exception as e:
        return {'chip_fallback_error': f'K线自算筹码失败: {e}'}


# ============================================================
# 4. 十大流通股东 + 机构持仓变动
# ============================================================
def holder_analysis(code):
    """十大流通股东 + 机构持仓变动 (datacenter API)"""
    result = {'code': code, 'timestamp': datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()}
    
    suffix = 'SH' if code.startswith('6') or code.startswith('5') else 'SZ'
    
    try:
        url = (f'https://datacenter-web.eastmoney.com/api/data/v1/get?'
               f'reportName=RPT_F10_EH_FREEHOLDERS&'
               f'filter=(SECUCODE%3D%22{code}.{suffix}%22)&'
               f'pageNumber=1&pageSize=30&'
               f'sortColumns=END_DATE%2CHOLDER_RANK&sortTypes=-1%2C1&columns=ALL')
        
        raw = _get(url)
        data = json.loads(raw)
        items = (data.get('result') or {}).get('data') or []
        
        if not items:
            result['holder_error'] = '无股东数据'
            return result
        
        # 按报告日期分组
        from collections import defaultdict
        by_date = defaultdict(list)
        for h in items:
            d = (h.get('END_DATE') or '')[:10]
            by_date[d].append(h)
        
        dates = sorted(by_date.keys(), reverse=True)
        latest_date = dates[0]
        latest_holders = by_date[latest_date][:10]
        
        result['report_date'] = latest_date
        result['top10_holders'] = []
        
        institutional = 0
        individual = 0
        
        for h in latest_holders:
            htype = h.get('HOLDER_TYPE', '')
            name = h.get('HOLDER_NAME', '')
            change = h.get('HOLD_NUM_CHANGE', '')
            ratio = h.get('FREE_HOLDNUM_RATIO', 0)
            
            holder_info = {
                'rank': h.get('HOLDER_RANK', ''),
                'name': name,
                'shares': h.get('HOLD_NUM', ''),
                'ratio': f'{ratio:.2f}%' if isinstance(ratio, (int, float)) else str(ratio),
                'change': change if change else '不变',
                'type': htype,
            }
            result['top10_holders'].append(holder_info)
            
            if any(kw in htype for kw in ['基金', '保险', '信托', '证券', 'QFII', '社保']):
                institutional += 1
            elif '个人' in htype:
                individual += 1
        
        result['holder_structure'] = {
            'institutional_count': institutional,
            'individual_count': individual,
            'signal': '机构主导' if institutional > 5 else ('散户较多' if individual > 5 else '混合'),
        }
        
        # 与上期对比
        if len(dates) >= 2:
            prev_date = dates[1]
            prev_holders = by_date[prev_date][:10]
            
            current_names = {h.get('HOLDER_NAME', '') for h in latest_holders}
            prev_names = {h.get('HOLDER_NAME', '') for h in prev_holders}
            
            new_in = current_names - prev_names
            exited = prev_names - current_names
            
            result['holder_changes'] = {
                'prev_date': prev_date,
                'new_entries': list(new_in),
                'exits': list(exited),
                'new_count': len(new_in),
                'exit_count': len(exited),
            }
            
    except Exception as e:
        result['holder_error'] = str(e)
    
    return result


# ============================================================
# 5. 综合主力意图分析
# ============================================================
def full_analysis(code):
    """综合主力意图分析"""
    result = {
        'code': code,
        'analysis_time': datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
    }
    
    result['money_flow'] = stock_money_flow(code)
    result['tick'] = tick_analysis(code)
    result['chip'] = chip_distribution(code)
    result['holders'] = holder_analysis(code)
    
    # 综合信号判断
    signals = []
    
    # 资金流信号
    consec = result['money_flow'].get('consecutive', {})
    if consec.get('days', 0) >= 3:
        if '流入' in consec.get('direction', ''):
            signals.append(f"✅ 主力资金连续{consec['days']}天净流入，累计{consec['total']}亿")
        else:
            signals.append(f"⚠️ 主力资金连续{consec['days']}天净流出，累计{consec['total']}亿")
    
    # 大单信号
    tick_sig = result['tick'].get('tick_signal', '')
    if tick_sig:
        signals.append(tick_sig)
    
    # 股东信号
    holder_sig = result['holders'].get('holder_structure', {}).get('signal', '')
    if holder_sig:
        signals.append(f"👥 股东结构：{holder_sig}")
    
    changes = result['holders'].get('holder_changes', {})
    if changes.get('new_count', 0) > 0:
        signals.append(f"📈 新进股东{changes['new_count']}家")
    if changes.get('exit_count', 0) > 0:
        signals.append(f"📉 退出股东{changes['exit_count']}家")
    
    result['signals'] = signals
    
    return result


def batch_analysis():
    """自选股批量分析"""
    stocks = load_watchlist()

    results = []
    for s in stocks:
        code = s if isinstance(s, str) else s.get('code', '')
        name = s.get('name', '') if isinstance(s, dict) else ''
        if code:
            print(f"分析主力意图 {code} {name}...", file=sys.stderr)
            r = full_analysis(code)
            r['name'] = name
            results.append(r)
    
    return results


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'batch'
    
    if cmd == 'flow':
        code = sys.argv[2] if len(sys.argv) > 2 else ''
        data = stock_money_flow(code)
    elif cmd == 'tick':
        code = sys.argv[2] if len(sys.argv) > 2 else ''
        data = tick_analysis(code)
    elif cmd == 'chip':
        code = sys.argv[2] if len(sys.argv) > 2 else ''
        data = chip_distribution(code)
    elif cmd == 'holder':
        code = sys.argv[2] if len(sys.argv) > 2 else ''
        data = holder_analysis(code)
    elif cmd == 'analysis':
        code = sys.argv[2] if len(sys.argv) > 2 else ''
        data = full_analysis(code)
    elif cmd == 'batch':
        data = batch_analysis()
    else:
        print(f"Usage: {sys.argv[0]} [flow|tick|chip|holder|analysis <code>|batch]", file=sys.stderr)
        sys.exit(1)
    
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
