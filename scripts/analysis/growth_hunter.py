#!/usr/bin/env python3
"""
growth_hunter.py - 成长性财务向量分析

核心设计理念：不用硬门槛筛选，而是提取"财务变化向量"供模型做上下文分析。
脚本只负责：
  1. 拉取财务三表数据（资产负债/利润/现金流）
  2. 计算各指标的趋势向量（不是单点值）
  3. 输出结构化的"财务故事"，让模型能做具体分析

用法:
    python3 scripts/analysis/growth_hunter.py analyze <code>
    python3 scripts/analysis/growth_hunter.py batch <code1> <code2> ...
    python3 scripts/analysis/growth_hunter.py scan [--sector 半导体]
"""

import sys
import json
import os
import warnings
import time
from datetime import datetime, timedelta
from pathlib import Path

warnings.filterwarnings('ignore')

try:
    import akshare as ak
    import pandas as pd
    import numpy as np
except ImportError as e:
    print(json.dumps({"error": f"缺少依赖: {e}"}, ensure_ascii=False))
    sys.exit(1)

_HERE = Path(__file__).resolve().parents[2]
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from scripts.utils.common import safe_float as _safe_float, WORKSPACE_ROOT  # noqa: E402

WORKSPACE = str(WORKSPACE_ROOT)


# ─────────────────────────────────────────────
# 缓存读取（market_cache.py 兼容）
# ─────────────────────────────────────────────

def load_cache():
    """读最近5天内的最新缓存"""
    cache_dir = WORKSPACE_ROOT / 'data' / 'market_cache'
    if not cache_dir.exists():
        return None
    today = datetime.now()
    for i in range(5):
        date_str = (today - timedelta(days=i)).strftime('%Y-%m-%d')
        cache_file = cache_dir / f'{date_str}.json'
        if cache_file.exists():
            with open(cache_file, encoding='utf-8') as f:
                return json.load(f)
    return None


# ─────────────────────────────────────────────
# 模式库
# ─────────────────────────────────────────────
PATTERNS = {
    "毛利率持续提升": lambda v: len(v) >= 3 and all(v[i] < v[i + 1] for i in range(len(v) - 1)),
    "ROE底部回升": lambda v: len(v) >= 3 and v[-1] > v[-2] and v[-2] <= min(v),
    "现金流覆盖净利>1": lambda ratio: ratio >= 1.0,
    "增速放缓但毛利扩张": None,   # 组合规则，在代码里判断
    "资本支出加速": None,         # 组合规则
    "低机构覆盖": lambda n: n is not None and n < 10,
    "营收现金含量下降": None,      # 组合规则
}


# ─────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────

def is_trading_day():
    weekday = datetime.now().weekday()
    return weekday < 5  # 0=周一 4=周五


def safe_float(v, default=None):
    """Parse float, handling strings like '47.14亿', '19.91%', '1.40'"""
    return _safe_float(v, default=default, units=True)


def calc_cagr(start, end, n_years):
    """CAGR = (end/start)^(1/(n-1)) - 1"""
    try:
        if start is None or end is None or n_years <= 0:
            return None
        start, end = float(start), float(end)
        if start <= 0 or end <= 0:
            return None
        return round(((end / start) ** (1.0 / n_years) - 1) * 100, 2)
    except Exception:
        return None


def linear_trend(values):
    """用线性回归斜率判断趋势"""
    try:
        v = [x for x in values if x is not None]
        if len(v) < 2:
            return "数据不足"
        x = np.arange(len(v), dtype=float)
        slope = np.polyfit(x, v, 1)[0]
        std = np.std(v)
        if std < 1e-9:
            return "持平"
        ratio = slope / (std + 1e-9)
        if ratio > 0.3:
            return "上升"
        elif ratio < -0.3:
            return "下降"
        else:
            return "波动"
    except Exception:
        return "未知"


def classify_trend_verbose(values, label=""):
    """返回更丰富的趋势描述"""
    v = [x for x in values if x is not None]
    if len(v) < 2:
        return "数据不足"
    trend = linear_trend(v)
    # 检测加速/减速
    if len(v) >= 3:
        growth_rates = [(v[i + 1] - v[i]) / abs(v[i]) * 100 if v[i] != 0 else 0
                        for i in range(len(v) - 1)]
        if trend == "上升":
            if growth_rates[-1] < growth_rates[0]:
                return "稳定增长"
            else:
                return "加速增长"
        elif trend == "下降":
            return "持续下滑"
    return trend


def get_revenue_growth_rates(revenues):
    """计算各年营收增速（%）"""
    rates = []
    for i in range(1, len(revenues)):
        if revenues[i - 1] and revenues[i - 1] != 0:
            rates.append(round((revenues[i] - revenues[i - 1]) / abs(revenues[i - 1]) * 100, 1))
        else:
            rates.append(None)
    return rates


def detect_v_pattern(values):
    """检测V型或倒V型"""
    v = [x for x in values if x is not None]
    if len(v) < 3:
        return None
    mid = len(v) // 2
    first_half = v[:mid + 1]
    second_half = v[mid:]
    # V型：先降后升
    if min(first_half) == min(v) or (v[-1] > v[len(v) // 2] and v[0] > v[len(v) // 2]):
        if v[-1] > v[-2] and v[-2] <= min(v):
            return "V型"
    return None


# ─────────────────────────────────────────────
# 数据获取
# ─────────────────────────────────────────────

def fetch_financial_abstract_ths(code, indicator='按年度', timeout=15):
    """同花顺财务摘要"""
    try:
        df = ak.stock_financial_abstract_ths(symbol=code, indicator=indicator)
        return df
    except Exception as e:
        return None


def fetch_profit_sheet_em(code, timeout=15):
    """东财利润表（按年度）"""
    try:
        df = ak.stock_profit_sheet_by_yearly_em(symbol=code)
        return df
    except Exception as e:
        return None


def fetch_balance_sheet_em(code, timeout=15):
    """东财资产负债表（按年度）"""
    try:
        df = ak.stock_balance_sheet_by_yearly_em(symbol=code)
        return df
    except Exception as e:
        return None


def fetch_cashflow_sheet_em(code, timeout=15):
    """东财现金流量表（按年度）"""
    try:
        df = ak.stock_cash_flow_sheet_by_yearly_em(symbol=code)
        return df
    except Exception as e:
        return None


def fetch_profit_forecast_ths(code, timeout=15):
    """同花顺盈利预测"""
    try:
        df = ak.stock_profit_forecast_ths(symbol=code)
        return df
    except Exception as e:
        return None


def get_stock_name(code):
    """获取股票名称"""
    try:
        # 尝试从实时行情获取
        df = ak.stock_zh_a_spot_em()
        row = df[df['代码'] == code]
        if not row.empty:
            return row.iloc[0]['名称']
    except Exception:
        pass
    return code


# ─────────────────────────────────────────────
# 数据解析
# ─────────────────────────────────────────────

def parse_ths_abstract(df_annual, df_quarterly=None):
    """解析同花顺财务摘要，返回结构化数据"""
    result = {
        "source": "同花顺财务摘要",
        "years": [],
        "revenue": [],          # 营业总收入（亿元）
        "net_profit": [],       # 净利润（亿元）
        "revenue_growth": [],   # 营收同比增速
        "profit_growth": [],    # 净利润同比增速
        "eps_cashflow": [],     # 每股经营现金流
        "gross_margin": [],     # 销售毛利率
        "roe": [],              # 净资产收益率
        "debt_ratio": [],       # 资产负债率
        "quarterly_revenue": [],
        "quarterly_profit": [],
    }

    if df_annual is None or df_annual.empty:
        return result

    # 打印列名帮助调试
    cols = df_annual.columns.tolist()

    # 标准化列名映射
    col_map = {}
    for c in cols:
        lc = str(c).lower()
        if '报告期' in c or '年份' in c:
            col_map['period'] = c
        elif c == '净利润':
            col_map['net_profit'] = c
        elif '净利润同比' in c or (c == '净利润同比增长率'):
            col_map['profit_growth'] = c
        elif c == '营业总收入':
            col_map['revenue'] = c
        elif '营业总收入同比' in c:
            col_map['revenue_growth'] = c
        elif '每股经营现金流' in c:
            col_map['eps_cashflow'] = c
        elif '销售毛利率' in c or c == '毛利率':
            col_map['gross_margin'] = c
        elif c == '净资产收益率':
            col_map['roe'] = c
        elif '资产负债率' in c:
            col_map['debt_ratio'] = c

    if 'period' not in col_map:
        # 尝试第一列
        col_map['period'] = cols[0]

    # 取近4~6年数据，按时间升序
    try:
        df = df_annual.copy()
        df['_period_str'] = df[col_map['period']].astype(str)
        # 过滤出年报（含年度字样或者4位年份）
        mask = df['_period_str'].str.contains(r'年报|年度|\d{4}', regex=True, na=False)
        df = df[mask].copy()
        # 提取年份数字排序
        df['_year'] = df['_period_str'].str.extract(r'(\d{4})')[0].astype(float)
        df = df.dropna(subset=['_year'])
        df = df.sort_values('_year', ascending=True)
        df = df.tail(6)  # 最近6年

        for _, row in df.iterrows():
            year = int(row['_year'])
            result['years'].append(year)

            for key, col in col_map.items():
                if key == 'period':
                    continue
                val = safe_float(row.get(col))
                # safe_float 对 "47.14亿" 返回 4.714e9，对 "19.91%" 返回 19.91
                # 营收/净利润统一换算成亿元
                if key in ('revenue', 'net_profit') and val is not None:
                    if abs(val) >= 1e8:          # 原始单位是元，转亿
                        val = round(val / 1e8, 4)
                    elif abs(val) >= 1e4:        # 原始单位是万元，转亿
                        val = round(val / 1e4, 4)
                    # else: 已经是亿（如同花顺部分接口直接返回亿）
                result[key].append(val)
    except Exception as e:
        pass

    # 季度数据
    if df_quarterly is not None and not df_quarterly.empty:
        try:
            dq = df_quarterly.copy()
            dq['_period_str'] = dq[col_map['period']].astype(str)
            dq['_year'] = dq['_period_str'].str.extract(r'(\d{4})')[0].astype(float)
            dq = dq.dropna(subset=['_year'])
            dq = dq.sort_values('_year', ascending=True)
            dq = dq.tail(8)
            for _, row in dq.iterrows():
                rv = safe_float(row.get(col_map.get('revenue')))
                np_ = safe_float(row.get(col_map.get('net_profit')))
                if rv is not None and abs(rv) > 1e8:
                    rv = round(rv / 1e8, 4)
                if np_ is not None and abs(np_) > 1e8:
                    np_ = round(np_ / 1e8, 4)
                result['quarterly_revenue'].append(rv)
                result['quarterly_profit'].append(np_)
        except Exception:
            pass

    return result


def parse_em_profit(df):
    """解析东财利润表"""
    result = {"years": [], "revenue": [], "gross_profit": [], "net_profit": [],
              "operating_profit": [], "source": "东财利润表"}
    if df is None or df.empty:
        return result
    try:
        cols = df.columns.tolist()
        # 东财利润表通常行是科目，列是日期
        # 转置：行是日期，列是科目
        df2 = df.set_index(cols[0]).T
        df2.index.name = 'period'
        df2 = df2.reset_index()
        df2['_year'] = df2['period'].astype(str).str.extract(r'(\d{4})')[0].astype(float)
        df2 = df2.dropna(subset=['_year'])
        # 只取年报
        mask = df2['period'].astype(str).str.contains(r'年报|1231|12-31|年度', regex=True, na=False)
        df2 = df2[mask]
        df2 = df2.sort_values('_year', ascending=True).tail(6)

        subcols = df2.columns.tolist()

        def find_col(keywords, exclude=None):
            for c in subcols:
                cs = str(c)
                if any(k in cs for k in keywords):
                    if exclude and any(e in cs for e in exclude):
                        continue
                    return c
            return None

        rev_col = find_col(['营业总收入', '营业收入'], ['同比', '增速'])
        gp_col = find_col(['毛利', '营业毛利'])
        np_col = find_col(['净利润', '归母净利润', '归属于母公司'], ['同比', '增速', '少数'])
        op_col = find_col(['营业利润'], ['同比'])

        for _, row in df2.iterrows():
            year = int(row['_year'])
            result['years'].append(year)

            def get_val(col):
                if col is None:
                    return None
                v = safe_float(row.get(col))
                if v is not None and abs(v) > 1e8:
                    v = round(v / 1e8, 4)
                return v

            result['revenue'].append(get_val(rev_col))
            result['gross_profit'].append(get_val(gp_col))
            result['net_profit'].append(get_val(np_col))
            result['operating_profit'].append(get_val(op_col))
    except Exception as e:
        pass
    return result


def parse_em_balance(df):
    """解析东财资产负债表"""
    result = {"years": [], "total_assets": [], "total_liabilities": [],
              "equity": [], "debt_ratio": [], "source": "东财资产负债表"}
    if df is None or df.empty:
        return result
    try:
        cols = df.columns.tolist()
        df2 = df.set_index(cols[0]).T
        df2.index.name = 'period'
        df2 = df2.reset_index()
        df2['_year'] = df2['period'].astype(str).str.extract(r'(\d{4})')[0].astype(float)
        df2 = df2.dropna(subset=['_year'])
        mask = df2['period'].astype(str).str.contains(r'年报|1231|12-31|年度', regex=True, na=False)
        df2 = df2[mask]
        df2 = df2.sort_values('_year', ascending=True).tail(6)

        subcols = df2.columns.tolist()

        def find_col(keywords, exclude=None):
            for c in subcols:
                cs = str(c)
                if any(k in cs for k in keywords):
                    if exclude and any(e in cs for e in exclude):
                        continue
                    return c
            return None

        ta_col = find_col(['资产合计', '总资产'])
        tl_col = find_col(['负债合计', '总负债'])
        eq_col = find_col(['所有者权益', '股东权益合计', '归属于母公司所有者权益'])

        for _, row in df2.iterrows():
            year = int(row['_year'])
            result['years'].append(year)

            def get_val(col):
                if col is None:
                    return None
                v = safe_float(row.get(col))
                if v is not None and abs(v) > 1e8:
                    v = round(v / 1e8, 4)
                return v

            ta = get_val(ta_col)
            tl = get_val(tl_col)
            eq = get_val(eq_col)
            result['total_assets'].append(ta)
            result['total_liabilities'].append(tl)
            result['equity'].append(eq)
            # 计算负债率
            if ta and tl and ta != 0:
                result['debt_ratio'].append(round(tl / ta * 100, 2))
            else:
                result['debt_ratio'].append(None)
    except Exception as e:
        pass
    return result


def parse_em_cashflow(df):
    """解析东财现金流量表"""
    result = {"years": [], "operating_cf": [], "investing_cf": [],
              "capex": [], "source": "东财现金流量表"}
    if df is None or df.empty:
        return result
    try:
        cols = df.columns.tolist()
        df2 = df.set_index(cols[0]).T
        df2.index.name = 'period'
        df2 = df2.reset_index()
        df2['_year'] = df2['period'].astype(str).str.extract(r'(\d{4})')[0].astype(float)
        df2 = df2.dropna(subset=['_year'])
        mask = df2['period'].astype(str).str.contains(r'年报|1231|12-31|年度', regex=True, na=False)
        df2 = df2[mask]
        df2 = df2.sort_values('_year', ascending=True).tail(6)

        subcols = df2.columns.tolist()

        def find_col(keywords, exclude=None):
            for c in subcols:
                cs = str(c)
                if any(k in cs for k in keywords):
                    if exclude and any(e in cs for e in exclude):
                        continue
                    return c
            return None

        ocf_col = find_col(['经营活动产生的现金流量净额', '经营活动现金流量净额'])
        icf_col = find_col(['投资活动产生的现金流量净额', '投资活动现金流量净额'])
        capex_col = find_col(['购建固定资产', '资本支出', '购置固定资产'])

        for _, row in df2.iterrows():
            year = int(row['_year'])
            result['years'].append(year)

            def get_val(col):
                if col is None:
                    return None
                v = safe_float(row.get(col))
                if v is not None and abs(v) > 1e8:
                    v = round(v / 1e8, 4)
                return v

            result['operating_cf'].append(get_val(ocf_col))
            result['investing_cf'].append(get_val(icf_col))
            result['capex'].append(get_val(capex_col))
    except Exception as e:
        pass
    return result


# ─────────────────────────────────────────────
# 核心分析
# ─────────────────────────────────────────────

def analyze_stock(code):
    """分析单只股票，返回财务向量结构"""
    result = {
        "code": code,
        "name": code,
        "data_period": "",
        "data_quality": "不足",
        "sources_used": [],
        "growth_vectors": {},
        "pattern_recognition": {},
        "analyst_consensus": {},
        "raw_summary": {},
    }

    # 1. 获取股票名称
    try:
        name = get_stock_name(code)
        result['name'] = name
    except Exception:
        pass

    # 2. 先尝试同花顺财务摘要（主要来源）
    print(f"  [1/4] 获取同花顺财务摘要...", file=sys.stderr)
    df_annual = fetch_financial_abstract_ths(code, '按年度')
    df_quarterly = fetch_financial_abstract_ths(code, '按报告期')

    ths_data = parse_ths_abstract(df_annual, df_quarterly)
    if df_annual is not None and not df_annual.empty:
        result['sources_used'].append('同花顺财务摘要(年度)')

    # 3. 尝试东财三表
    print(f"  [2/4] 获取东财三表（可能较慢）...", file=sys.stderr)
    df_profit = fetch_profit_sheet_em(code)
    df_balance = fetch_balance_sheet_em(code)
    df_cashflow = fetch_cashflow_sheet_em(code)

    em_profit = parse_em_profit(df_profit)
    em_balance = parse_em_balance(df_balance)
    em_cashflow = parse_em_cashflow(df_cashflow)

    if df_profit is not None and not df_profit.empty:
        result['sources_used'].append('东财利润表')
    if df_balance is not None and not df_balance.empty:
        result['sources_used'].append('东财资产负债表')
    if df_cashflow is not None and not df_cashflow.empty:
        result['sources_used'].append('东财现金流量表')

    # 4. 盈利预测
    print(f"  [3/4] 获取盈利预测...", file=sys.stderr)
    df_forecast = fetch_profit_forecast_ths(code)
    if df_forecast is not None and not df_forecast.empty:
        result['sources_used'].append('同花顺盈利预测')

    print(f"  [4/4] 计算财务向量...", file=sys.stderr)

    # ── 决定用哪个数据源的营收/净利润 ──
    # 优先东财（更详细），降级到同花顺摘要
    use_em_profit = len(em_profit['years']) >= 3
    use_ths = len(ths_data['years']) >= 3

    years = em_profit['years'] if use_em_profit else ths_data['years']
    revenue = em_profit['revenue'] if use_em_profit else ths_data['revenue']
    net_profit = em_profit['net_profit'] if use_em_profit else ths_data['net_profit']

    # ── 毛利率：需要东财利润表 ──
    gross_margin = ths_data['gross_margin'] if ths_data['gross_margin'] else []
    if not gross_margin and use_em_profit:
        # 计算毛利率 = 毛利/营收
        gross_margin = []
        for gp, rv in zip(em_profit['gross_profit'], em_profit['revenue']):
            if gp is not None and rv is not None and rv != 0:
                gross_margin.append(round(gp / rv * 100, 2))
            else:
                gross_margin.append(None)

    # ── ROE / 负债率：优先同花顺，降级东财 ──
    roe_vals = ths_data['roe'] if ths_data['roe'] else []
    debt_ratio_vals = (ths_data['debt_ratio'] if ths_data['debt_ratio']
                       else em_balance['debt_ratio'])

    # ── 现金流 ──
    operating_cf = em_cashflow['operating_cf']
    eps_cf = ths_data['eps_cashflow'] if ths_data['eps_cashflow'] else []
    capex = em_cashflow['capex']

    # 对齐年份
    if years:
        result['data_period'] = f"{min(years)}-{max(years)}"

    n_years = len([y for y in years if y is not None])
    if n_years >= 3:
        result['data_quality'] = "完整"
    elif n_years >= 2:
        result['data_quality'] = "部分"
    else:
        result['data_quality'] = "不足"

    # ── 构建 growth_vectors ──
    gv = {}

    # 营收向量
    if len([v for v in revenue if v is not None]) >= 2:
        rev_valid = [v for v in revenue if v is not None]
        yr_valid = [years[i] for i, v in enumerate(revenue) if v is not None]
        growth_rates = get_revenue_growth_rates(rev_valid)
        cagr = calc_cagr(rev_valid[0], rev_valid[-1], len(rev_valid) - 1) if len(rev_valid) >= 2 else None
        trend_label = classify_trend_verbose(rev_valid, "营收")
        accel = ""
        if len(growth_rates) >= 2:
            first_rate = next((r for r in growth_rates if r is not None), None)
            last_rate = next((r for r in reversed(growth_rates) if r is not None), None)
            if first_rate is not None and last_rate is not None:
                if last_rate < first_rate:
                    accel = f"放缓（增速从{first_rate:.0f}%→{last_rate:.0f}%）"
                elif last_rate > first_rate:
                    accel = f"加速（增速从{first_rate:.0f}%→{last_rate:.0f}%）"
                else:
                    accel = "增速稳定"
        signal = ""
        if trend_label == "稳定增长" and accel and "放缓" in accel:
            signal = "⚠️ 增速下台阶，需关注新业务能否接力"
        elif trend_label in ("加速增长",):
            signal = "✅ 营收加速增长，成长动能强劲"
        elif trend_label == "下降":
            signal = "❌ 营收下滑，需判断是否触底"
        else:
            signal = "🟡 营收增长但需关注增速变化"

        gv['revenue'] = {
            "values": rev_valid,
            "years": yr_valid,
            "cagr_3yr": cagr,
            "trend": trend_label,
            "acceleration": accel,
            "signal": signal
        }

    # 毛利率向量
    gm_valid = [v for v in gross_margin if v is not None]
    if len(gm_valid) >= 2:
        trend = linear_trend(gm_valid)
        delta = round(gm_valid[-1] - gm_valid[0], 2) if len(gm_valid) >= 2 else None
        if PATTERNS["毛利率持续提升"](gm_valid):
            signal = "✅ 产品结构升级信号，定价权增强"
        elif trend == "下降":
            signal = "❌ 毛利率持续下滑，盈利能力承压"
        else:
            signal = "🟡 毛利率波动，注意趋势方向"
        gv['gross_margin'] = {
            "values": gm_valid,
            "trend": trend,
            "delta_3yr": f"{'+' if delta and delta > 0 else ''}{delta}pct" if delta is not None else None,
            "signal": signal
        }

    # ROE向量
    roe_valid = [v for v in roe_vals if v is not None]
    if len(roe_valid) >= 2:
        trend = linear_trend(roe_valid)
        pattern = detect_v_pattern(roe_valid)
        if PATTERNS["ROE底部回升"](roe_valid):
            signal = "✅ ROE触底回升，盈利能力恢复"
        elif trend == "上升":
            signal = "✅ ROE持续提升，资本效率增强"
        elif trend == "下降":
            signal = "⚠️ ROE下滑，注意是否结构性问题"
        else:
            signal = "🟡 ROE波动，需结合杜邦分析"
        gv['roe'] = {
            "values": roe_valid,
            "trend": trend,
            "pattern": pattern,
            "signal": signal
        }

    # 经营现金流/净利润比率
    if operating_cf and net_profit and len(operating_cf) >= 2:
        cf_np_ratio = []
        cf_years = em_cashflow['years'] if em_cashflow['years'] else years
        for i, (cf, np_) in enumerate(zip(operating_cf, net_profit)):
            if cf is not None and np_ is not None and np_ != 0:
                cf_np_ratio.append(round(cf / np_, 2))
            else:
                cf_np_ratio.append(None)
        valid_ratios = [v for v in cf_np_ratio if v is not None]
        if valid_ratios:
            latest_ratio = valid_ratios[-1]
            trend = linear_trend(valid_ratios)
            if latest_ratio >= 1.0:
                signal = "✅ 现金流覆盖净利>1，盈利质量高，利润是真实的"
            elif latest_ratio >= 0.7:
                signal = "🟡 现金流基本覆盖净利，盈利质量尚可"
            else:
                signal = "❌ 现金流覆盖不足，利润含金量低"
            gv['operating_cashflow_ratio'] = {
                "values": valid_ratios,
                "description": "经营现金流/净利润",
                "latest": latest_ratio,
                "trend": trend,
                "signal": signal
            }

    # Capex/营收比率
    if capex and revenue and len(capex) >= 2:
        capex_rev = []
        for cx, rv in zip(capex, revenue):
            if cx is not None and rv is not None and rv != 0:
                capex_rev.append(round(abs(cx) / abs(rv) * 100, 2))
            else:
                capex_rev.append(None)
        valid_cr = [v for v in capex_rev if v is not None]
        if valid_cr:
            capex_trend = linear_trend(valid_cr)
            if capex_trend == "上升":
                signal = "✅ 持续重投入，扩产意愿强"
            elif capex_trend == "下降":
                signal = "⚠️ 资本支出减少，扩张放缓"
            else:
                signal = "🟡 资本支出稳定"
            gv['capex_vs_revenue'] = {
                "values": valid_cr,
                "description": "资本支出/营收(%)",
                "trend": capex_trend,
                "signal": signal
            }

    # 负债结构
    dr_valid = [v for v in debt_ratio_vals if v is not None]
    if len(dr_valid) >= 2:
        trend = linear_trend(dr_valid)
        latest_dr = dr_valid[-1]
        if latest_dr > 70:
            signal = "❌ 负债率偏高（>70%），财务风险需关注"
        elif latest_dr > 60:
            signal = "⚠️ 负债率较高，需关注有息负债结构"
        else:
            signal = "✅ 负债率合理"
        gv['debt_structure'] = {
            "total_debt_ratio": dr_valid,
            "trend": trend,
            "latest": latest_dr,
            "signal": signal
        }

    result['growth_vectors'] = gv

    # ── 模式识别 ──
    matched_patterns = []

    gm_vals = gv.get('gross_margin', {}).get('values', [])
    if gm_vals and PATTERNS["毛利率持续提升"](gm_vals):
        matched_patterns.append("毛利率持续提升")

    roe_vals2 = gv.get('roe', {}).get('values', [])
    if roe_vals2 and PATTERNS["ROE底部回升"](roe_vals2):
        matched_patterns.append("ROE底部回升")

    latest_cf_ratio = gv.get('operating_cashflow_ratio', {}).get('latest')
    if latest_cf_ratio is not None and PATTERNS["现金流覆盖净利>1"](latest_cf_ratio):
        matched_patterns.append("现金流覆盖净利>1")

    # 增速放缓但毛利扩张
    if gv.get('revenue', {}).get('acceleration', '') and "放缓" in gv.get('revenue', {}).get('acceleration', ''):
        if gv.get('gross_margin', {}).get('trend') in ("上升",):
            matched_patterns.append("增速放缓但毛利扩张（量换质信号）")

    # 资本支出加速
    if gv.get('capex_vs_revenue', {}).get('trend') == "上升":
        matched_patterns.append("资本支出加速")

    # 确定成长阶段
    stage = "成长阶段未明"
    if gv.get('revenue', {}).get('trend') in ("稳定增长", "加速增长"):
        if "毛利率持续提升" in matched_patterns:
            stage = "成长中期（量质双升）"
        else:
            stage = "成长扩张期"
    elif gv.get('revenue', {}).get('trend') == "下降":
        stage = "成熟/收缩期"
    elif "ROE底部回升" in matched_patterns:
        stage = "复苏反转期"

    # 关注指标（基于已有向量提取）
    watch_metrics = []
    if gv.get('revenue'):
        next_yr = max(years) + 1 if years else ""
        watch_metrics.append(f"Q1-{next_yr}营收增速")
    if "毛利率持续提升" in matched_patterns:
        watch_metrics.append("毛利率是否继续提升")
    if gv.get('operating_cashflow_ratio', {}).get('trend') in ("下降", "波动"):
        watch_metrics.append("经营现金流质量")

    result['pattern_recognition'] = {
        "stage": stage,
        "matched_patterns": matched_patterns,
        "pattern_count": len(matched_patterns),
        "watch_metrics": watch_metrics,
        "note": "模式识别仅供参考，结论由模型综合判断"
    }

    # ── 分析师一致预期 ──
    analyst = {}
    if df_forecast is not None and not df_forecast.empty:
        try:
            fcols = df_forecast.columns.tolist()
            # 尝试提取覆盖家数、目标价、评级
            for c in fcols:
                if '评级' in c or 'rating' in c.lower():
                    analyst['rating'] = str(df_forecast[c].iloc[0]) if not df_forecast.empty else None
                if '家数' in c or '覆盖' in c or '机构' in c:
                    analyst['coverage'] = safe_float(df_forecast[c].iloc[0])
                if 'eps' in c.lower() or '每股收益' in c:
                    analyst['next_year_eps_estimate'] = safe_float(df_forecast[c].iloc[0])
                if '目标价' in c:
                    analyst['target_price_avg'] = safe_float(df_forecast[c].iloc[0])
        except Exception:
            pass
    result['analyst_consensus'] = analyst

    # ── 原始摘要 ──
    raw = {}
    if years:
        raw['最新年报期'] = f"{max(years)}年报"
    if revenue and net_profit:
        rev_latest = next((v for v in reversed(revenue) if v is not None), None)
        np_latest = next((v for v in reversed(net_profit) if v is not None), None)
        if rev_latest is not None:
            raw['营收亿元'] = rev_latest
        if np_latest is not None:
            raw['净利润亿元'] = np_latest
    if roe_vals and roe_vals[-1] is not None:
        raw['ROE'] = roe_vals[-1]
    if gm_vals:
        v = next((v for v in reversed(gm_vals) if v is not None), None)
        if v:
            raw['毛利率'] = v
    if dr_valid:
        raw['资产负债率'] = dr_valid[-1]
    if eps_cf:
        v = next((v for v in reversed(eps_cf) if v is not None), None)
        if v:
            raw['每股现金流'] = v

    result['raw_summary'] = raw

    return result


# ─────────────────────────────────────────────
# 命令处理
# ─────────────────────────────────────────────

def cmd_analyze(code):
    """分析单只股票"""
    # 规范化 code（去掉前缀）
    code = code.replace('SZ', '').replace('SH', '').replace('sz', '').replace('sh', '').strip()

    print(f"开始分析 {code}...", file=sys.stderr)
    t0 = time.time()
    data = analyze_stock(code)
    elapsed = round(time.time() - t0, 1)
    data['elapsed_seconds'] = elapsed

    print(json.dumps(data, ensure_ascii=False, indent=2))

    # 打印确认行
    has_revenue = 'revenue' in data.get('growth_vectors', {})
    has_gm = 'gross_margin' in data.get('growth_vectors', {})
    has_stage = bool(data.get('pattern_recognition', {}).get('stage'))
    has_raw = bool(data.get('raw_summary'))

    status = []
    if has_revenue:
        status.append("✅ growth_vectors.revenue")
    else:
        status.append("❌ growth_vectors.revenue")
    if has_gm:
        status.append("✅ growth_vectors.gross_margin")
    else:
        status.append("❌ growth_vectors.gross_margin（数据不足）")
    if has_stage:
        status.append("✅ pattern_recognition.stage")
    else:
        status.append("❌ pattern_recognition.stage")
    if has_raw:
        status.append("✅ raw_summary")
    else:
        status.append("❌ raw_summary")

    print(f"\n{'='*50}", file=sys.stderr)
    print(f"字段验证：", file=sys.stderr)
    for s in status:
        print(f"  {s}", file=sys.stderr)
    print(f"\n可用接口: {data.get('sources_used', [])}", file=sys.stderr)
    print(f"耗时: {elapsed}s", file=sys.stderr)

    return data


def cmd_batch(codes):
    """批量分析"""
    results = []
    for code in codes:
        code = code.strip()
        if not code:
            continue
        print(f"\n{'='*50}", file=sys.stderr)
        print(f"分析: {code}", file=sys.stderr)
        try:
            data = analyze_stock(code.replace('SZ', '').replace('SH', '').strip())
            results.append(data)
        except Exception as e:
            results.append({"code": code, "error": str(e)})

    print(json.dumps(results, ensure_ascii=False, indent=2))
    print(f"\n批量分析完成，共 {len(results)} 只", file=sys.stderr)


def cmd_scan(sector=None):
    """全市场扫描：用缓存 growth_comparison / valuation_comparison 初筛，候选<50只再调 analyze_stock()"""
    from pathlib import Path as _Path

    CACHE_DIR = WORKSPACE_ROOT / 'data' / 'market_cache'
    is_weekday = datetime.now().weekday() < 5

    # 1. 读缓存（最近5天）
    cache_data = None
    today = datetime.now()
    for i in range(5):
        date_str = (today - timedelta(days=i)).strftime('%Y-%m-%d')
        f = CACHE_DIR / f'{date_str}-market.json'
        if f.exists():
            with open(f, encoding='utf-8') as fh:
                cache_data = json.load(fh)
            print(f"[使用缓存] 数据日期: {cache_data['date']}")
            break
    # 也兼容旧格式 {date}.json
    if not cache_data:
        cache_data = load_cache()
        if cache_data:
            print(f"[使用缓存] 数据日期: {cache_data.get('date','未知')}")

    if not cache_data:
        if not is_weekday:
            print("[无缓存] 周末且无缓存，请在工作日运行 market_cache.py save_market")
            result = {"status": "no_cache"}
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return
        # 工作日且无缓存：实时采集基础数据
        print("[实时获取] 工作日，采集基础数据...")
        try:
            df_growth = ak.stock_zh_growth_comparison_em(symbol="沪深京A股")
            df_val = ak.stock_zh_valuation_comparison_em(symbol="沪深京A股")
            growth_records = df_growth.to_dict('records')
            val_records = df_val.to_dict('records')
        except Exception as e:
            print(f"实时采集失败: {e}")
            result = {"status": "error", "msg": str(e)}
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return
    else:
        growth_records = cache_data.get('growth_comparison', [])
        val_records = cache_data.get('valuation_comparison', [])

    # 2. 初筛：从 growth_comparison 找净利增速>20% 的
    candidates = {}
    for row in (growth_records or []):
        code = str(row.get('代码', '') or row.get('股票代码', ''))
        if not code or code.startswith('4') or code.startswith('8'):
            continue
        name = row.get('名称', '') or row.get('股票简称', '')
        if 'ST' in str(name):
            continue
        growth = None
        for key in ['净利润增速', '净利润同比增长率', '净利润增长率', '归母净利润同比增速']:
            v = row.get(key)
            if v is not None:
                try:
                    growth = float(str(v).replace('%', '').replace(',', ''))
                    break
                except Exception:
                    pass
        if growth is not None and growth > 20:
            score = min(growth / 10, 8)
            if code not in candidates:
                candidates[code] = {'code': code, 'name': name, 'score': score, 'reasons': []}
            candidates[code]['reasons'].append(f"净利增速{growth:.1f}%")
            candidates[code]['score'] = max(candidates[code]['score'], score)

    # 3. 从 valuation_comparison 补充低估值候选
    for row in (val_records or []):
        code = str(row.get('代码', '') or row.get('股票代码', ''))
        if not code:
            continue
        name = row.get('名称', '') or row.get('股票简称', '')
        if 'ST' in str(name):
            continue
        pe = None
        pb = None
        for key in ['市盈率', '市盈率(TTM)', 'PE(TTM)']:
            try:
                pe = float(str(row.get(key, '')).replace(',', ''))
                break
            except Exception:
                pass
        for key in ['市净率', 'PB']:
            try:
                pb = float(str(row.get(key, '')).replace(',', ''))
                break
            except Exception:
                pass
        if pe and pb and 0 < pe < 25 and 0 < pb < 2.5:
            if code not in candidates:
                candidates[code] = {'code': code, 'name': name, 'score': 0, 'reasons': []}
            candidates[code]['reasons'].append(f"低估值PE{pe:.1f}/PB{pb:.1f}")
            candidates[code]['score'] += 2

    # 4. 机构调研加分
    surveyed = {
        str(r.get('股票代码', '') or r.get('代码', '')): True
        for r in (cache_data.get('institution_survey', []) if cache_data else [])
    }
    for code in surveyed:
        if code in candidates:
            candidates[code]['score'] += 3
            candidates[code]['reasons'].append("近期被机构调研")

    # 5. 排序取前50
    sorted_c = sorted(candidates.values(), key=lambda x: x['score'], reverse=True)[:50]
    if sector:
        sorted_c = [c for c in sorted_c if sector in c.get('name', '')][:30]

    print(f"\n✅ 初筛完成，候选: {len(sorted_c)} 只（从{len(candidates)}只中选出）")
    print("前10只：")
    for c in sorted_c[:10]:
        print(f"  {c['code']} {c['name']} 得分:{c['score']:.1f} {', '.join(c['reasons'])}")

    if not sorted_c:
        result = {"status": "no_candidates", "note": "初筛未发现候选股，请检查缓存数据字段"}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    # 6. 对候选逐只调 analyze_stock（<50只，约50秒）
    print(f"\n开始对 {len(sorted_c)} 只候选做财务向量分析...")
    results = []
    for i, c in enumerate(sorted_c):
        print(f"[{i+1}/{len(sorted_c)}] {c['code']} {c['name']}")
        try:
            result = analyze_stock(c['code'])
            result['scan_score'] = c['score']
            result['scan_reasons'] = c['reasons']
            results.append(result)
        except Exception as e:
            print(f"  analyze失败: {e}")
        time.sleep(0.2)

    # 7. 按模式匹配数排序
    results.sort(
        key=lambda x: len(x.get('pattern_recognition', {}).get('matched_patterns', [])),
        reverse=True
    )
    output = {
        "scan_date": datetime.now().strftime('%Y-%m-%d'),
        "total_candidates": len(sorted_c),
        "results": results[:20]
    }

    print(f"\n=== 扫描完成 ===")
    print(f"最终推荐关注（财务向量改善最显著）：")
    for r in results[:5]:
        pr = r.get('pattern_recognition', {})
        print(f"  {r.get('code','')} {r.get('name','')} | 阶段:{pr.get('stage','')} | 匹配模式:{len(pr.get('matched_patterns',[]))}个")

    print(json.dumps(output, ensure_ascii=False, indent=2))


# ─────────────────────────────────────────────
# 入口
# ─────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print("用法:")
        print("  python3 scripts/analysis/growth_hunter.py analyze <code>")
        print("  python3 scripts/analysis/growth_hunter.py batch <code1> <code2> ...")
        print("  python3 scripts/analysis/growth_hunter.py scan [--sector 半导体]")
        sys.exit(1)

    cmd = sys.argv[1].lower()

    if cmd == 'analyze':
        if len(sys.argv) < 3:
            print("请提供股票代码，例如: analyze 002475")
            sys.exit(1)
        cmd_analyze(sys.argv[2])

    elif cmd == 'batch':
        if len(sys.argv) < 3:
            print("请提供至少一个股票代码")
            sys.exit(1)
        cmd_batch(sys.argv[2:])

    elif cmd == 'scan':
        sector = None
        args = sys.argv[2:]
        for i, a in enumerate(args):
            if a == '--sector' and i + 1 < len(args):
                sector = args[i + 1]
        cmd_scan(sector)

    else:
        print(f"未知命令: {cmd}")
        sys.exit(1)


if __name__ == '__main__':
    main()
