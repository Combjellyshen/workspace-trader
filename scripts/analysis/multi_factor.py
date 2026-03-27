#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
multi_factor.py — 多因子评分选股引擎
支持命令: score / report / diff
"""

import sys
import io
import os
import re
import json
import math
import argparse
import datetime

# BOM/编码处理
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

try:
    import openpyxl
except ImportError:
    print("请先安装 openpyxl: pip install openpyxl", file=sys.stderr)
    sys.exit(1)

# ─────────────────────────── 列索引（0-based） ───────────────────────────
COL_CODE      = 1
COL_NAME      = 2
COL_PRICE     = 3
COL_CASHFLOW  = 5   # 每股经营活动现金流（含后缀，取|前）
COL_ROE_2024  = 7   # ROE 2024年报
COL_ROE_2023  = 8
COL_ROE_2022  = 9
COL_PEG       = 10  # 历史PEG
COL_LOWMARK   = 13  # 低位标注
COL_INDUSTRY  = 14
COL_MKTCAP    = 19  # 总市值（含"亿"）

# ─────────────────────────── 数字解析工具 ────────────────────────────────

def parse_number(raw) -> float | None:
    """解析各种格式的数字：带|的、带亿的、百分比等"""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s or s in ('-', '--', 'N/A', 'n/a', '无'):
        return None
    # 取|前的部分
    if '|' in s:
        s = s.split('|')[0].strip()
    # 去掉亿
    s = s.replace('亿', '').strip()
    # 去掉%
    s = s.replace('%', '').strip()
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def parse_roe(raw) -> float | None:
    """解析ROE，结果为百分制（如 25.3 代表 25.3%）"""
    v = parse_number(raw)
    return v  # 已经是百分制


# ─────────────────────────── 打分逻辑 ────────────────────────────────────

def score_peg(peg: float | None) -> float:
    if peg is None or peg <= 0:
        return 0.0
    if peg < 0.2:
        return 100.0
    elif peg < 0.4:
        # 0.2→100, 0.4→80
        return 100.0 - (peg - 0.2) / (0.4 - 0.2) * 20.0
    elif peg < 0.6:
        # 0.4→80, 0.6→60
        return 80.0 - (peg - 0.4) / (0.6 - 0.4) * 20.0
    elif peg < 0.8:
        # 0.6→60, 0.8→40
        return 60.0 - (peg - 0.6) / (0.8 - 0.6) * 20.0
    elif peg <= 1.0:
        # 0.8→40, 1.0→20
        return 40.0 - (peg - 0.8) / (1.0 - 0.8) * 20.0
    else:
        return 0.0


def score_roe(roe_2024: float | None, roe_2023: float | None, roe_2022: float | None) -> tuple[float, dict]:
    """返回 (分数, 调试信息)"""
    valid = [v for v in [roe_2024, roe_2023, roe_2022] if v is not None]
    if not valid:
        return 20.0, {"avg": None, "std": None, "level_score": 20.0, "penalty": 0}

    avg = sum(valid) / len(valid)

    # 水平评分
    if avg > 30:
        level = 100.0
    elif avg > 25:
        level = 85.0 + (avg - 25) / 5 * 15
    elif avg > 20:
        level = 70.0 + (avg - 20) / 5 * 15
    elif avg > 15:
        level = 55.0 + (avg - 15) / 5 * 15
    elif avg > 12:
        level = 40.0 + (avg - 12) / 3 * 15
    else:
        level = 20.0

    # 稳定性惩罚：std > 10 时扣分
    penalty = 0.0
    std = 0.0
    if len(valid) >= 2:
        variance = sum((v - avg) ** 2 for v in valid) / len(valid)
        std = math.sqrt(variance)
        if std > 10:
            penalty = min(20.0, 10.0 + (std - 10) * 1.0)

    score = max(0.0, level - penalty)
    return round(score, 1), {"avg": round(avg, 2), "std": round(std, 2), "level_score": round(level, 1), "penalty": round(penalty, 1)}


def score_growth(roe_2024: float | None, roe_2022: float | None) -> tuple[float, str]:
    """用ROE改善趋势评分，返回 (分数, 趋势描述)"""
    if roe_2024 is None or roe_2022 is None:
        return 50.0, "数据缺失"
    if roe_2022 == 0:
        if roe_2024 > 0:
            return 80.0, "+改善"
        elif roe_2024 < 0:
            return 10.0, "-下滑"
        else:
            return 50.0, "持平"

    change = (roe_2024 - roe_2022) / abs(roe_2022)  # 相对变化率

    if change > 0.30:
        s = 100.0
    elif change > 0.10:
        # 0.10→70, 0.30→100
        s = 70.0 + (change - 0.10) / 0.20 * 30.0
    elif change > 0:
        # 0→50, 0.10→70
        s = 50.0 + change / 0.10 * 20.0
    elif change > -0.20:
        # 0→50, -0.20→30
        s = 50.0 + change / 0.20 * 20.0
    else:
        # -0.20→30, <-0.20→0-30线性（到-1.0为0）
        s = max(0.0, 30.0 + (change + 0.20) / 0.80 * 30.0)

    trend = f"{'+' if change >= 0 else ''}{change*100:.1f}%"
    return round(s, 1), trend


def score_cashflow(cf: float | None) -> float:
    if cf is None:
        return 30.0
    if cf > 5.0:
        return 100.0
    elif cf > 3.0:
        return 80.0
    elif cf > 1.0:
        return 60.0
    elif cf > 0.1:
        return 40.0
    elif cf >= 0:
        return 20.0
    else:
        return 0.0


def score_mktcap(mktcap: float | None) -> float:
    if mktcap is None:
        return 50.0
    if 50 <= mktcap <= 150:
        return 100.0
    elif mktcap <= 300:
        # 150→100, 300→85
        return 100.0 - (mktcap - 150) / 150 * 15
    elif mktcap <= 500:
        return 85.0 - (mktcap - 300) / 200 * 15
    elif mktcap <= 1000:
        return 70.0 - (mktcap - 500) / 500 * 15
    elif mktcap > 1000:
        return 40.0
    else:
        # < 50
        return 60.0


# ─────────────────────────── Excel 读取 ──────────────────────────────────

def load_xlsx(path: str) -> list[dict]:
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb.active
    rows_data = []
    header_skipped = False

    for row in ws.iter_rows(values_only=True):
        # 跳过前几行空行和标题行
        if not header_skipped:
            # 判断是否是数据行：代码列应是数字字符串或纯数字
            raw_code = row[COL_CODE] if len(row) > COL_CODE else None
            if raw_code is None:
                continue
            code_str = str(raw_code).strip()
            # 如果不是6位数字，跳过（可能是表头）
            if not re.match(r'^\d{6}$', code_str):
                continue
            header_skipped = True

        if len(row) <= max(COL_CODE, COL_NAME, COL_PRICE, COL_CASHFLOW,
                           COL_ROE_2024, COL_ROE_2023, COL_ROE_2022,
                           COL_PEG, COL_INDUSTRY, COL_MKTCAP):
            continue

        raw_code = row[COL_CODE]
        code_str = str(raw_code).strip() if raw_code else ''
        if not re.match(r'^\d{6}$', code_str):
            continue

        stock = {
            'code':      code_str,
            'name':      str(row[COL_NAME]).strip() if row[COL_NAME] else '',
            'price':     parse_number(row[COL_PRICE]),
            'cashflow':  parse_number(row[COL_CASHFLOW]),
            'roe_2024':  parse_roe(row[COL_ROE_2024]),
            'roe_2023':  parse_roe(row[COL_ROE_2023]),
            'roe_2022':  parse_roe(row[COL_ROE_2022]),
            'peg':       parse_number(row[COL_PEG]),
            'lowmark':   str(row[COL_LOWMARK]).strip() if row[COL_LOWMARK] else '',
            'industry':  str(row[COL_INDUSTRY]).strip() if row[COL_INDUSTRY] else '',
            'mktcap':    parse_number(row[COL_MKTCAP]),
        }
        rows_data.append(stock)

    return rows_data


# ─────────────────────────── 综合评分 ────────────────────────────────────

def compute_scores(stock: dict, weights: dict) -> dict:
    peg_s = score_peg(stock['peg'])
    roe_s, roe_debug = score_roe(stock['roe_2024'], stock['roe_2023'], stock['roe_2022'])
    growth_s, growth_trend = score_growth(stock['roe_2024'], stock['roe_2022'])
    cf_s = score_cashflow(stock['cashflow'])
    mktcap_s = score_mktcap(stock['mktcap'])

    composite = (
        peg_s    * weights['peg'] +
        roe_s    * weights['roe'] +
        growth_s * weights['growth'] +
        cf_s     * weights['cashflow'] +
        mktcap_s * weights['mktcap']
    )

    # 自动生成亮点/警示
    highlights = []
    warnings = []
    p = stock.get('peg')
    if p is not None and 0 < p < 0.4:
        highlights.append(f"PEG极低({p:.2f})")
    if roe_debug['avg'] is not None and roe_debug['avg'] > 25:
        highlights.append(f"ROE优秀({roe_debug['avg']:.1f}%)")
    cf = stock.get('cashflow')
    if cf is not None and cf > 3:
        highlights.append(f"现金流充沛({cf:.2f})")
    if roe_debug['penalty'] > 10:
        warnings.append(f"ROE波动大(σ={roe_debug['std']:.1f}%)")
    if p is not None and p > 1.0:
        warnings.append(f"PEG偏高({p:.2f})")
    if cf is not None and cf < 0:
        warnings.append(f"经营现金流为负({cf:.2f})")

    return {
        'scores': {
            'peg':      round(peg_s, 1),
            'roe':      round(roe_s, 1),
            'growth':   round(growth_s, 1),
            'cashflow': round(cf_s, 1),
            'mktcap':   round(mktcap_s, 1),
        },
        'composite_score': round(composite, 1),
        'raw': {
            'peg':          stock['peg'],
            'roe_avg':      roe_debug['avg'],
            'roe_trend':    growth_trend,
            'cashflow_ps':  stock['cashflow'],
            'mktcap_yi':    stock['mktcap'],
        },
        'highlight': '+'.join(highlights) if highlights else None,
        'warning':   '；'.join(warnings) if warnings else None,
        '_roe_debug': roe_debug,
    }


def rank_stocks(stocks: list[dict], weights: dict) -> list[dict]:
    results = []
    for s in stocks:
        scored = compute_scores(s, weights)
        results.append({
            'code':            s['code'],
            'name':            s['name'],
            'industry':        s['industry'],
            'price':           s['price'],
            'mktcap_yi':       s['mktcap'],
            'lowmark':         s['lowmark'],
            'composite_score': scored['composite_score'],
            'scores':          scored['scores'],
            'raw':             scored['raw'],
            'highlight':       scored['highlight'],
            'warning':         scored['warning'],
        })
    results.sort(key=lambda x: x['composite_score'], reverse=True)
    for i, r in enumerate(results, 1):
        r['rank'] = i
    return results


# ─────────────────────────── 分层 ────────────────────────────────────────

def tier_summary(rankings: list[dict]) -> dict:
    tiers = {
        'strong_buy(>75)': [],
        'buy(60-75)': [],
        'watch(45-60)': [],
        'low_priority(<45)': [],
    }
    for r in rankings:
        s = r['composite_score']
        item = {'rank': r['rank'], 'code': r['code'], 'name': r['name'], 'score': s}
        if s > 75:
            tiers['strong_buy(>75)'].append(item)
        elif s > 60:
            tiers['buy(60-75)'].append(item)
        elif s > 45:
            tiers['watch(45-60)'].append(item)
        else:
            tiers['low_priority(<45)'].append(item)
    return tiers


def industry_top(rankings: list[dict]) -> dict:
    seen = {}
    for r in rankings:
        ind = r['industry'] or '未知'
        if ind not in seen:
            seen[ind] = {'rank': r['rank'], 'code': r['code'], 'name': r['name'],
                         'score': r['composite_score']}
    return seen


# ─────────────────────────── 权重解析 ────────────────────────────────────

def normalize_weights(peg, roe, growth, cashflow, mktcap) -> dict:
    total = peg + roe + growth + cashflow + mktcap
    if total == 0:
        total = 100
    return {
        'peg':      peg / total,
        'roe':      roe / total,
        'growth':   growth / total,
        'cashflow': cashflow / total,
        'mktcap':   mktcap / total,
    }


# ─────────────────────────── score 命令 ──────────────────────────────────

def cmd_score(args):
    weights = normalize_weights(args.peg, args.roe, args.growth, args.cashflow, args.mktcap)
    stocks = load_xlsx(args.xlsx)
    if not stocks:
        print("❌ 未能从文件中读取到有效股票数据", file=sys.stderr)
        sys.exit(1)

    rankings = rank_stocks(stocks, weights)
    top_n = args.top if args.top else len(rankings)
    top = rankings[:top_n]

    meta = {
        'file':    os.path.basename(args.xlsx),
        'date':    datetime.date.today().isoformat(),
        'weights': {k: round(v, 4) for k, v in weights.items()},
        'total':   len(rankings),
    }

    result = {
        'meta':         meta,
        'rankings':     top,
        'tier_summary': tier_summary(rankings),
        'industry_top': industry_top(rankings),
    }

    print(json.dumps(result, ensure_ascii=False, indent=2))


# ─────────────────────────── report 命令 ─────────────────────────────────

def cmd_report(args):
    weights = normalize_weights(args.peg, args.roe, args.growth, args.cashflow, args.mktcap)
    stocks = load_xlsx(args.xlsx)
    rankings = rank_stocks(stocks, weights)
    top_n = args.top if args.top else min(30, len(rankings))
    top = rankings[:top_n]
    tiers = tier_summary(rankings)
    ind_top = industry_top(rankings)
    today = datetime.date.today().isoformat()

    w = weights
    lines = []
    lines.append(f"# 多因子选股评分报告\n")
    lines.append(f"**日期**：{today}  ")
    lines.append(f"**数据来源**：{os.path.basename(args.xlsx)}  ")
    lines.append(f"**权重**：PEG {w['peg']*100:.0f}% | ROE {w['roe']*100:.0f}% | 成长 {w['growth']*100:.0f}% | 现金流 {w['cashflow']*100:.0f}% | 市值 {w['mktcap']*100:.0f}%  ")
    lines.append(f"**样本数**：{len(rankings)} 只\n")

    lines.append(f"## 综合评分排行榜（TOP {top_n}）\n")
    lines.append("| 排名 | 代码 | 名称 | 行业 | 综合分 | PEG分 | ROE分 | 成长分 | 现金流分 | 市值分 | 亮点/警示 |")
    lines.append("|------|------|------|------|--------|-------|-------|--------|----------|--------|-----------|")
    for r in top:
        s = r['scores']
        hi = r['highlight'] or ''
        wa = r['warning'] or ''
        note = f"✅{hi}" if hi else ''
        if wa:
            note += f" ⚠️{wa}"
        lines.append(
            f"| {r['rank']} | {r['code']} | {r['name']} | {r['industry']} "
            f"| **{r['composite_score']}** | {s['peg']} | {s['roe']} | {s['growth']} "
            f"| {s['cashflow']} | {s['mktcap']} | {note} |"
        )

    lines.append("\n## 分层总结\n")
    for tier_name, items in tiers.items():
        if not items:
            continue
        label_map = {
            'strong_buy(>75)': '🔥 强力关注（>75分）',
            'buy(60-75)':      '👍 值得关注（60-75分）',
            'watch(45-60)':    '👀 备选观察（45-60分）',
            'low_priority(<45)': '💤 暂时观望（<45分）',
        }
        label = label_map.get(tier_name, tier_name)
        lines.append(f"### {label}（{len(items)}只）\n")
        names = ', '.join([f"{x['name']}({x['score']})" for x in items[:10]])
        lines.append(names + '\n')

    lines.append("\n## 各行业最优标的\n")
    lines.append("| 行业 | 代码 | 名称 | 综合分 | 排名 |")
    lines.append("|------|------|------|--------|------|")
    for ind, info in sorted(ind_top.items(), key=lambda x: x[1]['score'], reverse=True):
        lines.append(f"| {ind} | {info['code']} | {info['name']} | {info['score']} | #{info['rank']} |")

    lines.append("\n## 权重敏感性提示\n")
    # 调高ROE权重到50%，其余按比例压缩
    alt_weights = normalize_weights(args.peg * 0.5, args.roe * 2.0, args.growth * 0.8, args.cashflow * 0.8, args.mktcap * 0.8)
    alt_rankings = rank_stocks(stocks, alt_weights)
    lines.append(f"若将ROE权重加倍（当前{w['roe']*100:.0f}%→约{alt_weights['roe']*100:.0f}%），前3名变化：\n")
    lines.append("| 场景 | #1 | #2 | #3 |")
    lines.append("|------|----|----|----|")
    orig3 = [f"{r['name']}({r['composite_score']})" for r in top[:3]]
    alt3  = [f"{r['name']}({r['composite_score']})" for r in alt_rankings[:3]]
    lines.append(f"| 当前权重 | {orig3[0] if len(orig3)>0 else '-'} | {orig3[1] if len(orig3)>1 else '-'} | {orig3[2] if len(orig3)>2 else '-'} |")
    lines.append(f"| ROE权重加倍 | {alt3[0] if len(alt3)>0 else '-'} | {alt3[1] if len(alt3)>1 else '-'} | {alt3[2] if len(alt3)>2 else '-'} |")

    md = '\n'.join(lines)

    out_path = args.output if hasattr(args, 'output') and args.output else args.md
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(md)
    print(f"✅ 报告已保存：{out_path}")
    print(f"📊 共分析 {len(rankings)} 只，TOP{top_n} 已写入。")


# ─────────────────────────── diff 命令 ───────────────────────────────────

def cmd_diff(args):
    weights = normalize_weights(args.peg, args.roe, args.growth, args.cashflow, args.mktcap)

    new_stocks = load_xlsx(args.new_xlsx)
    old_stocks = load_xlsx(args.old_xlsx)

    new_ranked = rank_stocks(new_stocks, weights)
    old_ranked = rank_stocks(old_stocks, weights)

    new_map = {r['code']: r for r in new_ranked}
    old_map = {r['code']: r for r in old_ranked}

    new_codes = set(new_map.keys())
    old_codes = set(old_map.keys())

    entered = [new_map[c] for c in (new_codes - old_codes)]
    exited  = [old_map[c] for c in (old_codes - new_codes)]

    # 排名变化最大的
    common = new_codes & old_codes
    changes = []
    for c in common:
        n = new_map[c]
        o = old_map[c]
        score_delta = n['composite_score'] - o['composite_score']
        rank_delta  = o['rank'] - n['rank']  # 正数=上升
        changes.append({
            'code':        c,
            'name':        n['name'],
            'old_score':   o['composite_score'],
            'new_score':   n['composite_score'],
            'score_delta': round(score_delta, 1),
            'old_rank':    o['rank'],
            'new_rank':    n['rank'],
            'rank_delta':  rank_delta,
        })
    changes.sort(key=lambda x: abs(x['score_delta']), reverse=True)

    result = {
        'new_file':    os.path.basename(args.new_xlsx),
        'old_file':    os.path.basename(args.old_xlsx),
        'new_entered': [{'rank': r['rank'], 'code': r['code'], 'name': r['name'],
                          'score': r['composite_score']} for r in entered[:20]],
        'exited':      [{'rank': r['rank'], 'code': r['code'], 'name': r['name'],
                          'score': r['composite_score']} for r in exited[:20]],
        'top5_changes': changes[:5],
    }

    print(json.dumps(result, ensure_ascii=False, indent=2))


# ─────────────────────────── 主入口 ──────────────────────────────────────

def build_weight_args(parser):
    parser.add_argument('--peg',      type=float, default=30.0, help='PEG权重（默认30）')
    parser.add_argument('--roe',      type=float, default=25.0, help='ROE权重（默认25）')
    parser.add_argument('--growth',   type=float, default=20.0, help='成长性权重（默认20）')
    parser.add_argument('--cashflow', type=float, default=15.0, help='现金流权重（默认15）')
    parser.add_argument('--mktcap',   type=float, default=10.0, help='市值权重（默认10）')
    parser.add_argument('--top',      type=int,   default=None, help='只显示TOP N')


def main():
    if len(sys.argv) < 2:
        print("用法: python3 multi_factor.py <score|report|diff> [选项]")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == 'score':
        parser = argparse.ArgumentParser(prog='multi_factor.py score')
        parser.add_argument('xlsx', help='Excel文件路径')
        build_weight_args(parser)
        args = parser.parse_args(sys.argv[2:])
        cmd_score(args)

    elif cmd == 'report':
        parser = argparse.ArgumentParser(prog='multi_factor.py report')
        parser.add_argument('xlsx', help='Excel文件路径')
        parser.add_argument('md',   help='输出Markdown路径')
        build_weight_args(parser)
        args = parser.parse_args(sys.argv[2:])
        cmd_report(args)

    elif cmd == 'diff':
        parser = argparse.ArgumentParser(prog='multi_factor.py diff')
        parser.add_argument('new_xlsx', help='新Excel文件路径')
        parser.add_argument('old_xlsx', help='旧Excel文件路径')
        build_weight_args(parser)
        args = parser.parse_args(sys.argv[2:])
        cmd_diff(args)

    else:
        print(f"未知命令：{cmd}。支持：score / report / diff", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
