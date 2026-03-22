#!/usr/bin/env python3
"""跨资产市场快照

当前取数策略：
1) 海外指数、外汇：优先走 FMP MCP 历史接口
2) 商品：继续走 Yahoo Finance
3) 美国国债收益率：继续走 FRED CSV
4) FMP 失败时，回退到 Yahoo Finance（若该品种配置了 Yahoo symbol）

输出字段重点：
- latest_close / latest_value：最新收盘或最新值
- previous_close / previous_value：上一有效观测值
- weekly_change_pct：过去 5 个有效交易日涨跌幅（指数/商品/外汇）
- weekly_change_bp：过去 5 个有效交易日变动（收益率，单位 bp）
"""

from __future__ import annotations

import csv
import io
import json
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36"
}
TIMEOUT = 15
STOOQ_TIMEOUT = 6
MCP_TIMEOUT = 60
MCP_RETRIES = 2
YAHOO_RANGE = "3mo"
YAHOO_INTERVAL = "1d"
YAHOO_BATCH_CHUNK = 2
YAHOO_BATCH_DELAY_SEC = 0.8
YAHOO_DIRECT_DELAY_SEC = 1.2
YAHOO_PRELOAD_ENABLED = False
FMP_LOOKBACK_DAYS = 90
WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
MCPORTER_CONFIG = WORKSPACE_ROOT / "config" / "mcporter.json"
FMP_TOOLSET_BY_TOOL = {
    "fmp.getHistoricalIndexFullChart": "indexes",
    "fmp.getForexHistoricalFullChart": "forex",
    "fmp.getCryptocurrencyHistoricalFullChart": "crypto",
}
ENABLED_FMP_TOOLSETS: set[str] = set()
LAST_YAHOO_DIRECT_TS = 0.0

ASSET_MAP = {
    "global_indexes": [
        {"name": "标普500", "symbol": "^GSPC", "source": "fmp_index", "unit": "points", "yahoo_symbol": "^GSPC", "stooq_symbol": "^spx"},
        {"name": "纳斯达克", "symbol": "^IXIC", "source": "fmp_index", "unit": "points", "yahoo_symbol": "^IXIC", "stooq_symbol": "^ndq"},
        {"name": "道琼斯", "symbol": "^DJI", "source": "fmp_index", "unit": "points", "yahoo_symbol": "^DJI", "stooq_symbol": "^dji"},
        {"name": "恒生指数", "symbol": "^HSI", "source": "fmp_index", "unit": "points", "yahoo_symbol": "^HSI", "stooq_symbol": "^hsi"},
        {"name": "日经225", "symbol": "^N225", "source": "fmp_index", "unit": "points", "yahoo_symbol": "^N225", "stooq_symbol": "^nkx"},
        {"name": "德国DAX", "symbol": "^GDAXI", "source": "investing_html", "unit": "points", "investing_url": "https://www.investing.com/indices/germany-30-historical-data"},
    ],
    "commodities": [
        {"name": "WTI原油", "symbol": "CL=F", "source": "investing_html", "unit": "USD/bbl", "investing_url": "https://www.investing.com/commodities/crude-oil-historical-data"},
        {"name": "黄金", "symbol": "GC=F", "source": "investing_html", "unit": "USD/oz", "investing_url": "https://www.investing.com/commodities/gold-historical-data"},
        {"name": "铜", "symbol": "HG=F", "source": "investing_html", "unit": "USD/lb", "investing_url": "https://www.investing.com/commodities/copper-historical-data"},
    ],
    "fx_rates": [
        {"name": "美元指数", "symbol": "DX-Y.NYB", "source": "investing_html", "unit": "index", "investing_url": "https://www.investing.com/currencies/us-dollar-index-historical-data"},
        {
            "name": "USD/CNH",
            "symbol": "USDCNH",
            "source": "fmp_forex",
            "unit": "rate",
            "fallback_symbol": "USDCNY",
            "fallback_note": "FMP 的 USDCNH 受权限/覆盖限制时，回退到 USDCNY 作为近似代理。",
            "yahoo_symbol": "CNH=X",
            "yahoo_fallback_symbol": "CNY=X",
            "yahoo_fallback_note": "Yahoo 的 CNH 日线历史常不完整，回退到 CNY=X 作为近似代理。",
        },
        {"name": "USD/JPY", "symbol": "USDJPY", "source": "fmp_forex", "unit": "rate", "yahoo_symbol": "JPY=X"},
        {"name": "EUR/USD", "symbol": "EURUSD", "source": "fmp_forex", "unit": "rate", "yahoo_symbol": "EURUSD=X"},
    ],
    "crypto": [
        {"name": "BTC/USD", "symbol": "BTCUSD", "source": "fmp_crypto", "unit": "USD", "yahoo_symbol": "BTC-USD"},
        {"name": "ETH/USD", "symbol": "ETHUSD", "source": "fmp_crypto", "unit": "USD", "yahoo_symbol": "ETH-USD"},
    ],
    "rates": [
        {"name": "美国2Y", "symbol": "DGS2", "source": "fred", "unit": "%"},
        {"name": "美国10Y", "symbol": "DGS10", "source": "fred", "unit": "%"},
        {"name": "美国30Y", "symbol": "DGS30", "source": "fred", "unit": "%"},
    ],
}


def _round(value: Optional[float], digits: int = 4) -> Optional[float]:
    if value is None:
        return None
    try:
        return round(float(value), digits)
    except Exception:
        return None


def _safe_pct_change(current: Optional[float], base: Optional[float]) -> Optional[float]:
    if current is None or base in (None, 0):
        return None
    try:
        return round((float(current) / float(base) - 1) * 100, 4)
    except Exception:
        return None


def _last_valid_points(series: List[Tuple[str, Optional[float]]]) -> List[Tuple[str, float]]:
    out: List[Tuple[str, float]] = []
    for dt, value in series:
        if value is None:
            continue
        try:
            out.append((dt, float(value)))
        except Exception:
            continue
    return out


def _pick_reference(points: List[Tuple[str, float]], lookback: int = 5) -> Optional[Tuple[str, float]]:
    if not points:
        return None
    if len(points) > lookback:
        return points[-(lookback + 1)]
    if len(points) >= 2:
        return points[0]
    return None


def _format_display(name: str, latest: Optional[float], weekly_change: Optional[float], unit: str, change_label: str) -> str:
    if latest is None:
        return f"{name}: 数据缺失"
    change_text = "N/A" if weekly_change is None else f"{weekly_change:+.2f}{change_label}"
    return f"{name}: {latest} {unit} ({change_text}/5个交易日)"


def _unique_keep_order(values: Iterable[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _chunked(values: List[str], size: int) -> Iterable[List[str]]:
    for idx in range(0, len(values), size):
        yield values[idx: idx + size]


def _fmp_date_window() -> Tuple[str, str]:
    end_date = datetime.now(timezone.utc).date()
    start_date = end_date - timedelta(days=FMP_LOOKBACK_DAYS)
    return start_date.isoformat(), end_date.isoformat()


def _get_yahoo_symbols_for_asset(asset: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    if asset.get("source") == "yahoo":
        return asset.get("symbol"), asset.get("fallback_symbol")
    return asset.get("yahoo_symbol"), asset.get("yahoo_fallback_symbol")


def _enable_fmp_toolset(toolset: str) -> None:
    if toolset in ENABLED_FMP_TOOLSETS:
        return
    cmd = [
        "mcporter",
        "call",
        "fmp.enable_toolset",
        "--args",
        json.dumps({"name": toolset}, ensure_ascii=False),
        "--output",
        "json",
        "--config",
        str(MCPORTER_CONFIG),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=MCP_TIMEOUT)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or f"启用 FMP toolset 失败: {toolset}").strip())
    ENABLED_FMP_TOOLSETS.add(toolset)


def call_mcp_tool(server_tool: str, args: Dict[str, Any]) -> Any:
    if not MCPORTER_CONFIG.exists():
        raise RuntimeError(f"mcporter config 不存在: {MCPORTER_CONFIG}")

    toolset = FMP_TOOLSET_BY_TOOL.get(server_tool)
    if toolset:
        _enable_fmp_toolset(toolset)

    cmd = [
        "mcporter",
        "call",
        server_tool,
        "--args",
        json.dumps(args, ensure_ascii=False),
        "--output",
        "json",
        "--config",
        str(MCPORTER_CONFIG),
    ]

    last_error: Optional[Exception] = None
    for attempt in range(1, MCP_RETRIES + 1):
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=MCP_TIMEOUT)
            stdout = (proc.stdout or "").strip()
            stderr = (proc.stderr or "").strip()

            if proc.returncode != 0:
                raise RuntimeError(stderr or stdout or f"mcporter 调用失败: {server_tool}")
            if not stdout:
                raise RuntimeError(f"mcporter 返回空输出: {server_tool}")

            if "Tool " in stdout and " not found" in stdout and toolset:
                _enable_fmp_toolset(toolset)
                raise RuntimeError(f"FMP toolset {toolset} 未就绪，已尝试启用后重试")
            if "status code 402" in stdout:
                raise RuntimeError(stdout)

            try:
                return json.loads(stdout)
            except json.JSONDecodeError as e:
                raise RuntimeError(f"mcporter 返回非 JSON: {server_tool}; {stdout[:300]}") from e
        except Exception as e:
            last_error = e
            if attempt < MCP_RETRIES:
                time.sleep(1.2 * attempt)
                continue
            break

    raise RuntimeError(str(last_error) if last_error else f"mcporter 调用失败: {server_tool}")


def _parse_generic_history(
    requested_symbol: str,
    actual_symbol: str,
    rows: Any,
    source_name: str,
) -> Dict[str, Any]:
    if not isinstance(rows, list):
        return {
            "ok": False,
            "symbol": requested_symbol,
            "resolved_symbol": actual_symbol,
            "source": source_name,
            "error": "历史数据返回格式异常",
        }

    pairs: List[Tuple[str, Optional[float]]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        dt = row.get("date")
        close = row.get("close")
        if not dt:
            continue
        try:
            pairs.append((str(dt), None if close is None else float(close)))
        except Exception:
            pairs.append((str(dt), None))

    pairs.sort(key=lambda x: x[0])
    valid_points = _last_valid_points(pairs)
    if len(valid_points) < 2:
        return {
            "ok": False,
            "symbol": requested_symbol,
            "resolved_symbol": actual_symbol,
            "source": source_name,
            "error": "无法获取足够的有效历史数据",
        }

    latest_date, latest_close = valid_points[-1]
    prev_date, prev_close = valid_points[-2]
    ref = _pick_reference(valid_points, lookback=5)
    weekly_ref_date = ref[0] if ref else None
    weekly_ref_close = ref[1] if ref else None
    weekly_change_pct = _safe_pct_change(latest_close, weekly_ref_close)

    return {
        "ok": True,
        "symbol": requested_symbol,
        "resolved_symbol": actual_symbol,
        "source": source_name,
        "latest_close": _round(latest_close, 4),
        "previous_close": _round(prev_close, 4),
        "weekly_reference_close": _round(weekly_ref_close, 4),
        "weekly_change_pct": weekly_change_pct,
        "as_of": latest_date,
        "previous_as_of": prev_date,
        "weekly_reference_as_of": weekly_ref_date,
        "points_used": len(valid_points),
        "recent_5": [
            {"date": dt, "close": _round(close, 4)} for dt, close in valid_points[-5:]
        ],
        "used_fallback": actual_symbol != requested_symbol,
    }


def fetch_fmp_history(tool_name: str, symbol: str, fallback_symbol: Optional[str] = None) -> Dict[str, Any]:
    start_date, end_date = _fmp_date_window()
    tried: List[str] = []

    for actual_symbol in [symbol, fallback_symbol]:
        if not actual_symbol:
            continue
        tried.append(actual_symbol)
        try:
            rows = call_mcp_tool(tool_name, {"symbol": actual_symbol, "from": start_date, "to": end_date})
            parsed = _parse_generic_history(symbol, actual_symbol, rows, f"FMP MCP {tool_name}")
            parsed["tried_symbols"] = tried.copy()
            if parsed.get("ok"):
                return parsed
        except Exception as e:
            last_error = str(e)
            continue
    return {
        "ok": False,
        "symbol": symbol,
        "source": f"FMP MCP {tool_name}",
        "error": locals().get("last_error", "无法获取足够的有效历史数据"),
        "tried_symbols": tried,
    }


def fetch_stooq_csv_series(symbol: str) -> Dict[str, Any]:
    url = f"https://stooq.com/q/d/l/?s={quote(symbol, safe='^.')}&i=d"
    response = requests.get(url, headers=HEADERS, timeout=STOOQ_TIMEOUT)
    response.raise_for_status()
    text = (response.text or "").strip()
    if not text or text.lower() == "no data":
        return {
            "ok": False,
            "symbol": symbol,
            "resolved_symbol": symbol,
            "source": "Stooq CSV",
            "error": "Stooq CSV 无有效数据",
        }

    reader = csv.DictReader(io.StringIO(text))
    points: List[Tuple[str, Optional[float]]] = []
    for row in reader:
        dt = row.get("Date")
        close = row.get("Close")
        if not dt:
            continue
        try:
            points.append((dt, None if close in (None, "") else float(close)))
        except Exception:
            points.append((dt, None))
    return _parse_generic_history(symbol, symbol, [{"date": dt, "close": close} for dt, close in points], "Stooq CSV")


_MONTH_MAP = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}


def _parse_stooq_date(text: str) -> Optional[str]:
    parts = (text or "").split()
    if len(parts) != 3:
        return None
    day, mon, year = parts
    if mon not in _MONTH_MAP:
        return None
    try:
        return f"{int(year):04d}-{_MONTH_MAP[mon]:02d}-{int(day):02d}"
    except Exception:
        return None


_DATE_RE = re.compile(r"^\d{1,2}\s[A-Z][a-z]{2}\s20\d{2}$")
_INVESTING_DATE_RE = re.compile(r"^[A-Z][a-z]{2}\s\d{1,2},\s20\d{2}$")


def fetch_stooq_html_series(symbol: str) -> Dict[str, Any]:
    url = f"https://stooq.com/q/d/?s={quote(symbol, safe='^.') }"
    response = requests.get(url, headers=HEADERS, timeout=STOOQ_TIMEOUT)
    response.raise_for_status()
    html = response.text

    soup = BeautifulSoup(html, "html.parser")
    history_table = None
    for table in soup.find_all("table"):
        text = " ".join(table.get_text(" ", strip=True).split())
        if "Historical values" in text and "Date" in text and "Open" in text and "Close" in text:
            history_table = table
            break
    if history_table is None:
        raise RuntimeError("Stooq 历史表未找到")

    rows: List[Dict[str, Any]] = []
    for tr in history_table.find_all("tr"):
        cols = [c.get_text(" ", strip=True) for c in tr.find_all("td")]
        if len(cols) < 6:
            continue
        raw_date = cols[1]
        if not _DATE_RE.fullmatch(raw_date):
            continue
        iso_date = _parse_stooq_date(raw_date)
        if not iso_date:
            continue
        try:
            close = float(cols[5].replace(",", ""))
        except Exception:
            continue
        rows.append({
            "date": iso_date,
            "open": cols[2],
            "high": cols[3],
            "low": cols[4],
            "close": close,
        })

    if not rows:
        raise RuntimeError("Stooq 历史表解析失败")

    rows.sort(key=lambda x: x["date"])
    return _parse_generic_history(symbol, symbol, rows, "Stooq HTML historical table")


def fetch_investing_html_series(url: str, symbol: str) -> Dict[str, Any]:
    response = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    rows: List[Dict[str, Any]] = []
    for tr in soup.find_all("tr"):
        parts = [p.strip() for p in tr.get_text(" | ", strip=True).split("|")]
        if len(parts) < 5:
            continue
        raw_date = parts[0]
        if not _INVESTING_DATE_RE.fullmatch(raw_date):
            continue
        try:
            iso_date = datetime.strptime(raw_date, "%b %d, %Y").strftime("%Y-%m-%d")
            close = float(parts[1].replace(",", ""))
        except Exception:
            continue
        rows.append({"date": iso_date, "close": close})

    if not rows:
        return {
            "ok": False,
            "symbol": symbol,
            "resolved_symbol": symbol,
            "source": "Investing.com HTML",
            "error": "Investing 历史表解析失败",
        }

    rows.sort(key=lambda x: x["date"])
    return _parse_generic_history(symbol, symbol, rows, "Investing.com HTML")


def fetch_yahoo_batch(symbols: List[str]) -> Dict[str, Any]:
    unique_symbols = _unique_keep_order(symbols)
    if not unique_symbols:
        return {}

    symbol_param = quote(",".join(unique_symbols), safe="")
    urls = [
        f"https://query2.finance.yahoo.com/v7/finance/spark?symbols={symbol_param}&range={YAHOO_RANGE}&interval={YAHOO_INTERVAL}",
        f"https://query1.finance.yahoo.com/v7/finance/spark?symbols={symbol_param}&range={YAHOO_RANGE}&interval={YAHOO_INTERVAL}",
    ]

    last_error: Optional[Exception] = None
    for url in urls:
        try:
            response = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            response.raise_for_status()
            payload = response.json()
            results = ((payload.get("spark") or {}).get("result")) or []

            mapped: Dict[str, Any] = {}
            for item in results:
                symbol = item.get("symbol")
                responses = item.get("response") or []
                if symbol and responses:
                    mapped[symbol] = responses[0]
            if mapped:
                return mapped
        except Exception as e:
            last_error = e
            continue

    if last_error:
        raise last_error
    return {}


def fetch_yahoo_batch_all(symbols: List[str], chunk_size: int = YAHOO_BATCH_CHUNK) -> Dict[str, Any]:
    payload_by_symbol: Dict[str, Any] = {}
    errors: List[str] = []
    for idx, chunk in enumerate(_chunked(_unique_keep_order(symbols), chunk_size)):
        if idx > 0:
            time.sleep(YAHOO_BATCH_DELAY_SEC)
        try:
            payload_by_symbol.update(fetch_yahoo_batch(chunk))
        except Exception as e:
            errors.append(f"{chunk}: {e}")
    if not payload_by_symbol and errors:
        raise RuntimeError("; ".join(errors))
    return payload_by_symbol


def _respect_yahoo_direct_rate_limit() -> None:
    global LAST_YAHOO_DIRECT_TS
    now = time.time()
    wait = YAHOO_DIRECT_DELAY_SEC - (now - LAST_YAHOO_DIRECT_TS)
    if wait > 0:
        time.sleep(wait)
    LAST_YAHOO_DIRECT_TS = time.time()


def _parse_yahoo_response(requested_symbol: str, actual_symbol: str, block: Dict[str, Any], source_name: str) -> Dict[str, Any]:
    meta = block.get("meta") or {}
    timestamps = block.get("timestamp") or []
    quote_data = ((block.get("indicators") or {}).get("quote") or [{}])[0]
    closes = quote_data.get("close") or []

    pairs: List[Tuple[str, Optional[float]]] = []
    for ts, close in zip(timestamps, closes):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
        pairs.append((dt, None if close is None else float(close)))

    valid_points = _last_valid_points(pairs)
    if len(valid_points) < 2:
        return {
            "ok": False,
            "symbol": requested_symbol,
            "resolved_symbol": actual_symbol,
            "source": source_name,
            "error": "无法获取足够的有效历史数据",
        }

    latest_date, latest_close = valid_points[-1]
    prev_date, prev_close = valid_points[-2]
    ref = _pick_reference(valid_points, lookback=5)
    weekly_ref_date = ref[0] if ref else None
    weekly_ref_close = ref[1] if ref else None
    weekly_change_pct = _safe_pct_change(latest_close, weekly_ref_close)

    return {
        "ok": True,
        "symbol": requested_symbol,
        "resolved_symbol": actual_symbol,
        "source": source_name,
        "currency": meta.get("currency"),
        "exchange": meta.get("fullExchangeName") or meta.get("exchangeName"),
        "latest_close": _round(latest_close, 4),
        "previous_close": _round(prev_close, 4),
        "weekly_reference_close": _round(weekly_ref_close, 4),
        "weekly_change_pct": weekly_change_pct,
        "as_of": latest_date,
        "previous_as_of": prev_date,
        "weekly_reference_as_of": weekly_ref_date,
        "regular_market_time": datetime.fromtimestamp(meta.get("regularMarketTime", 0), tz=timezone.utc).isoformat().replace("+00:00", "Z") if meta.get("regularMarketTime") else None,
        "points_used": len(valid_points),
        "recent_5": [
            {"date": dt, "close": _round(close, 4)} for dt, close in valid_points[-5:]
        ],
        "used_fallback": actual_symbol != requested_symbol,
    }


def fetch_yahoo_series_from_batch(payload_by_symbol: Dict[str, Any], symbol: str, fallback_symbol: Optional[str] = None) -> Dict[str, Any]:
    tried = []
    for actual_symbol in [symbol, fallback_symbol]:
        if not actual_symbol:
            continue
        tried.append(actual_symbol)
        block = payload_by_symbol.get(actual_symbol)
        if not block:
            continue
        parsed = _parse_yahoo_response(symbol, actual_symbol, block, "Yahoo Finance Spark API (batched)")
        parsed["tried_symbols"] = tried.copy()
        if parsed.get("ok"):
            return parsed

    return {
        "ok": False,
        "symbol": symbol,
        "source": "Yahoo Finance Spark API (batched)",
        "error": "无法获取足够的有效历史数据",
        "tried_symbols": tried,
    }


def fetch_yahoo_direct_series(symbol: str, fallback_symbol: Optional[str] = None) -> Dict[str, Any]:
    tried = []
    last_error: Optional[str] = None
    for actual_symbol in [symbol, fallback_symbol]:
        if not actual_symbol:
            continue
        tried.append(actual_symbol)
        urls = [
            f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(actual_symbol, safe='')}?range={YAHOO_RANGE}&interval={YAHOO_INTERVAL}",
            f"https://query2.finance.yahoo.com/v8/finance/chart/{quote(actual_symbol, safe='')}?range={YAHOO_RANGE}&interval={YAHOO_INTERVAL}",
        ]
        for url in urls:
            try:
                _respect_yahoo_direct_rate_limit()
                response = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
                if response.status_code == 429:
                    last_error = "Yahoo Finance 429 Too Many Requests"
                    time.sleep(2.5)
                    continue
                response.raise_for_status()
                payload = response.json()
                result = (payload.get("chart") or {}).get("result") or []
                error = (payload.get("chart") or {}).get("error")
                if error or not result:
                    last_error = str(error) if error else "Yahoo Finance result empty"
                    continue
                parsed = _parse_yahoo_response(symbol, actual_symbol, result[0], "Yahoo Finance Chart API")
                parsed["tried_symbols"] = tried.copy()
                if parsed.get("ok"):
                    return parsed
                last_error = parsed.get("error") or last_error
            except Exception as e:
                last_error = str(e)
                continue

    return {
        "ok": False,
        "symbol": symbol,
        "source": "Yahoo Finance Chart API",
        "error": last_error or "无法获取足够的有效历史数据",
        "tried_symbols": tried,
    }


FRED_TIMEOUT = 10  # seconds — if FRED is slow, fall back to Treasury.gov

# Mapping from FRED series IDs to Treasury.gov CSV column headers
_TREASURY_COL_MAP: Dict[str, str] = {
    "DGS2": "2 Yr",
    "DGS10": "10 Yr",
    "DGS30": "30 Yr",
}

TREASURY_GOV_CSV_URL = (
    "https://home.treasury.gov/resource-center/data-chart-center/"
    "interest-rates/daily-treasury-rates.csv/all/{year}"
    "?type=daily_treasury_yield_curve&field_tdr_date_value={year}"
    "&page&_format=csv"
)


def _fetch_treasury_gov_fallback(series_id: str) -> Dict[str, Any]:
    """Fetch yield data directly from Treasury.gov CSV as a FRED fallback."""
    col_name = _TREASURY_COL_MAP.get(series_id)
    if col_name is None:
        return {
            "ok": False,
            "symbol": series_id,
            "source": "Treasury.gov CSV",
            "error": f"No Treasury.gov column mapping for {series_id}",
        }

    year = datetime.now(timezone.utc).year
    url = TREASURY_GOV_CSV_URL.format(year=year)
    response = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    response.raise_for_status()

    reader = csv.DictReader(io.StringIO(response.text))
    points: List[Tuple[str, float]] = []
    for row in reader:
        raw = row.get(col_name)
        if raw in (None, "", "N/A"):
            continue
        # Treasury.gov dates are MM/DD/YYYY — normalise to YYYY-MM-DD
        raw_date = row.get("Date", "")
        try:
            parsed_date = datetime.strptime(raw_date, "%m/%d/%Y").strftime("%Y-%m-%d")
        except Exception:
            parsed_date = raw_date
        try:
            points.append((parsed_date, float(raw)))
        except Exception:
            continue

    # Sort chronologically (Treasury.gov may list newest first)
    points.sort(key=lambda p: p[0])

    if len(points) < 2:
        return {
            "ok": False,
            "symbol": series_id,
            "source": "Treasury.gov CSV",
            "error": "有效数据点不足",
        }

    return _build_fred_result(series_id, points, source="Treasury.gov CSV")


def _build_fred_result(series_id: str, points: List[Tuple[str, float]],
                       source: str = "FRED CSV") -> Dict[str, Any]:
    """Shared result builder for FRED-style yield data."""
    if len(points) < 2:
        return {
            "ok": False,
            "symbol": series_id,
            "source": source,
            "error": "有效数据点不足",
        }

    latest_date, latest_value = points[-1]
    prev_date, prev_value = points[-2]
    ref = _pick_reference(points, lookback=5)
    weekly_ref_date = ref[0] if ref else None
    weekly_ref_value = ref[1] if ref else None
    weekly_change_bp = None if weekly_ref_value is None else round((latest_value - weekly_ref_value) * 100, 2)
    weekly_change_pct = _safe_pct_change(latest_value, weekly_ref_value)

    return {
        "ok": True,
        "symbol": series_id,
        "source": source,
        "latest_value": _round(latest_value, 4),
        "previous_value": _round(prev_value, 4),
        "weekly_reference_value": _round(weekly_ref_value, 4),
        "weekly_change_bp": weekly_change_bp,
        "weekly_change_pct": weekly_change_pct,
        "as_of": latest_date,
        "previous_as_of": prev_date,
        "weekly_reference_as_of": weekly_ref_date,
        "points_used": len(points),
        "recent_5": [
            {"date": dt, "value": _round(value, 4)} for dt, value in points[-5:]
        ],
    }


def fetch_fred_series(series_id: str) -> Dict[str, Any]:
    end_date = datetime.now(timezone.utc).date()
    start_date = end_date - timedelta(days=90)
    url = (
        "https://fred.stlouisfed.org/graph/fredgraph.csv"
        f"?id={quote(series_id, safe='')}&cosd={start_date.isoformat()}&coed={end_date.isoformat()}"
    )

    # Try FRED first with a tight timeout; fall back to Treasury.gov on failure
    try:
        response = requests.get(url, headers=HEADERS, timeout=FRED_TIMEOUT)
        response.raise_for_status()
    except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
        print(f"  [cross_asset] FRED timeout/connection error for {series_id}: {exc}  — trying Treasury.gov fallback")
        return _fetch_treasury_gov_fallback(series_id)

    reader = csv.DictReader(io.StringIO(response.text))
    points: List[Tuple[str, float]] = []
    for row in reader:
        raw = row.get(series_id)
        if raw in (None, "", "."):
            continue
        try:
            points.append((row["observation_date"], float(raw)))
        except Exception:
            continue

    if len(points) < 2:
        # FRED returned data but too few points — try Treasury.gov
        print(f"  [cross_asset] FRED returned < 2 points for {series_id} — trying Treasury.gov fallback")
        return _fetch_treasury_gov_fallback(series_id)

    return _build_fred_result(series_id, points, source="FRED CSV")


def _collect_yahoo_symbols() -> List[str]:
    symbols: List[str] = []
    for assets in ASSET_MAP.values():
        for asset in assets:
            primary, fallback = _get_yahoo_symbols_for_asset(asset)
            if primary:
                symbols.append(primary)
            if fallback:
                symbols.append(fallback)
    return _unique_keep_order(symbols)


def _fetch_with_yahoo_fallback(asset: Dict[str, Any], yahoo_payload_by_symbol: Dict[str, Any], yahoo_batch_error: Optional[str]) -> Dict[str, Any]:
    yahoo_symbol, yahoo_fallback_symbol = _get_yahoo_symbols_for_asset(asset)
    if not yahoo_symbol:
        return {
            "ok": False,
            "symbol": asset["symbol"],
            "source": "Yahoo fallback unavailable",
            "error": "未配置 Yahoo fallback symbol",
        }

    item = fetch_yahoo_series_from_batch(yahoo_payload_by_symbol, yahoo_symbol, yahoo_fallback_symbol)
    if not item.get("ok"):
        item = fetch_yahoo_direct_series(yahoo_symbol, yahoo_fallback_symbol)
        if yahoo_batch_error and item.get("error"):
            item["error"] = f"批量抓取失败: {yahoo_batch_error}; 单品种回退也失败: {item['error']}"
    item["fallback_from_primary_source"] = True
    return item


def build_snapshot() -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "generated_at": datetime.now().isoformat(),
        "sources": {
            "market_data": "FMP MCP（indexes/forex/crypto） + Investing.com HTML（DAX / DXY / commodities） + Yahoo Finance（fallback）",
            "rates": "FRED CSV (public)",
        },
        "sections": {},
        "summary": {},
        "errors": [],
    }

    total_ok = 0
    total_assets = 0

    yahoo_payload_by_symbol: Dict[str, Any] = {}
    yahoo_batch_error: Optional[str] = None
    if YAHOO_PRELOAD_ENABLED:
        try:
            yahoo_payload_by_symbol = fetch_yahoo_batch_all(_collect_yahoo_symbols())
        except Exception as e:
            yahoo_batch_error = str(e)
            result["errors"].append({
                "section": "market_data",
                "name": "Yahoo batch preload",
                "error": yahoo_batch_error,
            })

    for section, assets in ASSET_MAP.items():
        section_rows: List[Dict[str, Any]] = []
        for asset in assets:
            total_assets += 1
            try:
                if asset["source"] == "yahoo":
                    item = fetch_yahoo_series_from_batch(
                        yahoo_payload_by_symbol,
                        asset["symbol"],
                        asset.get("fallback_symbol"),
                    )
                    if not item.get("ok"):
                        item = fetch_yahoo_direct_series(asset["symbol"], asset.get("fallback_symbol"))
                        if yahoo_batch_error and item.get("error"):
                            item["error"] = f"批量抓取失败: {yahoo_batch_error}; 单品种回退也失败: {item['error']}"

                    row: Dict[str, Any] = {
                        "name": asset["name"],
                        "symbol": asset["symbol"],
                        "unit": asset["unit"],
                        **item,
                    }
                    if row.get("ok"):
                        total_ok += 1
                        if row.get("used_fallback") and asset.get("fallback_note"):
                            row["note"] = asset["fallback_note"]
                        row["display"] = _format_display(
                            asset["name"], row.get("latest_close"), row.get("weekly_change_pct"), asset["unit"], "%"
                        )

                elif asset["source"] == "fmp_index":
                    item = fetch_fmp_history("fmp.getHistoricalIndexFullChart", asset["symbol"], asset.get("fallback_symbol"))
                    if not item.get("ok") and asset.get("stooq_symbol"):
                        try:
                            stooq_item = fetch_stooq_csv_series(asset["stooq_symbol"])
                            if stooq_item.get("ok"):
                                item = stooq_item
                                item["resolved_symbol"] = asset["stooq_symbol"]
                                item["fallback_from_primary_source"] = True
                        except Exception as stooq_err:
                            item["stooq_fallback_error"] = str(stooq_err)
                    if not item.get("ok"):
                        yahoo_item = _fetch_with_yahoo_fallback(asset, yahoo_payload_by_symbol, yahoo_batch_error)
                        if yahoo_item.get("ok"):
                            item = yahoo_item
                        else:
                            item["yahoo_fallback_error"] = yahoo_item.get("error")

                    row = {
                        "name": asset["name"],
                        "symbol": asset["symbol"],
                        "unit": asset["unit"],
                        **item,
                    }
                    if row.get("ok"):
                        total_ok += 1
                        row["display"] = _format_display(
                            asset["name"], row.get("latest_close"), row.get("weekly_change_pct"), asset["unit"], "%"
                        )

                elif asset["source"] == "fmp_forex":
                    item = fetch_fmp_history("fmp.getForexHistoricalFullChart", asset["symbol"], asset.get("fallback_symbol"))
                    if not item.get("ok"):
                        yahoo_item = _fetch_with_yahoo_fallback(asset, yahoo_payload_by_symbol, yahoo_batch_error)
                        if yahoo_item.get("ok"):
                            item = yahoo_item
                            if asset.get("yahoo_fallback_note") and item.get("used_fallback"):
                                item["note"] = asset["yahoo_fallback_note"]
                        else:
                            item["yahoo_fallback_error"] = yahoo_item.get("error")

                    row = {
                        "name": asset["name"],
                        "symbol": asset["symbol"],
                        "unit": asset["unit"],
                        **item,
                    }
                    if row.get("ok"):
                        total_ok += 1
                        if row.get("used_fallback") and asset.get("fallback_note") and str(row.get("resolved_symbol")) != str(row.get("symbol")):
                            row["note"] = asset["fallback_note"]
                        row["display"] = _format_display(
                            asset["name"], row.get("latest_close"), row.get("weekly_change_pct"), asset["unit"], "%"
                        )

                elif asset["source"] == "fmp_crypto":
                    item = fetch_fmp_history("fmp.getCryptocurrencyHistoricalFullChart", asset["symbol"], asset.get("fallback_symbol"))
                    if not item.get("ok"):
                        yahoo_item = _fetch_with_yahoo_fallback(asset, yahoo_payload_by_symbol, yahoo_batch_error)
                        if yahoo_item.get("ok"):
                            item = yahoo_item
                        else:
                            item["yahoo_fallback_error"] = yahoo_item.get("error")

                    row = {
                        "name": asset["name"],
                        "symbol": asset["symbol"],
                        "unit": asset["unit"],
                        **item,
                    }
                    if row.get("ok"):
                        total_ok += 1
                        row["display"] = _format_display(
                            asset["name"], row.get("latest_close"), row.get("weekly_change_pct"), asset["unit"], "%"
                        )

                elif asset["source"] == "investing_html":
                    try:
                        item = fetch_investing_html_series(asset["investing_url"], asset["symbol"])
                    except Exception as investing_err:
                        item = {
                            "ok": False,
                            "symbol": asset["symbol"],
                            "source": "Investing.com HTML",
                            "error": str(investing_err),
                        }
                    row = {
                        "name": asset["name"],
                        "symbol": asset["symbol"],
                        "unit": asset["unit"],
                        **item,
                    }
                    if row.get("ok"):
                        total_ok += 1
                        if asset.get("note"):
                            row.setdefault("note", asset.get("note"))
                        row["display"] = _format_display(
                            asset["name"], row.get("latest_close"), row.get("weekly_change_pct"), asset["unit"], "%"
                        )

                elif asset["source"] == "stooq_html":
                    try:
                        item = fetch_stooq_html_series(asset["stooq_symbol"])
                    except Exception as stooq_err:
                        item = {
                            "ok": False,
                            "symbol": asset["symbol"],
                            "source": "Stooq HTML",
                            "error": str(stooq_err),
                        }
                    if not item.get("ok"):
                        yahoo_item = _fetch_with_yahoo_fallback(asset, yahoo_payload_by_symbol, yahoo_batch_error)
                        if yahoo_item.get("ok"):
                            item = yahoo_item
                        else:
                            item["yahoo_fallback_error"] = yahoo_item.get("error")
                    row = {
                        "name": asset["name"],
                        "symbol": asset["symbol"],
                        "unit": asset["unit"],
                        **item,
                    }
                    if row.get("ok"):
                        total_ok += 1
                        if asset.get("note"):
                            row.setdefault("note", asset.get("note"))
                        row["display"] = _format_display(
                            asset["name"], row.get("latest_close"), row.get("weekly_change_pct"), asset["unit"], "%"
                        )

                elif asset["source"] == "stooq_csv":
                    try:
                        item = fetch_stooq_csv_series(asset["stooq_symbol"])
                    except Exception as stooq_err:
                        item = {
                            "ok": False,
                            "symbol": asset["symbol"],
                            "source": "Stooq CSV",
                            "error": str(stooq_err),
                        }
                    if not item.get("ok"):
                        yahoo_item = _fetch_with_yahoo_fallback(asset, yahoo_payload_by_symbol, yahoo_batch_error)
                        if yahoo_item.get("ok"):
                            item = yahoo_item
                        else:
                            item["yahoo_fallback_error"] = yahoo_item.get("error")
                    row = {
                        "name": asset["name"],
                        "symbol": asset["symbol"],
                        "unit": asset["unit"],
                        **item,
                    }
                    if row.get("ok"):
                        total_ok += 1
                        row["display"] = _format_display(
                            asset["name"], row.get("latest_close"), row.get("weekly_change_pct"), asset["unit"], "%"
                        )

                else:
                    item = fetch_fred_series(asset["symbol"])
                    row = {
                        "name": asset["name"],
                        "symbol": asset["symbol"],
                        "unit": asset["unit"],
                        **item,
                    }
                    if row.get("ok"):
                        total_ok += 1
                        if asset.get("note"):
                            row.setdefault("note", asset.get("note"))
                        weekly_bp = row.get("weekly_change_bp")
                        bp_text = "N/A" if weekly_bp is None else f"{weekly_bp:+.2f}bp"
                        latest_value = row.get("latest_value")
                        row["display"] = (
                            f"{asset['name']}: {latest_value} {asset['unit']} ({bp_text}/5个交易日)"
                            if latest_value is not None
                            else f"{asset['name']}: 数据缺失"
                        )

                if not row.get("ok"):
                    result["errors"].append({"section": section, "name": asset["name"], "error": row.get("error")})
                section_rows.append(row)
            except Exception as e:
                row = {
                    "name": asset["name"],
                    "symbol": asset["symbol"],
                    "unit": asset["unit"],
                    "ok": False,
                    "error": str(e),
                }
                result["errors"].append({"section": section, "name": asset["name"], "error": str(e)})
                section_rows.append(row)

        result["sections"][section] = section_rows

    result["summary"] = {
        "coverage": f"{total_ok}/{total_assets}",
        "coverage_ratio": round(total_ok / total_assets, 4) if total_assets else None,
        "global_indexes_ok": sum(1 for x in result["sections"].get("global_indexes", []) if x.get("ok")),
        "commodities_ok": sum(1 for x in result["sections"].get("commodities", []) if x.get("ok")),
        "fx_rates_ok": sum(1 for x in result["sections"].get("fx_rates", []) if x.get("ok")),
        "crypto_ok": sum(1 for x in result["sections"].get("crypto", []) if x.get("ok")),
        "rates_ok": sum(1 for x in result["sections"].get("rates", []) if x.get("ok")),
    }

    return result


if __name__ == '__main__':
    print(json.dumps(build_snapshot(), ensure_ascii=False, indent=2))
