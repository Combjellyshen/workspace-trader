#!/usr/bin/env python3
"""
市场情绪面数据采集器

数据源：
  1. 同花顺热股排行（TOP100 + 概念标签 + 上榜状态）
  2. 东方财富人气榜（TOP20）
  3. 百度股市通热搜

子命令：
  hot_stocks    — 同花顺热股 TOP N（默认20）
  popularity    — 东财人气榜 TOP20
  baidu         — 百度股市通热搜
  all           — 全部汇总（默认）
"""
import urllib.request
import json
import sys


def ths_hot_stocks(top_n=30):
    """同花顺热股排行 — 含概念标签、上榜状态"""
    url = 'https://dq.10jqka.com.cn/fuyao/hot_list_data/out/hot_list/v1/stock?stock_type=a&type=hour&list_type=normal'
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        data = json.loads(resp.read())
        stocks = data.get('data', {}).get('stock_list', [])
        results = []
        for s in stocks[:top_n]:
            tag = s.get('tag', {})
            results.append({
                'rank': s.get('order', 0),
                'code': s.get('code', ''),
                'name': s.get('name', ''),
                'hot_value': s.get('rate', 0),
                'concepts': tag.get('concept_tag', []) if isinstance(tag, dict) else [],
                'status': tag.get('popularity_tag', '') if isinstance(tag, dict) else '',
            })
        return {'source': '同花顺', 'count': len(results), 'stocks': results}
    except Exception as e:
        return {'source': '同花顺', 'error': str(e)}


def eastmoney_popularity(top_n=20):
    """东方财富人气榜"""
    url = 'https://emappdata.eastmoney.com/stockrank/getAllCurrentList'
    body = json.dumps({
        'appId': 'appId01',
        'globalId': '786e4c21-70dc-435a-93bb-38',
        'marketType': '',
        'pageNo': 1,
        'pageSize': top_n,
    }).encode()
    req = urllib.request.Request(url, data=body, headers={
        'Content-Type': 'application/json',
        'User-Agent': 'Mozilla/5.0',
    })
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        data = json.loads(resp.read())
        results = []
        for s in (data.get('data') or []):
            code_raw = s.get('sc', '')
            # SZ002261 -> 002261
            code = code_raw[2:] if len(code_raw) > 2 else code_raw
            market = code_raw[:2] if len(code_raw) > 2 else ''
            results.append({
                'rank': s.get('rk', 0),
                'code': code,
                'market': market,
                'raw_code': code_raw,
            })
        return {'source': '东财人气榜', 'count': len(results), 'stocks': results}
    except Exception as e:
        return {'source': '东财人气榜', 'error': str(e)}


def baidu_hot():
    """百度股市通热搜"""
    url = ('https://gushitong.baidu.com/opendata?resource_id=5352'
           '&query=A%E8%82%A1%E6%9C%80%E7%83%AD%E6%A6%9C'
           '&code=ab_rise&market=ab&fin_type=stock&direct=desc&pn=0&rn=20')
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        data = json.loads(resp.read())
        results = []
        for item in data.get('Result', []):
            stock_list = (item.get('DisplayData', {})
                          .get('resultData', {})
                          .get('tplData', {})
                          .get('result', {})
                          .get('list', []))
            for s in stock_list:
                results.append({
                    'name': s.get('name', ''),
                    'code': s.get('code', ''),
                    'change_pct': s.get('changepercent', ''),
                    'exchange': s.get('exchange', ''),
                })
        return {'source': '百度股市通', 'count': len(results), 'stocks': results}
    except Exception as e:
        return {'source': '百度股市通', 'error': str(e)}


def all_sentiment():
    """全部情绪面数据汇总"""
    return {
        'ths_hot': ths_hot_stocks(),
        'eastmoney_popularity': eastmoney_popularity(),
        'baidu_hot': baidu_hot(),
    }


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'all'
    top_n = int(sys.argv[2]) if len(sys.argv) > 2 else 20

    if cmd == 'hot_stocks':
        data = ths_hot_stocks(top_n)
    elif cmd == 'popularity':
        data = eastmoney_popularity(top_n)
    elif cmd == 'baidu':
        data = baidu_hot()
    elif cmd == 'all':
        data = all_sentiment()
    else:
        print(f"Usage: {sys.argv[0]} [hot_stocks|popularity|baidu|all] [top_n]", file=sys.stderr)
        sys.exit(1)

    print(json.dumps(data, ensure_ascii=False, indent=2))
