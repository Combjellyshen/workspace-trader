#!/usr/bin/env python3
"""
跨日对比分析器 — 发现资金流/情绪面/板块的边际变化

需要 memory/ 下有历史数据才能对比
无历史数据时只输出当日快照

子命令：
  flow_delta     — 资金流向边际变化（行业+概念，今 vs 昨）
  sentiment_delta — 情绪面变化（热股进退榜、概念轮动）
  sector_rotation — 板块轮动分析
  all            — 全部
"""
import json
import sys
import os
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

_HERE = Path(__file__).resolve().parents[2]
import sys  # noqa: E402
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from scripts.utils.common import WORKSPACE_ROOT  # noqa: E402

WORKSPACE = WORKSPACE_ROOT
MEMORY = WORKSPACE / 'memory'
SNAPSHOT_DIR = MEMORY / 'snapshots'

# Ensure dir
SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)


def today_str():
    return datetime.now(ZoneInfo("Asia/Shanghai")).strftime('%Y-%m-%d')


def _load_snapshot(date_str):
    """加载某日快照"""
    f = SNAPSHOT_DIR / f'{date_str}.json'
    if f.exists():
        with open(f, encoding='utf-8') as fp:
            return json.load(fp)
    return None


def save_snapshot(data, date_str=None):
    """保存当日快照（供后续对比）"""
    if not date_str:
        date_str = today_str()
    f = SNAPSHOT_DIR / f'{date_str}.json'
    with open(f, 'w', encoding='utf-8') as fp:
        json.dump(data, fp, ensure_ascii=False, indent=2)
    return str(f)


def _get_prev_trading_day(date_str=None):
    """获取前一个工作日"""
    from datetime import date
    d = datetime.strptime(date_str, '%Y-%m-%d').date() if date_str else date.today()
    d -= timedelta(days=1)
    while d.weekday() >= 5:  # 跳过周末
        d -= timedelta(days=1)
    return d.strftime('%Y-%m-%d')


def flow_delta():
    """资金流向边际变化"""
    result = {'analysis_time': datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()}
    
    # 获取当前数据
    sys.path.insert(0, str(WORKSPACE / 'scripts'))
    from scripts.data import deep_data as dd
    
    today_industry = dd.sector_money_flow(fenlei=0, num=30)
    today_concept = dd.sector_money_flow(fenlei=1, num=30)
    
    today_data = {
        'date': today_str(),
        'industry': {s['name']: s['net_inflow'] for s in today_industry},
        'concept': {s['name']: s['net_inflow'] for s in today_concept},
    }
    
    # 保存当日快照
    snapshot = _load_snapshot(today_str()) or {}
    snapshot['flow'] = today_data
    save_snapshot(snapshot)
    
    result['today'] = {
        'industry_top5': sorted(today_industry, key=lambda x: x['net_inflow'], reverse=True)[:5],
        'industry_bottom5': sorted(today_industry, key=lambda x: x['net_inflow'])[:5],
        'concept_top5': sorted(today_concept, key=lambda x: x['net_inflow'], reverse=True)[:5],
        'concept_bottom5': sorted(today_concept, key=lambda x: x['net_inflow'])[:5],
    }
    
    # 对比前日
    prev_date = _get_prev_trading_day()
    prev_snapshot = _load_snapshot(prev_date)
    
    if prev_snapshot and 'flow' in prev_snapshot:
        prev = prev_snapshot['flow']
        
        # 行业资金流变化
        industry_delta = []
        for name, today_val in today_data['industry'].items():
            prev_val = prev.get('industry', {}).get(name, 0)
            delta = round(today_val - prev_val, 2)
            industry_delta.append({
                'name': name,
                'today': today_val,
                'prev': prev_val,
                'delta': delta,
                'signal': '🔥 加速流入' if delta > 5 else ('⚠️ 加速流出' if delta < -5 else ''),
            })
        
        industry_delta.sort(key=lambda x: x['delta'], reverse=True)
        result['industry_delta'] = {
            'biggest_increase': industry_delta[:5],
            'biggest_decrease': industry_delta[-5:],
        }
        
        # 概念资金流变化
        concept_delta = []
        for name, today_val in today_data['concept'].items():
            prev_val = prev.get('concept', {}).get(name, 0)
            delta = round(today_val - prev_val, 2)
            concept_delta.append({
                'name': name,
                'today': today_val,
                'prev': prev_val,
                'delta': delta,
            })
        
        concept_delta.sort(key=lambda x: x['delta'], reverse=True)
        result['concept_delta'] = {
            'biggest_increase': concept_delta[:5],
            'biggest_decrease': concept_delta[-5:],
        }
        
        # 新进/退出 TOP10
        today_top10 = set(list(today_data['industry'].keys())[:10])
        prev_top10 = set(list(prev.get('industry', {}).keys())[:10])
        result['rotation'] = {
            'new_in_top10': list(today_top10 - prev_top10),
            'dropped_from_top10': list(prev_top10 - today_top10),
        }
    else:
        result['no_prev_data'] = f'无前日({prev_date})快照可对比'
    
    return result


def sentiment_delta():
    """情绪面变化"""
    result = {'analysis_time': datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()}
    
    sys.path.insert(0, str(WORKSPACE / 'scripts'))
    from scripts.analysis import sentiment as sm
    
    today_hot = sm.ths_hot_stocks(30)
    today_stocks = today_hot.get('stocks', [])
    today_names = {s['name'] for s in today_stocks}
    today_codes = {s['code'] for s in today_stocks}
    
    # 保存
    snapshot = _load_snapshot(today_str()) or {}
    snapshot['sentiment'] = {
        'hot_stocks': [{'name': s['name'], 'code': s['code'], 'rank': s['rank']} 
                       for s in today_stocks],
    }
    save_snapshot(snapshot)
    
    result['today_top10'] = today_stocks[:10]
    
    # 对比前日
    prev_date = _get_prev_trading_day()
    prev_snapshot = _load_snapshot(prev_date)
    
    if prev_snapshot and 'sentiment' in prev_snapshot:
        prev_stocks = prev_snapshot['sentiment'].get('hot_stocks', [])
        prev_names = {s['name'] for s in prev_stocks}
        
        new_in = [s for s in today_stocks if s['name'] not in prev_names]
        dropped = [s for s in prev_stocks if s['name'] not in today_names]
        
        # 排名变化
        prev_rank = {s['name']: s['rank'] for s in prev_stocks}
        rank_changes = []
        for s in today_stocks[:20]:
            if s['name'] in prev_rank:
                change = prev_rank[s['name']] - s['rank']  # 正=升 负=降
                rank_changes.append({
                    'name': s['name'],
                    'today_rank': s['rank'],
                    'prev_rank': prev_rank[s['name']],
                    'change': change,
                })
        
        result['delta'] = {
            'new_entries': new_in[:10],
            'dropped': dropped[:10],
            'new_count': len(new_in),
            'dropped_count': len(dropped),
            'turnover_rate': round(len(new_in) / max(len(today_stocks), 1) * 100, 1),
        }
        result['rank_movers'] = {
            'risers': sorted([r for r in rank_changes if r['change'] > 0], 
                           key=lambda x: x['change'], reverse=True)[:5],
            'fallers': sorted([r for r in rank_changes if r['change'] < 0], 
                            key=lambda x: x['change'])[:5],
        }
        
        # 换手率评估
        turnover = len(new_in) / max(len(today_stocks), 1)
        if turnover > 0.5:
            result['turnover_signal'] = '🔄 热点高度轮动，追高风险大'
        elif turnover > 0.3:
            result['turnover_signal'] = '🔄 热点有一定轮动'
        else:
            result['turnover_signal'] = '✅ 热点相对持续，主线明确'
    else:
        result['no_prev_data'] = f'无前日({prev_date})情绪快照'
    
    return result


def full_delta():
    """全部边际变化分析"""
    return {
        'flow_delta': flow_delta(),
        'sentiment_delta': sentiment_delta(),
    }


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'all'
    
    if cmd == 'flow_delta':
        data = flow_delta()
    elif cmd == 'sentiment_delta':
        data = sentiment_delta()
    elif cmd == 'all':
        data = full_delta()
    else:
        print(f"Usage: {sys.argv[0]} [flow_delta|sentiment_delta|all]", file=sys.stderr)
        sys.exit(1)
    
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
