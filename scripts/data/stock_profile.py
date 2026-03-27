#!/usr/bin/env python3
"""
个股深度画像 — 公司基本面 + 股东 + 财务 + 公告/新闻

数据源：东方财富 + AKShare + Web搜索
功能：
  profile <code>     — 公司基本信息（行业/主营/市值/PE/PB）
  finance <code>     — 核心财务指标（营收/利润/ROE/负债率/现金流）
  announce <code>    — 最新公告摘要
  news <code>        — 公司相关新闻
  full <code>        — 完整画像
  batch              — 自选股批量
"""
import urllib.parse
import json
import sys
from datetime import datetime
from pathlib import Path

_HERE = Path(__file__).resolve().parents[2]
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from scripts.utils.common import http_get as _get, load_watchlist  # noqa: E402

HEADERS = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://data.eastmoney.com'}


def _em_code(code):
    """东财股票代码格式"""
    return ('SH' if code.startswith('6') or code.startswith('5') else 'SZ') + code


# ============================================================
# 1. 公司基本信息
# ============================================================
def company_profile(code):
    """公司画像 — 行业/主营/市值/PE/PB/总股本/流通股"""
    result = {'code': code, 'timestamp': datetime.now().isoformat()}
    
    try:
        # 东财F10基本信息
        url = (f'https://emweb.securities.eastmoney.com/PC_HSF10/CompanySurvey/CompanySurveyAjax?'
               f'code={_em_code(code)}')
        raw = _get(url)
        if raw.startswith('\ufeff'):
            raw = raw[1:]
        data = json.loads(raw)
        
        basic = data.get('jbzl', {})
        if basic:
            result['profile'] = {
                'name': basic.get('gsmc', ''),
                'short_name': basic.get('gsjc', ''),
                'industry': basic.get('sshy', ''),  # 所属行业
                'region': basic.get('ssdq', ''),  # 所属地区
                'main_business': basic.get('zyyw', ''),  # 主营业务
                'products': basic.get('jyfw', ''),  # 经营范围
                'chairman': basic.get('frdb', ''),  # 法人代表
                'gm': basic.get('gm', ''),  # 总经理
                'employees': basic.get('ygrs', ''),  # 员工人数
                'listing_date': basic.get('ssrq', ''),  # 上市日期
                'registered_capital': basic.get('zczb', ''),  # 注册资本
            }
        
        # 实时估值数据
        secid = ('1.' if code.startswith('6') or code.startswith('5') else '0.') + code
        val_url = (f'http://push2.eastmoney.com/api/qt/stock/get?'
                   f'secid={secid}&fields=f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f14,'
                   f'f15,f16,f17,f18,f20,f21,f23,f24,f25,f43,f44,f45,f46,f47,f48,f49,f50,'
                   f'f55,f57,f58,f60,f62,f84,f85,f92,f105,f115,f116,f117,f162,f163,f164,f167,f168,f169,f170')
        val_raw = _get(val_url)
        val_data = json.loads(val_raw).get('data') or {}
        
        if val_data:
            result['valuation'] = {
                'price': val_data.get('f43', 0) / 100 if val_data.get('f43') else None,
                'pe_ttm': val_data.get('f164', 'N/A'),  # 市盈率TTM
                'pe_static': val_data.get('f162', 'N/A'),  # 静态市盈率
                'pb': val_data.get('f23', 'N/A'),  # 市净率
                'ps': val_data.get('f163', 'N/A'),  # 市销率
                'market_cap': round(val_data.get('f20', 0) / 1e8, 2) if val_data.get('f20') else None,  # 总市值（亿）
                'float_cap': round(val_data.get('f21', 0) / 1e8, 2) if val_data.get('f21') else None,  # 流通市值（亿）
                'total_shares': round(val_data.get('f84', 0) / 1e8, 2) if val_data.get('f84') else None,  # 总股本（亿股）
                'float_shares': round(val_data.get('f85', 0) / 1e8, 2) if val_data.get('f85') else None,  # 流通股（亿股）
                'turnover_rate': val_data.get('f8', 'N/A'),  # 换手率
                'volume_ratio': val_data.get('f10', 'N/A'),  # 量比
                'pct_change': val_data.get('f3', 'N/A'),  # 涨跌幅
            }
    except Exception as e:
        result['profile_error'] = str(e)
    
    return result


# ============================================================
# 2. 核心财务指标
# ============================================================
def financial_summary(code):
    """核心财务指标 — 近4期 (datacenter API)"""
    result = {'code': code, 'timestamp': datetime.now().isoformat()}
    
    suffix = 'SH' if code.startswith('6') or code.startswith('5') else 'SZ'
    
    try:
        url = (f'https://datacenter-web.eastmoney.com/api/data/v1/get?'
               f'reportName=RPT_F10_FINANCE_MAINFINADATA&'
               f'filter=(SECUCODE%3D%22{code}.{suffix}%22)&'
               f'pageNumber=1&pageSize=4&'
               f'sortColumns=REPORT_DATE&sortTypes=-1&columns=ALL')
        
        raw = _get(url)
        data = json.loads(raw)
        items = (data.get('result') or {}).get('data') or []
        
        financials = []
        for r in items[:4]:
            f = {
                'date': r.get('REPORT_DATE_NAME', ''),
                'net_profit_yi': round(r.get('PARENTNETPROFIT', 0) / 1e8, 2) if r.get('PARENTNETPROFIT') else None,
                'profit_yoy': round(r.get('PARENTNETPROFITTZ', 0), 2) if r.get('PARENTNETPROFITTZ') else None,
                'operate_profit_yi': round(r.get('OPERATE_PROFIT_PK', 0) / 1e8, 2) if r.get('OPERATE_PROFIT_PK') else None,
                'roe': round(r.get('ROEJQ', 0), 2) if r.get('ROEJQ') else None,
                'eps': r.get('EPSJB'),
                'eps_yoy': round(r.get('EPSJBTZ', 0), 2) if r.get('EPSJBTZ') else None,
                'bps': round(r.get('BPS', 0), 2) if r.get('BPS') else None,
                'debt_ratio': round(r.get('INTEREST_DEBT_RATIO', 0), 2) if r.get('INTEREST_DEBT_RATIO') else None,
                'ocf_yi': round(r.get('NETCASH_OPERATE_PK', 0) / 1e8, 2) if r.get('NETCASH_OPERATE_PK') else None,
                'cash_ratio': round(r.get('CASH_RATIO', 0), 2) if r.get('CASH_RATIO') else None,
            }
            financials.append(f)
        
        result['financials'] = financials
        
        if financials:
            latest = financials[0]
            signals = []
            
            profit_yoy = latest.get('profit_yoy')
            if profit_yoy is not None:
                if profit_yoy > 50:
                    signals.append(f'🚀 净利高增长 {profit_yoy}%')
                elif profit_yoy > 0:
                    signals.append(f'📈 净利正增长 {profit_yoy}%')
                else:
                    signals.append(f'📉 净利下滑 {profit_yoy}%')
            
            roe = latest.get('roe')
            if roe is not None:
                if roe > 15:
                    signals.append(f'✅ ROE优秀 {roe}%')
                elif roe > 8:
                    signals.append(f'🟡 ROE一般 {roe}%')
                else:
                    signals.append(f'⚠️ ROE偏低 {roe}%')
            
            debt = latest.get('debt_ratio')
            if debt is not None and debt > 60:
                signals.append(f'⚠️ 负债率偏高 {debt}%')
            
            ocf = latest.get('ocf_yi')
            if ocf is not None and ocf < 0:
                signals.append(f'🔴 经营现金流为负 {ocf}亿')
            
            result['financial_signals'] = signals
            
    except Exception as e:
        result['finance_error'] = str(e)
    
    return result


# ============================================================
# 3. 最新公告
# ============================================================
def latest_announcements(code, limit=10):
    """最新公告摘要"""
    result = {'code': code, 'timestamp': datetime.now().isoformat()}
    
    try:
        url = (f'https://np-anotice-stock.eastmoney.com/api/security/ann?'
               f'stock_list={code}&page_size={limit}&page_index=1&'
               f'ann_type=SHA,SZA&client_source=web&f_node=0')
        
        raw = _get(url)
        data = json.loads(raw)
        
        items = (data.get('data') or {}).get('list') or []
        announcements = []
        
        for item in items:
            ann = {
                'title': item.get('title', ''),
                'date': item.get('notice_date', ''),
                'type': item.get('columns', [{}])[0].get('column_name', '') if item.get('columns') else '',
            }
            
            # 重要公告标记
            title = ann['title']
            if any(kw in title for kw in ['业绩', '利润', '营收', '增长', '下降', '亏损', '预告', '快报']):
                ann['importance'] = '🔴 业绩相关'
            elif any(kw in title for kw in ['股东', '增持', '减持', '回购', '质押']):
                ann['importance'] = '🟡 股东变动'
            elif any(kw in title for kw in ['合同', '中标', '订单', '签署']):
                ann['importance'] = '🟢 业务进展'
            elif any(kw in title for kw in ['分红', '派息', '转增']):
                ann['importance'] = '💰 分红送转'
            elif any(kw in title for kw in ['风险', '警示', '退市', '处罚', '诉讼']):
                ann['importance'] = '⚠️ 风险事项'
            else:
                ann['importance'] = ''
            
            announcements.append(ann)
        
        result['announcements'] = announcements
        
        # 统计近期公告类型
        important = [a for a in announcements if a['importance']]
        result['important_count'] = len(important)
        
    except Exception as e:
        result['announce_error'] = str(e)
    
    return result


# ============================================================
# 4. 公司相关新闻
# ============================================================
def company_news(code, limit=10):
    """公司相关新闻，优先东财搜索，失败时退化到公告标题备用。"""
    result = {'code': code, 'timestamp': datetime.now().isoformat()}
    
    try:
        param = {
            'uid': '',
            'keyword': code,
            'type': ['cmsArticleWebOld'],
            'client': 'web',
            'clientType': 'web',
            'clientVersion': 'curr',
            'param': {
                'cmsArticleWebOld': {
                    'searchScope': 'default',
                    'sort': 'default',
                    'pageIndex': 1,
                    'pageSize': limit,
                }
            }
        }
        encoded = urllib.parse.quote(json.dumps(param, ensure_ascii=False, separators=(',', ':')))
        url = f'https://search-api-web.eastmoney.com/search/jsonp?cb=jQueryCallback&param={encoded}'
        raw = _get(url)
        start = raw.find('(')
        end = raw.rfind(')')
        if start != -1 and end != -1 and end > start:
            raw = raw[start+1:end]
        data = json.loads(raw)
        articles = (data.get('result') or {}).get('cmsArticleWebOld') or []
        news = []
        for a in articles:
            news.append({
                'title': a.get('title', '').replace('<em>', '').replace('</em>', ''),
                'date': a.get('date', ''),
                'source': a.get('mediaName', ''),
                'url': a.get('url', ''),
            })
        if news:
            result['news'] = news
            result['news_source'] = 'eastmoney_search'
            return result
    except Exception as e:
        result['news_error'] = str(e)
    
    # 备用：退化到最近公告标题，至少给出公司层增量信息线索
    try:
        backup = latest_announcements(code, limit=limit)
        anns = backup.get('announcements', [])[:limit]
        result['news'] = [
            {
                'title': a.get('title', ''),
                'date': a.get('date', ''),
                'source': '公告备用',
                'url': '',
            }
            for a in anns
        ]
        result['news_source'] = 'announcement_fallback'
    except Exception as e:
        result['news_fallback_error'] = str(e)
    
    return result


# ============================================================
# 5. 完整画像
# ============================================================
def full_profile(code):
    """个股完整画像"""
    result = {
        'code': code,
        'analysis_time': datetime.now().isoformat(),
    }
    
    result['company'] = company_profile(code)
    result['finance'] = financial_summary(code)
    result['announcements'] = latest_announcements(code)
    result['news'] = company_news(code)
    
    return result


def batch_profile():
    """自选股批量画像"""
    stocks = load_watchlist()
    
    results = []
    for s in stocks:
        code = s if isinstance(s, str) else s.get('code', '')
        name = s.get('name', '') if isinstance(s, dict) else ''
        if code:
            print(f"画像 {code} {name}...", file=sys.stderr)
            r = full_profile(code)
            r['name'] = name
            results.append(r)
    
    return results


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'batch'
    
    if cmd == 'profile':
        data = company_profile(sys.argv[2])
    elif cmd == 'finance':
        data = financial_summary(sys.argv[2])
    elif cmd == 'announce':
        data = latest_announcements(sys.argv[2])
    elif cmd == 'news':
        data = company_news(sys.argv[2])
    elif cmd == 'full':
        data = full_profile(sys.argv[2])
    elif cmd == 'batch':
        data = batch_profile()
    else:
        print(f"Usage: {sys.argv[0]} [profile|finance|announce|news|full <code>|batch]", file=sys.stderr)
        sys.exit(1)
    
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
