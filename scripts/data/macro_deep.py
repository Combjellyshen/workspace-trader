#!/usr/bin/env python3
"""
macro_deep.py — 多维宏观分析工具

五大维度（超越单一PMI+PE）：
  1. 流动性  — M1/M2增速、社融、DR007趋势、融资余额
  2. 盈利周期 — PPI年率趋势（企业盈利领先指标）
  3. 风险溢价 — ERP = 沪深300盈利收益率 - 10年国债收益率（股票真实吸引力）
  4. 政策意图 — 利率方向、债券收益率曲线形态
  5. 市场仓位 — 融资余额变化（聪明资金还是散户情绪？）

用法：
    python3 macro_deep.py all          # 全维度分析
    python3 macro_deep.py liquidity    # 流动性维度
    python3 macro_deep.py earnings     # 盈利周期（PPI）
    python3 macro_deep.py erp          # 股权风险溢价
    python3 macro_deep.py policy       # 政策/利率
    python3 macro_deep.py position     # 市场仓位/融资余额
    python3 macro_deep.py summary      # 综合评分（多空信号汇总）
"""

import sys
import json
import time
from datetime import datetime, timedelta
import warnings
warnings.filterwarnings('ignore')

try:
    import akshare as ak
    import pandas as pd
except ImportError as e:
    print(json.dumps({"error": f"缺少依赖: {e}"}))
    sys.exit(1)


def get_liquidity():
    """
    流动性维度：M1/M2增速趋势 + 社融 + 融资余额
    核心逻辑：M1是企业活期存款，代表真实经济活力；M1>M2=资金活化；M1<M2=钱趴着不动
    """
    result = {
        "dimension": "流动性",
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }

    # M1 / M2
    try:
        df = ak.macro_china_money_supply()
        # 数据是倒序的（最新在最后），取最近6个月
        df = df.sort_values('月份', ascending=True)
        recent = df.tail(6)

        m1_latest = float(recent.iloc[-1].get('货币(M1)-同比增长', 0) or 0)
        m2_latest = float(recent.iloc[-1].get('货币和准货币(M2)-同比增长', 0) or 0)
        m1_prev = float(recent.iloc[-2].get('货币(M1)-同比增长', 0) or 0)
        m2_prev = float(recent.iloc[-2].get('货币和准货币(M2)-同比增长', 0) or 0)

        m1_trend = "↑上行" if m1_latest > m1_prev else "↓下行"
        m2_trend = "↑上行" if m2_latest > m2_prev else "↓下行"
        spread = round(m1_latest - m2_latest, 2)

        # M1-M2剪刀差解读
        if spread > 2:
            spread_signal = "🟢🟢 剪刀差扩张——资金活化，企业经营信心强，利多股市"
        elif spread > 0:
            spread_signal = "🟢 M1>M2——资金小幅活化，温和利多"
        elif spread > -3:
            spread_signal = "🟡 M1<M2但差距不大——资金定期化，观望情绪"
        else:
            spread_signal = "🔴 剪刀差收缩深——资金大量趴在定存，实体需求弱，利空"

        result["m1_m2"] = {
            "m1_yoy": m1_latest,
            "m2_yoy": m2_latest,
            "m1_trend": m1_trend,
            "m2_trend": m2_trend,
            "m1_m2_spread": spread,
            "spread_signal": spread_signal,
            "latest_month": str(recent.iloc[-1]['月份']),
            "history_6m": [
                {"month": str(r['月份']),
                 "m1": float(r.get('货币(M1)-同比增长') or 0),
                 "m2": float(r.get('货币和准货币(M2)-同比增长') or 0)}
                for _, r in recent.iterrows()
            ]
        }
    except Exception as e:
        result["m1_m2"] = {"error": str(e)[:80]}

    # 社融增量（最近4个月趋势）
    try:
        df_shrzgm = ak.macro_china_shrzgm()
        recent_sf = df_shrzgm.tail(4)
        sf_values = []
        for _, row in recent_sf.iterrows():
            sf_values.append({
                "month": str(row['月份']),
                "total_bn": round(float(row['社会融资规模增量'] or 0) / 100, 1),  # 转换为百亿
                "loans_bn": round(float(row['其中-人民币贷款'] or 0) / 100, 1),
                "bonds_bn": round(float(row['其中-企业债券'] or 0) / 100, 1),
            })

        # 判断趋势
        if len(sf_values) >= 2:
            latest_sf = sf_values[-1]['total_bn']
            prev_sf = sf_values[-2]['total_bn']
            sf_trend = "↑扩张" if latest_sf > prev_sf else "↓收缩"
            sf_signal = "🟢 社融扩张——信贷需求回暖，流动性宽松" if latest_sf > prev_sf else "🔴 社融收缩——信贷需求不足，流动性偏紧"
        else:
            sf_trend = "数据不足"
            sf_signal = ""

        result["social_financing"] = {
            "trend": sf_trend,
            "signal": sf_signal,
            "recent_4m": sf_values
        }
    except Exception as e:
        result["social_financing"] = {"error": str(e)[:80]}

    # 融资余额（市场加杠杆情绪）
    try:
        df_margin = ak.macro_china_market_margin_sh()
        recent_margin = df_margin.tail(10)
        latest_margin = float(recent_margin.iloc[-1]['融资余额']) / 1e8  # 转亿元
        prev_margin = float(recent_margin.iloc[-6]['融资余额']) / 1e8
        margin_change_5d = round(latest_margin - prev_margin, 2)
        margin_change_pct = round(margin_change_5d / prev_margin * 100, 2)

        if margin_change_pct > 2:
            margin_signal = "🟡⚠️ 融资余额快速增加——散户加杠杆，短期过热风险"
        elif margin_change_pct > 0:
            margin_signal = "🟢 融资余额温和增加——市场信心回升"
        elif margin_change_pct > -2:
            margin_signal = "✅ 融资余额基本稳定"
        else:
            margin_signal = "🔴 融资余额快速减少——去杠杆，市场信心弱"

        result["margin_finance"] = {
            "latest_bn": round(latest_margin, 2),
            "5d_change_bn": margin_change_5d,
            "5d_change_pct": margin_change_pct,
            "signal": margin_signal,
            "date": str(recent_margin.iloc[-1]['日期']),
        }
    except Exception as e:
        result["margin_finance"] = {"error": str(e)[:80]}

    # 综合流动性评分
    result["liquidity_score"] = _score_liquidity(result)
    return result


def _score_liquidity(r):
    score = 0
    reasons = []

    m1 = r.get("m1_m2", {})
    spread = m1.get("m1_m2_spread")
    if spread is not None:
        if spread > 0:
            score += 2
            reasons.append("M1>M2(+2)")
        elif spread > -3:
            score += 1
            reasons.append("M1-M2差距小(+1)")
        else:
            score -= 1
            reasons.append("M1-M2大幅负值(-1)")

    margin = r.get("margin_finance", {})
    pct = margin.get("5d_change_pct")
    if pct is not None:
        if 0 < pct < 2:
            score += 1
            reasons.append("融资温和增加(+1)")
        elif pct >= 2:
            score -= 1
            reasons.append("融资过热(-1)")
        elif pct < -2:
            score -= 1
            reasons.append("融资去杠杆(-1)")

    label = "🟢 流动性偏宽" if score >= 2 else ("✅ 流动性中性" if score >= 0 else "🔴 流动性偏紧")
    return {"score": score, "label": label, "reasons": reasons}


def get_earnings_cycle():
    """
    盈利周期维度：PPI是企业盈利的领先指标（约领先利润3-6个月）
    PPI上行 → 企业收入扩张 → 利润增速改善 → PE自动下降（分母变大）
    PPI下行 → 通缩压力 → 企业盈利承压 → 即使PE低也可能是价值陷阱
    """
    result = {
        "dimension": "盈利周期",
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }

    try:
        df = ak.macro_china_ppi_yearly()
        df = df.dropna(subset=['今值'])
        df = df.sort_values('日期')
        recent = df.tail(8)

        latest = recent.iloc[-1]
        prev = recent.iloc[-2]

        ppi_current = float(latest['今值'])
        ppi_prev = float(prev['今值'])
        ppi_trend = "↑上行" if ppi_current > ppi_prev else "↓下行"

        # PPI连续几个月的趋势
        values = [float(r['今值']) for _, r in recent.iterrows()]
        months_improving = 0
        for i in range(len(values) - 1, 0, -1):
            if values[i] > values[i-1]:
                months_improving += 1
            else:
                break

        months_deteriorating = 0
        for i in range(len(values) - 1, 0, -1):
            if values[i] < values[i-1]:
                months_deteriorating += 1
            else:
                break

        if ppi_current > 0:
            ppi_signal = "🟢🟢 PPI转正——企业收入端扩张，盈利拐点确认"
        elif ppi_current > -1 and months_improving >= 2:
            ppi_signal = "🟢 PPI负值但持续改善——盈利底部回升中，领先3-6个月"
        elif ppi_current > -2:
            ppi_signal = "🟡 PPI温和通缩——企业盈利压力存在但可控"
        elif months_deteriorating >= 3:
            ppi_signal = "🔴 PPI持续恶化——通缩加深，企业盈利承压，PE低估可能是陷阱"
        else:
            ppi_signal = "🔴 PPI深度通缩——小心价值陷阱"

        result["ppi"] = {
            "current_yoy": ppi_current,
            "prev_yoy": ppi_prev,
            "trend": ppi_trend,
            "months_improving": months_improving,
            "months_deteriorating": months_deteriorating,
            "signal": ppi_signal,
            "latest_date": str(latest['日期']),
            "forecast": float(latest['预测值']) if not pd.isna(latest.get('预测值', float('nan'))) else None,
            "history_8m": [
                {"date": str(r['日期']), "ppi_yoy": float(r['今值'])}
                for _, r in recent.iterrows()
            ]
        }

        # 二阶推导
        result["ppi"]["second_order_analysis"] = _ppi_second_order(ppi_current, months_improving)

    except Exception as e:
        result["ppi"] = {"error": str(e)[:80]}

    return result


def _ppi_second_order(ppi_current, months_improving):
    """PPI → 盈利 → 估值的二阶推导链"""
    if ppi_current > 0:
        return ("PPI>0 → 工业品出厂价上涨 → 企业收入端改善 → "
                "净利润增速预期提升 → 动态PE自然下降 → 低PE信号有效 → 可信赖建仓信号")
    elif ppi_current > -2 and months_improving >= 2:
        return ("PPI负但改善中 → 通缩压力减轻 → 企业盈利止跌回稳预期 → "
                "市场开始定价盈利改善 → 低PE信号半可信（需要等待PPI转正确认）")
    elif ppi_current < -3:
        return ("PPI深度通缩 → 企业收入缩水 → 利润率压缩 → "
                "当前净利润可能是虚高基数 → 真实PE可能比显示的更高 → 低PE可能是价值陷阱")
    else:
        return ("PPI温和负值 → 通缩压力有限 → 估值参考价值中性 → 需结合其他维度判断")


def get_erp():
    """
    股权风险溢价（ERP）= 股票盈利收益率 - 无风险利率（10年国债）
    这才是股票相对债券"真正贵不贵"的度量
    ERP高 = 股票比债券便宜，值得持有
    ERP低 = 股票相对债券贵，性价比差
    历史规律：ERP > 3% 是买入区，ERP < 1% 是谨慎区
    """
    result = {
        "dimension": "股权风险溢价(ERP)",
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }

    try:
        # 10年期国债收益率
        df_bond = ak.bond_zh_us_rate()
        df_bond = df_bond.sort_values('日期')
        latest_bond = df_bond.iloc[-1]
        cn_10y = float(latest_bond['中国国债收益率10年'])
        bond_date = str(latest_bond['日期'])

        result["cn_10y_bond"] = {
            "yield": cn_10y,
            "date": bond_date,
            "interpretation": f"10年国债收益率{cn_10y}%——{'较低，债券吸引力弱，利好股票' if cn_10y < 2.5 else '较高，债券与股票竞争激烈'}"
        }

        # 用大盘PE反推盈利收益率
        # 沪深300 PE 约用当前大盘平均PE来估算
        # 股票盈利收益率 = 1/PE * 100%
        # 我们用 sector_screener pe_history 得到的PE（大约15-20x区间）
        # 这里直接从macro_monitor的pe数据估算
        try:
            df_pe = ak.stock_index_pe_lg()
            df_pe = df_pe.dropna(subset=['滚动市盈率'])
            latest_pe = float(df_pe.iloc[-1]['滚动市盈率'])
            earnings_yield = round(100 / latest_pe, 2)  # 盈利收益率%

            erp = round(earnings_yield - cn_10y, 2)

            if erp > 4:
                erp_signal = "🟢🟢 ERP极高——股票相对债券极度低估，历史级别买入机会"
            elif erp > 3:
                erp_signal = "🟢 ERP较高——股票相对债券低估，值得持有"
            elif erp > 2:
                erp_signal = "✅ ERP合理——股票债券性价比均衡"
            elif erp > 1:
                erp_signal = "🟡 ERP偏低——股票相对债券偏贵，谨慎"
            else:
                erp_signal = "🔴 ERP极低——股票远贵于债券，历史上对应回调风险"

            result["erp"] = {
                "market_pe": round(latest_pe, 2),
                "earnings_yield_pct": earnings_yield,
                "risk_free_rate_pct": cn_10y,
                "erp_pct": erp,
                "signal": erp_signal,
                "interpretation": (
                    f"股票盈利收益率{earnings_yield}% vs 10年国债{cn_10y}% → "
                    f"超额回报{erp}%。"
                    f"{'持有股票比债券每年多赚' + str(erp) + '%，性价比高' if erp > 2 else '股债性价比差距不大，需要精选个股'}"
                )
            }
        except Exception as e2:
            result["erp"] = {"error": f"PE数据获取失败: {str(e2)[:60]}"}

        # 收益率曲线形态（10年-2年利差）
        cn_2y = float(latest_bond.get('中国国债收益率2年') or 0)
        curve_spread = round(cn_10y - cn_2y, 4)

        if curve_spread > 0.5:
            curve_signal = "✅ 收益率曲线正常（长>短）——经济预期正常，流动性无异常"
        elif curve_spread > 0:
            curve_signal = "🟡 收益率曲线趋平——市场对长期增长信心下降"
        else:
            curve_signal = "🔴 收益率曲线倒挂——历史上衰退先行信号（中国适用性有限）"

        result["yield_curve"] = {
            "cn_10y": cn_10y,
            "cn_2y": cn_2y,
            "spread_10y_2y": curve_spread,
            "signal": curve_signal,
        }

    except Exception as e:
        result["error"] = str(e)[:100]

    return result


def get_policy():
    """
    政策意图：利率方向 + 债券市场信号
    央行降息 = 宽松周期，利好股票
    国债收益率持续下行 = 市场预期宽松，对成长股利好
    """
    result = {
        "dimension": "政策意图",
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }

    # 利率历史趋势
    try:
        df_bond = ak.bond_zh_us_rate()
        df_bond = df_bond.sort_values('日期')
        recent = df_bond.tail(60)  # 约3个月

        cn_10y_now = float(recent.iloc[-1]['中国国债收益率10年'])
        cn_10y_3m_ago = float(recent.iloc[0]['中国国债收益率10年'])
        change_3m = round(cn_10y_now - cn_10y_3m_ago, 4)

        # 中美利差
        us_10y_now = float(recent.iloc[-1].get('美国国债收益率10年') or 0)
        cn_us_spread = round(cn_10y_now - us_10y_now, 4)

        if change_3m < -0.1:
            rate_trend_signal = "🟢 国债收益率3个月持续下行——市场预期宽松延续，有利于估值扩张"
        elif change_3m < 0:
            rate_trend_signal = "✅ 国债收益率小幅下行——温和宽松预期"
        elif change_3m < 0.1:
            rate_trend_signal = "🟡 国债收益率小幅上行——宽松预期减弱"
        else:
            rate_trend_signal = "🔴 国债收益率快速上行——流动性收紧，对估值压制"

        if cn_us_spread < -1.5:
            spread_signal = "⚠️ 中美利差倒挂深——人民币贬值压力+外资流出压力并存"
        elif cn_us_spread < -0.5:
            spread_signal = "🟡 中美利差倒挂——外资持有人民币资产吸引力下降"
        else:
            spread_signal = "✅ 中美利差合理——外资流出压力可控"

        result["interest_rate"] = {
            "cn_10y_current": cn_10y_now,
            "cn_10y_3m_change": change_3m,
            "trend_signal": rate_trend_signal,
            "cn_us_spread": cn_us_spread,
            "us_10y": us_10y_now,
            "spread_signal": spread_signal,
        }

    except Exception as e:
        result["interest_rate"] = {"error": str(e)[:80]}

    return result


def get_position():
    """
    市场仓位：融资余额趋势（反映市场参与者加减杠杆行为）
    核心逻辑：融资余额持续增加 = 散户/投机资金加杠杆 = 短期过热信号
            融资余额持续减少 = 去杠杆 = 悲观或风险释放
    """
    result = {
        "dimension": "市场仓位",
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }

    try:
        df = ak.macro_china_market_margin_sh()
        df = df.sort_values('日期')
        recent = df.tail(20)

        latest = recent.iloc[-1]
        week_ago = recent.iloc[-5] if len(recent) >= 5 else recent.iloc[0]
        month_ago = recent.iloc[-20] if len(recent) >= 20 else recent.iloc[0]

        latest_bal = float(latest['融资余额']) / 1e8
        week_bal = float(week_ago['融资余额']) / 1e8
        month_bal = float(month_ago['融资余额']) / 1e8

        week_chg = round(latest_bal - week_bal, 2)
        week_chg_pct = round(week_chg / week_bal * 100, 2)
        month_chg = round(latest_bal - month_bal, 2)
        month_chg_pct = round(month_chg / month_bal * 100, 2)

        # 综合判断
        if week_chg_pct > 3 and month_chg_pct > 5:
            position_signal = "🔴 融资余额快速增长——散户加杠杆明显，短期过热风险高，历史上是顶部信号"
        elif week_chg_pct > 1:
            position_signal = "🟡 融资余额温和增长——市场信心回升，但注意监控是否过热"
        elif -1 <= week_chg_pct <= 1:
            position_signal = "✅ 融资余额稳定——市场情绪平稳"
        elif week_chg_pct < -3:
            position_signal = "🟢 融资余额快速去杠杆——过度悲观，历史上往往是阶段性底部"
        else:
            position_signal = "🟡 融资余额温和减少——去杠杆过程中，谨慎"

        result["margin"] = {
            "latest_bn": round(latest_bal, 2),
            "week_change_bn": week_chg,
            "week_change_pct": week_chg_pct,
            "month_change_pct": month_chg_pct,
            "signal": position_signal,
            "date": str(latest['日期']),
        }

        # 历史对比（简单分位）
        all_bal = [float(r['融资余额']) / 1e8 for _, r in df.iterrows()]
        pct_rank = round((sum(1 for x in all_bal if x < latest_bal) / len(all_bal)) * 100, 1)
        result["margin"]["historical_percentile"] = pct_rank
        result["margin"]["percentile_signal"] = (
            f"当前融资余额处于历史{pct_rank}%分位——"
            + ("偏低，悲观情绪浓" if pct_rank < 30 else ("适中" if pct_rank < 70 else "偏高，乐观情绪浓"))
        )

    except Exception as e:
        result["margin"] = {"error": str(e)[:80]}

    return result


def get_summary():
    """
    综合评分：5个维度汇总 → 多空信号计分 → 市场环境判断
    """
    result = {
        "dimension": "综合宏观评分",
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }

    signals = {}
    score = 0

    # 各维度采集
    print("[1/5] 流动性...", file=__import__('sys').stderr)
    liquidity = get_liquidity()
    liq_score = liquidity.get("liquidity_score", {}).get("score", 0)
    score += liq_score
    signals["流动性"] = {
        "score": liq_score,
        "label": liquidity.get("liquidity_score", {}).get("label", ""),
        "key_data": {
            "M1-M2剪刀差": liquidity.get("m1_m2", {}).get("m1_m2_spread"),
            "融资余额周变化%": liquidity.get("margin_finance", {}).get("5d_change_pct"),
        }
    }

    print("[2/5] 盈利周期...", file=__import__('sys').stderr)
    earnings = get_earnings_cycle()
    ppi = earnings.get("ppi", {})
    ppi_val = ppi.get("current_yoy", 0)
    ppi_improving = ppi.get("months_improving", 0)
    if ppi_val > 0:
        e_score = 2
    elif ppi_val > -2 and ppi_improving >= 2:
        e_score = 1
    elif ppi_val < -3:
        e_score = -2
    else:
        e_score = 0
    score += e_score
    signals["盈利周期(PPI)"] = {
        "score": e_score,
        "label": "🟢盈利扩张" if e_score > 0 else ("✅中性" if e_score == 0 else "🔴盈利承压"),
        "key_data": {
            "PPI年率%": ppi_val,
            "连续改善月数": ppi_improving,
        }
    }

    print("[3/5] 股权风险溢价...", file=__import__('sys').stderr)
    erp_data = get_erp()
    erp_val = erp_data.get("erp", {}).get("erp_pct")
    if erp_val is not None:
        if erp_val > 3:
            erp_score = 2
        elif erp_val > 2:
            erp_score = 1
        elif erp_val > 1:
            erp_score = 0
        else:
            erp_score = -1
    else:
        erp_score = 0
    score += erp_score
    signals["股权风险溢价"] = {
        "score": erp_score,
        "label": "🟢股票便宜" if erp_score > 0 else ("✅均衡" if erp_score == 0 else "🔴股票偏贵"),
        "key_data": {
            "ERP%": erp_val,
            "10年国债%": erp_data.get("cn_10y_bond", {}).get("yield"),
        }
    }

    print("[4/5] 政策利率...", file=__import__('sys').stderr)
    policy = get_policy()
    rate_3m_chg = policy.get("interest_rate", {}).get("cn_10y_3m_change", 0)
    if rate_3m_chg < -0.1:
        p_score = 1
    elif rate_3m_chg > 0.1:
        p_score = -1
    else:
        p_score = 0
    score += p_score
    signals["政策利率"] = {
        "score": p_score,
        "label": "🟢利率下行" if p_score > 0 else ("✅利率稳定" if p_score == 0 else "🔴利率上行"),
        "key_data": {
            "10年国债3个月变化%": rate_3m_chg,
            "中美利差%": policy.get("interest_rate", {}).get("cn_us_spread"),
        }
    }

    print("[5/5] 市场仓位...", file=__import__('sys').stderr)
    position = get_position()
    margin_pct = position.get("margin", {}).get("week_change_pct", 0)
    if margin_pct < -3:
        pos_score = 2  # 过度悲观，底部信号
    elif margin_pct < 0:
        pos_score = 1
    elif margin_pct < 2:
        pos_score = 0
    else:
        pos_score = -1  # 过热
    score += pos_score
    signals["市场仓位"] = {
        "score": pos_score,
        "label": "🟢仓位偏低" if pos_score > 0 else ("✅仓位适中" if pos_score == 0 else "🔴仓位偏高"),
        "key_data": {
            "融资余额周变化%": margin_pct,
            "历史分位%": position.get("margin", {}).get("historical_percentile"),
        }
    }

    # 综合判断
    if score >= 5:
        overall = "🟢🟢 强烈多头信号——五维度共振利多，可积极布局"
    elif score >= 3:
        overall = "🟢 多头偏向——多数维度利多，适合分批建仓"
    elif score >= 1:
        overall = "🟡 轻微多头——整体偏暖但不强，精选个股"
    elif score >= -1:
        overall = "✅ 中性——多空信号平衡，持仓不动，等待信号明朗"
    elif score >= -3:
        overall = "🟡 防御偏向——多数维度转弱，控制仓位"
    else:
        overall = "🔴 熊市信号——五维度共振利空，大幅减仓"

    result["total_score"] = score
    result["max_possible"] = 10
    result["overall_signal"] = overall
    result["dimension_signals"] = signals

    # 关键矛盾点（如果维度之间有明显背离）
    contradictions = []
    if signals.get("流动性", {}).get("score", 0) > 0 and signals.get("股权风险溢价", {}).get("score", 0) < 0:
        contradictions.append("⚠️ 矛盾：流动性宽松但股票估值偏贵——钱多但没有便宜股票可买，需要选择性")
    if signals.get("盈利周期(PPI)", {}).get("score", 0) < 0 and signals.get("股权风险溢价", {}).get("score", 0) > 0:
        contradictions.append("⚠️ 矛盾：PPI通缩（盈利承压）但ERP显示股票便宜——低PE可能是盈利恶化的价值陷阱")
    if signals.get("市场仓位", {}).get("score", 0) < 0 and signals.get("流动性", {}).get("score", 0) > 0:
        contradictions.append("⚠️ 矛盾：流动性宽松但市场仓位高——资金够但已经重仓，增量资金有限")

    result["key_contradictions"] = contradictions if contradictions else ["各维度信号基本一致，无明显矛盾"]

    return result


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "summary"

    if cmd == "liquidity":
        print(json.dumps(get_liquidity(), ensure_ascii=False, indent=2, default=str))
    elif cmd == "earnings":
        print(json.dumps(get_earnings_cycle(), ensure_ascii=False, indent=2, default=str))
    elif cmd == "erp":
        print(json.dumps(get_erp(), ensure_ascii=False, indent=2, default=str))
    elif cmd == "policy":
        print(json.dumps(get_policy(), ensure_ascii=False, indent=2, default=str))
    elif cmd == "position":
        print(json.dumps(get_position(), ensure_ascii=False, indent=2, default=str))
    elif cmd in ("summary", "all"):
        print(json.dumps(get_summary(), ensure_ascii=False, indent=2, default=str))
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
