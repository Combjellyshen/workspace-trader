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
import concurrent.futures
from datetime import datetime, timedelta
from pathlib import Path
import warnings
warnings.filterwarnings('ignore')

# 确保 IPv4 优先（common.py 的 monkey-patch）
_HERE = Path(__file__).resolve().parents[2]
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
import scripts.utils.common  # noqa: F401, E402 — activates IPv4 preference


def _ak_call(fn, timeout=20):
    """Run an akshare function with timeout protection"""
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = executor.submit(fn)
    try:
        return future.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        executor.shutdown(wait=False, cancel_futures=True)
        raise

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
        df = _ak_call(lambda: ak.macro_bank_china_interest_rate())
        if not df.empty:
            latest = df.iloc[-1]
            result["china_bank_rate"] = {
                "date": str(latest.iloc[0]),
                "rate": str(latest.iloc[1]) if len(latest) > 1 else None,
                "recent": df.tail(3).to_dict('records'),
            }
    except (Exception, TimeoutError) as e:
        result["china_bank_rate_error"] = "timeout" if isinstance(e, (TimeoutError, concurrent.futures.TimeoutError)) else str(e)[:80]
    
    # PMI
    try:
        df_pmi = _ak_call(lambda: ak.index_pmi_man_cx())
        if not df_pmi.empty:
            result["caixin_mfg_pmi"] = {
                "latest": df_pmi.tail(3).to_dict('records')
            }
    except (Exception, TimeoutError) as e:
        result["pmi_error"] = "timeout" if isinstance(e, (TimeoutError, concurrent.futures.TimeoutError)) else str(e)[:80]
    
    # 美联储利率
    try:
        df_fed = _ak_call(lambda: ak.macro_bank_usa_interest_rate())
        if not df_fed.empty:
            latest = df_fed.iloc[-1]
            result["fed_rate"] = {
                "date": str(latest.iloc[0]),
                "rate": str(latest.iloc[1]) if len(latest) > 1 else None,
                "recent": df_fed.tail(3).to_dict('records'),
            }
    except (Exception, TimeoutError) as e:
        result["fed_rate_error"] = "timeout" if isinstance(e, (TimeoutError, concurrent.futures.TimeoutError)) else str(e)[:80]
    
    return result


def get_pmi():
    """PMI数据（财新制造业/服务业）"""
    result = {}
    
    try:
        df_mfg = _ak_call(lambda: ak.index_pmi_man_cx())
        if not df_mfg.empty:
            result["caixin_manufacturing"] = {
                "latest_date": str(df_mfg.iloc[-1].iloc[0]),
                "latest_value": str(df_mfg.iloc[-1].iloc[1]),
                "trend": "扩张" if float(str(df_mfg.iloc[-1].iloc[1]).replace(",","")) > 50 else "收缩",
                "recent_3m": df_mfg.tail(3).to_dict('records'),
            }
    except (Exception, TimeoutError) as e:
        result["mfg_pmi_error"] = "timeout" if isinstance(e, (TimeoutError, concurrent.futures.TimeoutError)) else str(e)[:80]
    
    try:
        df_ser = _ak_call(lambda: ak.index_pmi_ser_cx())
        if not df_ser.empty:
            result["caixin_services"] = {
                "latest_date": str(df_ser.iloc[-1].iloc[0]),
                "latest_value": str(df_ser.iloc[-1].iloc[1]),
                "trend": "扩张" if float(str(df_ser.iloc[-1].iloc[1]).replace(",","")) > 50 else "收缩",
                "recent_3m": df_ser.tail(3).to_dict('records'),
            }
    except (Exception, TimeoutError) as e:
        result["ser_pmi_error"] = "timeout" if isinstance(e, (TimeoutError, concurrent.futures.TimeoutError)) else str(e)[:80]
    
    try:
        df_com = _ak_call(lambda: ak.index_pmi_com_cx())
        if not df_com.empty:
            result["caixin_composite"] = {
                "latest_date": str(df_com.iloc[-1].iloc[0]),
                "latest_value": str(df_com.iloc[-1].iloc[1]),
                "recent_3m": df_com.tail(3).to_dict('records'),
            }
    except (Exception, TimeoutError) as e:
        result["com_pmi_error"] = "timeout" if isinstance(e, (TimeoutError, concurrent.futures.TimeoutError)) else str(e)[:80]
    
    return result


def get_news():
    """国内财经要闻"""
    import json as _json
    import urllib.request
    result = {}
    today = datetime.now().strftime("%Y%m%d")

    # 央视新闻联播 — 直接调用 CNTV JSON API（akshare 的 news_cctv 会挂起）
    try:
        url = ('https://api.cntv.cn/NewVideo/getVideoListByColumn?'
               'id=TOPC1451528971114112&n=20&sort=desc&p=1&mode=0&serviceId=tvcctv')
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        raw = urllib.request.urlopen(req, timeout=8).read().decode()
        data = _json.loads(raw)
        videos = data.get('data', {}).get('list', [])
        if videos:
            headlines = []
            for v in videos[:15]:
                item = {'title': v.get('title', ''), 'time': v.get('time', '')}
                if v.get('brief'):
                    item['brief'] = v['brief'][:200]
                headlines.append(item)
            result["cctv_news"] = {
                "date": videos[0].get('time', '')[:10].replace('-', ''),
                "count": len(headlines),
                "headlines": headlines,
            }
    except Exception as e:
        result["cctv_error"] = str(e)[:80]

    # 百度财经要闻
    try:
        df_baidu = _ak_call(lambda: ak.news_economic_baidu(date=today))
        if not df_baidu.empty:
            result["baidu_economic_news"] = {
                "count": len(df_baidu),
                "headlines": df_baidu.head(15).to_dict('records'),
            }
    except (Exception, TimeoutError) as e:
        result["baidu_news_error"] = "timeout" if isinstance(e, (TimeoutError, concurrent.futures.TimeoutError)) else str(e)[:80]

    return result


def get_hot_keywords():
    """市场热点（百度热搜）"""
    result = {}
    today = datetime.now().strftime("%Y%m%d")
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")

    # 百度热搜股（替代已不可用的雪球/东财热搜接口）
    for date in [today, yesterday]:
        for time_period in ["全天", "上午"]:
            try:
                df = _ak_call(lambda d=date, t=time_period: ak.stock_hot_search_baidu(
                    symbol="A股", date=d, time=t))
                if hasattr(df, 'empty') and not df.empty:
                    result["baidu_hot_stocks"] = {
                        "date": date,
                        "count": len(df),
                        "top15": df.head(15).to_dict('records'),
                    }
                    break
            except (Exception, TimeoutError):
                continue
        if "baidu_hot_stocks" in result:
            break

    return result


def get_global_markets():
    """全球关键指标：美元指数(DXY)、VIX、原油(WTI/Brent)等"""
    result = {}

    # index_global_spot_em 一次拉全球指数实时行情，从中筛选关键标的
    try:
        df = _ak_call(lambda: ak.index_global_spot_em(), timeout=15)
        if not df.empty:
            targets = {
                "美元指数": "DXY",
                "道琼斯": "DJIA",
                "标普500": "SPX",
                "纳斯达克": "NASDAQ",
                "恒生指数": "HSI",
                "日经225": "NIKKEI",
                "富时A50": "A50",
            }
            for cn_name, label in targets.items():
                row = df[df["名称"].str.contains(cn_name, na=False)]
                if not row.empty:
                    r = row.iloc[0]
                    result[label] = {
                        "name": str(r.get("名称", "")),
                        "price": str(r.get("最新价", "")),
                        "change_pct": str(r.get("涨跌幅", "")),
                        "change_amt": str(r.get("涨跌额", "")),
                    }
    except (Exception, TimeoutError) as e:
        result["global_index_error"] = "timeout" if isinstance(e, (TimeoutError, concurrent.futures.TimeoutError)) else str(e)[:80]

    # 中国波动率指数 (iVIX / QVIX 50ETF)
    try:
        df_vix = _ak_call(lambda: ak.index_option_50etf_qvix(), timeout=15)
        if not df_vix.empty:
            latest = df_vix.iloc[-1]
            result["CN_VIX"] = {
                "name": "50ETF QVIX",
                "date": str(latest.iloc[0]),
                "value": str(latest.iloc[1]) if len(latest) > 1 else None,
                "recent_5d": df_vix.tail(5).to_dict("records"),
            }
    except (Exception, TimeoutError) as e:
        result["cn_vix_error"] = "timeout" if isinstance(e, (TimeoutError, concurrent.futures.TimeoutError)) else str(e)[:80]

    # 原油 — 用 energy_oil_hist 拿 WTI/Brent 历史近几日
    try:
        df_oil = _ak_call(lambda: ak.energy_oil_hist(), timeout=15)
        if not df_oil.empty:
            result["OIL"] = {
                "recent": df_oil.tail(5).to_dict("records"),
            }
    except (Exception, TimeoutError) as e:
        result["oil_error"] = "timeout" if isinstance(e, (TimeoutError, concurrent.futures.TimeoutError)) else str(e)[:80]

    return result


def get_fmp_data():
    """FMP 数据：美股板块表现、涨跌榜、财报日历"""
    import subprocess
    MCPORTER_CONFIG = str(Path(__file__).resolve().parents[2] / "config" / "mcporter.json")

    def _mcporter_call(tool, **kwargs):
        cmd = ["mcporter", "call", f"fmp.{tool}", "--output", "json",
               "--config", MCPORTER_CONFIG]
        for k, v in kwargs.items():
            cmd.append(f"{k}={v}")
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        if proc.returncode != 0 or not proc.stdout.strip():
            return None
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError:
            return None

    result = {}

    # 美股板块表现
    try:
        data = _mcporter_call("get_sector_performance")
        if data and isinstance(data, list):
            result["us_sectors"] = [
                {"sector": s.get("sector", ""), "change": s.get("averageChange", 0)}
                for s in data[:15]
            ]
    except Exception as e:
        result["us_sectors_error"] = str(e)[:80]

    # 涨幅榜 top 5
    try:
        data = _mcporter_call("get_market_gainers")
        if data and isinstance(data, dict) and data.get("filePath"):
            raw = Path(data["filePath"]).read_text(encoding="utf-8")
            items = json.loads(raw)
            result["us_gainers"] = [
                {"symbol": i.get("symbol"), "name": i.get("name", "")[:20],
                 "change_pct": i.get("changesPercentage", 0), "price": i.get("price", 0)}
                for i in (items if isinstance(items, list) else [])[:5]
            ]
        elif data and isinstance(data, list):
            result["us_gainers"] = [
                {"symbol": i.get("symbol"), "change_pct": i.get("changesPercentage", 0)}
                for i in data[:5]
            ]
    except Exception as e:
        result["us_gainers_error"] = str(e)[:80]

    # 跌幅榜 top 5
    try:
        data = _mcporter_call("get_market_losers")
        if data and isinstance(data, dict) and data.get("filePath"):
            raw = Path(data["filePath"]).read_text(encoding="utf-8")
            items = json.loads(raw)
            result["us_losers"] = [
                {"symbol": i.get("symbol"), "name": i.get("name", "")[:20],
                 "change_pct": i.get("changesPercentage", 0), "price": i.get("price", 0)}
                for i in (items if isinstance(items, list) else [])[:5]
            ]
        elif data and isinstance(data, list):
            result["us_losers"] = [
                {"symbol": i.get("symbol"), "change_pct": i.get("changesPercentage", 0)}
                for i in data[:5]
            ]
    except Exception as e:
        result["us_losers_error"] = str(e)[:80]

    # 最活跃 top 5
    try:
        data = _mcporter_call("get_most_active")
        if data and isinstance(data, dict) and data.get("filePath"):
            raw = Path(data["filePath"]).read_text(encoding="utf-8")
            items = json.loads(raw)
            result["us_most_active"] = [
                {"symbol": i.get("symbol"), "name": i.get("name", "")[:20],
                 "volume": i.get("volume", 0), "change_pct": i.get("changesPercentage", 0)}
                for i in (items if isinstance(items, list) else [])[:5]
            ]
        elif data and isinstance(data, list):
            result["us_most_active"] = [
                {"symbol": i.get("symbol"), "volume": i.get("volume", 0)}
                for i in data[:5]
            ]
    except Exception as e:
        result["us_most_active_error"] = str(e)[:80]

    # 下周财报日历
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        end = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")
        data = _mcporter_call("get_earnings_calendar", **{"from": today, "to": end})
        if data and isinstance(data, list):
            result["upcoming_earnings"] = [
                {"date": e.get("date"), "symbol": e.get("symbol"),
                 "eps_est": e.get("epsEstimated"), "rev_est": e.get("revenueEstimated")}
                for e in data[:15]
            ]
    except Exception as e:
        result["earnings_error"] = str(e)[:80]

    return result


def get_all():
    """全套宏观数据（6 个 section 并行采集）"""
    sections = {
        "rates": get_interest_rates,
        "pmi": get_pmi,
        "news": get_news,
        "hot": get_hot_keywords,
        "global_markets": get_global_markets,
        "fmp": get_fmp_data,
    }
    results = {"timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        future_map = {pool.submit(fn): name for name, fn in sections.items()}
        for future in concurrent.futures.as_completed(future_map):
            name = future_map[future]
            try:
                results[name] = future.result()
            except Exception as e:
                results[name] = {f"{name}_error": str(e)[:120]}

    return results


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
    elif cmd == "global":
        print(json.dumps(get_global_markets(), ensure_ascii=False, indent=2, default=str))
    elif cmd == "fmp":
        print(json.dumps(get_fmp_data(), ensure_ascii=False, indent=2, default=str))
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
