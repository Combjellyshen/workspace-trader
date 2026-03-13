#!/usr/bin/env python3
"""
券商研报采集器 — 东方财富研报中心 API
无需注册，无频率限制

功能：
  stock <code>  — 某只股票的研报/评级
  industry     — 行业研报
  strategy     — 策略/宏观研报
  latest       — 最新个股研报（全市场）
  search <kw>  — 按关键词搜索研报
"""
import urllib.request
import json
import sys
from datetime import datetime, timedelta

BASE = 'https://reportapi.eastmoney.com/report/list'
HEADERS = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://data.eastmoney.com/report/'}


def _fetch(params):
    defaults = {
        'industryCode': '*',
        'pageSize': '20',
        'industry': '*',
        'rating': '',
        'ratingChange': '',
        'pageNo': '1',
        'fields': '',
        'orgCode': '',
        'rcode': '',
        'p': '1',
        'pageNum': '1',
    }
    defaults.update(params)
    qs = '&'.join(f'{k}={v}' for k, v in defaults.items())
    url = f'{BASE}?{qs}'
    req = urllib.request.Request(url, headers=HEADERS)
    resp = urllib.request.urlopen(req, timeout=15)
    return json.loads(resp.read())


def stock_reports(code, months=3, page_size=20):
    """某只股票的研报"""
    end = datetime.now().strftime('%Y-%m-%d')
    start = (datetime.now() - timedelta(days=months * 30)).strftime('%Y-%m-%d')
    data = _fetch({
        'qType': '0',
        'code': code,
        'beginTime': start,
        'endTime': end,
        'pageSize': str(page_size),
    })
    results = []
    for item in (data.get('data') or []):
        results.append({
            'title': item.get('title', ''),
            'org': item.get('orgSName', ''),
            'rating': item.get('emRatingName', ''),
            'stock': item.get('stockName', ''),
            'stock_code': item.get('stockCode', ''),
            'industry': item.get('indvInduName', ''),
            'researcher': item.get('researcher', ''),
            'date': (item.get('publishDate') or '')[:10],
            'abstract': item.get('contentSummary', ''),
        })
    return {
        'total': data.get('hits', 0),
        'reports': results,
    }


def industry_reports(days=7, page_size=20):
    """行业研报"""
    end = datetime.now().strftime('%Y-%m-%d')
    start = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    data = _fetch({
        'qType': '1',
        'beginTime': start,
        'endTime': end,
        'pageSize': str(page_size),
    })
    results = []
    for item in (data.get('data') or []):
        results.append({
            'title': item.get('title', ''),
            'org': item.get('orgSName', ''),
            'rating': item.get('emRatingName', ''),
            'industry': item.get('industryName', ''),
            'researcher': item.get('researcher', ''),
            'date': (item.get('publishDate') or '')[:10],
        })
    return {'total': data.get('hits', 0), 'reports': results}


def strategy_reports(days=7, page_size=20):
    """策略/宏观研报"""
    end = datetime.now().strftime('%Y-%m-%d')
    start = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    data = _fetch({
        'qType': '2',
        'beginTime': start,
        'endTime': end,
        'pageSize': str(page_size),
    })
    results = []
    for item in (data.get('data') or []):
        results.append({
            'title': item.get('title', ''),
            'org': item.get('orgSName', ''),
            'researcher': item.get('researcher', ''),
            'date': (item.get('publishDate') or '')[:10],
        })
    return {'total': data.get('hits', 0), 'reports': results}


def latest_reports(days=3, page_size=30):
    """最新个股研报"""
    end = datetime.now().strftime('%Y-%m-%d')
    start = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    data = _fetch({
        'qType': '0',
        'beginTime': start,
        'endTime': end,
        'pageSize': str(page_size),
    })
    results = []
    for item in (data.get('data') or []):
        results.append({
            'title': item.get('title', ''),
            'org': item.get('orgSName', ''),
            'rating': item.get('emRatingName', ''),
            'stock': item.get('stockName', ''),
            'stock_code': item.get('stockCode', ''),
            'date': (item.get('publishDate') or '')[:10],
        })
    return {'total': data.get('hits', 0), 'reports': results}


def search_reports(keyword, days=30, page_size=20):
    """按关键词搜索"""
    end = datetime.now().strftime('%Y-%m-%d')
    start = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    # 东财研报API不直接支持关键词搜索，通过title过滤
    data = _fetch({
        'qType': '0',
        'beginTime': start,
        'endTime': end,
        'pageSize': '50',
    })
    results = []
    for item in (data.get('data') or []):
        title = item.get('title', '')
        stock = item.get('stockName', '')
        industry = item.get('indvInduName', '') or ''
        if keyword.lower() in (title + stock + industry).lower():
            results.append({
                'title': title,
                'org': item.get('orgSName', ''),
                'rating': item.get('emRatingName', ''),
                'stock': stock,
                'stock_code': item.get('stockCode', ''),
                'date': (item.get('publishDate') or '')[:10],
            })
    return {'keyword': keyword, 'reports': results}


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'latest'

    if cmd == 'stock':
        code = sys.argv[2] if len(sys.argv) > 2 else ''
        if not code:
            print("Usage: research_reports.py stock <code>", file=sys.stderr)
            sys.exit(1)
        data = stock_reports(code)
    elif cmd == 'industry':
        data = industry_reports()
    elif cmd == 'strategy':
        data = strategy_reports()
    elif cmd == 'latest':
        data = latest_reports()
    elif cmd == 'search':
        kw = sys.argv[2] if len(sys.argv) > 2 else ''
        data = search_reports(kw)
    else:
        print(f"Usage: {sys.argv[0]} [stock <code>|industry|strategy|latest|search <keyword>]", file=sys.stderr)
        sys.exit(1)

    print(json.dumps(data, ensure_ascii=False, indent=2))
