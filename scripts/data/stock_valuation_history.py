#!/usr/bin/env python3
"""
stock_valuation_history.py — 个股历史估值分位工具

核心功能：
  - 查询个股PE/PB在自身历史中的分位（不是跟行业比，是跟自己的历史比）
  - "立讯精密现在的PE在它自己5年历史里是什么位置？"
  - 这比跟行业比更有意义——每只股有自己的合理估值区间

用法:
    python3 stock_valuation_history.py pe SZ002475          # 立讯精密PE历史分位
    python3 stock_valuation_history.py pb SH600900          # 长江电力PB历史分位
    python3 stock_valuation_history.py full SZ002475        # PE+PB+PS完整估值历史
    python3 stock_valuation_history.py batch                # 批量分析自选股
    python3 stock_valuation_history.py compare SZ002475 SZ000333  # 两只股估值对比
"""

import sys
import json
import os
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

WORKSPACE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def get_pe_history(ak_code: str, years: int = 5):
    """
    获取个股PE历史分位
    ak_code: 如 SZ002475, SH600900
    """
    result = {
        "code": ak_code,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "pe": {},
        "pb": {},
        "ps": {},
    }

    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=years * 365)).strftime("%Y%m%d")

    # 百度股市通个股历史估值（支持PE-TTM / PB / PS）
    period_map = {1: "近一年", 3: "3年", 5: "5年"}
    period_str = period_map.get(years, "5年")
    raw_code = ak_code[2:]  # 去掉SZ/SH前缀

    def fetch_indicator(indicator_name):
        """获取单个指标的历史序列"""
        try:
            df = ak.stock_zh_valuation_baidu(symbol=raw_code, indicator=indicator_name, period=period_str)
            if df.empty:
                return None, None
            df["value"] = pd.to_numeric(df["value"], errors="coerce")
            df = df.dropna(subset=["value"])
            df = df[df["value"] > 0]
            return df["value"], df.iloc[-1]
        except Exception as e:
            return None, str(e)[:60]

    def summarize(indicator_name, label):
        series, latest_row = fetch_indicator(indicator_name)
        if series is None:
            return {"error": f"{label}数据获取失败: {latest_row}"}

        current_val = float(latest_row["value"]) if isinstance(latest_row, pd.Series) else None
        if current_val is None:
            return {"error": "无最新值"}

        pct = round((series < current_val).sum() / len(series) * 100, 1)

        if pct < 20:
            assessment = "🟢🟢 历史低位（极度低估）"
        elif pct < 35:
            assessment = "🟢 历史偏低（低估）"
        elif pct < 60:
            assessment = "✅ 历史中位（合理）"
        elif pct < 80:
            assessment = "🟡 历史偏高（偏贵）"
        else:
            assessment = "🔴 历史高位（高估）"

        return {
            "current": round(current_val, 2),
            f"{years}yr_percentile": pct,
            "assessment": assessment,
            f"{years}yr_min": round(float(series.min()), 2),
            f"{years}yr_max": round(float(series.max()), 2),
            f"{years}yr_median": round(float(series.median()), 2),
            "data_points": len(series),
        }

    try:
        result["pe"] = summarize("市盈率(TTM)", "PE-TTM")
        time.sleep(0.3)
        result["pb"] = summarize("市净率", "PB")
        time.sleep(0.3)
        result["ps"] = summarize("市销率", "PS")

        # 综合评估
        pe_pct = result["pe"].get(f"{years}yr_percentile")
        pb_pct = result["pb"].get(f"{years}yr_percentile")
        if pe_pct is not None and pb_pct is not None:
            avg_pct = (pe_pct + pb_pct) / 2
            if avg_pct < 25:
                result["overall_assessment"] = "🟢🟢 估值历史低位 — 长线建仓窗口"
            elif avg_pct < 45:
                result["overall_assessment"] = "🟢 估值历史偏低 — 可分批建仓"
            elif avg_pct < 65:
                result["overall_assessment"] = "✅ 估值历史中位 — 合理持有"
            elif avg_pct < 80:
                result["overall_assessment"] = "🟡 估值历史偏高 — 谨慎追高"
            else:
                result["overall_assessment"] = "🔴 估值历史高位 — 不建议重仓"

        result["latest_date"] = datetime.now().strftime("%Y-%m-%d")
        result["period"] = period_str

    except Exception as e:
        result["error"] = str(e)[:120]

    return result


def batch_analyze():
    """批量分析自选池（短线 + 长线）"""
    codes = []

    # 短线自选
    wl_path = os.path.join(WORKSPACE, "watchlist.json")
    if os.path.exists(wl_path):
        with open(wl_path) as f:
            wl = json.load(f)
        for code in wl:
            if len(code) == 6 and not code.startswith("5"):  # 排除ETF
                prefix = "SH" if code.startswith("6") else "SZ"
                codes.append((f"{prefix}{code}", "短线自选", code))

    # 长线自选
    lt_path = os.path.join(WORKSPACE, "longterm_watchlist.json")
    if os.path.exists(lt_path):
        with open(lt_path) as f:
            lt = json.load(f)
        for sector, data in lt.get("sectors", {}).items():
            for stock in data.get("stocks", []):
                code = stock.get("code", "")
                if code and len(code) == 6 and not code.startswith("5"):
                    prefix = "SH" if code.startswith("6") else "SZ"
                    codes.append((f"{prefix}{code}", sector, code))

    if not codes:
        return {"error": "自选池为空或全为ETF"}

    results = []
    for ak_code, category, raw_code in codes:
        r = get_pe_history(ak_code)
        r["category"] = category
        results.append(r)
        time.sleep(0.5)

    # 按估值从低到高排序
    def sort_key(r):
        pe_pct = r.get("pe", {}).get("5yr_percentile")
        return pe_pct if pe_pct is not None else 999

    results.sort(key=sort_key)

    return {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "analyzed": len(results),
        "results": results,
        "summary": {
            "historical_low": [r["code"] for r in results if (r.get("pe", {}).get("5yr_percentile") or 100) < 30],
            "historical_high": [r["code"] for r in results if (r.get("pe", {}).get("5yr_percentile") or 0) > 75],
        }
    }


def compare_two(code1: str, code2: str):
    """对比两只股票的历史估值"""
    r1 = get_pe_history(code1)
    r2 = get_pe_history(code2)

    return {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "stock1": r1,
        "stock2": r2,
        "comparison": {
            "pe_percentile": {
                code1: r1.get("pe", {}).get("5yr_percentile"),
                code2: r2.get("pe", {}).get("5yr_percentile"),
                "cheaper": code1 if (r1.get("pe", {}).get("5yr_percentile") or 999) < (r2.get("pe", {}).get("5yr_percentile") or 999) else code2,
            },
            "pb_percentile": {
                code1: r1.get("pb", {}).get("5yr_percentile"),
                code2: r2.get("pb", {}).get("5yr_percentile"),
            },
            "overall": {
                code1: r1.get("overall_assessment", "N/A"),
                code2: r2.get("overall_assessment", "N/A"),
            }
        }
    }


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"

    if cmd in ("pe", "pb", "ps", "full"):
        code = sys.argv[2] if len(sys.argv) > 2 else "SZ002475"
        years = int(sys.argv[3]) if len(sys.argv) > 3 else 5
        result = get_pe_history(code, years=years)
        if cmd == "pe":
            print(json.dumps({"code": result["code"], "pe": result["pe"], "overall": result.get("overall_assessment"), "latest_date": result.get("latest_date")}, ensure_ascii=False, indent=2))
        elif cmd == "pb":
            print(json.dumps({"code": result["code"], "pb": result["pb"], "overall": result.get("overall_assessment"), "latest_date": result.get("latest_date")}, ensure_ascii=False, indent=2))
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2, default=str))

    elif cmd == "batch":
        print(json.dumps(batch_analyze(), ensure_ascii=False, indent=2, default=str))

    elif cmd == "compare":
        if len(sys.argv) < 4:
            print(json.dumps({"error": "需要两个股票代码，如: compare SZ002475 SZ000333"}, ensure_ascii=False))
            sys.exit(1)
        print(json.dumps(compare_two(sys.argv[2], sys.argv[3]), ensure_ascii=False, indent=2, default=str))

    else:
        print(__doc__)


if __name__ == "__main__":
    main()
