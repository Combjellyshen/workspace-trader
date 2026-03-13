#!/usr/bin/env python3
"""
sector_screener.py — 行业扫描+长线选股工具

用法:
    python3 sector_screener.py industries           # 列出所有行业板块
    python3 sector_screener.py sector <行业名>       # 扫描某行业成分股+财务筛选
    python3 sector_screener.py screen               # 全市场财务指标筛选（TOP候选股）
    python3 sector_screener.py valuation <code>     # 个股估值横向比较（如 SZ002475）
    python3 sector_screener.py growth <code>        # 个股成长性比较（如 SZ002475）
    python3 sector_screener.py watchlist            # 批量分析长线自选池
    python3 sector_screener.py pe_history           # 大盘PE历史分位
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


def get_industries():
    """获取所有行业板块列表（东财）"""
    try:
        df = ak.stock_board_industry_name_em()
        return {
            "source": "东财行业板块",
            "count": len(df),
            "industries": df[['板块名称', '板块代码']].to_dict('records') if '板块代码' in df.columns else df.to_dict('records')
        }
    except Exception as e:
        return {"error": str(e)}


def scan_sector(sector_name: str):
    """扫描某行业板块：成分股 + 财务筛选"""
    result = {"sector": sector_name, "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M")}
    
    # 1. 获取板块历史行情（近期涨跌）
    try:
        df_hist = ak.stock_board_industry_hist_em(
            symbol=sector_name, period="日k", start_date="20260101", end_date=datetime.now().strftime("%Y%m%d"), adjust=""
        )
        if not df_hist.empty:
            latest = df_hist.iloc[-1]
            result["sector_perf"] = {
                "latest_date": str(latest.get("日期", "")),
                "close": float(latest.get("收盘", 0)),
                "change_pct": float(latest.get("涨跌幅", 0)),
                "volume": float(latest.get("成交量", 0)),
                "turnover": float(latest.get("成交额", 0)),
            }
            # 近5日涨跌
            if len(df_hist) >= 5:
                result["sector_perf"]["5d_change_pct"] = round(
                    (df_hist.iloc[-1]["收盘"] / df_hist.iloc[-5]["收盘"] - 1) * 100, 2
                )
    except Exception as e:
        result["sector_perf_error"] = str(e)[:80]

    # 2. 获取成分股
    try:
        df_cons = ak.stock_board_industry_cons_em(symbol=sector_name)
        if df_cons.empty:
            result["stocks"] = []
            return result
        
        stocks = df_cons['代码'].tolist() if '代码' in df_cons.columns else []
        result["total_stocks"] = len(stocks)
        
        # 3. 对成分股批量获取财务摘要（取前30只）
        screened = []
        for code in stocks[:30]:
            try:
                # 财务摘要（同花顺）
                df_fin = ak.stock_financial_abstract_ths(symbol=code, indicator="按年度")
                if df_fin.empty:
                    continue
                
                # 取最近2年数据
                latest = df_fin.iloc[0] if len(df_fin) > 0 else None
                prev = df_fin.iloc[1] if len(df_fin) > 1 else None
                
                if latest is None:
                    continue
                
                from scripts.utils.common import safe_pct as _sp, safe_float as _sf
                
                roe = _sp(latest.get("净资产收益率"))
                revenue_growth = _sp(latest.get("营业总收入同比增长率"))
                net_profit_growth = _sp(latest.get("净利润同比增长率"))
                debt_ratio = _sp(latest.get("资产负债率"))
                
                # 筛选条件：ROE > 12% 且 净利增速 > 10%
                if roe is not None and net_profit_growth is not None:
                    stock_info = {
                        "code": code,
                        "report_period": str(latest.get("报告期", "")),
                        "roe": roe,
                        "revenue_growth_yoy": revenue_growth,
                        "net_profit_growth_yoy": net_profit_growth,
                        "debt_ratio": debt_ratio,
                        "net_profit_raw": str(latest.get("净利润", "")),
                        "revenue_raw": str(latest.get("营业总收入", "")),
                    }
                    screened.append(stock_info)
                
                time.sleep(0.1)  # 防止请求过快
                
            except Exception:
                continue
        
        # 按 ROE 排序
        screened.sort(key=lambda x: (x.get("roe") or -999), reverse=True)
        
        # 筛选优质股：ROE > 12% 且 净利增速 > 10%
        quality = [s for s in screened if (s.get("roe") or 0) > 12 and (s.get("net_profit_growth_yoy") or 0) > 10]
        
        result["screened_count"] = len(screened)
        result["quality_stocks"] = quality[:20]  # TOP20
        result["all_screened"] = screened[:50]
        
    except Exception as e:
        result["stocks_error"] = str(e)[:100]
    
    return result


def screen_market(min_roe=15, min_profit_growth=15, max_debt=65, top_n=50):
    """全市场财务筛选 - 用东财财务指标数据"""
    result = {
        "criteria": {
            "min_roe": min_roe,
            "min_profit_growth": min_profit_growth,
            "max_debt_ratio": max_debt,
        },
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    
    try:
        # 用 RPT_LICO_FN_CPD 获取全市场最新财务数据
        import requests
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://data.eastmoney.com/"
        }
        
        all_stocks = []
        page = 1
        
        while page <= 10:  # 最多10页，每页50条
            params = {
                "reportName": "RPT_LICO_FN_CPD",
                "columns": "SECUCODE,SECURITY_CODE,SECURITY_NAME_ABBR,BOARD_NAME,WEIGHTAVG_ROE,YSTZ,SJLTZ,TOTAL_OPERATE_INCOME,PARENT_NETPROFIT,ISNEW",
                "pageSize": 50,
                "pageNumber": page,
                "sortColumns": "WEIGHTAVG_ROE",
                "sortTypes": "-1",
                "filter": f'(ISNEW="1")',
            }
            
            try:
                r = requests.get(
                    "https://datacenter-web.eastmoney.com/api/data/v1/get",
                    params=params, headers=headers, timeout=10
                )
                d = r.json()
                
                if not d.get("result") or not d["result"].get("data"):
                    break
                
                data = d["result"]["data"]
                if not data:
                    break
                    
                for item in data:
                    try:
                        code = item.get("SECURITY_CODE", "")
                        name = item.get("SECURITY_NAME_ABBR", "")
                        
                        # 过滤 ST / 退市 / 北交所(8开头) / 科创(688)过小票
                        if not code or "ST" in name or name.startswith("*"):
                            continue
                        if code.startswith("8") or code.startswith("4"):  # 北交所/三板
                            continue
                        
                        roe = float(item.get("WEIGHTAVG_ROE") or 0)
                        rev_growth = float(item.get("YSTZ") or 0)
                        profit_growth = float(item.get("SJLTZ") or 0)
                        
                        # ROE 上限 100%（过高说明净资产异常）
                        if roe > 100 or roe < min_roe:
                            continue
                        if profit_growth < min_profit_growth:
                            continue
                        
                        all_stocks.append({
                            "code": code,
                            "name": name,
                            "industry": item.get("BOARD_NAME", ""),
                            "roe": round(roe, 2),
                            "revenue_growth": round(rev_growth, 2),
                            "profit_growth": round(profit_growth, 2),
                        })
                    except:
                        continue
                
                if len(data) < 50:
                    break
                    
                page += 1
                time.sleep(0.3)
                
            except Exception as e:
                result["page_error"] = str(e)[:80]
                break
        
        # 按 ROE 排序
        all_stocks.sort(key=lambda x: x["roe"], reverse=True)
        result["total_qualified"] = len(all_stocks)
        result["top_stocks"] = all_stocks[:top_n]
        
        # 按行业统计
        industry_count = {}
        for s in all_stocks:
            ind = s.get("industry", "未知")
            industry_count[ind] = industry_count.get(ind, 0) + 1
        result["industry_distribution"] = dict(sorted(industry_count.items(), key=lambda x: x[1], reverse=True)[:15])
        
    except Exception as e:
        result["error"] = str(e)
    
    return result


def get_valuation(code: str):
    """个股估值横向比较"""
    try:
        df = ak.stock_zh_valuation_comparison_em(symbol=code)
        if df.empty:
            return {"error": "无数据"}
        return {
            "code": code,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "data": df.to_dict('records'),
        }
    except Exception as e:
        return {"error": str(e)}


def get_growth(code: str):
    """个股成长性横向比较"""
    try:
        df = ak.stock_zh_growth_comparison_em(symbol=code)
        if df.empty:
            return {"error": "无数据"}
        return {
            "code": code,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "data": df.to_dict('records'),
        }
    except Exception as e:
        return {"error": str(e)}


def get_pe_history():
    """大盘PE历史（沪深300，万得全A）"""
    try:
        df = ak.stock_index_pe_lg()
        if df.empty:
            return {"error": "无数据"}
        
        # 取最近30天
        recent = df.tail(30)
        latest = df.iloc[-1]
        
        # 计算历史分位（近5年）
        five_yr_cutoff = (datetime.now() - timedelta(days=5*365)).date()
        five_yr = df[pd.to_datetime(df["日期"]).dt.date >= five_yr_cutoff]
        pe_col = "滚动市盈率"
        if pe_col in five_yr.columns:
            pe_vals = five_yr[pe_col].dropna().astype(float)
            current_pe = float(latest[pe_col]) if latest[pe_col] else None
            if current_pe and len(pe_vals) > 0:
                percentile = round((pe_vals < current_pe).sum() / len(pe_vals) * 100, 1)
            else:
                percentile = None
        else:
            percentile = None
        
        result = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "latest_date": str(latest["日期"]),
            "index_value": float(latest["指数"]) if latest["指数"] else None,
            "pe_ttm": float(latest["滚动市盈率"]) if latest["滚动市盈率"] else None,
            "pe_median": float(latest["滚动市盈率中位数"]) if latest["滚动市盈率中位数"] else None,
            "pe_5yr_percentile": percentile,
            "pe_5yr_min": round(float(pe_vals.min()), 2) if len(pe_vals) > 0 else None,
            "pe_5yr_max": round(float(pe_vals.max()), 2) if len(pe_vals) > 0 else None,
        }
        rows = recent[["日期", "指数", "滚动市盈率", "滚动市盈率中位数"]].tail(5).to_dict('records')
        for row in rows:
            for k, v in row.items():
                if hasattr(v, 'strftime'):
                    row[k] = str(v)
        result["recent_5d"] = rows
        return result
    except Exception as e:
        return {"error": str(e)}


def analyze_watchlist():
    """批量分析长线自选池"""
    import os
    wl_path = os.path.join(os.path.dirname(__file__), '..', '..', 'longterm_watchlist.json')
    if not os.path.exists(wl_path):
        return {"error": "longterm_watchlist.json 不存在，请先创建"}

    with open(wl_path, encoding='utf-8') as f:
        watchlist = json.load(f)

    sectors = watchlist.get("sectors", {})
    if not sectors:
        return {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "watchlist_stocks": 0,
            "results": [],
            "note": "长线观察池为空"
        }

    results = []
    for sector, data in sectors.items():
        stocks = data.get("stocks", []) if isinstance(data, dict) else []
        for stock in stocks:
            if not isinstance(stock, dict):
                continue
            code = str(stock.get("code", "")).strip()
            if not code:
                continue

            # 格式化为 AKShare 格式
            if code.startswith("6"):
                ak_code = f"SH{code}"
            else:
                ak_code = f"SZ{code}"

            stock_result = {
                "sector": sector,
                "code": code,
                "name": stock.get("name", ""),
                "reason": stock.get("reason", ""),
            }

            # 估值比较
            val = get_valuation(ak_code)
            if "data" in val:
                stock_data = [d for d in val["data"] if d.get("代码") == code or d.get("简称") not in ["行业中值", "行业平均"]]
                if stock_data:
                    stock_result["valuation"] = stock_data[0]

            # 成长比较
            growth = get_growth(ak_code)
            if "data" in growth:
                stock_growth = [d for d in growth["data"] if d.get("代码") == code]
                if stock_growth:
                    stock_result["growth"] = stock_growth[0]

            results.append(stock_result)
            time.sleep(0.5)

    return {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "watchlist_stocks": len(results),
        "results": results,
    }


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"
    
    if cmd == "industries":
        print(json.dumps(get_industries(), ensure_ascii=False, indent=2))
    
    elif cmd == "sector":
        if len(sys.argv) < 3:
            print(json.dumps({"error": "请指定行业名称，如: sector 半导体"}, ensure_ascii=False))
            sys.exit(1)
        sector_name = sys.argv[2]
        print(json.dumps(scan_sector(sector_name), ensure_ascii=False, indent=2))
    
    elif cmd == "screen":
        min_roe = float(sys.argv[2]) if len(sys.argv) > 2 else 15
        min_growth = float(sys.argv[3]) if len(sys.argv) > 3 else 15
        print(json.dumps(screen_market(min_roe=min_roe, min_profit_growth=min_growth), ensure_ascii=False, indent=2))
    
    elif cmd == "valuation":
        code = sys.argv[2] if len(sys.argv) > 2 else "SZ002475"
        print(json.dumps(get_valuation(code), ensure_ascii=False, indent=2))
    
    elif cmd == "growth":
        code = sys.argv[2] if len(sys.argv) > 2 else "SZ002475"
        print(json.dumps(get_growth(code), ensure_ascii=False, indent=2))
    
    elif cmd == "pe_history":
        print(json.dumps(get_pe_history(), ensure_ascii=False, indent=2))
    
    elif cmd == "watchlist":
        print(json.dumps(analyze_watchlist(), ensure_ascii=False, indent=2))
    
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
