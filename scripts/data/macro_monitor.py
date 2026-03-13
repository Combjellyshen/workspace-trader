#!/usr/bin/env python3
"""
macro_monitor.py — 宏观数据监测工具

用法:
    python3 macro_monitor.py all          # 全套宏观数据
    python3 macro_monitor.py rates        # 利率（LPR/逆回购/美联储/中国央行）
    python3 macro_monitor.py pmi          # PMI制造业/服务业
    python3 macro_monitor.py news         # 国内财经要闻（央视/百度财经）
    python3 macro_monitor.py hot          # 市场热点关键词（东财）
"""

import sys
import json
from datetime import datetime, timedelta
import warnings
warnings.filterwarnings('ignore')

try:
    import akshare as ak
    import pandas as pd
except ImportError as e:
    print(json.dumps({"error": f"缺少依赖: {e}"}))
    sys.exit(1)


def get_interest_rates():
    """利率数据：LPR / 中国央行基准利率 / 美联储"""
    result = {}
    
    # 中国央行利率
    try:
        df = ak.macro_bank_china_interest_rate()
        if not df.empty:
            latest = df.iloc[-1]
            result["china_bank_rate"] = {
                "date": str(latest.iloc[0]),
                "rate": str(latest.iloc[1]) if len(latest) > 1 else None,
                "recent": df.tail(3).to_dict('records'),
            }
    except Exception as e:
        result["china_bank_rate_error"] = str(e)[:80]
    
    # PMI
    try:
        df_pmi = ak.index_pmi_man_cx()
        if not df_pmi.empty:
            result["caixin_mfg_pmi"] = {
                "latest": df_pmi.tail(3).to_dict('records')
            }
    except Exception as e:
        result["pmi_error"] = str(e)[:80]
    
    # 美联储利率
    try:
        df_fed = ak.macro_bank_usa_interest_rate()
        if not df_fed.empty:
            latest = df_fed.iloc[-1]
            result["fed_rate"] = {
                "date": str(latest.iloc[0]),
                "rate": str(latest.iloc[1]) if len(latest) > 1 else None,
                "recent": df_fed.tail(3).to_dict('records'),
            }
    except Exception as e:
        result["fed_rate_error"] = str(e)[:80]
    
    return result


def get_pmi():
    """PMI数据（财新制造业/服务业）"""
    result = {}
    
    try:
        df_mfg = ak.index_pmi_man_cx()
        if not df_mfg.empty:
            result["caixin_manufacturing"] = {
                "latest_date": str(df_mfg.iloc[-1].iloc[0]),
                "latest_value": str(df_mfg.iloc[-1].iloc[1]),
                "trend": "扩张" if float(str(df_mfg.iloc[-1].iloc[1]).replace(",","")) > 50 else "收缩",
                "recent_3m": df_mfg.tail(3).to_dict('records'),
            }
    except Exception as e:
        result["mfg_pmi_error"] = str(e)[:80]
    
    try:
        df_ser = ak.index_pmi_ser_cx()
        if not df_ser.empty:
            result["caixin_services"] = {
                "latest_date": str(df_ser.iloc[-1].iloc[0]),
                "latest_value": str(df_ser.iloc[-1].iloc[1]),
                "trend": "扩张" if float(str(df_ser.iloc[-1].iloc[1]).replace(",","")) > 50 else "收缩",
                "recent_3m": df_ser.tail(3).to_dict('records'),
            }
    except Exception as e:
        result["ser_pmi_error"] = str(e)[:80]
    
    try:
        df_com = ak.index_pmi_com_cx()
        if not df_com.empty:
            result["caixin_composite"] = {
                "latest_date": str(df_com.iloc[-1].iloc[0]),
                "latest_value": str(df_com.iloc[-1].iloc[1]),
                "recent_3m": df_com.tail(3).to_dict('records'),
            }
    except Exception as e:
        result["com_pmi_error"] = str(e)[:80]
    
    return result


def get_news():
    """国内财经要闻"""
    result = {}
    today = datetime.now().strftime("%Y%m%d")
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
    
    # 央视财经新闻
    for date in [today, yesterday]:
        try:
            df = ak.news_cctv(date=date)
            if not df.empty:
                result["cctv_news"] = {
                    "date": date,
                    "count": len(df),
                    "headlines": df.head(15).to_dict('records'),
                }
                break
        except Exception as e:
            result[f"cctv_error_{date}"] = str(e)[:60]
    
    # 百度财经要闻
    try:
        df_baidu = ak.news_economic_baidu(date=today)
        if not df_baidu.empty:
            result["baidu_economic_news"] = {
                "count": len(df_baidu),
                "headlines": df_baidu.head(15).to_dict('records'),
            }
    except Exception as e:
        result["baidu_news_error"] = str(e)[:80]
    
    return result


def get_hot_keywords():
    """东财热门关键词/市场热点"""
    result = {}
    
    # 市场主题热词
    try:
        df = ak.stock_hot_keyword_em(symbol="SZ000001")  # 用平安银行作为基准
        # 这个接口返回的是个股热词，不是全市场，换用别的
        result["note"] = "东财热词需要指定股票"
    except Exception as e:
        result["hot_keyword_error"] = str(e)[:80]
    
    # 雪球热门股
    try:
        df_hot = ak.stock_hot_deal_xq()
        if not df_hot.empty:
            result["xueqiu_hot_deal"] = {
                "count": len(df_hot),
                "top15": df_hot.head(15).to_dict('records'),
            }
    except Exception as e:
        result["xueqiu_hot_error"] = str(e)[:80]
    
    # 东财热搜股
    try:
        df_em_hot = ak.stock_hot_rank_detail_realtime_em(symbol="沪深A股")
        if not df_em_hot.empty:
            result["eastmoney_hot"] = {
                "count": len(df_em_hot),
                "top15": df_em_hot.head(15).to_dict('records'),
            }
    except Exception as e:
        result["em_hot_error"] = str(e)[:80]
    
    return result


def get_all():
    """全套宏观数据"""
    return {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "rates": get_interest_rates(),
        "pmi": get_pmi(),
        "news": get_news(),
        "hot": get_hot_keywords(),
    }


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    
    if cmd == "all":
        print(json.dumps(get_all(), ensure_ascii=False, indent=2, default=str))
    elif cmd == "rates":
        print(json.dumps(get_interest_rates(), ensure_ascii=False, indent=2, default=str))
    elif cmd == "pmi":
        print(json.dumps(get_pmi(), ensure_ascii=False, indent=2, default=str))
    elif cmd == "news":
        print(json.dumps(get_news(), ensure_ascii=False, indent=2, default=str))
    elif cmd == "hot":
        print(json.dumps(get_hot_keywords(), ensure_ascii=False, indent=2, default=str))
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
