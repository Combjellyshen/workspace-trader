#!/usr/bin/env python3
"""
market_cache.py - 全市场日度快照缓存

每个交易日收盘后存全市场关键财务快照，供周末离线使用。

用法:
    python3 scripts/data/market_cache.py save    # 存今日快照
    python3 scripts/data/market_cache.py load    # 读最近一份快照（优先今天，否则最近5天内）
    python3 scripts/data/market_cache.py status  # 显示缓存状态
    python3 scripts/data/market_cache.py sectors # 分析缓存数据，输出行业资金流+景气度排名
"""

import sys
import json
import os
import warnings
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

warnings.filterwarnings('ignore')

try:
    import akshare as ak
    import pandas as pd
except ImportError as e:
    print(f"缺少依赖: {e}")
    sys.exit(1)

_HERE = Path(__file__).resolve().parents[2]
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from scripts.utils.common import WORKSPACE_ROOT  # noqa: E402

CACHE_DIR = WORKSPACE_ROOT / 'data' / 'market_cache'
CACHE_DIR.mkdir(parents=True, exist_ok=True)

MAX_KEEP_DAYS = 10  # 只保留最近10天


def _df_to_list(df, max_rows=None):
    """DataFrame → list of dicts（NaN→None）"""
    if df is None or df.empty:
        return []
    if max_rows:
        df = df.head(max_rows)
    return json.loads(df.to_json(orient='records', force_ascii=False, default_handler=str))


def _cleanup_old_caches():
    """清理超过 MAX_KEEP_DAYS 的缓存文件"""
    cutoff = datetime.now() - timedelta(days=MAX_KEEP_DAYS)
    for f in sorted(CACHE_DIR.glob('*.json')):
        try:
            file_date = datetime.strptime(f.stem, '%Y-%m-%d')
            if file_date < cutoff:
                f.unlink()
                print(f"[清理] 已删除旧缓存: {f.name}")
        except ValueError:
            pass  # 跳过非日期命名的文件


def load_cache():
    """读最近5天内的最新缓存，供外部模块调用"""
    if not CACHE_DIR.exists():
        return None
    today = datetime.now()
    for i in range(5):
        date_str = (today - timedelta(days=i)).strftime('%Y-%m-%d')
        cache_file = CACHE_DIR / f'{date_str}.json'
        if cache_file.exists():
            with open(cache_file, encoding='utf-8') as f:
                return json.load(f)
    return None


def cmd_save():
    """存今日快照"""
    today_str = datetime.now().strftime('%Y-%m-%d')
    saved_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cache_file = CACHE_DIR / f'{today_str}.json'

    result = {
        "date": today_str,
        "saved_at": saved_at,
        "industry_flow": [],
        "concept_flow": [],
        "market_snapshot_count": 0,
        "market_snapshot": [],
        "north_flow": [],
        "growth_comparison": [],
        "valuation_comparison": []
    }

    errors = []

    # 1. 行业资金流（近5日）
    print("[1/4] 获取行业资金流...")
    try:
        df_industry = ak.stock_fund_flow_industry(symbol="近5日")
        if df_industry is not None and not df_industry.empty:
            # 按主力净流入降序排列
            flow_col = None
            for c in df_industry.columns:
                if '主力净流入' in c and '占比' not in c:
                    flow_col = c
                    break
            if flow_col:
                try:
                    df_industry[flow_col] = pd.to_numeric(df_industry[flow_col], errors='coerce')
                    df_industry = df_industry.sort_values(flow_col, ascending=False)
                except Exception:
                    pass
            result['industry_flow'] = _df_to_list(df_industry)
            print(f"  ✅ 行业资金流: {len(result['industry_flow'])} 条")
        else:
            print("  ⚠️ 行业资金流返回空数据")
    except Exception as e:
        errors.append(f"行业资金流: {e}")
        print(f"  ❌ 行业资金流失败: {e}")

    # 2. 概念资金流（近5日，取前30）
    print("[2/4] 获取概念资金流...")
    try:
        df_concept = ak.stock_fund_flow_concept(symbol="近5日")
        if df_concept is not None and not df_concept.empty:
            flow_col = None
            for c in df_concept.columns:
                if '主力净流入' in c and '占比' not in c:
                    flow_col = c
                    break
            if flow_col:
                try:
                    df_concept[flow_col] = pd.to_numeric(df_concept[flow_col], errors='coerce')
                    df_concept = df_concept.sort_values(flow_col, ascending=False)
                except Exception:
                    pass
            result['concept_flow'] = _df_to_list(df_concept, max_rows=30)
            print(f"  ✅ 概念资金流: {len(result['concept_flow'])} 条（取前30）")
        else:
            print("  ⚠️ 概念资金流返回空数据")
    except Exception as e:
        errors.append(f"概念资金流: {e}")
        print(f"  ❌ 概念资金流失败: {e}")

    # 3. 全市场行情快照（关键字段）
    print("[3/4] 获取全市场行情快照...")
    try:
        df_spot = ak.stock_zh_a_spot_em()
        if df_spot is not None and not df_spot.empty:
            keep_cols = ['代码', '名称', '涨跌幅', '成交额', '市盈率-动态', '市净率']
            available_cols = [c for c in keep_cols if c in df_spot.columns]
            df_spot_slim = df_spot[available_cols]
            result['market_snapshot_count'] = len(df_spot_slim)
            result['market_snapshot'] = _df_to_list(df_spot_slim)
            print(f"  ✅ 全市场快照: {result['market_snapshot_count']} 只股票，字段: {available_cols}")
        else:
            print("  ⚠️ 全市场行情返回空数据")
    except Exception as e:
        errors.append(f"全市场快照: {e}")
        print(f"  ❌ 全市场快照失败: {e}")

    # 4. 北向资金当日汇总
    print("[4/4] 获取北向资金...")
    try:
        df_north = ak.stock_em_hsgt_board_sz(market="沪深港通", indicator="今日排行")
        if df_north is not None and not df_north.empty:
            result['north_flow'] = _df_to_list(df_north)
            print(f"  ✅ 北向资金: {len(result['north_flow'])} 条")
        else:
            print("  ⚠️ 北向资金返回空数据")
    except Exception as e:
        errors.append(f"北向资金: {e}")
        print(f"  ❌ 北向资金失败: {e}")

    # 补充 growth_comparison / valuation_comparison（供 growth_hunter scan 使用）
    print("[5/4] 获取成长性对比数据...")
    try:
        import akshare as _ak2
        df_growth = _ak2.stock_zh_growth_comparison_em(symbol="沪深京A股")
        if df_growth is not None and not df_growth.empty:
            result['growth_comparison'] = _df_to_list(df_growth)
            print(f"  ✅ 成长性对比: {len(result['growth_comparison'])} 只")
        else:
            result['growth_comparison'] = []
    except Exception as e:
        errors.append(f"成长性对比: {e}")
        result['growth_comparison'] = []
        print(f"  ❌ 成长性对比失败: {e}")

    print("[6/4] 获取估值对比数据...")
    try:
        import akshare as _ak3
        df_val = _ak3.stock_zh_valuation_comparison_em(symbol="沪深京A股")
        if df_val is not None and not df_val.empty:
            result['valuation_comparison'] = _df_to_list(df_val)
            print(f"  ✅ 估值对比: {len(result['valuation_comparison'])} 只")
        else:
            result['valuation_comparison'] = []
    except Exception as e:
        errors.append(f"估值对比: {e}")
        result['valuation_comparison'] = []
        print(f"  ❌ 估值对比失败: {e}")

    # 存文件
    with open(cache_file, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    file_size_kb = cache_file.stat().st_size / 1024

    print(f"\n✅ 快照已保存: {cache_file}")
    print(f"   日期: {today_str} | 股票数: {result['market_snapshot_count']} | 文件大小: {file_size_kb:.1f} KB")
    if errors:
        print(f"   ⚠️ 部分数据失败（不影响主体）: {'; '.join(errors)}")

    # 清理旧缓存
    _cleanup_old_caches()

    return result


def cmd_load():
    """读最近一份快照，打印摘要"""
    data = load_cache()
    if data is None:
        print("❌ 无缓存，请在工作日运行 market_cache.py save")
        return None

    print(f"✅ 加载缓存成功")
    print(f"   数据日期: {data.get('date', 'N/A')}")
    print(f"   保存时间: {data.get('saved_at', 'N/A')}")
    print(f"   全市场股票数: {data.get('market_snapshot_count', 0)}")
    print(f"   行业资金流条数: {len(data.get('industry_flow', []))}")
    print(f"   概念资金流条数: {len(data.get('concept_flow', []))}")
    print(f"   北向资金条数: {len(data.get('north_flow', []))}")
    return data


def cmd_status():
    """显示缓存状态"""
    files = sorted(CACHE_DIR.glob('*.json'), reverse=True)
    if not files:
        print("❌ 无缓存，请在工作日运行: python3 scripts/data/market_cache.py save")
        return

    print(f"📦 缓存目录: {CACHE_DIR}")
    print(f"   共 {len(files)} 个缓存文件（保留最近 {MAX_KEEP_DAYS} 天）\n")

    for f in files[:5]:
        try:
            with open(f, encoding='utf-8') as fp:
                data = json.load(fp)
            size_kb = f.stat().st_size / 1024
            print(f"  📅 {data.get('date', f.stem)}")
            print(f"     保存时间: {data.get('saved_at', 'N/A')}")
            print(f"     股票数: {data.get('market_snapshot_count', 0)}")
            print(f"     文件大小: {size_kb:.1f} KB")
            print()
        except Exception as e:
            print(f"  ⚠️ {f.name}: 读取失败 ({e})")


def _format_flow_value(v):
    """格式化资金流数值"""
    try:
        v = float(v)
        if abs(v) >= 1e8:
            return f"{v/1e8:+.1f}亿"
        elif abs(v) >= 1e4:
            return f"{v/1e4:+.1f}万"
        else:
            return f"{v:+.0f}"
    except Exception:
        return str(v)


def cmd_sectors():
    """分析缓存数据，输出各行业资金流+景气度排名"""
    data = load_cache()
    if data is None:
        print("❌ 无缓存数据，请先运行: python3 scripts/data/market_cache.py save")
        return

    date = data.get('date', 'N/A')
    industry_flow = data.get('industry_flow', [])

    print(f"\n📊 赛道景气度排名（基于5日资金流，数据日期: {date}）")
    print("=" * 60)

    if not industry_flow:
        print("⚠️ 行业资金流缓存为空，尝试实时回退获取...")
        try:
            df_industry = ak.stock_fund_flow_industry(symbol="近5日")
            if df_industry is not None and not df_industry.empty:
                industry_flow = _df_to_list(df_industry)
                print(f"✅ 实时回退获取行业资金流成功: {len(industry_flow)} 条")
            else:
                print("⚠️ 实时回退后行业资金流仍为空")
        except Exception as e:
            print(f"⚠️ akshare实时回退失败: {e}")
            try:
                proc = subprocess.run(
                    ['python3', 'scripts/data/deep_data.py', 'snapshot'],
                    capture_output=True,
                    text=True,
                    cwd=str(WORKSPACE_ROOT),
                    timeout=120,
                    check=True,
                )
                snap = json.loads(proc.stdout)
                industry_flow = snap.get('industry_flow', []) or []
                if industry_flow:
                    print(f"✅ deep_data 回退获取行业资金流成功: {len(industry_flow)} 条")
            except Exception as e2:
                print(f"⚠️ deep_data 回退也失败: {e2}")
    if not industry_flow:
        print("⚠️ 行业资金流数据为空")
    else:
        # 找主力净流入列
        if industry_flow:
            sample = industry_flow[0]
            flow_key = None
            name_key = None
            for k in sample.keys():
                if ('主力净流入' in k or '净流入' in k) and '占比' not in k:
                    flow_key = k
                if '行业' in k or '名称' in k or '板块' in k:
                    name_key = k
            if flow_key is None:
                for k in sample.keys():
                    if k in ('net_inflow', 'net_inflow_raw') or '净额' in k or '净流入' in k:
                        flow_key = k
                        break
            if name_key is None:
                for k in sample.keys():
                    if k in ('name', 'industry', 'sector'):
                        name_key = k
                        break
            if not name_key:
                # 取第一个字段作为名称
                name_key = list(sample.keys())[0]

            # 排序
            try:
                sorted_flow = sorted(
                    [r for r in industry_flow if r.get(flow_key) is not None],
                    key=lambda x: float(str(x.get(flow_key, 0)).replace(',', '') or 0),
                    reverse=True
                )
            except Exception:
                sorted_flow = industry_flow

            print("\n🔥 赛道景气度排名（主力净流入 TOP 10）：")
            top_sectors = []
            for i, row in enumerate(sorted_flow[:10], 1):
                name = row.get(name_key, '未知')
                flow_val = row.get(flow_key, 0)
                flow_str = _format_flow_value(flow_val)

                # 景气度图标
                try:
                    fv = float(str(flow_val).replace(',', '') or 0)
                    if fv > 3e8:
                        icon = "🔥"
                    elif fv > 1e8:
                        icon = "📈"
                    elif fv > 0:
                        icon = "📊"
                    else:
                        icon = "📉"
                except Exception:
                    icon = "📊"

                print(f"  {i:2d}. {name:<12} 主力净流入 {flow_str:>10}  景气度：{icon}")
                if i <= 3:
                    top_sectors.append(name)

            print("\n⚠️  资金净流出前 5（规避）：")
            for i, row in enumerate(sorted_flow[-5:][::-1], 1):
                name = row.get(name_key, '未知')
                flow_val = row.get(flow_key, 0)
                flow_str = _format_flow_value(flow_val)
                print(f"  {i}. {name:<12} 净流出 {flow_str:>10}")

            if top_sectors:
                print(f"\n💡 建议本周重点关注赛道：{' / '.join(top_sectors)}")

    # 概念资金流 TOP 10
    concept_flow = data.get('concept_flow', [])
    if concept_flow:
        print("\n" + "=" * 60)
        print("🏷️  热门概念资金流 TOP 10：")
        sample = concept_flow[0]
        flow_key = None
        name_key = None
        for k in sample.keys():
            if '主力净流入' in k and '占比' not in k:
                flow_key = k
            if '概念' in k or '名称' in k or '板块' in k:
                name_key = k
        if not name_key:
            name_key = list(sample.keys())[0]

        try:
            sorted_concept = sorted(
                [r for r in concept_flow if r.get(flow_key) is not None],
                key=lambda x: float(str(x.get(flow_key, 0)).replace(',', '') or 0),
                reverse=True
            )
        except Exception:
            sorted_concept = concept_flow

        for i, row in enumerate(sorted_concept[:10], 1):
            name = row.get(name_key, '未知')
            flow_val = row.get(flow_key, 0)
            flow_str = _format_flow_value(flow_val)
            print(f"  {i:2d}. {name:<15} {flow_str:>10}")

    print()


def main():
    cmd = sys.argv[1].lower() if len(sys.argv) > 1 else "status"

    if cmd == 'save' or cmd == 'save_market':
        cmd_save()
    elif cmd == 'load' or cmd == 'load_market':
        data = load_cache()
        if data:
            print(f"日期: {data.get('date')}, 全市场股票数: {len(data.get('market_spot', []))}")
        else:
            print("无缓存")
    elif cmd == 'load_stocks':
        data = load_cache()
        if data:
            print(f"日期: {data.get('date')}, 个股数: {data.get('market_snapshot_count', 0)}")
        else:
            print("无缓存")
    elif cmd == 'status':
        cmd_status()
    elif cmd == 'sectors':
        cmd_sectors()
    elif cmd == 'screen':
        # screen: 行业筛选（与sectors相同入口）
        cmd_sectors()
    elif cmd == 'save_stocks':
        # save_stocks: 保存个股快照（调用 cmd_save 的子集，或直接 save_market）
        cmd_save()
    elif cmd == 'save_all':
        cmd_save()
        cmd_sectors()
    else:
        print(f"未知命令: {cmd}")
        print("可用命令: save / save_market / load / load_market / load_stocks / status / sectors / screen / save_stocks / save_all")
        sys.exit(1)


if __name__ == '__main__':
    main()
