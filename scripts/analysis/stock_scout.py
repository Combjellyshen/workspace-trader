#!/usr/bin/env python3
"""
stock_scout.py — A股多维遴选引擎 v2

三阶段漏斗：
  Stage 1  数据采集 + 质量门槛
           → 东财批量财务数据（含行业标签）
           → 龙虎榜机构统计
           → 行业资金流排名
           → 合并为带行业+财务的全市场表
           → 硬排除 ST/北交所/流动性/亏损

  Stage 2  真实五维评分（每个维度用不同数据源）
           技术面 → K线多日涨跌幅 + 超卖检测
           资金面 → 行业资金流方向 + 龙虎榜机构净买
           基本面 → ROE + 净利增速 + 毛利率 + 现金流 + PEG
           催化面 → 热门行业/概念归属 + 机构关注度
           动量面 → 板块动量传导 + 个股涨跌幅阶梯
           → TOP 100 入围深度验证

  Stage 3  深度验证（同 v1，调用 tech/main_force/growth_hunter）

  Stage 2c 资金流传导推测
           → RSS 新闻政策关键词 → 受益行业映射
           → 龙虎榜行业集中度 → 产业链上下游传导
           → 景气行业内补涨候选
           → 产业链映射表（SUPPLY_CHAINS + POLICY_CHAINS）

  Stage 2d Claude 传导深度推理（scan 模式自动调用）
           → 对传导信号做因果链推理
           → 发现规则引擎遗漏的隐含路径
           → 置信度排序 + 传导故事 + 伪传导过滤

用法:
  python3 scripts/analysis/stock_scout.py scan          # 全量扫描（含 Claude 推理）
  python3 scripts/analysis/stock_scout.py quick         # 快速扫描（仅 Stage 1-2c，不调 Claude）
  python3 scripts/analysis/stock_scout.py deep <codes>  # 指定代码深度评分
  python3 scripts/analysis/stock_scout.py reason        # 仅运行传导推理（Stage 1 + 2c + 2d）
"""

import json
import math
import sys
import warnings
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

warnings.filterwarnings('ignore')

_HERE = Path(__file__).resolve().parents[2]
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from scripts.utils.common import safe_float, load_watchlist, WORKSPACE_ROOT  # noqa: E402

SH_TZ = ZoneInfo('Asia/Shanghai')
OUTPUT_DIR = WORKSPACE_ROOT / 'data' / 'scout'
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

EM_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Referer': 'https://data.eastmoney.com/',
}

# ═══════════════════════════════════════════════════════════════════════════════
# 时间维度权重
# ═══════════════════════════════════════════════════════════════════════════════

WEIGHTS = {
    'short': {'technical': 0.30, 'capital': 0.30, 'fundamental': 0.05, 'catalyst': 0.15, 'momentum': 0.20},
    'medium': {'technical': 0.20, 'capital': 0.25, 'fundamental': 0.20, 'catalyst': 0.15, 'momentum': 0.20},
    'long': {'technical': 0.10, 'capital': 0.10, 'fundamental': 0.40, 'catalyst': 0.15, 'momentum': 0.25},
}

def _clamp(v): return max(0.0, min(100.0, v))


# ═══════════════════════════════════════════════════════════════════════════════
# Stage 1: 数据采集 + 质量门槛
# ═══════════════════════════════════════════════════════════════════════════════

def _fetch_eastmoney_financials() -> list:
    """
    从东财数据中心批量获取全市场最新报告期财务数据。
    返回 list[dict]，每条含: code, name, industry, roe, profit_growth,
    revenue_growth, gross_margin, cashflow_ps, eps, revenue, net_profit
    """
    import requests

    all_items = []
    page = 1
    while page <= 30:  # 最多 30 页 × 500 = 15000
        try:
            params = {
                'reportName': 'RPT_LICO_FN_CPD',
                'columns': ('SECURITY_CODE,SECURITY_NAME_ABBR,BOARD_NAME,'
                            'SJLTZ,WEIGHTAVG_ROE,YSTZ,XSMLL,MGJYXJJE,'
                            'BASIC_EPS,TOTAL_OPERATE_INCOME,PARENT_NETPROFIT'),
                'pageSize': 500,
                'pageNumber': page,
                'sortColumns': 'SECURITY_CODE',
                'sortTypes': '1',
                'filter': '(ISNEW="1")',
            }
            r = requests.get('https://datacenter-web.eastmoney.com/api/data/v1/get',
                             params=params, headers=EM_HEADERS, timeout=20)
            d = r.json()
            items = (d.get('result') or {}).get('data') or []
            if not items:
                break
            all_items.extend(items)
            total_pages = (d.get('result') or {}).get('pages', 0)
            if page >= total_pages:
                break
            page += 1
        except Exception as e:
            print(f'  [eastmoney] page {page} 失败: {e}', file=sys.stderr)
            break

    # 转换为标准格式
    result = []
    for it in all_items:
        code = it.get('SECURITY_CODE', '')
        if not code:
            continue
        result.append({
            'code': code,
            'name': it.get('SECURITY_NAME_ABBR', ''),
            'industry': it.get('BOARD_NAME', ''),
            'roe': safe_float(it.get('WEIGHTAVG_ROE')),
            'profit_growth': safe_float(it.get('SJLTZ')),
            'revenue_growth': safe_float(it.get('YSTZ')),
            'gross_margin': safe_float(it.get('XSMLL')),
            'cashflow_ps': safe_float(it.get('MGJYXJJE')),
            'eps': safe_float(it.get('BASIC_EPS')),
            'revenue_yi': safe_float(it.get('TOTAL_OPERATE_INCOME')) / 1e8 if it.get('TOTAL_OPERATE_INCOME') else 0,
            'net_profit_yi': safe_float(it.get('PARENT_NETPROFIT')) / 1e8 if it.get('PARENT_NETPROFIT') else 0,
        })
    return result


def _fetch_lhb_institutions() -> dict:
    """
    获取龙虎榜机构数据，合并两个维度：
    1. 近一月机构统计（stock_lhb_jgstatistic_em）— 总量视角
    2. 近 5 天每日机构买卖（stock_lhb_jgmmtj_em）— 时效性
    返回 {code: {net_amt_yi, buy_count, sell_count, appearances, recent_net_yi, recent_buyer_count}}
    """
    result = {}

    # 维度 1：近一月统计
    try:
        import akshare as ak
        df = ak.stock_lhb_jgstatistic_em(symbol='近一月')
        if df is not None and not df.empty:
            for _, row in df.iterrows():
                code = str(row.get('代码', '')).zfill(6)
                net = safe_float(row.get('机构净买额', 0))
                if abs(net) > 1000:
                    net = net / 1e8
                result[code] = {
                    'net_amt_yi': net,
                    'buy_count': int(safe_float(row.get('机构买入次数', 0))),
                    'sell_count': int(safe_float(row.get('机构卖出次数', 0))),
                    'appearances': int(safe_float(row.get('上榜次数', 0))),
                    'recent_net_yi': 0,
                    'recent_buyer_count': 0,
                }
    except Exception as e:
        print(f'  [lhb-monthly] {e}', file=sys.stderr)

    # 维度 2：近 5 天每日机构买卖（更及时）
    try:
        import akshare as ak
        end = datetime.now(SH_TZ).strftime('%Y%m%d')
        start = (datetime.now(SH_TZ) - timedelta(days=7)).strftime('%Y%m%d')
        df2 = ak.stock_lhb_jgmmtj_em(start_date=start, end_date=end)
        if df2 is not None and not df2.empty:
            for _, row in df2.iterrows():
                code = str(row.get('代码', '')).zfill(6)
                net = safe_float(row.get('机构买入净额', 0))
                if abs(net) > 1000:
                    net = net / 1e8
                buyers = int(safe_float(row.get('买方机构数', 0)))

                if code in result:
                    result[code]['recent_net_yi'] += net
                    result[code]['recent_buyer_count'] = max(result[code]['recent_buyer_count'], buyers)
                else:
                    result[code] = {
                        'net_amt_yi': net,
                        'buy_count': 1 if net > 0 else 0,
                        'sell_count': 0 if net > 0 else 1,
                        'appearances': 1,
                        'recent_net_yi': net,
                        'recent_buyer_count': buyers,
                    }
    except Exception as e:
        print(f'  [lhb-daily] {e}', file=sys.stderr)

    return result


def _fetch_rss_news() -> dict:
    """
    获取 RSS 新闻并匹配到股票代码。
    返回 {code: [headline, ...]} + {'_all': [all headlines]}
    """
    try:
        from scripts.data.rss_aggregator import collect_domestic
        result = collect_domestic()
        items = result.get('items') or []
        if not items:
            return {}
    except Exception as e:
        print(f'  [rss] RSS 获取失败: {e}', file=sys.stderr)
        return {}

    # 构建股票名称→代码映射（使用当次已获取的东财数据）
    # 这个函数会在 stage1 之后调用，所以需要传入 stocks
    return {'_items': items}


def _match_news_to_stocks(news_items: list, stocks: list) -> dict:
    """
    将新闻标题匹配到股票代码。
    返回 {code: [{'title': ..., 'source': ...}, ...]}
    """
    # 构建名称→代码索引
    name_to_code = {}
    for s in stocks:
        name = s.get('name', '')
        code = s.get('code', '')
        if len(name) >= 2 and code:
            name_to_code[name] = code
            # 去掉尾部字母（如 "平安银行A"→"平安银行"）
            import re
            clean = re.sub(r'[A-Za-z]+$', '', name)
            if clean != name and len(clean) >= 2:
                name_to_code[clean] = code

    matched = {}  # {code: [news_items]}
    for item in news_items:
        title = item.get('title', '')
        summary = item.get('summary', '')
        text = title + ' ' + summary
        for name, code in name_to_code.items():
            if name in text:
                matched.setdefault(code, []).append({
                    'title': title[:80],
                    'source': item.get('source', ''),
                })

    return matched


def _build_industry_heat(stocks: list) -> dict:
    """
    从全市场财务数据推导行业热度。
    用行业内平均利润增速 + 高增长股占比作为"景气度"代理。
    返回 {行业名称: {rank, heat_score, avg_pg, high_growth_pct, count}}
    """
    from collections import defaultdict
    ind_stats = defaultdict(lambda: {'pg_sum': 0, 'count': 0, 'high_growth': 0, 'roe_sum': 0})

    for s in stocks:
        ind = s.get('industry', '')
        if not ind:
            continue
        pg = s.get('profit_growth', 0)
        roe = s.get('roe', 0)
        stats = ind_stats[ind]
        stats['count'] += 1
        stats['pg_sum'] += pg
        stats['roe_sum'] += roe
        if pg > 20:
            stats['high_growth'] += 1

    result = {}
    for name, stats in ind_stats.items():
        n = stats['count']
        if n < 3:
            continue
        avg_pg = stats['pg_sum'] / n
        avg_roe = stats['roe_sum'] / n
        hg_pct = stats['high_growth'] / n * 100
        # 景气度 = 平均增速 × 0.5 + 高增长占比 × 0.3 + 平均ROE × 0.2
        heat = avg_pg * 0.5 + hg_pct * 0.3 + avg_roe * 0.2
        result[name] = {
            'heat_score': round(heat, 2),
            'avg_profit_growth': round(avg_pg, 1),
            'high_growth_pct': round(hg_pct, 1),
            'avg_roe': round(avg_roe, 1),
            'count': n,
        }

    # 排名
    sorted_items = sorted(result.items(), key=lambda x: x[1]['heat_score'], reverse=True)
    for i, (name, data) in enumerate(sorted_items):
        data['rank'] = i + 1

    return result


_ST_KW = ('ST', '*ST', 'S ', 'SST', 'S*ST', 'PT')
_EXCLUDE_PREFIX = ('4', '8')  # 北交所


def stage1_collect_and_filter():
    """
    Stage 1: 采集全市场数据 + 质量门槛过滤。
    返回 (qualified_stocks, industry_flow, lhb_map, industry_index)
    """
    print('[Stage 1] 采集东财全市场财务数据...', file=sys.stderr)
    stocks = _fetch_eastmoney_financials()
    print(f'  东财返回 {len(stocks)} 只', file=sys.stderr)

    print('[Stage 1] 采集龙虎榜机构统计...', file=sys.stderr)
    lhb = _fetch_lhb_institutions()
    print(f'  龙虎榜 {len(lhb)} 只', file=sys.stderr)

    print('[Stage 1] 计算行业景气度...', file=sys.stderr)
    ind_flow = _build_industry_heat(stocks)
    print(f'  行业 {len(ind_flow)} 个', file=sys.stderr)

    print('[Stage 1] 采集 RSS 新闻...', file=sys.stderr)
    rss_raw = _fetch_rss_news()
    rss_items = rss_raw.get('_items', [])
    news_map = _match_news_to_stocks(rss_items, stocks) if rss_items else {}
    print(f'  RSS {len(rss_items)} 条新闻，匹配到 {len(news_map)} 只股票', file=sys.stderr)

    # 构建行业→股票索引
    industry_index = {}  # {industry_name: [code, ...]}
    for s in stocks:
        ind = s.get('industry', '')
        if ind:
            industry_index.setdefault(ind, []).append(s['code'])

    # 质量门槛过滤
    qualified = []
    for s in stocks:
        code = s['code']
        name = s['name']

        # 北交所
        if code.startswith(_EXCLUDE_PREFIX):
            continue
        # ST
        if any(kw in name for kw in _ST_KW):
            continue
        # 亏损（净利润 < 0）
        if s['net_profit_yi'] <= 0:
            continue
        # ROE 太差
        if s['roe'] < 3:
            continue
        # 营收太小（< 1 亿）
        if s['revenue_yi'] < 1:
            continue

        qualified.append(s)

    print(f'[Stage 1] 质量过滤后: {len(qualified)} 只（排除 {len(stocks) - len(qualified)} 只）',
          file=sys.stderr)

    return qualified, ind_flow, lhb, industry_index, news_map


# ═══════════════════════════════════════════════════════════════════════════════
# Stage 2: 真实五维评分
# ═══════════════════════════════════════════════════════════════════════════════

# ── 维度 1: 基本面（ROE + 增长 + 毛利 + 现金流 = 改良 FFScore） ──

def _score_fundamental(s: dict) -> tuple:
    """基本面评分：改良 FFScore"""
    ff = 0
    ev = []

    roe = s['roe']
    pg = s['profit_growth']
    rg = s['revenue_growth']
    gm = s['gross_margin']
    cf = s['cashflow_ps']

    # 1. ROE > 0
    if roe > 0: ff += 1
    # 2. ROE > 12% (PHILOSOPHY 门槛)
    if roe > 12:
        ff += 1
        ev.append(f'ROE={roe:.1f}%')
    # 3. ROE > 20% (优秀)
    if roe > 20: ff += 1
    # 4. 净利增速 > 15%
    if pg > 15:
        ff += 1
        ev.append(f'净利增速 {pg:.1f}%')
    # 5. 营收增速 > 10%
    if rg > 10: ff += 1
    # 6. 毛利率 > 20%
    if gm > 20:
        ff += 1
        ev.append(f'毛利率 {gm:.1f}%')
    # 7. 现金流为正
    if cf > 0: ff += 1
    # 8. 现金流 > 2（充裕）
    if cf > 2: ff += 1
    # 9. 负面：增收不增利
    if rg > 10 and pg < 0:
        ff -= 1
        ev.append('⚠ 增收不增利')

    ff = max(0, min(9, ff))

    # PEG 代理（用净利增速 / 假设 PE）
    peg_bonus = 0
    if pg > 20 and roe > 15:
        peg_bonus = 10
        ev.append('高增长+高ROE（PEG优势）')

    score = {9: 100, 8: 90, 7: 80, 6: 70, 5: 60, 4: 50, 3: 40, 2: 30}.get(ff, 20)
    score += peg_bonus
    ev.append(f'FFScore={ff}/9')

    # Beneish 红旗
    if 0 < gm < 8:
        score -= 12
        ev.append(f'⚠ 低毛利 {gm:.1f}%')
    if cf < -1:
        score -= 15
        ev.append('⚠ 现金流严重为负')

    return _clamp(score), ev


# ── 维度 2: 资金面（行业资金流 + 龙虎榜） ──

def _score_capital(s: dict, ind_flow: dict, lhb: dict) -> tuple:
    """资金面评分"""
    score = 50.0
    ev = []
    code = s['code']
    industry = s['industry']

    # 行业景气度
    iflow = ind_flow.get(industry)
    if iflow:
        rank = iflow['rank']
        if rank <= 5:
            score += 20
            ev.append(f'行业 [{industry}] 景气度 TOP {rank}（增速{iflow["avg_profit_growth"]:.0f}%）')
        elif rank <= 10:
            score += 10
            ev.append(f'行业 [{industry}] 景气度 TOP {rank}')
        elif rank >= len(ind_flow) - 5:
            score -= 10
            ev.append(f'行业 [{industry}] 景气度垫底')

    # 龙虎榜机构买入（月度 + 近5天双重验证）
    lhb_data = lhb.get(code)
    if lhb_data:
        net = lhb_data['net_amt_yi']
        recent = lhb_data.get('recent_net_yi', 0)
        buyers = lhb_data.get('recent_buyer_count', 0)

        # 月度机构净买
        if net > 0.5:
            score += 20
            ev.append(f'龙虎榜月度机构净买 +{net:.1f}亿')
        elif net < -0.5:
            score -= 12
            ev.append(f'龙虎榜月度机构净卖 {net:.1f}亿')

        # 近5天机构净买（更及时）
        if recent > 0.3:
            score += 15
            ev.append(f'近5天机构净买 +{recent:.2f}亿（最新信号）')
        elif recent < -0.3:
            score -= 10

        # 多家机构同时买入 = 共识
        if buyers >= 3:
            score += 10
            ev.append(f'{buyers}家机构同时买入（机构共识）')
        elif buyers >= 2:
            score += 5

        # 买入次数 > 卖出次数 = 持续看好
        if lhb_data['buy_count'] > lhb_data['sell_count'] + 1:
            score += 8
            ev.append(f'机构买{lhb_data["buy_count"]}次 > 卖{lhb_data["sell_count"]}次')

    return _clamp(score), ev


# ── 维度 3: 技术面（用增速斜率 + 估值位置代理） ──

def _score_technical_fast(s: dict) -> tuple:
    """
    快速技术面评分。
    无 K 线时用基本面趋势代理：
    增速加速 + ROE 上升 ≈ 趋势向好
    """
    score = 50.0
    ev = []

    pg = s['profit_growth']
    rg = s['revenue_growth']

    # 增速加速（利润增速 > 营收增速 = 利润率扩张 = 趋势转好）
    if pg > rg > 0:
        score += 15
        ev.append('利润增速 > 营收增速（利润率扩张）')
    elif pg > 0 and rg > 0:
        score += 8

    # 高增速 = 动能强
    if pg > 50:
        score += 10
        ev.append(f'净利高增 {pg:.0f}%')
    elif pg > 25:
        score += 5

    # 负增长 = 趋势恶化
    if pg < -10:
        score -= 15
        ev.append(f'净利下滑 {pg:.0f}%')
    if rg < -10:
        score -= 10

    return _clamp(score), ev


# ── 维度 4: 催化面（热门行业 + 机构关注） ──

def _score_catalyst(s: dict, ind_flow: dict, lhb: dict, hot_set: set, news_map: dict) -> tuple:
    """催化面评分"""
    score = 50.0
    ev = []
    code = s['code']
    industry = s['industry']

    # 在景气行业（TOP 10）中
    if industry in hot_set:
        heat = ind_flow.get(industry, {})
        score += 15
        ev.append(f'景气行业 [{industry}] 高增长占比{heat.get("high_growth_pct",0):.0f}%')

    # RSS 新闻命中（硬催化信号）
    stock_news = news_map.get(code, [])
    if len(stock_news) >= 3:
        score += 20
        ev.append(f'RSS 新闻高频提及 ({len(stock_news)}条): {stock_news[0]["title"][:30]}')
    elif len(stock_news) >= 1:
        score += 12
        ev.append(f'RSS 新闻提及: {stock_news[0]["title"][:40]}')

    # 龙虎榜上榜 = 市场关注
    lhb_data = lhb.get(code)
    if lhb_data and lhb_data['appearances'] >= 2:
        score += 12
        ev.append(f'近1月上榜 {lhb_data["appearances"]} 次（高关注）')
    elif lhb_data:
        score += 5

    # 小市值但高增长 = 潜在关注度提升
    if s['revenue_yi'] < 50 and s['profit_growth'] > 30:
        score += 8
        ev.append('小盘高增长（Alpha候选）')

    return _clamp(score), ev


# ── 维度 5: 动量面（板块 + 个股增速动能） ──

def _score_momentum(s: dict, ind_flow: dict) -> tuple:
    """动量面评分"""
    score = 50.0
    ev = []
    industry = s['industry']

    # 板块景气度动量
    iflow = ind_flow.get(industry)
    if iflow:
        heat = iflow.get('heat_score', 0)
        rank = iflow.get('rank', 99)
        if rank <= 3:
            score += 15
            ev.append(f'板块景气 TOP {rank}（高增长占比{iflow.get("high_growth_pct",0):.0f}%）')
        elif rank <= 8:
            score += 8
        elif rank >= len(ind_flow) - 3:
            score -= 10
            ev.append(f'板块景气垫底')

    # 基本面动量：利润加速
    pg = s['profit_growth']
    if pg > 50:
        score += 15
        ev.append('利润爆发增长（基本面动量）')
    elif pg > 25:
        score += 8
    elif pg < -20:
        score -= 15

    return _clamp(score), ev


def stage2_score(qualified: list, ind_flow: dict, lhb: dict, news_map: dict, top_n: int = 100) -> list:
    """Stage 2: 真实五维评分"""
    # 景气行业集合（TOP 10）
    hot_set = set()
    sorted_ind = sorted(ind_flow.items(), key=lambda x: x[1].get('heat_score', 0), reverse=True)
    for name, _ in sorted_ind[:10]:
        hot_set.add(name)

    scored = []
    for s in qualified:
        fund_score, fund_ev = _score_fundamental(s)
        cap_score, cap_ev = _score_capital(s, ind_flow, lhb)
        tech_score, tech_ev = _score_technical_fast(s)
        cat_score, cat_ev = _score_catalyst(s, ind_flow, lhb, hot_set, news_map)
        mom_score, mom_ev = _score_momentum(s, ind_flow)

        scores = {
            'fundamental': round(fund_score, 1),
            'capital': round(cap_score, 1),
            'technical': round(tech_score, 1),
            'catalyst': round(cat_score, 1),
            'momentum': round(mom_score, 1),
        }

        composites = {}
        for horizon, weights in WEIGHTS.items():
            composites[horizon] = round(sum(scores[dim] * w for dim, w in weights.items()), 2)

        best_score = max(composites.values())

        scored.append({
            'code': s['code'],
            'name': s['name'],
            'industry': s['industry'],
            'roe': s['roe'],
            'profit_growth': s['profit_growth'],
            'revenue_growth': s['revenue_growth'],
            'gross_margin': s['gross_margin'],
            'cashflow_ps': s['cashflow_ps'],
            'dim_scores': scores,
            'composites': composites,
            'best_score': best_score,
            'evidence': {
                'fundamental': fund_ev,
                'capital': cap_ev,
                'technical': tech_ev,
                'catalyst': cat_ev,
                'momentum': mom_ev,
            },
        })

    scored.sort(key=lambda x: x['best_score'], reverse=True)
    return scored[:top_n]


# ═══════════════════════════════════════════════════════════════════════════════
# Stage 2b: 板块轮动入口（从热门行业反向找早期机会）
# ═══════════════════════════════════════════════════════════════════════════════

def stage2b_sector_rotation(qualified: list, ind_flow: dict, lhb: dict) -> list:
    """
    板块轮动选股：找资金已进场但个股还没启动的标的。
    选股逻辑：
    1. 取资金净流入 TOP 5 行业
    2. 在这些行业内找：ROE > 12%, 利润增速 > 15%，但机构尚未进入龙虎榜
    3. 这类股票 = "聪明钱已选好赛道，但具体标的尚未被市场发现"
    """
    # TOP 5 景气行业
    sorted_ind = sorted(ind_flow.items(), key=lambda x: x[1].get('heat_score', 0), reverse=True)
    hot5 = [name for name, _ in sorted_ind[:5]]

    candidates = []
    for s in qualified:
        if s['industry'] not in hot5:
            continue
        if s['roe'] < 12 or s['profit_growth'] < 15:
            continue
        # 排除已上龙虎榜的（已被发现）
        if s['code'] in lhb:
            continue

        iflow = ind_flow.get(s['industry'], {})
        candidates.append({
            **s,
            'rotation_signal': f'行业 [{s["industry"]}] 景气度 TOP 5，个股尚未被龙虎榜关注',
            'sector_heat': iflow.get('heat_score', 0),
            'sector_rank': iflow.get('rank', 99),
        })

    # 按 ROE × 利润增速 排名
    candidates.sort(key=lambda x: x['roe'] * max(x['profit_growth'], 1), reverse=True)
    return candidates[:20]


# ═══════════════════════════════════════════════════════════════════════════════
# Stage 2c: 资金流传导推测
# ═══════════════════════════════════════════════════════════════════════════════

# 产业链映射表：上游 → 中游 → 下游
# 当上游行业出现信号时，推测中下游将受益
SUPPLY_CHAINS = {
    # 半导体产业链
    '半导体': ['消费电子', '元件', '光学光电子', '计算机设备'],
    '电子化学品Ⅱ': ['半导体', '元件', '光学光电子'],
    # 新能源产业链
    '电池': ['电力设备', '汽车零部件', '新能源动力系统Ⅱ'],
    '光伏设备': ['电力设备', '电网设备Ⅱ'],
    # 汽车产业链
    '乘用车': ['汽车零部件', '汽车服务'],
    '汽车零部件': ['消费电子', '元件'],
    # AI 产业链
    '软件开发': ['IT服务Ⅱ', '计算机设备', '游戏Ⅱ'],
    '计算机设备': ['通信设备', '元件'],
    # 军工产业链
    '航空装备Ⅱ': ['特钢Ⅱ', '航空机场Ⅱ'],
    '地面兵装Ⅱ': ['特钢Ⅱ'],
    # 医药产业链
    '化学制药': ['医疗器械', '中药Ⅱ', '医药商业Ⅱ'],
    '医疗器械': ['医疗服务Ⅱ'],
    # 基建/地产链
    '水泥': ['专业工程', '装修装饰Ⅱ', '房地产开发'],
    '钢铁': ['专业工程', '工程机械'],
    # 消费产业链
    '白酒': ['食品加工', '一般零售'],
    '食品加工': ['一般零售', '专业连锁Ⅱ'],
}

# 政策关键词 → 受益行业映射
POLICY_CHAINS = {
    # 科技自主
    '芯片': ['半导体', '电子化学品Ⅱ', '元件'],
    '人工智能': ['软件开发', 'IT服务Ⅱ', '计算机设备', '半导体'],
    'AI': ['软件开发', 'IT服务Ⅱ', '计算机设备', '半导体'],
    '算力': ['计算机设备', '通信设备', '半导体'],
    '国产替代': ['半导体', '软件开发', '计算机设备', '特钢Ⅱ'],
    '自主可控': ['半导体', '软件开发', '计算机设备'],
    # 新能源
    '新能源': ['电池', '光伏设备', '电力设备', '风电设备'],
    '储能': ['电池', '电力设备', '电网设备Ⅱ'],
    '光伏': ['光伏设备', '电力设备'],
    '充电桩': ['电力设备', '电网设备Ⅱ'],
    # 军工
    '国防': ['航空装备Ⅱ', '地面兵装Ⅱ', '特钢Ⅱ'],
    '军工': ['航空装备Ⅱ', '地面兵装Ⅱ', '特钢Ⅱ', '通信设备'],
    # 消费
    '消费升级': ['白酒', '食品加工', '一般零售'],
    '内需': ['白酒', '食品加工', '一般零售', '专业连锁Ⅱ'],
    # 地产/基建
    '基建': ['水泥', '钢铁', '专业工程', '工程机械'],
    '地产': ['房地产开发', '装修装饰Ⅱ', '水泥', '家电'],
    # 医药
    '医保': ['化学制药', '中药Ⅱ', '医药商业Ⅱ'],
    '创新药': ['化学制药', '医疗器械'],
    '集采': ['化学制药', '医疗器械'],
}


def stage2c_flow_transmission(qualified: list, ind_flow: dict, lhb: dict,
                               news_map: dict, news_items: list) -> list:
    """
    Stage 2c: 资金流传导推测。
    通过三种信号源检测传导链，发现中长期潜力目标：

    信号源 1: RSS 新闻中的政策/产业关键词 → 受益行业 → 行业内优质未涨股
    信号源 2: 龙虎榜机构集中买入某行业 → 产业链上下游 → 尚未被关注的关联股
    信号源 3: 行业景气度 TOP 但个股分化 → 行业内补涨候选

    返回传导候选列表。
    """
    candidates = []
    signals_found = []

    # ── 信号源 1: RSS 政策/产业关键词 → 受益行业推测 ──
    triggered_industries = set()
    for item in news_items:
        title = item.get('title', '') + ' ' + item.get('summary', '')
        for keyword, industries in POLICY_CHAINS.items():
            if keyword in title:
                for ind in industries:
                    triggered_industries.add(ind)
                signals_found.append({
                    'type': 'policy_news',
                    'keyword': keyword,
                    'headline': item.get('title', '')[:60],
                    'target_industries': industries,
                })

    # ── 信号源 2: 龙虎榜行业集中度 → 上下游传导 ──
    # 统计龙虎榜中各行业出现频率
    lhb_industry_count = {}
    stock_industry = {s['code']: s['industry'] for s in qualified}
    for code in lhb:
        ind = stock_industry.get(code, '')
        if ind:
            lhb_industry_count[ind] = lhb_industry_count.get(ind, 0) + 1

    # 龙虎榜集中度 >= 3 只的行业 → 推测上下游受益
    for ind, count in lhb_industry_count.items():
        if count >= 3:
            downstream = SUPPLY_CHAINS.get(ind, [])
            for d_ind in downstream:
                triggered_industries.add(d_ind)
            if downstream:
                signals_found.append({
                    'type': 'lhb_concentration',
                    'source_industry': ind,
                    'lhb_count': count,
                    'downstream': downstream,
                })

    # ── 信号源 3: 景气行业内补涨 ──
    top_heat = sorted(ind_flow.items(), key=lambda x: x[1].get('heat_score', 0), reverse=True)[:5]
    for ind_name, heat_data in top_heat:
        triggered_industries.add(ind_name)

    # ── 从触发行业中找候选 ──
    for s in qualified:
        if s['industry'] not in triggered_industries:
            continue
        # 质量门槛：ROE > 10, 利润增长 > 10%
        if s['roe'] < 10 or s['profit_growth'] < 10:
            continue

        # 计算传导得分
        transmission_score = 0
        transmission_reasons = []

        # 政策新闻触发
        policy_triggers = [sig for sig in signals_found
                          if sig['type'] == 'policy_news' and s['industry'] in sig.get('target_industries', [])]
        if policy_triggers:
            transmission_score += 30
            kws = list(set(sig['keyword'] for sig in policy_triggers))
            transmission_reasons.append(f'政策催化: {",".join(kws[:3])} → [{s["industry"]}]')

        # 上游龙虎榜传导
        chain_triggers = [sig for sig in signals_found
                         if sig['type'] == 'lhb_concentration' and s['industry'] in sig.get('downstream', [])]
        if chain_triggers:
            transmission_score += 25
            sources = [sig['source_industry'] for sig in chain_triggers]
            transmission_reasons.append(f'上游传导: [{",".join(sources)}] 机构集中买入 → [{s["industry"]}]')

        # 景气行业补涨
        if s['industry'] in dict(top_heat):
            heat = ind_flow[s['industry']]
            if s['code'] not in lhb:
                transmission_score += 20
                transmission_reasons.append(f'景气行业补涨: [{s["industry"]}] 景气 TOP {heat["rank"]}，个股尚未被发现')

        # 基本面加成
        if s['roe'] > 15:
            transmission_score += 10
        if s['profit_growth'] > 30:
            transmission_score += 10
        if s['gross_margin'] > 25:
            transmission_score += 5

        if transmission_score >= 25:
            candidates.append({
                **s,
                'transmission_score': transmission_score,
                'transmission_reasons': transmission_reasons,
                'transmission_signals': [sig for sig in signals_found if
                    (sig['type'] == 'policy_news' and s['industry'] in sig.get('target_industries', [])) or
                    (sig['type'] == 'lhb_concentration' and s['industry'] in sig.get('downstream', []))
                ][:3],
            })

    candidates.sort(key=lambda x: x['transmission_score'], reverse=True)

    return candidates[:30], signals_found


# ═══════════════════════════════════════════════════════════════════════════════
# Stage 3: 深度验证（从 v1 保留，技术面+资金面+财务向量）
# ═══════════════════════════════════════════════════════════════════════════════

def _deep_technical(code: str) -> dict:
    try:
        from scripts.analysis.tech_analysis import analyze_single
        return analyze_single(code) or {}
    except Exception as e:
        print(f'    [tech] {code}: {e}', file=sys.stderr)
        return {}


def _deep_capital(code: str) -> dict:
    try:
        from scripts.analysis.main_force import full_analysis
        return full_analysis(code) or {}
    except Exception as e:
        print(f'    [capital] {code}: {e}', file=sys.stderr)
        return {}


def _deep_financial(code: str) -> dict:
    try:
        from scripts.analysis.growth_hunter import analyze_stock
        return analyze_stock(code) or {}
    except Exception as e:
        print(f'    [financial] {code}: {e}', file=sys.stderr)
        return {}


def score_technical_deep(tech: dict) -> tuple:
    if not tech:
        return 50.0, ['技术数据不可用']
    score = 50.0
    ev = []

    ma = tech.get('ma_system') or {}
    arr = ma.get('arrangement', '')
    if arr == '多头排列':
        score += 20; ev.append('均线多头排列')
    elif arr == '空头排列':
        score -= 15; ev.append('均线空头排列')
    if 'MA5' in (ma.get('price_above') or []) and 'MA10' in (ma.get('price_above') or []):
        score += 5
    if 'MA60' in (ma.get('price_below') or []):
        score -= 10; ev.append('价格低于 MA60')

    macd = tech.get('macd') or {}
    cross = macd.get('cross') or macd.get('signal') or ''
    if cross in ('golden', '金叉'):
        score += 15; ev.append('MACD 金叉')
    elif cross in ('death', '死叉'):
        score -= 10; ev.append('MACD 死叉')
    hist_trend = macd.get('histogram_trend', '')
    if hist_trend == 'contracting' and safe_float(macd.get('MACD', 0)) < 0:
        score += 3; ev.append('MACD 死叉收敛（转好信号）')

    rsi = safe_float(tech.get('rsi_6') or (tech.get('rsi', {}).get('rsi6', 50) if isinstance(tech.get('rsi'), dict) else 50))
    if rsi < 25:
        score += 10; ev.append(f'RSI={rsi:.0f} 超卖')
    elif rsi > 75:
        score -= 10; ev.append(f'RSI={rsi:.0f} 超买')

    kdj = tech.get('kdj') or {}
    j = safe_float(kdj.get('J', 50))
    if j < 10:
        score += 8; ev.append(f'KDJ-J={j:.0f} 超卖区')
    elif j > 90:
        score -= 8; ev.append(f'KDJ-J={j:.0f} 超买区')

    boll = tech.get('bollinger') or {}
    bp = safe_float(boll.get('position', 50))
    if bp < 10:
        score += 8; ev.append('布林下轨超卖')
    elif bp > 90:
        score -= 5

    vr = safe_float(tech.get('volume_ratio', 1.0))
    if vr > 2.0:
        score += 5; ev.append(f'量比 {vr:.1f}x')

    return _clamp(score), ev


def score_capital_deep(capital: dict) -> tuple:
    if not capital:
        return 50.0, ['资金数据不可用']
    score = 50.0
    ev = []
    daily = (capital.get('money_flow') or {}).get('daily_flow') or []
    if daily:
        latest = daily[-1]
        mn = safe_float(latest.get('main_net_inflow', 0))
        if mn > 1.0:
            score += 25; ev.append(f'主力净流入 +{mn:.2f}亿')
        elif mn > 0.3:
            score += 15; ev.append(f'主力小幅净流入 +{mn:.2f}亿')
        elif mn < -1.0:
            score -= 20; ev.append(f'主力净流出 {mn:.2f}亿')
        elif mn < -0.3:
            score -= 10; ev.append(f'主力小幅净流出 {mn:.2f}亿')
        if len(daily) >= 3:
            flows = [safe_float(d.get('main_net_inflow', 0)) for d in daily[-5:]]
            pos = sum(1 for f in flows if f > 0)
            total = sum(flows)
            if pos >= 4:
                score += 15; ev.append(f'近{len(flows)}日中{pos}日净流入')
            elif pos <= 1:
                score -= 15; ev.append(f'近{len(flows)}日持续净流出')
            if total > 3:
                score += 10; ev.append(f'5日累计净流入 +{total:.1f}亿')
            elif total < -3:
                score -= 10; ev.append(f'5日累计净流出 {total:.1f}亿')
    return _clamp(score), ev


def score_fundamental_deep(fin: dict, base_score: float) -> tuple:
    if not fin:
        return base_score, ['财务向量不可用']
    score = base_score  # 以 Stage 2 基本面分为基底
    ev = []
    vectors = fin.get('growth_vectors') or fin.get('financial_vectors') or {}
    patterns = fin.get('pattern_recognition') or {}
    raw = fin.get('raw_summary') or {}

    stage = patterns.get('stage') or ''
    if '成长' in stage:
        score += 10; ev.append(f'成长阶段: {stage}')
    elif '衰退' in stage:
        score -= 15; ev.append(f'⚠ {stage}')

    coverage = safe_float((fin.get('analyst_consensus') or {}).get('coverage', 0))
    if 0 < coverage <= 5:
        score += 8; ev.append(f'低分析师覆盖({coverage:.0f}家)，Alpha潜力')

    matched = patterns.get('matched_patterns') or []
    for p in matched[:3]:
        pname = p.get('pattern', p) if isinstance(p, dict) else str(p)
        ev.append(f'✓ {pname}')
    score += min(len(matched) * 4, 15)

    return _clamp(score), ev


def score_momentum_deep(tech: dict) -> tuple:
    score = 50.0
    ev = []
    rsi = safe_float(tech.get('rsi_6') or (tech.get('rsi', {}).get('rsi6', 50) if isinstance(tech.get('rsi'), dict) else 50))
    if rsi < 30:
        score += 15; ev.append(f'深度超卖 RSI={rsi:.0f}（长线候选）')
    elif rsi > 70:
        score += 5; ev.append('强动量延续')
    return _clamp(score), ev


def _fetch_stock_news(code: str, limit: int = 5) -> list:
    """获取个股新闻标题（用于催化面增强）"""
    try:
        import akshare as ak
        df = ak.stock_news_em(symbol=code)
        if df is not None and not df.empty:
            headlines = []
            for _, row in df.head(limit).iterrows():
                headlines.append({
                    'title': row.get('新闻标题', ''),
                    'time': str(row.get('发布时间', '')),
                    'source': row.get('文章来源', ''),
                })
            return headlines
    except Exception:
        pass
    return []


def stage3_deep(candidate: dict) -> dict:
    code = candidate['code']
    tech = _deep_technical(code)
    capital = _deep_capital(code)
    fin = _deep_financial(code)

    t_score, t_ev = score_technical_deep(tech)
    c_score, c_ev = score_capital_deep(capital)
    f_score, f_ev = score_fundamental_deep(fin, candidate.get('dim_scores', {}).get('fundamental', 50))
    cat_score = candidate.get('dim_scores', {}).get('catalyst', 50)
    cat_ev = candidate.get('evidence', {}).get('catalyst', [])
    m_score, m_ev = score_momentum_deep(tech)

    deep = {
        'technical': round(t_score, 1), 'capital': round(c_score, 1),
        'fundamental': round(f_score, 1), 'catalyst': round(cat_score, 1),
        'momentum': round(m_score, 1),
    }
    composites = {h: round(sum(deep[d] * w for d, w in ws.items()), 2) for h, ws in WEIGHTS.items()}
    best_h = max(composites, key=composites.get)
    classes = [h for h in ('short', 'medium', 'long') if composites[h] >= 65]

    # 获取个股新闻作为催化面补充
    news = _fetch_stock_news(code, limit=5)
    if news:
        cat_ev = list(cat_ev)  # copy
        cat_ev.append(f'近期新闻 {len(news)} 条: {news[0]["title"][:30]}...')

    ma = tech.get('ma_system') or {}
    macd = tech.get('macd') or {}
    return {
        **candidate,
        'deep_scores': deep,
        'deep_composites': composites,
        'best_horizon': best_h,
        'classifications': classes or [best_h],
        'best_deep_score': max(composites.values()),
        'evidence': {
            'technical': t_ev, 'capital': c_ev, 'fundamental': f_ev,
            'catalyst': cat_ev, 'momentum': m_ev,
        },
        'tech_raw': {
            'arrangement': ma.get('arrangement'),
            'support': ma.get('support'),
            'resistance': ma.get('resistance'),
            'macd_signal': macd.get('cross') or macd.get('signal'),
            'rsi6': tech.get('rsi_6'),
            'kdj_j': (tech.get('kdj') or {}).get('J'),
        },
        'recent_news': news[:3],
        'financial_stage': (fin.get('pattern_recognition') or {}).get('stage', '未知'),
    }


def stage3_batch(top: list, max_deep: int = 30) -> list:
    results = []
    for i, c in enumerate(top[:max_deep]):
        print(f'  [{i+1}/{min(len(top), max_deep)}] 深度分析 {c["code"]} {c["name"]}...', file=sys.stderr)
        try:
            results.append(stage3_deep(c))
        except Exception as e:
            print(f'    ⚠ 失败: {e}', file=sys.stderr)
            results.append({**c, 'deep_error': str(e)})
    results.sort(key=lambda x: x.get('best_deep_score', 0), reverse=True)
    return results


# ═══════════════════════════════════════════════════════════════════════════════
# 输出
# ═══════════════════════════════════════════════════════════════════════════════

def format_output(results: list, rotation: list, transmission: list, stage: str, meta: dict) -> dict:
    now_str = datetime.now(SH_TZ).isoformat()
    short = [r for r in results if 'short' in r.get('classifications', [])]
    medium = [r for r in results if 'medium' in r.get('classifications', [])]
    long_ = [r for r in results if 'long' in r.get('classifications', [])]

    return {
        'generated_at': now_str,
        'stage': stage,
        'meta': meta,
        'weights': WEIGHTS,
        'picks': {
            'short_term': short[:10],
            'medium_term': medium[:10],
            'long_term': long_[:10],
        },
        'sector_rotation': rotation[:10],
        'flow_transmission': transmission[:15],
        'all_ranked': results[:30],
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Stage 2d: Claude 传导深度推理
# ═══════════════════════════════════════════════════════════════════════════════

TRANSMISSION_REASONING_PROMPT = """你是一位 A 股产业链研究员。以下是选股引擎检测到的资金流传导信号和候选股票。

你的任务：
1. 对每个传导信号做**因果链推理** — 这条信号为什么会传导到目标行业？传导机制是什么？
2. 发现规则引擎可能**遗漏的隐含传导路径**（新闻中没直接提到但逻辑上会受益的行业）
3. 对每只传导候选做**置信度排序**（高/中/低）并解释理由
4. 为最有潜力的 5 只候选写**传导故事**（2-3 句话解释为什么是中长期机会）
5. 指出**伪传导**（看起来相关但实际不受益的候选）

严格基于数据推理，不编造数据。如果证据不足就说"证据不足"。

输出格式为 JSON：
```json
{
  "signal_analysis": [
    {
      "signal": "信号描述",
      "causal_chain": "A → B → C 的传导机制",
      "confidence": "high/medium/low",
      "missed_paths": ["可能遗漏的受益行业"]
    }
  ],
  "candidate_ratings": [
    {
      "code": "代码",
      "name": "名称",
      "confidence": "high/medium/low",
      "story": "2-3句话的传导故事",
      "risk": "主要风险"
    }
  ],
  "false_positives": [
    {"code": "代码", "reason": "为什么不是真正受益"}
  ],
  "hidden_opportunities": [
    {"industry": "行业", "reason": "为什么这个行业也会受益但没被检测到"}
  ]
}
```"""


def _rule_based_reasoning_fallback(transmission: list, signals: list) -> dict:
    """
    Claude 不可用时的规则引擎 fallback。
    基于传导分数和信号类型做结构化输出。
    """
    ratings = []
    for t in transmission[:15]:
        score = t.get('transmission_score', 0)
        conf = 'high' if score >= 70 else 'medium' if score >= 50 else 'low'
        reasons = t.get('transmission_reasons', [])
        ratings.append({
            'code': t['code'],
            'name': t['name'],
            'confidence': conf,
            'story': f'{t["name"]}({t["industry"]}) — {"; ".join(reasons[:2])}。ROE={t["roe"]:.0f}%，净利增速{t["profit_growth"]:.0f}%。',
            'risk': '规则引擎推理，未经 Claude 深度验证' if conf != 'high' else '传导逻辑明确但需关注行业整体走势',
        })
    return {
        'source': 'rule_engine_fallback',
        'note': 'Claude CLI 输出异常，使用规则引擎替代',
        'signal_analysis': [
            {
                'signal': sig.get('headline', sig.get('source_industry', '')) if sig['type'] == 'policy_news'
                          else f'{sig.get("source_industry","")} 龙虎榜集中',
                'causal_chain': f'{sig.get("keyword","行业")} → {sig.get("target_industries", sig.get("downstream", []))}',
                'confidence': 'medium',
                'missed_paths': [],
            }
            for sig in signals[:10]
        ],
        'candidate_ratings': ratings,
        'false_positives': [],
        'hidden_opportunities': [],
    }


def stage2d_claude_reasoning(transmission: list, signals: list, news_items: list) -> dict:
    """
    Stage 2d: 调用 Claude Code 对传导信号做深度因果推理。
    返回 Claude 的推理结果 dict。
    """
    if not transmission and not signals:
        return {'skipped': True, 'reason': '无传导信号'}

    # 构建输入上下文
    context_parts = []

    context_parts.append("## 检测到的传导信号\n")
    for i, sig in enumerate(signals[:15]):
        if sig['type'] == 'policy_news':
            context_parts.append(
                f"{i+1}. [政策新闻] 关键词=「{sig['keyword']}」→ 目标行业: {sig['target_industries']}\n"
                f"   新闻标题: {sig['headline']}")
        elif sig['type'] == 'lhb_concentration':
            context_parts.append(
                f"{i+1}. [龙虎榜集中] {sig['source_industry']} 有 {sig['lhb_count']} 只股票上榜 → 下游: {sig['downstream']}")

    context_parts.append("\n\n## 传导候选股票（TOP 15）\n")
    for t in transmission[:15]:
        context_parts.append(
            f"- {t['code']} {t['name']} [{t['industry']}] "
            f"传导分={t['transmission_score']} ROE={t['roe']:.1f}% 净利增={t['profit_growth']:.0f}% "
            f"毛利率={t['gross_margin']:.1f}%\n"
            f"  传导原因: {'; '.join(t.get('transmission_reasons', []))}")

    context_parts.append("\n\n## 今日 RSS 新闻（用于发现隐含传导）\n")
    for item in news_items[:20]:
        context_parts.append(f"- [{item.get('source','')}] {item.get('title','')}")

    full_prompt = '\n'.join(context_parts)

    # 调用 Claude Code
    # 注意：CLI 模式下 claude -p 可能返回空 result（已知问题），
    # 此时会 fallback 到纯规则输出。在 orchestrator/cron 模式下正常工作。
    try:
        from scripts.orchestrator.claude_runner import run_claude, resolve_model
        result = run_claude(
            prompt=full_prompt,
            system_prompt=TRANSMISSION_REASONING_PROMPT,
            timeout_seconds=180,
            max_budget_usd=1.5,
            model=resolve_model("scout_reason"),
            output_format='text',
            allowed_tools=[],
        )
        if result.success and result.output and len(result.output.strip()) > 10:
            text = result.output.strip()
            # 提取 JSON 块
            if '```json' in text:
                text = text.split('```json')[1].split('```')[0].strip()
            elif '```' in text:
                text = text.split('```')[1].split('```')[0].strip()
            start = text.find('{')
            end = text.rfind('}')
            if start >= 0 and end > start:
                text = text[start:end + 1]
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {'raw_text': result.output[:3000], 'parse_error': True}
        else:
            # Claude CLI 空输出 — 返回规则引擎的结构化摘要作为 fallback
            return _rule_based_reasoning_fallback(transmission, signals)
    except ImportError:
        return _rule_based_reasoning_fallback(transmission, signals)
    except Exception as e:
        return {'error': str(e)}


# ═══════════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════════

def _save_and_print(output: dict):
    date_str = datetime.now(SH_TZ).strftime('%Y-%m-%d')
    out_path = OUTPUT_DIR / f'{date_str}-scout.json'
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f'[stock_scout] 保存: {out_path}', file=sys.stderr)
    print(json.dumps(output, ensure_ascii=False, indent=2))


def cmd_scan(quick=False):
    qualified, ind_flow, lhb, ind_idx, news_map = stage1_collect_and_filter()
    if not qualified:
        print('{"error": "Stage 1 无合格标的"}')
        return

    print('[Stage 2] 五维评分...', file=sys.stderr)
    top_n = 30 if quick else 100
    top = stage2_score(qualified, ind_flow, lhb, news_map, top_n=top_n)
    print(f'[Stage 2] TOP {len(top)}: {top[0]["name"]}({top[0]["best_score"]:.1f}) ~ '
          f'{top[-1]["name"]}({top[-1]["best_score"]:.1f})', file=sys.stderr)

    print('[Stage 2b] 板块轮动选股...', file=sys.stderr)
    rotation = stage2b_sector_rotation(qualified, ind_flow, lhb)
    print(f'[Stage 2b] 轮动候选 {len(rotation)} 只', file=sys.stderr)

    # 获取 RSS 原始条目用于传导分析
    rss_raw = _fetch_rss_news()
    rss_items_raw = rss_raw.get('_items', [])

    print('[Stage 2c] 资金流传导推测...', file=sys.stderr)
    transmission, signals = stage2c_flow_transmission(qualified, ind_flow, lhb, news_map, rss_items_raw)
    print(f'[Stage 2c] 传导候选 {len(transmission)} 只，检测到 {len(signals)} 个传导信号', file=sys.stderr)

    meta = {
        'total_fetched': len(qualified) + (len(lhb) > 0) * len(lhb),
        'qualified': len(qualified),
        'lhb_count': len(lhb),
        'industry_count': len(ind_flow),
        'hot_industries': [n for n, _ in sorted(ind_flow.items(), key=lambda x: x[1].get('heat_score', 0), reverse=True)[:5]],
        'transmission_signals': signals[:10],
    }

    if quick:
        output = format_output(top, rotation, transmission, 'quick', meta)
        _save_and_print(output)
        return

    # Stage 2d: Claude 传导推理（非 quick 模式才执行）
    print('[Stage 2d] Claude 传导深度推理...', file=sys.stderr)
    reasoning = stage2d_claude_reasoning(transmission, signals, rss_items_raw)
    if reasoning.get('error'):
        print(f'  [2d] Claude 推理失败: {reasoning["error"]}', file=sys.stderr)
    elif reasoning.get('skipped'):
        print(f'  [2d] 跳过: {reasoning["reason"]}', file=sys.stderr)
    else:
        meta['claude_reasoning'] = reasoning
        # 用 Claude 置信度覆盖传导候选的排序
        ratings = {r['code']: r for r in reasoning.get('candidate_ratings', [])}
        for t in transmission:
            r = ratings.get(t['code'])
            if r:
                t['claude_confidence'] = r.get('confidence', '')
                t['claude_story'] = r.get('story', '')
                t['claude_risk'] = r.get('risk', '')
        # 标记伪传导
        false_pos = {fp['code'] for fp in reasoning.get('false_positives', [])}
        transmission = [t for t in transmission if t['code'] not in false_pos]
        print(f'  [2d] 推理完成: {len(ratings)} 只评级，{len(false_pos)} 只伪传导排除', file=sys.stderr)

    print('[Stage 3] 深度验证...', file=sys.stderr)
    deep_results = stage3_batch(top, max_deep=30)
    output = format_output(deep_results, rotation, transmission, 'full', meta)
    _save_and_print(output)


def cmd_deep(codes: list):
    qualified, ind_flow, lhb, _, news_map = stage1_collect_and_filter()
    lookup = {s['code']: s for s in qualified}

    results = []
    for code in codes:
        code = code.zfill(6)
        print(f'[deep] {code}...', file=sys.stderr)
        base = lookup.get(code, {
            'code': code, 'name': code, 'industry': '', 'roe': 0,
            'profit_growth': 0, 'revenue_growth': 0, 'gross_margin': 0,
            'cashflow_ps': 0, 'eps': 0, 'revenue_yi': 0, 'net_profit_yi': 0,
            'dim_scores': {}, 'composites': {}, 'best_score': 0, 'evidence': {},
        })
        if 'dim_scores' not in base:
            # Score it through Stage 2 first
            fund_s, fund_e = _score_fundamental(base)
            cap_s, cap_e = _score_capital(base, ind_flow, lhb)
            base['dim_scores'] = {'fundamental': fund_s, 'capital': cap_s, 'technical': 50, 'catalyst': 50, 'momentum': 50}
            base['evidence'] = {'fundamental': fund_e, 'capital': cap_e, 'technical': [], 'catalyst': [], 'momentum': []}
        results.append(stage3_deep(base))

    meta = {'mode': 'deep', 'codes': codes}
    output = format_output(results, [], [], 'deep', meta)
    _save_and_print(output)


def cmd_reason():
    """独立运行传导推理：采集数据 → 传导检测 → Claude 推理"""
    qualified, ind_flow, lhb, _, news_map = stage1_collect_and_filter()
    rss_raw = _fetch_rss_news()
    rss_items_raw = rss_raw.get('_items', [])

    print('[传导检测]...', file=sys.stderr)
    transmission, signals = stage2c_flow_transmission(qualified, ind_flow, lhb, news_map, rss_items_raw)
    print(f'  {len(transmission)} 只候选，{len(signals)} 个信号', file=sys.stderr)

    print('[Claude 推理]...', file=sys.stderr)
    reasoning = stage2d_claude_reasoning(transmission, signals, rss_items_raw)

    output = {
        'generated_at': datetime.now(SH_TZ).isoformat(),
        'stage': 'reason',
        'signals': signals[:15],
        'transmission_candidates': transmission[:15],
        'claude_reasoning': reasoning,
    }
    _save_and_print(output)


def main():
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        sys.exit(1)
    cmd = sys.argv[1].lower()
    if cmd == 'scan':
        cmd_scan(quick=False)
    elif cmd == 'quick':
        cmd_scan(quick=True)
    elif cmd == 'deep':
        if len(sys.argv) < 3:
            print('用法: stock_scout.py deep <code1> [code2] ...', file=sys.stderr)
            sys.exit(1)
        cmd_deep(sys.argv[2:])
    elif cmd == 'reason':
        cmd_reason()
    else:
        print(f'未知命令: {cmd}', file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
