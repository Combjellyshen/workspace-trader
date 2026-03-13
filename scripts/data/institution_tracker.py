#!/usr/bin/env python3
"""
institution_tracker.py — 机构行为追踪（散户最容易忽视但信号最强的数据）

用法:
    python3 institution_tracker.py north_top      # 北向资金持股排行（近5日净买入）
    python3 institution_tracker.py north_history  # 北向资金历史净流入趋势
    python3 institution_tracker.py block_trade    # 大宗交易（折价买入=机构低吸信号）
    python3 institution_tracker.py institute_hold # 机构持仓汇总（最新季报披露）
    python3 institution_tracker.py fund_position  # 基金重仓股（公募重仓=被认可的价值股）
    python3 institution_tracker.py all            # 全套机构行为数据
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


def get_north_top():
    """北向资金持股排行 — 外资最爱买什么"""
    result = {"timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"), "data": {}}
    
    # 沪股通近5日净买入
    for market, label in [("沪股通", "sh_connect"), ("深股通", "sz_connect")]:
        for indicator in ["5日排行", "10日排行"]:
            try:
                df = ak.stock_hsgt_hold_stock_em(market=market, indicator=indicator)
                if not df.empty:
                    result["data"][f"{label}_{indicator}"] = {
                        "market": market,
                        "indicator": indicator,
                        "count": len(df),
                        "columns": df.columns.tolist(),
                        "top15": df.head(15).to_dict('records'),
                    }
                time.sleep(0.3)
            except Exception as e:
                result["data"][f"{label}_{indicator}_error"] = str(e)[:80]
    
    return result


def get_north_history():
    """北向资金历史净流入趋势 — 外资是在持续买入还是流出"""
    result = {"timestamp": datetime.now().strftime("%Y-%m-%d %H:%M")}
    
    try:
        # 北向资金每日流向
        df = ak.stock_hsgt_fund_flow_summary_em()
        if not df.empty:
            result["fund_flow_summary"] = {
                "columns": df.columns.tolist(),
                "recent_20d": df.tail(20).to_dict('records'),
            }
    except Exception as e:
        result["fund_flow_error"] = str(e)[:80]
    
    try:
        # 北向持股统计（按日）
        end = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=30)).strftime("%Y%m%d")
        df2 = ak.stock_hsgt_stock_statistics_em(symbol="北向持股", start_date=start, end_date=end)
        if not df2.empty:
            result["north_hold_stats"] = {
                "period": f"{start}-{end}",
                "count": len(df2),
                "latest5": df2.head(5).to_dict('records'),
            }
    except Exception as e:
        result["north_hold_stats_error"] = str(e)[:80]
    
    return result


def get_block_trade():
    """大宗交易 — 折价率<-3%的净买入是机构低吸的经典信号"""
    result = {"timestamp": datetime.now().strftime("%Y-%m-%d %H:%M")}
    
    today = datetime.now()
    
    # 尝试最近几个交易日
    for days_back in range(0, 7):
        date = (today - timedelta(days=days_back)).strftime("%Y%m%d")
        try:
            df = ak.stock_dzjy_mrtj(start_date=date, end_date=date)
            if not df.empty:
                result["date"] = date
                result["total_count"] = len(df)
                result["columns"] = df.columns.tolist()
                
                # 转换数值列
                df_work = df.copy()
                
                # 找折价率列
                discount_col = None
                for col in df.columns:
                    if '折' in col or '溢' in col or '价格' in col or '%' in str(col):
                        discount_col = col
                        break
                
                result["all_trades"] = df.head(30).to_dict('records')
                
                # 统计：买入金额前10
                try:
                    amount_cols = [c for c in df.columns if '金额' in c or '成交额' in c]
                    if amount_cols:
                        df_sorted = df.sort_values(amount_cols[0], ascending=False)
                        result["top10_by_amount"] = df_sorted.head(10).to_dict('records')
                except:
                    pass
                
                break
        except Exception as e:
            result[f"error_{date}"] = str(e)[:80]
            continue
    
    # 大宗交易买方统计（机构最爱买的股）
    try:
        df_buy = ak.stock_dzjy_hygtj(start_date=(today - timedelta(days=7)).strftime("%Y%m%d"),
                                       end_date=today.strftime("%Y%m%d"))
        if not df_buy.empty:
            result["weekly_stock_summary"] = {
                "count": len(df_buy),
                "top20": df_buy.head(20).to_dict('records'),
            }
    except Exception as e:
        result["weekly_summary_error"] = str(e)[:80]
    
    return result


def get_institute_hold():
    """机构持仓汇总 — 新浪机构持股，参数格式如 '20253'(2025三季报), '20254'(2025年报)"""
    result = {"timestamp": datetime.now().strftime("%Y-%m-%d %H:%M")}
    
    # 新浪格式：YYYY + 季度编号(1=一季报,2=中报,3=三季报,4=年报)
    quarters = ["20254", "20253", "20252", "20251", "20244"]
    
    for q in quarters:
        try:
            df = ak.stock_institute_hold(symbol=q)
            if not df.empty:
                result["report_period"] = q
                result["description"] = f"{'一季报' if q[-1]=='1' else '中报' if q[-1]=='2' else '三季报' if q[-1]=='3' else '年报'} {q[:4]}年"
                result["total_stocks"] = len(df)
                result["columns"] = df.columns.tolist()
                # 按机构数/持股数量排序
                sort_col = next((c for c in df.columns if '机构' in c or '数量' in c), df.columns[-1])
                try:
                    df_sorted = df.sort_values(sort_col, ascending=False)
                    result["top30_by_institutions"] = df_sorted.head(30).to_dict('records')
                except:
                    result["top30"] = df.head(30).to_dict('records')
                break
        except Exception as e:
            result[f"error_{q}"] = str(e)[:60]
    
    # 备用：机构推荐评级（不依赖季报日期）
    if "total_stocks" not in result:
        try:
            df2 = ak.stock_institute_recommend(symbol="近一月")
            if not df2.empty:
                result["fallback_recommend"] = {
                    "source": "机构推荐近一月",
                    "count": len(df2),
                    "columns": df2.columns.tolist(),
                    "top20": df2.head(20).to_dict('records'),
                }
        except Exception as e:
            result["recommend_error"] = str(e)[:80]
    
    return result


def _latest_disclosed_quarters(n=4):
    """动态计算最近 n 个已可能披露的季报日期（YYYYMMDD 格式）
    季报披露规律：Q1(0331)→4月底, Q2(0630)→8月底, Q3(0930)→10月底, Q4(1231)→次年4月底
    """
    today = datetime.now()
    candidates = []
    for year in [today.year, today.year - 1]:
        for q_end, disc_month in [("1231", 4), ("0930", 10), ("0630", 8), ("0331", 4)]:
            disc_year = year + 1 if q_end == "1231" else year
            disc_deadline = datetime(disc_year, disc_month + 1, 1) if disc_month < 12 else datetime(disc_year + 1, 1, 1)
            if today >= disc_deadline:
                candidates.append(f"{year}{q_end}")
        if len(candidates) >= n:
            break
    return candidates[:n]


def get_fund_position():
    """基金重仓股 — 公募基金最新季报重仓股（机构认可的核心资产）"""
    result = {"timestamp": datetime.now().strftime("%Y-%m-%d %H:%M")}
    quarters = _latest_disclosed_quarters(4)
    
    for q in quarters:
        try:
            df = ak.stock_institute_hold_detail(symbol=q)
            if not df.empty:
                result["report_date"] = q
                result["count"] = len(df)
                result["columns"] = df.columns.tolist()
                result["top30"] = df.head(30).to_dict('records')
                return result
        except Exception:
            continue
    
    # 全部失败后尝试东财基金持仓备用
    result["fund_position_error"] = f"所有季度均无数据: {quarters}"
    try:
        for q in quarters:
            df2 = ak.stock_institute_hold(symbol=q)
            if not df2.empty:
                result["institute_hold_fallback"] = {
                    "quarter": q,
                    "top20": df2.head(20).to_dict('records')
                }
                break
    except Exception as e2:
        result["fallback_error"] = str(e2)[:80]
    
    return result


def get_all():
    """全套机构行为数据"""
    return {
        "report_type": "institution_tracker",
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "north_top": get_north_top(),
        "block_trade": get_block_trade(),
        "institute_hold": get_institute_hold(),
    }


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    
    dispatch = {
        "north_top": get_north_top,
        "north_history": get_north_history,
        "block_trade": get_block_trade,
        "institute_hold": get_institute_hold,
        "fund_position": get_fund_position,
        "all": get_all,
    }
    
    fn = dispatch.get(cmd)
    if fn:
        print(json.dumps(fn(), ensure_ascii=False, indent=2, default=str))
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
