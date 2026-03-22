#!/usr/bin/env python3
"""
财经消息面 RSS 聚合器 v2
仅保留已验证可用的源，带超时保护
"""
import feedparser
import json
import sys
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

# ============================================================
# 已验证可用的 RSS 源
# ============================================================
FEEDS = {
    # === 国内（via rssforever.com） ===
    "财联社快讯": "https://rsshub.rssforever.com/cls/telegraph",
    "华尔街见闻-全球": "https://rsshub.rssforever.com/wallstreetcn/news/global",
    "华尔街见闻-A股": "https://rsshub.rssforever.com/wallstreetcn/news/shares",
    "第一财经": "https://rsshub.rssforever.com/yicai/news",

    # === 国内（替代中文源 via RSSHub） ===
    "东方财富-要闻": "https://rsshub.rssforever.com/eastmoney/report/important",
    "金十数据": "https://rsshub.rssforever.com/jin10/flash",
    "格隆汇": "https://rsshub.rssforever.com/gelonghui/live",

    # === 国际（直连） ===
    "CNBC": "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    "MarketWatch": "https://feeds.marketwatch.com/marketwatch/topstories",
    "ZeroHedge": "https://feeds.feedburner.com/zerohedge/feed",
    "CoinDesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "FT-Markets": "https://www.ft.com/markets?format=rss",
    "Yahoo-Finance": "https://finance.yahoo.com/news/rssindex",
    "Bloomberg-Markets": "https://feeds.bloomberg.com/markets/news.rss",

    # === 日本/亚太 ===
    "Nikkei-Asia": "https://asia.nikkei.com/rss/feed.rss",
    "SCMP-Economy": "https://www.scmp.com/rss/5/feed",

    # === 欧洲/宏观 ===
    "ECB-Press": "https://www.ecb.europa.eu/rss/press.html",
    "BBC-Business": "https://feeds.bbci.co.uk/news/business/rss.xml",

    # === 逆向/宏观研究 ===
    "Mises-Wire": "https://mises.org/wire.xml",
    "Naked-Capitalism": "https://www.nakedcapitalism.com/feed",

    # === 加密/DeFi ===
    "TheBlock": "https://www.theblock.co/rss.xml",

    # === 大宗/能源 ===
    "OilPrice": "https://oilprice.com/rss/main",
    "Investing-Commodities": "https://www.investing.com/rss/news_14.rss",
    "WorldOilNews": "https://www.worldoil.com/rss/breaking-news",
}

# 按类别分组
DOMESTIC_SOURCES = ["财联社快讯", "华尔街见闻-全球", "华尔街见闻-A股",
                    "第一财经", "东方财富-要闻", "金十数据", "格隆汇"]
INTERNATIONAL_SOURCES = ["CNBC", "MarketWatch", "ZeroHedge", "FT-Markets", "Yahoo-Finance",
                          "Bloomberg-Markets"]
ASIAN_SOURCES = ["Nikkei-Asia", "SCMP-Economy"]
EUROPEAN_SOURCES = ["ECB-Press", "BBC-Business"]
MACRO_RESEARCH_SOURCES = ["Mises-Wire", "Naked-Capitalism"]
COMMODITY_SOURCES = ["OilPrice", "Investing-Commodities", "WorldOilNews"]
CRYPTO_SOURCES = ["CoinDesk", "TheBlock"]
POLICY_SOURCES = []  # removed broken regulatory feeds; re-add when working sources found


def fetch_feed(name, url, timeout=12):
    """抓取单个 RSS 源，带超时"""
    try:
        import socket
        socket.setdefaulttimeout(timeout)
        feed = feedparser.parse(url, request_headers={
            'User-Agent': 'Mozilla/5.0 (compatible; TraderBot/1.0)'
        })
        if feed.bozo and not feed.entries:
            return name, [], f"parse error: {feed.bozo_exception}"

        items = []
        for entry in feed.entries[:20]:
            pub = getattr(entry, 'published', '') or getattr(entry, 'updated', '')
            items.append({
                "title": entry.get('title', '').strip(),
                "link": entry.get('link', ''),
                "published": pub,
                "summary": (entry.get('summary', '') or '')[:500].strip(),
                "source": name,
            })
        return name, items, None
    except Exception as e:
        return name, [], str(e)


def collect(sources=None, max_workers=6):
    """并发抓取 RSS 源"""
    feeds = FEEDS if sources is None else {k: v for k, v in FEEDS.items() if k in sources}

    all_items = []
    source_stats = {}

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(fetch_feed, name, url): name for name, url in feeds.items()}
        for future in as_completed(futures, timeout=20):
            name = futures[future]
            try:
                src, items, err = future.result(timeout=15)
                source_stats[src] = {"count": len(items), "error": err}
                all_items.extend(items)
            except Exception as e:
                source_stats[name] = {"count": 0, "error": str(e)}

    # 去重（按标题）
    seen = set()
    unique = []
    for item in all_items:
        key = item['title'][:50]
        if key not in seen:
            seen.add(key)
            unique.append(item)

    # 按时间排序
    unique.sort(key=lambda x: x.get('published', ''), reverse=True)

    ok = sum(1 for s in source_stats.values() if s['count'] > 0)

    return {
        "collected_at": datetime.utcnow().isoformat() + "Z",
        "total_items": len(unique),
        "sources_ok": ok,
        "sources_total": len(feeds),
        "source_stats": source_stats,
        "items": unique,
    }


def collect_domestic():
    return collect(sources=DOMESTIC_SOURCES)

def collect_international():
    return collect(sources=INTERNATIONAL_SOURCES + ASIAN_SOURCES + EUROPEAN_SOURCES
                   + MACRO_RESEARCH_SOURCES + COMMODITY_SOURCES + CRYPTO_SOURCES)

def collect_all():
    return collect()


# === 消息面分析辅助 ===
def categorize_news(items):
    """将新闻按主题分类"""
    categories = {
        "宏观/央行": [],
        "A股/板块": [],
        "美股/全球": [],
        "日本/亚太": [],
        "欧洲/英国": [],
        "大宗商品/能源": [],
        "地缘政治": [],
        "科技/AI": [],
        "加密货币": [],
        "其他": [],
    }

    keywords_map = {
        "宏观/央行": ["央行", "降准", "降息", "MLF", "LPR", "货币", "利率", "Fed", "Powell", "FOMC",
                    "interest rate", "ECB", "BOJ", "央行行长", "货币政策", "QE", "QT", "taper",
                    "inflation", "通胀", "CPI", "PPI", "GDP", "PMI", "就业", "unemployment"],
        "A股/板块": ["A股", "沪指", "深指", "创业板", "涨停", "板块", "北向", "资金流", "两市", "上证"],
        "美股/全球": ["美股", "纳斯达克", "标普", "道指", "S&P", "Nasdaq", "Wall Street", "stock"],
        "日本/亚太": ["日本", "Japan", "Nikkei", "日経", "Asia", "亚太", "东盟", "ASEAN", "印度", "India"],
        "欧洲/英国": ["ECB", "欧洲", "Europe", "英国", "UK", "Brexit", "欧央行", "德国", "Germany", "法国"],
        "大宗商品/能源": ["原油", "黄金", "白银", "铜", "oil", "gold", "crude", "commodity", "OPEC"],
        "地缘政治": ["伊朗", "以色列", "俄", "乌克兰", "关税", "制裁", "tariff", "Iran", "Israel", "war"],
        "科技/AI": ["AI", "芯片", "算力", "半导体", "NVIDIA", "OpenAI", "GPU", "chip", "semiconductor"],
        "加密货币": ["比特币", "Bitcoin", "BTC", "ETH", "crypto", "加密"],
    }

    for item in items:
        text = (item.get('title', '') + ' ' + item.get('summary', '')).lower()
        categorized = False
        for cat, keywords in keywords_map.items():
            if any(kw.lower() in text for kw in keywords):
                categories[cat].append(item)
                categorized = True
                break
        if not categorized:
            categories["其他"].append(item)

    return {k: v for k, v in categories.items() if v}


if __name__ == '__main__':
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"

    if mode == "domestic":
        data = collect_domestic()
    elif mode == "international":
        data = collect_international()
    elif mode == "all":
        data = collect_all()
    elif mode == "categorized":
        raw = collect_all()
        data = {
            "meta": {k: v for k, v in raw.items() if k != 'items'},
            "categories": categorize_news(raw['items']),
        }
    else:
        print(f"Usage: {sys.argv[0]} [all|domestic|international|categorized]", file=sys.stderr)
        sys.exit(1)

    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
