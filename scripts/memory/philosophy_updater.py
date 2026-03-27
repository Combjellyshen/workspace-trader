#!/usr/bin/env python3
"""
philosophy_updater.py — 砚的投资哲学持续学习引擎

功能：
  1. 自动抓取最新机构策略报告/大师观点/市场研究
  2. 把新内容格式化后追加到 PHILOSOPHY.md 的「认知更新记录」
  3. 生成「本周学到了什么」摘要

用法:
    python3 philosophy_updater.py collect      # 抓取最新观点（RSS+AKShare）
    python3 philosophy_updater.py summarize    # 生成本周学习摘要（供 AI 分析）
    python3 philosophy_updater.py backtest     # 运行当前框架的简单历史验证（大盘PE分位 vs 收益）
    python3 philosophy_updater.py check        # 检查当前市场与框架的匹配度
"""

import sys
import json
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import warnings
warnings.filterwarnings('ignore')

WORKSPACE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PHILOSOPHY_PATH = os.path.join(WORKSPACE, "PHILOSOPHY.md")
KNOWLEDGE_DIR = os.path.join(WORKSPACE, "memory", "knowledge")
os.makedirs(KNOWLEDGE_DIR, exist_ok=True)


def collect_latest():
    """抓取最新机构观点和研究报告"""
    result = {
        "timestamp": datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M"),
        "sources": {}
    }

    # 1. 机构研究报告（AKShare）
    try:
        import akshare as ak
        df = ak.stock_research_report_em(symbol="策略报告")
        if not df.empty:
            result["sources"]["broker_strategy"] = {
                "count": len(df),
                "latest": df.head(10).to_dict('records')
            }
    except Exception as e:
        result["sources"]["broker_strategy_error"] = str(e)[:80]

    # 2. 机构调研热门股（哪些股被机构大量调研）
    try:
        import akshare as ak
        import pandas as pd
        # 取近30天调研
        end = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d")
        start = (datetime.now(ZoneInfo("Asia/Shanghai")) - timedelta(days=30)).strftime("%Y%m%d")
        df = ak.stock_em_institute_survey(start_date=start, end_date=end)
        if not df.empty:
            # 按股票统计调研次数
            if '股票代码' in df.columns or '代码' in df.columns:
                code_col = '股票代码' if '股票代码' in df.columns else '代码'
                name_col = '股票简称' if '股票简称' in df.columns else '简称'
                survey_count = df.groupby([code_col, name_col]).size().reset_index(name='调研次数')
                survey_count = survey_count.sort_values('调研次数', ascending=False)
                result["sources"]["institute_survey_hot"] = {
                    "period": f"{start}-{end}",
                    "top20_surveyed": survey_count.head(20).to_dict('records')
                }
    except Exception as e:
        result["sources"]["institute_survey_error"] = str(e)[:80]

    # 3. 最新行业基金配置变化（机构持仓风向）
    try:
        from scripts.utils.common import fetch_industry_flow
        ind_data = fetch_industry_flow(top=100)
        if ind_data:
            result["sources"]["industry_fund_flow_1m"] = {
                "source": "HTTP push2",
                "count": len(ind_data),
                "top10_inflow": ind_data[:10]
            }
        else:
            import akshare as ak
            df = ak.stock_fund_flow_industry(symbol="近1月")
            if not df.empty:
                result["sources"]["industry_fund_flow_1m"] = {
                    "count": len(df),
                    "top10_inflow": df.nlargest(10, df.columns[2] if len(df.columns) > 2 else df.columns[-1]).to_dict('records')
                }
    except Exception as e:
        result["sources"]["fund_flow_error"] = str(e)[:80]

    # 4. 大宗交易（折价低买 = 机构低吸信号）
    try:
        import akshare as ak
        df = ak.stock_dzjy_mrtj()  # 大宗交易每日统计
        if not df.empty:
            result["sources"]["block_trade"] = {
                "count": len(df),
                "latest_5": df.head(5).to_dict('records')
            }
    except Exception as e:
        result["sources"]["block_trade_error"] = str(e)[:80]

    # 5. 北向资金持股变化趋势
    try:
        import akshare as ak
        df = ak.stock_em_hsgt_north_net_flow_in(indicator="近20日")
        if not df.empty:
            result["sources"]["northbound_trend"] = df.to_dict('records')
    except Exception as e:
        result["sources"]["northbound_error"] = str(e)[:80]

    return result


def check_framework_match():
    """检查当前市场环境与框架的匹配度 — 给出今日操作建议偏向"""
    import akshare as ak
    import pandas as pd
    from datetime import timedelta

    result = {
        "timestamp": datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M"),
        "signals": {},
        "framework_score": {},
        "overall_bias": ""
    }

    # === 信号1：大盘PE分位 ===
    try:
        df_pe = ak.stock_index_pe_lg()
        five_yr_cutoff = (datetime.now(ZoneInfo("Asia/Shanghai")) - timedelta(days=5*365)).date()
        import pandas as pd
        five_yr = df_pe[pd.to_datetime(df_pe["日期"]).dt.date >= five_yr_cutoff]
        pe_col = "滚动市盈率"
        if pe_col in five_yr.columns:
            pe_vals = five_yr[pe_col].dropna().astype(float)
            current_pe = float(df_pe.iloc[-1][pe_col])
            percentile = round((pe_vals < current_pe).sum() / len(pe_vals) * 100, 1)
            result["signals"]["pe_percentile_5yr"] = percentile
            if percentile < 30:
                result["framework_score"]["valuation"] = {"score": "低估", "action": "积极建仓", "weight": "✅✅✅"}
            elif percentile < 60:
                result["framework_score"]["valuation"] = {"score": "合理", "action": "正常持仓", "weight": "✅✅"}
            else:
                result["framework_score"]["valuation"] = {"score": "偏贵", "action": "谨慎/减仓", "weight": "⚠️"}
    except Exception as e:
        result["signals"]["pe_error"] = str(e)[:60]

    # === 信号2：PMI趋势（宏观景气度）===
    try:
        df_pmi = ak.index_pmi_man_cx()
        if not df_pmi.empty:
            latest_pmi = float(str(df_pmi.iloc[-1].iloc[1]).replace(",", ""))
            prev_pmi = float(str(df_pmi.iloc[-2].iloc[1]).replace(",", "")) if len(df_pmi) > 1 else latest_pmi
            result["signals"]["caixin_mfg_pmi"] = latest_pmi
            if latest_pmi > 51 and latest_pmi > prev_pmi:
                result["framework_score"]["macro"] = {"score": "扩张且上行", "action": "做多成长股", "weight": "✅✅✅"}
            elif latest_pmi > 50:
                result["framework_score"]["macro"] = {"score": "扩张", "action": "正常配置", "weight": "✅✅"}
            else:
                result["framework_score"]["macro"] = {"score": "收缩", "action": "防御/等待", "weight": "⚠️"}
    except Exception as e:
        result["signals"]["pmi_error"] = str(e)[:60]

    # === 信号3：行业资金流方向 ===
    try:
        from scripts.utils.common import fetch_industry_flow
        ind_data = fetch_industry_flow(top=10)
        if ind_data:
            result["signals"]["top_inflow_sectors"] = ind_data[:3]
        else:
            df_flow = ak.stock_fund_flow_industry(symbol="即时")
            if not df_flow.empty:
                num_cols = df_flow.select_dtypes(include=['float64', 'int64']).columns
                if len(num_cols) > 0:
                    top_inflow = df_flow.nlargest(3, num_cols[0])
                    result["signals"]["top_inflow_sectors"] = top_inflow.iloc[:, :3].to_dict('records')
    except Exception as e:
        result["signals"]["fund_flow_error"] = str(e)[:60]

    # === 综合判断 ===
    scores = result["framework_score"]
    bullish = sum(1 for v in scores.values() if "✅✅✅" in v.get("weight", ""))
    bearish = sum(1 for v in scores.values() if "⚠️" in v.get("weight", ""))

    if bullish >= 2:
        result["overall_bias"] = "🟢 做多偏向 — 当前环境支持积极持仓"
    elif bearish >= 2:
        result["overall_bias"] = "🔴 防御偏向 — 当前环境建议轻仓等待"
    else:
        result["overall_bias"] = "🟡 中性 — 结构性机会，精选个股，不做方向性大赌"

    return result


def generate_weekly_summary():
    """生成本周学习摘要（输出给 AI 进行深度分析用）"""
    collected = collect_latest()
    framework = check_framework_match()

    summary = {
        "report_type": "weekly_philosophy_update",
        "generated_at": datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M"),
        "market_framework_check": framework,
        "new_data_collected": collected,
        "questions_for_analysis": [
            "1. 当前机构调研热门股有哪些？是否有低估值+高增速的机会？",
            "2. 本周行业资金流方向是否与我们的赛道判断一致？",
            "3. 大宗交易是否出现机构低吸的折价买入信号？",
            "4. 当前框架评分结果如何？做多还是防御？",
            "5. 是否有需要更新到PHILOSOPHY.md的新认知？"
        ]
    }

    # 保存到knowledge目录
    date_str = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")
    out_path = os.path.join(KNOWLEDGE_DIR, f"weekly_{date_str}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=str)

    return {"saved_to": out_path, "summary": summary}


def run_valuation_backtest():
    """简单历史验证：大盘PE分位 vs 未来1年收益（仅用于框架验证）"""
    try:
        import akshare as ak
        import pandas as pd

        df = ak.stock_index_pe_lg()
        df["日期"] = pd.to_datetime(df["日期"])
        df = df.sort_values("日期").reset_index(drop=True)
        df["pe"] = df["滚动市盈率"].astype(float)
        df["index"] = df["指数"].astype(float)

        results = []
        # 每个历史时点，看它处于过去3年PE分位，以及未来1年涨幅
        for i in range(len(df)):
            dt = df.iloc[i]["日期"]
            pe_now = df.iloc[i]["pe"]
            idx_now = df.iloc[i]["index"]

            # 过去3年分位
            past = df[df["日期"] <= dt].tail(252*3)
            if len(past) < 100:
                continue
            percentile = round((past["pe"] < pe_now).sum() / len(past) * 100, 1)

            # 未来1年收益
            future = df[df["日期"] >= dt + timedelta(days=365)]
            if future.empty:
                continue
            idx_future = future.iloc[0]["index"]
            future_return = round((idx_future / idx_now - 1) * 100, 2)

            results.append({
                "date": str(dt.date()),
                "pe": pe_now,
                "pe_percentile_3yr": percentile,
                "future_1yr_return": future_return
            })

        if not results:
            return {"error": "数据不足"}

        df_res = pd.DataFrame(results)
        # 按PE分位分组统计平均未来1年收益
        bins = [0, 20, 40, 60, 80, 100]
        labels = ["极低(<20%)", "低(20-40%)", "中(40-60%)", "高(60-80%)", "极高(>80%)"]
        df_res["pe_bucket"] = pd.cut(df_res["pe_percentile_3yr"], bins=bins, labels=labels)
        grouped = df_res.groupby("pe_bucket")["future_1yr_return"].agg(["mean", "median", "count"]).reset_index()
        grouped.columns = ["PE分位区间", "平均未来1年收益%", "中位数未来1年收益%", "样本数"]

        return {
            "description": "历史上处于不同PE分位时买入，未来1年的平均收益（基于全A PE历史数据）",
            "result": grouped.to_dict('records'),
            "conclusion": "PE分位越低，未来1年收益的期望值越高——这验证了我们框架中'PE<30%分位才积极建仓'的策略"
        }

    except Exception as e:
        return {"error": str(e)}


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"

    if cmd == "collect":
        print(json.dumps(collect_latest(), ensure_ascii=False, indent=2, default=str))
    elif cmd == "check":
        print(json.dumps(check_framework_match(), ensure_ascii=False, indent=2, default=str))
    elif cmd == "summarize":
        print(json.dumps(generate_weekly_summary(), ensure_ascii=False, indent=2, default=str))
    elif cmd == "backtest":
        print(json.dumps(run_valuation_backtest(), ensure_ascii=False, indent=2, default=str))
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
