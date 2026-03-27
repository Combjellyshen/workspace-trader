#!/usr/bin/env python3
"""
市场宽度 & 涨停板分析器

数据源：新浪 + 东方财富（fallback）
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
from zoneinfo import ZoneInfo

import requests

_HERE = Path(__file__).resolve().parents[2]
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from scripts.utils.common import http_get as _get  # noqa: E402

HEADERS = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://data.eastmoney.com'}
SINA_HEADERS = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://finance.sina.com.cn'}


def _fetch_all_sina():
    """通过新浪分页 API 获取全 A 股行情（海外可用），3 线程并行抓取"""
    import urllib.request
    import concurrent.futures

    count_url = 'https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeStockCount?node=hs_a'
    req = urllib.request.Request(count_url, headers=SINA_HEADERS)
    total = int(urllib.request.urlopen(req, timeout=10).read().decode().strip().strip('"'))

    page_size = 80
    pages = (total // page_size) + 1

    def fetch_page(page):
        url = (f'https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/'
               f'Market_Center.getHQNodeData?page={page}&num={page_size}'
               f'&sort=symbol&asc=1&node=hs_a')
        for attempt in range(3):
            try:
                req = urllib.request.Request(url, headers=SINA_HEADERS)
                raw = urllib.request.urlopen(req, timeout=15).read().decode('gbk')
                return json.loads(raw) or []
            except urllib.request.HTTPError as e:
                if e.code == 456 and attempt < 2:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise
        return []

    all_stocks = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(fetch_page, p): p for p in range(1, pages + 1)}
        page_results = {}
        for future in concurrent.futures.as_completed(futures):
            page_n = futures[future]
            try:
                page_results[page_n] = future.result()
            except Exception:
                page_results[page_n] = []
        for p in range(1, pages + 1):
            all_stocks.extend(page_results.get(p, []))

    # Convert to internal format (f12=code, f14=name, f3=changepercent, f2=price)
    stocks = []
    for s in all_stocks:
        stocks.append({
            'f12': s.get('code', ''),
            'f14': s.get('name', ''),
            'f3': s.get('changepercent'),
            'f2': s.get('trade'),
        })
    return stocks, total


def _fetch_market_quotes_from_tushare():
    """通过 tushare 获取全 A 股行情（单次 API 调用，稳定可靠）"""
    from datetime import timedelta
    from scripts.data.tushare_data import init_pro

    pro = init_pro()

    # 尝试最近 5 个自然日（跳过周末/节假日）
    last_error = None
    for days_back in range(5):
        trade_date = (datetime.now(ZoneInfo("Asia/Shanghai")) - timedelta(days=days_back)).strftime('%Y%m%d')
        try:
            df = pro.daily(trade_date=trade_date)
            if df is not None and len(df) > 0:
                stocks = []
                for _, row in df.iterrows():
                    code = str(row.get('ts_code', ''))[:6]
                    pre_close = row.get('pre_close', 0)
                    close = row.get('close', 0)
                    pct = round((close / pre_close - 1) * 100, 2) if pre_close else row.get('pct_chg', 0)
                    stocks.append({
                        'f12': code,
                        'f14': '',  # tushare daily 不含股票名称
                        'f3': pct,
                        'f2': close,
                        'source': 'tushare.daily',
                    })
                return stocks, len(stocks)
        except Exception as e:
            last_error = e

    raise last_error if last_error else RuntimeError('tushare 获取全市场行情失败')


_MARKET_QUOTES_CACHE = None


def _fetch_all_market_quotes(max_pages: int = 80):
    global _MARKET_QUOTES_CACHE
    if _MARKET_QUOTES_CACHE is not None:
        return _MARKET_QUOTES_CACHE

    # 优先新浪（海外稳定）
    try:
        stocks, total = _fetch_all_sina()
        if stocks:
            _MARKET_QUOTES_CACHE = (stocks, total, [])
            return _MARKET_QUOTES_CACHE
    except Exception as sina_err:
        errors = [f'sina: {sina_err}']
    else:
        errors = []

    # 退回 AkShare
    try:
        stocks, total = _fetch_market_quotes_from_tushare()
        _MARKET_QUOTES_CACHE = (stocks, total, errors)
        return _MARKET_QUOTES_CACHE
    except Exception as ak_err:
        errors.append(f'akshare: {ak_err}')

    return [], 0, errors


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
        'fallback_source': 'sina + akshare fallback',
        'errors': errors,
    }


# ============================================================
# 1. 涨跌家数 + 涨跌停统计
# ============================================================
def market_breadth():
    """全市场涨跌家数统计"""
    result = {
        'timestamp': datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
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
def _fetch_zt_pool_akshare():
    """通过 akshare stock_zt_pool_em 获取涨停池（含连板数）"""
    import akshare as ak
    date_str = datetime.now(ZoneInfo("Asia/Shanghai")).strftime('%Y%m%d')
    df = ak.stock_zt_pool_em(date=date_str)
    if df is None or df.empty:
        return []
    pool = []
    for _, row in df.iterrows():
        streak = int(row.get('连板数', 1) or 1)
        ft = str(row.get('首次封板时间', '') or '')
        lt = str(row.get('最后封板时间', '') or '')
        # 封板时间格式 HHMMSS → HH:MM:SS
        if len(ft) == 6 and ft.isdigit():
            ft = f'{ft[:2]}:{ft[2:4]}:{ft[4:]}'
        if len(lt) == 6 and lt.isdigit():
            lt = f'{lt[:2]}:{lt[2:4]}:{lt[4:]}'
        amount = row.get('成交额')
        pool.append({
            'code': str(row.get('代码', '')),
            'name': str(row.get('名称', '')),
            'pct': round(float(row.get('涨跌幅', 0) or 0), 2),
            'amount': round(float(amount) / 1e8, 2) if amount else None,
            'turnover': round(float(row.get('换手率', 0) or 0), 2),
            'first_time': ft,
            'last_time': lt,
            'open_count': int(row.get('炸板次数', 0) or 0),
            'streak': streak,
            'reason': str(row.get('所属行业', '') or ''),
            'fallback_generated': False,
        })
    return pool


def _build_ladder(stocks):
    """从涨停股列表构建连板梯队"""
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
    return ladder


def limit_up_detail():
    """涨停板详情 — 含首板/连板/炸板。akshare → push2ex → 全市场fallback"""
    result = {'timestamp': datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()}

    stocks = []
    source = None

    # 1) akshare stock_zt_pool_em（含真实连板数）
    try:
        pool = _fetch_zt_pool_akshare()
        if pool:
            stocks = pool
            source = 'akshare_zt_pool_em'
    except Exception:
        pass

    # 2) push2ex（原始接口，可能被封）
    if not stocks:
        try:
            url = ('http://push2ex.eastmoney.com/getTopicZTPool?'
                   'ut=7eea3edcaed734bea9telerik&dession=&'
                   'sort=fbt:asc&Ession=')
            raw = _get(url)
            data = json.loads(raw)
            pool = (data.get('data') or {}).get('pool') or []
            if pool:
                for s in pool:
                    streak = s.get('zbt', 1)
                    stocks.append({
                        'code': s.get('c', ''),
                        'name': s.get('n', ''),
                        'pct': s.get('zdp', 0),
                        'amount': round(s.get('amount', 0) / 1e8, 2) if s.get('amount') else None,
                        'turnover': s.get('hs'),
                        'first_time': s.get('fbt', ''),
                        'last_time': s.get('lbt', ''),
                        'open_count': s.get('oc'),
                        'streak': streak,
                        'reason': s.get('hybk', ''),
                        'fallback_generated': False,
                    })
                source = 'push2ex'
        except Exception:
            pass

    # 3) 全市场价格 fallback（无连板信息）
    if not stocks:
        fallback = _fallback_limit_pool('up')
        stocks = fallback['pool']
        source = fallback['fallback_source']
        result['fallback_total_market'] = fallback['total_market']
        result['fallback_errors'] = fallback.get('errors', [])

    result['source'] = source
    result['limit_up'] = {
        'total': len(stocks),
        'stocks': stocks,
        'fallback_used': source not in ('akshare_zt_pool_em', 'push2ex'),
    }
    result['ladder'] = _build_ladder(stocks)

    if stocks:
        max_streak = max(s['streak'] for s in stocks)
        result['max_streak'] = max_streak
        result['highest_board'] = [s for s in stocks if s['streak'] == max_streak]

    return result


# ============================================================
# 3. 跌停板详情
# ============================================================
def limit_down_detail():
    """跌停板详情"""
    url = ('http://push2ex.eastmoney.com/getTopicDTPool?'
           'ut=7eea3edcaed734bea9telerik&dession=&Ession=')

    result = {'timestamp': datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()}

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
    url = ('http://push2ex.eastmoney.com/getTopicZBPool?'
           'ut=7eea3edcaed734bea9telerik&dession=&Ession=')
    
    result = {'timestamp': datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()}
    
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
        'analysis_time': datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
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
