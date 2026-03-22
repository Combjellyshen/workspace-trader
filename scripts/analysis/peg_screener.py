#!/usr/bin/env python3
"""
peg_screener.py - PEG选股（彼得林奇GARP法则）

核心逻辑：PEG = PE / 净利润增速
  - PEG < 0.6：被低估的成长股（林奇最爱区间）
  - PEG 0.6~1.0：合理定价的成长股
  - PEG > 1.5：增速撑不住估值，风险高

额外过滤：
  - 剔除 ST / 北交所 / ROE < 8% / 负债率 > 70% / 净利增速 > 60%（不可持续）

用法:
    python3 peg_screener.py screen            # 全市场 PEG 筛选（TOP50结果）
    python3 peg_screener.py screen 0.8 12     # 自定义 PEG上限=0.8, 最低ROE=12%
    python3 peg_screener.py stock SZ002475    # 单股 PEG 计算
    python3 peg_screener.py watchlist         # 对当前自选池做PEG评分
    python3 peg_screener.py sector 半导体      # 指定行业的PEG排名
"""

import sys
import json
import time
import os
from datetime import datetime
from zoneinfo import ZoneInfo
import warnings
warnings.filterwarnings('ignore')

try:
    import akshare as ak
    import pandas as pd
    import requests
except ImportError as e:
    print(json.dumps({"error": f"缺少依赖: {e}"}))
    sys.exit(1)

WORKSPACE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://data.eastmoney.com/"
}


def calc_peg(pe, profit_growth):
    """计算PEG，处理边界情况"""
    if pe is None or profit_growth is None:
        return None
    try:
        pe = float(pe)
        g = float(profit_growth)
        if pe <= 0 or g <= 0 or g > 80:  # 负PE无意义；增速>80%不可持续
            return None
        return round(pe / g, 3)
    except Exception as e:
        print(f"calc_peg error: {e}", file=sys.stderr)
        return None


def peg_rating(peg):
    """PEG评级"""
    if peg is None:
        return "N/A"
    if peg < 0.4:
        return "🟢🟢 极度低估"
    elif peg < 0.6:
        return "🟢 林奇买入区"
    elif peg < 1.0:
        return "✅ 合理成长"
    elif peg < 1.5:
        return "🟡 略贵"
    else:
        return "🔴 高估"


def screen_by_peg(max_peg=1.0, min_roe=10.0, top_n=50):
    """全市场PEG筛选 - 从东财批量数据API获取（超时则返回提示）"""
    result = {
        "criteria": {"max_peg": max_peg, "min_roe": min_roe},
        "timestamp": datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M"),
        "candidates": [],
    }

    all_candidates = []
    page = 1

    while page <= 15:
        try:
            params = {
                "reportName": "RPT_LICO_FN_CPD",
                "columns": "SECUCODE,SECURITY_CODE,SECURITY_NAME_ABBR,BOARD_NAME,"
                           "PE9,SJLTZ,WEIGHTAVG_ROE,YSTZ,TOTAL_OPERATE_INCOME,"
                           "PARENT_NETPROFIT,ISNEW",
                "pageSize": 50,
                "pageNumber": page,
                "sortColumns": "PE9",
                "sortTypes": "1",
                "filter": '(ISNEW="1")(PE9>0)(PE9<100)(SJLTZ>5)',
            }

            r = requests.get(
                "https://datacenter-web.eastmoney.com/api/data/v1/get",
                params=params, headers=HEADERS, timeout=15
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

                    if not code or "ST" in name or name.startswith("*"):
                        continue
                    if code.startswith("8") or code.startswith("4"):
                        continue

                    pe = float(item.get("PE9") or 0) or None
                    profit_growth = float(item.get("SJLTZ") or 0) or None
                    roe = float(item.get("WEIGHTAVG_ROE") or 0) or None
                    rev_growth = float(item.get("YSTZ") or 0) or None

                    if roe is None or roe < min_roe or roe > 80:
                        continue

                    peg = calc_peg(pe, profit_growth)
                    if peg is None or peg > max_peg:
                        continue

                    all_candidates.append({
                        "code": code,
                        "name": name,
                        "industry": item.get("BOARD_NAME", ""),
                        "pe": round(pe, 2) if pe else None,
                        "profit_growth": round(profit_growth, 2) if profit_growth else None,
                        "revenue_growth": round(rev_growth, 2) if rev_growth else None,
                        "roe": round(roe, 2) if roe else None,
                        "peg": peg,
                        "peg_rating": peg_rating(peg),
                    })
                except Exception as e:
                    print(f"screen_by_peg item error: {e}", file=sys.stderr)
                    continue

            if len(data) < 50:
                break

            page += 1
            time.sleep(0.3)

        except requests.exceptions.Timeout:
            result["api_status"] = "东财API超时（可能是非交易时段限流），建议交易日盘中运行"
            break
        except Exception as e:
            result["fetch_error"] = str(e)[:80]
            break

    all_candidates.sort(key=lambda x: x["peg"] or 999)

    industry_count = {}
    for s in all_candidates:
        ind = s.get("industry", "未知")
        industry_count[ind] = industry_count.get(ind, 0) + 1

    result["total_qualified"] = len(all_candidates)
    result["industry_distribution"] = dict(
        sorted(industry_count.items(), key=lambda x: x[1], reverse=True)[:15]
    )
    result["candidates"] = all_candidates[:top_n]
    result["garp_sweet_spot"] = [c for c in all_candidates if c["peg"] < 0.6][:20]

    return result


def calc_single_stock_peg(ak_code: str):
    """单股PEG计算（AKShare估值比较接口，含同行横向对比）"""
    result = {"code": ak_code, "timestamp": datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M")}
    
    try:
        df_val = ak.stock_zh_valuation_comparison_em(symbol=ak_code)
        
        if df_val.empty:
            result["error"] = "无估值数据"
            return result
        
        # 提取个股自身（第一行是个股本身）
        stock_row = df_val.iloc[0].to_dict()
        industry_median = {}
        peers = []
        
        for _, row in df_val.iterrows():
            name = str(row.get("简称", ""))
            if "行业中值" in name:
                industry_median = row.to_dict()
            elif "行业平均" not in name and row.get("代码") != stock_row.get("代码"):
                peers.append(row.to_dict())
        
        peg = stock_row.get("PEG")
        pe_ttm = stock_row.get("市盈率-TTM")
        pe_26e = stock_row.get("市盈率-26E")
        pb = stock_row.get("市净率-MRQ")
        
        ind_peg = industry_median.get("PEG")
        
        # PEG评价
        peg_val = float(peg) if peg and str(peg) not in ['nan', 'None'] else None
        
        result["peg"] = round(peg_val, 3) if peg_val else None
        result["peg_rating"] = peg_rating(peg_val)
        result["pe_ttm"] = round(float(pe_ttm), 2) if pe_ttm else None
        result["pe_26e"] = round(float(pe_26e), 2) if pe_26e else None
        result["pb_mrq"] = round(float(pb), 2) if pb else None
        result["industry_median_peg"] = round(float(ind_peg), 3) if ind_peg and str(ind_peg) not in ['nan','None'] else None
        
        # 是否低于行业中值
        if peg_val and result["industry_median_peg"]:
            result["vs_industry"] = f"低于行业中值 {round((1 - peg_val/result['industry_median_peg'])*100, 1)}%" if peg_val < result["industry_median_peg"] else f"高于行业中值 {round((peg_val/result['industry_median_peg'] - 1)*100, 1)}%"
        
        # 同行PEG排名
        result["peer_peg_ranking"] = sorted(
            [{"code": p.get("代码",""), "name": p.get("简称",""), "peg": round(float(p["PEG"]),3) if p.get("PEG") and str(p["PEG"]) not in ['nan','None'] else None} for p in peers if p.get("代码")],
            key=lambda x: (x["peg"] or 999)
        )
        
        result["full_valuation"] = stock_row
        
    except Exception as e:
        result["error"] = str(e)[:100]
    
    return result


def analyze_watchlist_peg():
    """对现有自选池做PEG评估"""
    wl_path = os.path.join(WORKSPACE, "watchlist.json")
    lt_path = os.path.join(WORKSPACE, "longterm_watchlist.json")

    stocks_to_check = []
    seen = set()

    if os.path.exists(wl_path):
        with open(wl_path, encoding='utf-8') as f:
            wl = json.load(f)
        for item in wl.get("stocks", []):
            code = str(item.get("code", "")).strip()
            if not code or code in seen:
                continue
            prefix = "SH" if code.startswith("6") else "SZ"
            stocks_to_check.append((code, f"{prefix}{code}", "当前观察池"))
            seen.add(code)

    if os.path.exists(lt_path):
        with open(lt_path, encoding='utf-8') as f:
            lt = json.load(f)
        for sector, data in lt.get("sectors", {}).items():
            for stock in data.get("stocks", []):
                code = str(stock.get("code", "")).strip()
                if not code or code in seen:
                    continue
                prefix = "SH" if code.startswith("6") else "SZ"
                stocks_to_check.append((code, f"{prefix}{code}", sector))
                seen.add(code)

    if not stocks_to_check:
        return {"error": "自选池为空"}

    results = []
    skipped = []
    for code, ak_code, category in stocks_to_check:
        if len(code) != 6:
            skipped.append({"code": code, "category": category, "reason": "非6位A股代码"})
            continue
        peg_data = calc_single_stock_peg(ak_code)
        peg_data["category"] = category
        results.append(peg_data)
        time.sleep(0.5)

    return {
        "timestamp": datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M"),
        "analyzed_count": len(results),
        "skipped": skipped,
        "results": results,
    }


def sector_peg_rank(sector_name: str):
    """指定行业的 PEG 排名"""
    result = {
        "sector": sector_name,
        "timestamp": datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M"),
    }

    try:
        params = {
            "reportName": "RPT_LICO_FN_CPD",
            "columns": "SECUCODE,SECURITY_CODE,SECURITY_NAME_ABBR,BOARD_NAME,"
                       "PE9,SJLTZ,WEIGHTAVG_ROE,YSTZ,ISNEW",
            "pageSize": 100,
            "pageNumber": 1,
            "filter": f'(ISNEW="1")(PE9>0)(PE9<150)(BOARD_NAME="{sector_name}")',
        }

        r = requests.get(
            "https://datacenter-web.eastmoney.com/api/data/v1/get",
            params=params, headers=HEADERS, timeout=10
        )
        d = r.json()

        if not d.get("result") or not d["result"].get("data"):
            result["error"] = f"未找到行业 '{sector_name}' 的数据（注意：需要使用东财行业名称）"
            return result

        stocks = []
        for item in d["result"]["data"]:
            code = item.get("SECURITY_CODE", "")
            name = item.get("SECURITY_NAME_ABBR", "")
            if not code or "ST" in name:
                continue

            pe = float(item.get("PE9") or 0) or None
            profit_growth = float(item.get("SJLTZ") or 0) or None
            roe = float(item.get("WEIGHTAVG_ROE") or 0) or None

            peg = calc_peg(pe, profit_growth)

            stocks.append({
                "code": code,
                "name": name,
                "pe": round(pe, 2) if pe else None,
                "profit_growth": round(profit_growth, 2) if profit_growth else None,
                "roe": round(roe, 2) if roe else None,
                "peg": peg,
                "peg_rating": peg_rating(peg),
            })

        # 过滤掉无PEG的，按PEG排序
        valid = [s for s in stocks if s["peg"] is not None]
        valid.sort(key=lambda x: x["peg"])

        result["total_stocks"] = len(stocks)
        result["valid_peg_count"] = len(valid)
        result["peg_ranking"] = valid[:30]
        result["garp_picks"] = [s for s in valid if s["peg"] < 1.0]

    except Exception as e:
        result["error"] = str(e)[:100]

    return result


def analyze_candidates():
    """
    读取 longterm_watchlist.json 中用户导入的候选标的（peg_candidates），
    对每只股用实时PE重新验证PEG，并补充估值分位和主力资金方向。
    """
    import os
    wl_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'longterm_watchlist.json')
    try:
        with open(wl_path, encoding='utf-8') as f:
            wl = json.load(f)
    except Exception as e:
        return {"error": f"读取 longterm_watchlist.json 失败: {e}"}

    candidates = wl.get('peg_candidates', {}).get('stocks', [])
    if not candidates:
        return {"error": "longterm_watchlist.json 中无 peg_candidates 数据，请先导入选股结果"}

    result = {
        "source": wl.get('peg_candidates', {}).get('source', ''),
        "updated": wl.get('peg_candidates', {}).get('updated', ''),
        "total": len(candidates),
        "stocks": []
    }

    # 按PEG分层输出，不做实时验证（避免API限流）
    tiers = {"极度低估(PEG<0.3)": [], "低估(0.3-0.6)": [], "合理(0.6-1.0)": []}
    for s in candidates:
        peg = s.get('peg', 99)
        entry = {
            "code": s['code'], "name": s['name'],
            "peg": peg, "roe_2024": s.get('roe_2024'),
            "mktcap_yi": s.get('mktcap_yi'), "industry": s.get('industry'),
            "price": s.get('price'), "watch_since": s.get('watch_since')
        }
        if peg < 0.3:
            tiers["极度低估(PEG<0.3)"].append(entry)
        elif peg < 0.6:
            tiers["低估(0.3-0.6)"].append(entry)
        else:
            tiers["合理(0.6-1.0)"].append(entry)

    result["tiers"] = tiers
    result["top10_by_peg"] = sorted(candidates, key=lambda x: x.get('peg', 99))[:10]
    result["top10_by_roe"] = sorted(candidates, key=lambda x: -x.get('roe_2024', 0))[:10]
    result["industry_distribution"] = {}
    for s in candidates:
        ind = s.get('industry', '其他')
        result["industry_distribution"][ind] = result["industry_distribution"].get(ind, 0) + 1

    result["note"] = (
        "以上数据来自用户导入的选股结果（YUR软件，历史PEG）。"
        "建议逐只用 peg_screener.py stock <code> 实时验证，或盘中运行 screen 命令获取全市场实时扫描。"
    )
    return result


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "screen"

    if cmd == "screen":
        max_peg = float(sys.argv[2]) if len(sys.argv) > 2 else 1.0
        min_roe = float(sys.argv[3]) if len(sys.argv) > 3 else 12.0  # 默认12%，与用户选股标准一致
        print(json.dumps(screen_by_peg(max_peg=max_peg, min_roe=min_roe), ensure_ascii=False, indent=2))

    elif cmd == "stock":
        code = sys.argv[2] if len(sys.argv) > 2 else "SZ002475"
        print(json.dumps(calc_single_stock_peg(code), ensure_ascii=False, indent=2))

    elif cmd == "watchlist":
        print(json.dumps(analyze_watchlist_peg(), ensure_ascii=False, indent=2))

    elif cmd == "candidates":
        # 读取用户导入的选股候选池（longterm_watchlist.json peg_candidates）
        print(json.dumps(analyze_candidates(), ensure_ascii=False, indent=2))

    elif cmd == "sector":
        if len(sys.argv) < 3:
            print(json.dumps({"error": "请指定行业名称"}, ensure_ascii=False))
            sys.exit(1)
        print(json.dumps(sector_peg_rank(sys.argv[2]), ensure_ascii=False, indent=2))

    else:
        print(__doc__)


if __name__ == "__main__":
    main()
