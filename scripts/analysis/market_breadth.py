#!/usr/bin/env python3
"""
市场宽度 & 涨停板分析器

数据源：东方财富 + 新浪
功能：
  breadth     — 涨跌家数 / 涨跌停统计 / 市场温度
  limit_up    — 涨停板详情（含连板梯队）
  limit_down  — 跌停板详情
  streak      — 连板梯队分析（高度板 → 首板晋级率）
  all         — 全部汇总
"""
import json
import sys
import re
import time
from datetime import datetime
from pathlib import Path

import requests

_HERE = Path(__file__).resolve().parents[2]
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from scripts.utils.common import http_get as _get  # noqa: E402

HEADERS = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://data.eastmoney.com'}
MARKET_FS = 'm:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048'
PAGE_SIZE = 100


def _market_page(page: int = 1):
    url = ('https://push2.eastmoney.com/api/qt/clist/get?'
           f'pn={page}&pz={PAGE_SIZE}&po=1&np=1&fltt=2&invt=2&'
           f'fs={MARKET_FS}&'
           'fields=f2,f3,f4,f12,f14,f15,f16,f17,f18')

    last_error = None
    for attempt in range(3):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=12)
            resp.raise_for_status()
            data = resp.json()
            payload = data.get('data') or {}
            return payload.get('diff') or [], int(payload.get('total') or 0)
        except Exception as e:
            last_error = e
            time.sleep(0.4 * (attempt + 1))
    raise last_error


def _fetch_market_quotes_from_akshare():
    import os
    os.environ.setdefault('TQDM_DISABLE', '1')
    import akshare as ak

    last_error = None
    for attempt in range(3):
        try:
            df = ak.stock_zh_a_spot()
            stocks = []
            for _, row in df.iterrows():
                stocks.append({
                    'f12': str(row.get('代码', '')).strip(),
                    'f14': str(row.get('名称', '')).strip(),
                    'f3': row.get('涨跌幅'),
                    'f2': row.get('最新价'),
                    'source': 'akshare.stock_zh_a_spot',
                })
            if stocks:
                return stocks, len(stocks)
            last_error = RuntimeError('akshare 返回空数据')
        except Exception as e:
            last_error = e
        time.sleep(1.2 * (attempt + 1))

    raise last_error if last_error else RuntimeError('akshare 获取全市场行情失败')


def _fetch_all_market_quotes(max_pages: int = 80):
    # 优先 AkShare（当前环境下稳定性高于 Eastmoney 分页）
    try:
        stocks, total = _fetch_market_quotes_from_akshare()
        return stocks, total, []
    except Exception as ak_err:
        errors = [f'akshare: {ak_err}']

    # 退回 Eastmoney 分页
    stocks = []
    total = 0
    for page in range(1, max_pages + 1):
        try:
            diff, total = _market_page(page)
        except Exception as e:
            errors.append(f'page{page}: {e}')
            break
        if not diff:
            break
        stocks.extend(diff)
        if total and len(stocks) >= total:
            break
        time.sleep(0.03)
    return stocks, total, errors


def _pct_value(stock: dict):
    pct = stock.get('f3')
    if pct is None:
        return None
    try:
        return float(str(pct).replace('%', '').replace(',', ''))
    except Exception:
        return None


def _is_limit_up(pct: float, name: str = '') -> bool:
    if pct is None:
        return False
    if 'ST' in (name or '').upper():
        return pct >= 4.9
    return pct >= 19.9 or pct >= 9.9


def _is_limit_down(pct: float, name: str = '') -> bool:
    if pct is None:
        return False
    if 'ST' in (name or '').upper():
        return pct <= -4.9
    return pct <= -19.9 or pct <= -9.9


def _fallback_limit_pool(direction: str):
    stocks, total, errors = _fetch_all_market_quotes()
    if not stocks:
        time.sleep(1.0)
        stocks2, total2, errors2 = _fetch_all_market_quotes()
        if len(stocks2) > len(stocks):
            stocks, total, errors = stocks2, total2, errors2
        else:
            errors = errors + errors2

    selected = []
    for s in stocks:
        name = s.get('f14', '')
        pct = _pct_value(s)
        if pct is None:
            continue
        ok = _is_limit_up(pct, name) if direction == 'up' else _is_limit_down(pct, name)
        if not ok:
            continue
        selected.append({
            'code': s.get('f12', ''),
            'name': name,
            'pct': pct,
            'amount': None,
            'turnover': None,
            'first_time': '',
            'last_time': '',
            'open_count': None,
            'streak': 1,
            'reason': '',
            'fallback_generated': True,
        })

    selected.sort(key=lambda x: x['pct'], reverse=(direction == 'up'))
    return {
        'total_market': total,
        'pool': selected,
        'fallback_source': 'eastmoney clist pagination',
        'errors': errors,
    }


# ============================================================
# 1. 涨跌家数 + 涨跌停统计
# ============================================================
def market_breadth():
    """全市场涨跌家数统计"""
    result = {
        'timestamp': datetime.now().isoformat(),
    }

    try:
        stocks, market_total, fetch_errors = _fetch_all_market_quotes()
        if not stocks:
            time.sleep(1.0)
            stocks2, market_total2, fetch_errors2 = _fetch_all_market_quotes()
            if len(stocks2) > len(stocks):
                stocks, market_total, fetch_errors = stocks2, market_total2, fetch_errors2
            else:
                fetch_errors = fetch_errors + fetch_errors2

        up = 0
        down = 0
        flat = 0
        limit_up_count = 0
        limit_down_count = 0
        up_gt5 = 0
        down_gt5 = 0

        for s in stocks:
            name = s.get('f14', '')
            pct = _pct_value(s)
            if pct is None:
                continue
            if pct > 0:
                up += 1
            elif pct < 0:
                down += 1
            else:
                flat += 1

            if _is_limit_up(pct, name):
                limit_up_count += 1
            if _is_limit_down(pct, name):
                limit_down_count += 1
            if pct >= 5:
                up_gt5 += 1
            if pct <= -5:
                down_gt5 += 1

        total = up + down + flat
        result['breadth'] = {
            'total': total,
            'market_total': market_total,
            'sample_ok': total == market_total or market_total == 0,
            'degraded': bool(fetch_errors),
            'fetch_errors': fetch_errors,
            'up': up,
            'down': down,
            'flat': flat,
            'up_pct': round(up / total * 100, 1) if total else 0,
            'down_pct': round(down / total * 100, 1) if total else 0,
            'up_down_ratio': round(up / down, 2) if down else float('inf'),
            'limit_up': limit_up_count,
            'limit_down': limit_down_count,
            'up_gt5pct': up_gt5,
            'down_gt5pct': down_gt5,
        }

        if total > 0:
            up_ratio = up / total
            if up_ratio > 0.7 and limit_up_count > 80:
                temp = '极热'
                score = 95
            elif up_ratio > 0.6 and limit_up_count > 50:
                temp = '偏热'
                score = 80
            elif up_ratio > 0.5:
                temp = '温和偏多'
                score = 60
            elif up_ratio > 0.4:
                temp = '温和偏空'
                score = 40
            elif up_ratio > 0.3:
                temp = '偏冷'
                score = 25
            else:
                temp = '冰点'
                score = 10

            result['temperature'] = {
                'label': temp,
                'score': score,
                'description': f'{up}涨/{down}跌/{flat}平 | 涨停{limit_up_count} 跌停{limit_down_count}',
            }

    except Exception as e:
        result['breadth_error'] = str(e)

    return result


# ============================================================
# 2. 涨停板详情
# ============================================================
def limit_up_detail():
    """涨停板详情 — 含首板/连板/炸板"""
    url = ('https://push2ex.eastmoney.com/getTopicZTPool?'
           'ut=7eea3edcaed734bea9telerik&dession=&'
           'sort=fbt:asc&Ession=')

    result = {'timestamp': datetime.now().isoformat()}

    try:
        raw = _get(url)
        data = json.loads(raw)
        pool = (data.get('data') or {}).get('pool') or []

        fallback_used = False
        if not pool:
            fallback = _fallback_limit_pool('up')
            pool = fallback['pool']
            fallback_used = True
            result['fallback_source'] = fallback['fallback_source']
            result['fallback_total_market'] = fallback['total_market']
            result['fallback_errors'] = fallback.get('errors', [])

        stocks = []
        streak_map = {}

        for s in pool:
            streak = s.get('zbt', s.get('streak', 1))
            info = {
                'code': s.get('c', s.get('code', '')),
                'name': s.get('n', s.get('name', '')),
                'pct': s.get('zdp', s.get('pct', 0)),
                'amount': round(s.get('amount', 0) / 1e8, 2) if s.get('amount') is not None else None,
                'turnover': s.get('hs', s.get('turnover')),
                'first_time': s.get('fbt', s.get('first_time', '')),
                'last_time': s.get('lbt', s.get('last_time', '')),
                'open_count': s.get('oc', s.get('open_count')),
                'streak': streak,
                'reason': s.get('hybk', s.get('reason', '')),
                'fallback_generated': s.get('fallback_generated', False),
            }
            stocks.append(info)

            if streak not in streak_map:
                streak_map[streak] = []
            streak_map[streak].append(info)

        result['limit_up'] = {
            'total': len(stocks),
            'stocks': stocks,
            'fallback_used': fallback_used,
        }

        ladder = {}
        for streak_n in sorted(streak_map.keys(), reverse=True):
            label = f'{streak_n}连板' if streak_n > 1 else '首板'
            ladder[label] = {
                'count': len(streak_map[streak_n]),
                'stocks': [{'code': s['code'], 'name': s['name'], 'reason': s['reason']}
                          for s in streak_map[streak_n]],
            }

        result['ladder'] = ladder

        if stocks:
            max_streak = max(s['streak'] for s in stocks)
            result['max_streak'] = max_streak
            result['highest_board'] = [s for s in stocks if s['streak'] == max_streak]

    except Exception as e:
        result['limit_up_error'] = str(e)
        try:
            fallback = _fallback_limit_pool('up')
            stocks = fallback['pool']
            result['fallback_source'] = fallback['fallback_source']
            result['fallback_total_market'] = fallback['total_market']
            result['fallback_errors'] = fallback.get('errors', [])
            result['limit_up'] = {
                'total': len(stocks),
                'stocks': stocks,
                'fallback_used': True,
            }
            streak_map = {}
            for s in stocks:
                streak = s.get('streak', 1)
                streak_map.setdefault(streak, []).append(s)
            ladder = {}
            for streak_n in sorted(streak_map.keys(), reverse=True):
                label = f'{streak_n}连板' if streak_n > 1 else '首板'
                ladder[label] = {
                    'count': len(streak_map[streak_n]),
                    'stocks': [{'code': s['code'], 'name': s['name'], 'reason': s.get('reason', '')}
                              for s in streak_map[streak_n]],
                }
            result['ladder'] = ladder
        except Exception as fb_err:
            result['limit_up_fallback_error'] = str(fb_err)

    return result


# ============================================================
# 3. 跌停板详情
# ============================================================
def limit_down_detail():
    """跌停板详情"""
    url = ('https://push2ex.eastmoney.com/getTopicDTPool?'
           'ut=7eea3edcaed734bea9telerik&dession=&Ession=')

    result = {'timestamp': datetime.now().isoformat()}

    try:
        raw = _get(url)
        data = json.loads(raw)
        pool = (data.get('data') or {}).get('pool') or []

        fallback_used = False
        if not pool:
            fallback = _fallback_limit_pool('down')
            pool = fallback['pool']
            fallback_used = True
            result['fallback_source'] = fallback['fallback_source']
            result['fallback_total_market'] = fallback['total_market']
            result['fallback_errors'] = fallback.get('errors', [])

        stocks = []
        for s in pool:
            amount = s.get('amount', None)
            stocks.append({
                'code': s.get('c', s.get('code', '')),
                'name': s.get('n', s.get('name', '')),
                'pct': s.get('zdp', s.get('pct', 0)),
                'amount': round(amount / 1e8, 2) if amount is not None else None,
                'reason': s.get('hybk', s.get('reason', '')),
                'fallback_generated': s.get('fallback_generated', False),
            })

        result['limit_down'] = {
            'total': len(stocks),
            'stocks': stocks,
            'fallback_used': fallback_used,
        }

    except Exception as e:
        result['limit_down_error'] = str(e)
        try:
            fallback = _fallback_limit_pool('down')
            stocks = fallback['pool']
            result['fallback_source'] = fallback['fallback_source']
            result['fallback_total_market'] = fallback['total_market']
            result['fallback_errors'] = fallback.get('errors', [])
            result['limit_down'] = {
                'total': len(stocks),
                'stocks': stocks,
                'fallback_used': True,
            }
        except Exception as fb_err:
            result['limit_down_fallback_error'] = str(fb_err)

    return result


# ============================================================
# 4. 炸板统计
# ============================================================
def broken_board():
    """炸板（曾涨停后打开）统计"""
    url = ('https://push2ex.eastmoney.com/getTopicZBPool?'
           'ut=7eea3edcaed734bea9telerik&dession=&Ession=')
    
    result = {'timestamp': datetime.now().isoformat()}
    
    try:
        raw = _get(url)
        data = json.loads(raw)
        pool = (data.get('data') or {}).get('pool') or []
        
        stocks = []
        for s in pool:
            stocks.append({
                'code': s.get('c', ''),
                'name': s.get('n', ''),
                'pct': s.get('zdp', 0),
                'amount': round(s.get('amount', 0) / 1e8, 2),
                'reason': s.get('hybk', ''),
                'first_time': s.get('fbt', ''),
            })
        
        result['broken_board'] = {
            'total': len(stocks),
            'stocks': stocks,
        }
        
    except Exception as e:
        result['broken_board_error'] = str(e)
    
    return result


# ============================================================
# 5. 综合分析
# ============================================================
def full_analysis():
    """全维度涨跌停 + 市场宽度分析"""
    result = {
        'analysis_time': datetime.now().isoformat(),
    }
    
    result.update(market_breadth())
    
    lu = limit_up_detail()
    result['limit_up_detail'] = lu
    
    ld = limit_down_detail()
    result['limit_down_detail'] = ld
    
    bb = broken_board()
    result['broken_board_detail'] = bb
    
    # 封板率
    lu_total = lu.get('limit_up', {}).get('total', 0)
    bb_total = bb.get('broken_board', {}).get('total', 0)
    total_touched = lu_total + bb_total
    if total_touched > 0:
        seal_rate = round(lu_total / total_touched * 100, 1)
        result['seal_rate'] = {
            'rate': seal_rate,
            'sealed': lu_total,
            'broken': bb_total,
            'total_touched': total_touched,
            'assessment': '强' if seal_rate > 70 else ('一般' if seal_rate > 50 else '弱'),
        }
    
    return result


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'all'
    
    if cmd == 'breadth':
        data = market_breadth()
    elif cmd == 'limit_up':
        data = limit_up_detail()
    elif cmd == 'limit_down':
        data = limit_down_detail()
    elif cmd == 'broken':
        data = broken_board()
    elif cmd == 'all':
        data = full_analysis()
    else:
        print(f"Usage: {sys.argv[0]} [breadth|limit_up|limit_down|broken|all]", file=sys.stderr)
        sys.exit(1)
    
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
