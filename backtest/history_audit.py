"""Read-only historical trade audit; run: python -m backtest.history_audit.

Outputs go to data/research. No scheduler, API credentials, or trading imports.
Exit experiments are conditional replays of observed entries, NOT strategy backtests.
They use completed hourly closes after entry, retain actual partial exits, and never
open a hypothetical new position. Prices within entry/exit boundary bars are omitted.
"""
import bisect
import csv
import hashlib
import json
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'data/research'
TZ = timezone(timedelta(hours=8))
SPLIT = '2026-09-01'


def stamp(s):
    return datetime.strptime(s, '%Y-%m-%d %H:%M:%S').replace(tzinfo=TZ).timestamp()


def phase(s):
    for end, label in [('2026-06-21', '01_pre_two_layer'),
                       ('2026-08-29', '02_two_layer'),
                       ('2026-09-08', '03_sizing_and_ladder'),
                       ('2026-09-18', '04_ai_stop'),
                       ('9999', '05_stop_clamps')]:
        if s < end:
            return label


def stats(rows, key='pnl'):
    ps = [r[key] for r in rows]
    win, loss = sum(p for p in ps if p > 0), -sum(p for p in ps if p < 0)
    cum = peak = dd = 0
    for p in ps:
        cum += p
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    return dict(n=len(ps), pnl=round(sum(ps), 2),
                win_pct=round(100 * sum(p > 0 for p in ps) / len(ps), 1) if ps else None,
                pf=round(win / loss, 3) if loss else None,
                mean=round(statistics.mean(ps), 3) if ps else None,
                median=round(statistics.median(ps), 3) if ps else None,
                max_realized_dd_usd=round(dd, 2))


def rebuild(name):
    path = ROOT / f'data/trading_state_{name}_sim.json'
    raw = path.read_bytes()
    state = json.loads(raw)
    rows, pending, previous, anomalies = [], None, None, []

    def finish(p, complete):
        nonlocal previous
        o, exits = p
        if not exits:
            anomalies.append({'open_id': o['id'], 'type': 'no_exit'})
            return
        end = exits[-1]
        q = o['amount']
        qty = sum(e['amount'] for e in exits)
        pnl = sum(e.get('pnl', 0) for e in exits)
        risk = abs(o['price'] - (o.get('levels') or {}).get('stop_loss', o['price'])) * q
        mi = o.get('market_indicators') or {}
        gross_turnover = q * o['price'] + sum(e['amount'] * e['price'] for e in exits)
        row = dict(account=name, open_id=o['id'], open=o['time'], close=end['time'],
                   direction=o['action'], entry=o['price'], exit=end['price'], amount=q,
                   pnl=pnl, risk_usd=risk, r=pnl / risk if risk > 0 else None,
                   complete=complete, quantity_ratio=qty/q,
                   quantity_ok=abs(qty-q) <= max(q*0.001, 0.000003),
                   hold_h=(stamp(end['time'])-stamp(o['time']))/3600,
                   phase=phase(o['time']), month=o['time'][:7],
                   period='early' if o['time'] < SPLIT else 'late',
                   leverage=o.get('leverage'), confidence=mi.get('ai_confidence_level', 'missing'),
                   entry_reason=o.get('trigger_reason'), exit_reason=end.get('trigger_reason'),
                   partial_count=len(exits)-1 if complete else len(exits),
                   ma=mi.get('ma_trend', 'missing'), divergence=mi.get('divergence_type','missing'),
                   rsi=mi.get('rsi_value'), volume=mi.get('vol_ratio'),
                   turnover=gross_turnover,
                   net_5bps=pnl-gross_turnover*0.0005,
                   net_10bps=pnl-gross_turnover*0.001,
                   gap_h=(stamp(o['time'])-stamp(previous['close']))/3600 if previous else None,
                   previous_same=previous['direction']==o['action'] if previous else False,
                   previous_win=previous['pnl']>0 if previous else False,
                   _open=o, _exits=exits)
        div=str(row['divergence']).lower()
        row['opposing_divergence'] = ('顶' in div or 'bearish' in div) if row['direction']=='LONG' else ('底' in div or 'bullish' in div)
        if not complete or not row['quantity_ok']:
            anomalies.append({'open_id':o['id'], 'type':'incomplete' if not complete else 'quantity_mismatch', 'quantity_ratio':row['quantity_ratio']})
        rows.append(row)
        previous = row if complete else None

    for t in state['trades']:
        if t['action'] in ('LONG','SHORT'):
            if pending:
                finish(pending, False)
            pending = (t, [])
        elif t['action'] in ('CLOSE','REDUCE','TP1_HALF'):
            if not pending:
                anomalies.append({'exit_id':t['id'], 'type':'orphan_exit'})
            else:
                pending[1].append(t)
                if t['action']=='CLOSE':
                    finish(pending, True)
                    pending=None
    if pending:
        finish(pending, False)
    all_pnl=sum(t.get('pnl',0) for t in state['trades'])
    backup=path.with_name(path.name+'.bak.20260901_margin_recalc')
    revision={}
    if backup.exists():
        old=json.loads(backup.read_text())['trades']
        current={(t['id'],t['time']):t for t in state['trades']}
        pairs=[(t,current[(t['id'],t['time'])]) for t in old if (t['id'],t['time']) in current]
        before=sum(x['pnl'] for x,y in pairs); after=sum(y['pnl'] for x,y in pairs)
        revision=dict(backup=str(backup.relative_to(ROOT)), matched=len(pairs),
                      changed_amount=sum(x['amount']!=y['amount'] for x,y in pairs),
                      old_pnl=before,current_matched_pnl=after,delta=after-before,
                      original_size_ledger_pnl=all_pnl-(after-before))
    manifest=dict(file=str(path.relative_to(ROOT)),sha256=hashlib.sha256(raw).hexdigest(),
                  raw_records=len(state['trades']), exit_records=sum(t['action'] in ('CLOSE','REDUCE','TP1_HALF') for t in state['trades']),
                  reconstructed_positions=len(rows), complete=sum(r['complete'] for r in rows),
                  record_pnl=all_pnl, saved_pnl=state['total_pnl'], saved_equity=state['equity'],
                  anomalies=anomalies,revision=revision)
    assert abs(sum(r['pnl'] for r in rows)-all_pnl)<0.001, 'Unreconciled realized PnL'
    return rows, manifest


def load_bars():
    bars={}
    for r in csv.DictReader((ROOT/'data/klines/BTCUSDT_1h.csv').open()):
        t=int(r['open_time'])/1000
        bars[t]=dict(t=t, end=(int(r['close_time'])+1)/1000,
                     high=float(r['high']), low=float(r['low']), close=float(r['close']))
    recent=OUT/'recent_1h_raw.json'
    if recent.exists():
        for r in json.loads(recent.read_text()):
            t=r[0]/1000
            bars[t]=dict(t=t,end=(r[6]+1)/1000,high=float(r[2]),low=float(r[3]),close=float(r[4]))
    now=datetime.now(TZ).timestamp()
    return sorted((b for b in bars.values() if b['end']<=now), key=lambda b:b['t'])


def replay(r, bars, kind):
    """Close-only replay; signal on bar close, market fill at that observed close.

    Touches within bars are deliberately not inferred. Actual historical exits are
    retained until an experimental full exit. Includes an explicit fee scenario.
    """
    q=r['amount']; entry=r['entry']; sign=1 if r['direction']=='LONG' else -1
    unit=r['risk_usd']/q
    peak=0.; events=[]
    for e in r['_exits']:
        events.append((stamp(e['time']), 0, e))
    for b in bars:
        events.append((b['end'], 1, b))
    gross=0.; turnover=q*entry; triggered=False
    for _, order, e in sorted(events,key=lambda x:(x[0],x[1])):
        if q <= 0.0000001:
            break
        if order==0:
            qty=min(q,e['amount'])
            # Keep ledger PnL instead of adding precision noise to the baseline.
            gross+=e['pnl']*qty/e['amount'] if e['amount'] else 0
            turnover+=qty*e['price'];q-=qty
            continue
        px=e['close']; cur=sign*(px-entry)/unit
        # Peak from past closes only. Activation cannot see this bar's high/low.
        exit_now=(kind=='tp_1r' and cur>=1) or (kind=='tp_1_5r' and cur>=1.5)
        if kind=='be_after_1r':exit_now=peak>=1 and cur<=0
        if kind=='trail_1_5r_0_6r':exit_now=peak>=1.5 and peak-cur>=0.6
        if kind=='giveback_0_75r_half':exit_now=peak>=0.75 and cur<=peak*0.5
        peak=max(peak,cur)
        if exit_now:
            gross+=sign*(px-entry)*q;turnover+=px*q;q=0;triggered=True
    return dict(pnl=gross,net_5bps=gross-turnover*.0005,triggered=triggered)


def enrich(rows, bars):
    starts=[b['t'] for b in bars]; ends=[b['end'] for b in bars]
    for r in rows:
        begin, end=stamp(r['open']),stamp(r['close'])
        i=bisect.bisect_left(starts, begin)
        j=bisect.bisect_right(ends, end)
        path=bars[i:j]
        r['path_hours']=len(path)
        r['path_covered']=bool(path) and begin>=starts[0] and end<=ends[-1] and all(b['t']-a['end']<1 for a,b in zip(path,path[1:]))
        # Entry sanity check against the containing hourly bar, not a fit metric.
        idx=bisect.bisect_right(starts,begin)-1
        r['price_consistent']=False
        if idx>=0 and begin<bars[idx]['end']:
            b=bars[idx]
            r['price_consistent']=b['low']*.999<=r['entry']<=b['high']*1.001
        prior_end=bisect.bisect_right(ends,begin)
        width=bars[0]['end']-bars[0]['t']
        needed=round(48*3600/width)
        prior=bars[max(0,prior_end-needed):prior_end]
        if len(prior)==needed and begin-prior[-1]['end']<width and all(b['t']-a['end']<1 for a,b in zip(prior,prior[1:])):
            low=min(b['low'] for b in prior); high=max(b['high'] for b in prior)
            pct=(r['entry']-low)/(high-low)*100 if high>low else 50
            r['range_pct']=pct if r['direction']=='LONG' else 100-pct
        else:r['range_pct']=None
        r['replay_ok']=r['complete'] and r['quantity_ok'] and r['path_covered'] and r['price_consistent'] and r['risk_usd']>0
        r['mfe_r_lower']=None
        if r['replay_ok']:
            unit=r['risk_usd']/r['amount']; sign=1 if r['direction']=='LONG' else -1
            prices=[r['entry']]+[e['price'] for e in r['_exits']]+[b['high'] if sign==1 else b['low'] for b in path]
            r['mfe_r_lower']=max(sign*(p-r['entry'])/unit for p in prices)
            r['_replays']={k:replay(r,path,k) for k in ['tp_1r','tp_1_5r','be_after_1r','trail_1_5r_0_6r','giveback_0_75r_half']}


def groups(rows, field):
    d=defaultdict(list)
    for r in rows:d[str(r.get(field))].append(r)
    return {k:stats(v) for k,v in sorted(d.items())}


def filter_study(rows):
    """Historical sample deletion, no synthetic entries or recomputed AI/equity."""
    candidates={
        'baseline': lambda r:False,
        'exclude_short':lambda r:r['direction']=='SHORT',
        'exclude_opposing_divergence':lambda r:r['opposing_divergence'],
        'exclude_same_win_reentry_1h':lambda r:r['previous_same'] and r['previous_win'] and r['gap_h'] is not None and r['gap_h']<1,
        'exclude_same_win_reentry_4h':lambda r:r['previous_same'] and r['previous_win'] and r['gap_h'] is not None and r['gap_h']<4,
        'exclude_all_reentry_1h':lambda r:r['gap_h'] is not None and r['gap_h']<1,
        'exclude_range_60_100':lambda r:r['range_pct'] is not None and 60<=r['range_pct']<100,
        'exclude_range_ge60':lambda r:r['range_pct'] is not None and r['range_pct']>=60,
    }
    out={}
    for name,pred in candidates.items():
        out[name]={}
        for period in ['all','early','late']:
            sample=[r for r in rows if period=='all' or r['period']==period]
            kept=[r for r in sample if not pred(r)];removed=[r for r in sample if pred(r)]
            out[name][period]={'kept':stats(kept),'net_5bps':stats(kept,'net_5bps'),'removed':stats(removed)}
    return out


def replay_summary(rows):
    result={}
    for kind in ['baseline','tp_1r','tp_1_5r','be_after_1r','trail_1_5r_0_6r','giveback_0_75r_half']:
        result[kind]={}
        for period in ['all','early','late']:
            sample=[r for r in rows if period=='all' or r['period']==period]
            vs=sample if kind=='baseline' else [r['_replays'][kind] for r in sample]
            result[kind][period]={'gross':stats(vs),'net_5bps':stats(vs,'net_5bps'),
                                 'changed':sum(v.get('triggered',False) for v in vs)}
    return result


def audit():
    OUT.mkdir(parents=True,exist_ok=True)
    rows=[];manifests={}
    for name in ['aggressive','conservative','standard']:
        rs,manifests[name]=rebuild(name);rows+=rs
    bars=load_bars();enrich(rows,bars)
    result={'method':{'timezone':'Asia/Shanghai','split':SPLIT,'split_is_true_holdout':False,
                      'fee_scenarios':'5 or 10 bps per side, not actual fee rates; no funding',
                      'replay':'observed entries, original sizes, hourly close execution, no new entries; not portfolio backtest',
                      'mfe':'lower bound from full internal hourly high/low and actual fills; boundary bars excluded',
                      'phases':'git commit dates are approximate policy eras, not verified deployment times'},
            'bar_last':datetime.fromtimestamp(bars[-1]['end'],TZ).isoformat(),'manifest':manifests,'accounts':{}}
    inputs=[ROOT/'data/klines/BTCUSDT_1h.csv',OUT/'recent_1h_raw.json',OUT/'recent_5m_raw.json']
    result['market_data_inputs']=[dict(file=str(p.relative_to(ROOT)),sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in inputs if p.exists()]
    for name in manifests:
        allrows=[r for r in rows if r['account']==name]
        closed=[r for r in allrows if r['complete'] and r['quantity_ok']]
        valid=[r for r in closed if r['replay_ok']]
        a={'all_recorded_positions':stats(allrows),'clean_complete':stats(closed),
           'net_5bps':stats(closed,'net_5bps'),'net_10bps':stats(closed,'net_10bps'),
           'groups':{k:groups(closed,k) for k in ['month','phase','direction','confidence','leverage','opposing_divergence','ma']},
           'filters':filter_study(closed),'replay_coverage':len(valid),'replays':{},
           'losers_with_mfe_0_5r':sum(r['pnl']<0 and r['mfe_r_lower']>=.5 for r in valid),
           'losers_with_mfe_1r':sum(r['pnl']<0 and r['mfe_r_lower']>=1 for r in valid),
           'replay_losers':sum(r['pnl']<0 for r in valid),
           'without_top3':stats(sorted(closed,key=lambda r:r['pnl'],reverse=True)[3:])}
        a['replays']=replay_summary(valid)
        a['replays_excluding_manual']=replay_summary([r for r in valid if '手动' not in (r['exit_reason'] or '')])
        a['without_manual']=stats([r for r in closed if '手动' not in (r['exit_reason'] or '')])
        a['risk_by_period']={}
        for period in ['early','late']:
            sample=[r for r in closed if r['period']==period and r['risk_usd']>0]
            a['risk_by_period'][period]={
                'n':len(sample),'median_initial_risk_usd':statistics.median(r['risk_usd'] for r in sample),
                'fixed_10usd_risk_pnl':sum(10*r['r'] for r in sample),
                'direction':groups(sample,'direction')}
        a['risk_caps']={str(cap):stats([dict(pnl=r['pnl']*min(1,cap/r['risk_usd'])) for r in closed if r['period']=='late' and r['risk_usd']>0]) for cap in [10,20]}
        # Supplement recent-policy study with the same five-minute resolution
        # for every variant. The full-history hourly study remains separate.
        p5=OUT/'recent_5m_raw.json'
        if p5.exists():
            raw5=json.loads(p5.read_text())
            five=[dict(t=b[0]/1000,end=(b[6]+1)/1000,high=float(b[2]),low=float(b[3]),close=float(b[4])) for b in raw5]
            recent=[dict(r) for r in closed if r['period']=='late']
            enrich(recent,five)
            recent=[r for r in recent if r['replay_ok']]
            a['recent_5m_replays']=replay_summary(recent)
            a['recent_5m_by_phase']={p:replay_summary([r for r in recent if r['phase']==p]) for p in sorted({r['phase'] for r in recent})}
            a['recent_5m_mfe']=[{k:r[k] for k in ['open_id','open','close','pnl','mfe_r_lower','r','risk_usd']} for r in recent]
            a['recent_5m_per_trade']=[dict(open_id=r['open_id'],baseline=r['pnl'],variants=r['_replays']) for r in recent]
        result['accounts'][name]=a
    (OUT/'history_audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    fields=[k for k in rows[0] if not k.startswith('_')]
    with (OUT/'round_trips.csv').open('w') as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
        w.writerows({k:r[k] for k in fields} for r in rows)
    print(json.dumps({'bar_last':result['bar_last'],'accounts':{k:{x:v[x] for x in ['clean_complete','net_5bps','replay_coverage']} for k,v in result['accounts'].items()}},indent=2))
    return result


if __name__=='__main__':
    audit()
