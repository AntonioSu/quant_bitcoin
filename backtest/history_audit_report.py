"""Render audit JSON/CSV into a standalone HTML report and exportable chart.

Run after history_audit: .venv/bin/python -m backtest.history_audit_report
"""
import csv
import html
import json
from datetime import datetime
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data/research'
LABELS={'aggressive':'激进模拟盘','conservative':'保守模拟盘','standard':'标准模拟盘'}
VARIANTS={'baseline':'原始退出','tp_1r':'达到1R全平','tp_1_5r':'达到1.5R全平',
          'be_after_1r':'达到1R后回成本退出','trail_1_5r_0_6r':'达到1.5R后回撤0.6R退出',
          'giveback_0_75r_half':'达到0.75R后回吐峰值50%退出'}


def table(headers, rows):
    return '<div class="scroll"><table><thead><tr>'+''.join('<th>'+html.escape(str(x))+'</th>' for x in headers)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+html.escape(str(x))+'</td>' for x in row)+'</tr>' for row in rows)+'</tbody></table></div>'


def money(x):
    return f'{x:+,.2f}'


def plot(data, rows):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    plt.rcParams.update({'font.size':10,'axes.spines.top':False,'axes.spines.right':False})
    fig, axs=plt.subplots(2,2,figsize=(14,9),layout='constrained')
    raw=json.loads((ROOT/'data/trading_state_aggressive_sim.json').read_text())['trades']
    old=json.loads((ROOT/'data/trading_state_aggressive_sim.json.bak.20260901_margin_recalc').read_text())['trades']
    lookup={(t['id'],t['time']):t for t in old}
    xs=[]; current=[]; original=[]; a=b=0
    for t in raw:
        if t['action'] not in ('CLOSE','REDUCE','TP1_HALF'):continue
        xs.append(datetime.fromisoformat(t['time']));a+=t['pnl'];b+=lookup.get((t['id'],t['time']),t)['pnl']
        current.append(a);original.append(b)
    axs[0,0].plot(xs,current,label='Current ledger',color='#2563eb')
    axs[0,0].plot(xs,original,label='Backup original sizing + later fills',color='#dc2626')
    axs[0,0].set_title('Aggressive: realized PnL provenance (no costs)')
    axs[0,0].set_ylabel('Cumulative USD');axs[0,0].legend(fontsize=8)
    axs[0,0].xaxis.set_major_formatter(mdates.DateFormatter('%m-%d'))
    names=list(LABELS)
    for i,n in enumerate(names):
        a=data['accounts'][n]
        axs[0,1].bar(i-.18,a['groups']['direction']['LONG']['pnl'],width=.36,color='#16a34a',label='LONG' if i==0 else None)
        axs[0,1].bar(i+.18,a['groups']['direction']['SHORT']['pnl'],width=.36,color='#dc2626',label='SHORT' if i==0 else None)
    axs[0,1].set_xticks(range(3),names);axs[0,1].set_title('Complete positions: gross PnL by side');axs[0,1].legend()
    a=data['accounts']['aggressive'];v=a['recent_5m_replays'];keys=list(VARIANTS)
    ys=[v[k]['all']['net_5bps']['pnl'] for k in keys]
    axs[1,0].barh(['Baseline','TP 1R','TP 1.5R','BE after 1R','Trail 1.5R / 0.6R','Peak 0.75R / 50%'],ys,color=['#16a34a' if y>0 else '#dc2626' for y in ys])
    axs[1,0].set_title('Recent 19: conditional 5m replay, 5bps/side');axs[1,0].set_xlabel('Net USD, excludes funding / slippage');axs[1,0].invert_yaxis()
    recent=a['recent_5m_mfe'];xs=range(len(recent))
    axs[1,1].plot(xs,[r['mfe_r_lower'] for r in recent],marker='o',label='MFE lower bound',color='#2563eb')
    axs[1,1].bar(xs,[r['r'] for r in recent],label='Realized R',color=['#16a34a' if r['r']>0 else '#dc2626' for r in recent])
    axs[1,1].set_title('Recent 19: favorable excursion vs realized R')
    axs[1,1].set_xticks(list(xs),[r['open'][5:10] for r in recent],rotation=60,fontsize=8);axs[1,1].legend()
    for ax in axs.flat:ax.axhline(0,color='#94a3b8',linewidth=.8);ax.grid(axis='y',alpha=.15)
    fig.savefig(OUT/'history_audit.png',dpi=160)
    fig.savefig(OUT/'history_audit.svg')
    plt.close(fig)


def main():
    data=json.loads((OUT/'history_audit.json').read_text())
    rows=list(csv.DictReader((OUT/'round_trips.csv').open()))
    plot(data,rows)
    a=data['accounts']['aggressive'];manifest=data['manifest']['aggressive'];rev=manifest['revision']
    sections=[]
    def add(title,text):sections.append('<section><h2>'+title+'</h2>'+text+'</section>')
    add('结论：先解决账本口径、做空质量和风险预算，再优化退出',
        '<p>全历史不能支持“只要及时止盈就能解决亏损”。激进盘做空长期为负，单笔止损风险显著增加，盈利回吐与快速重开也造成损失。'+
        '当前累计利润受到一次事后仓位重算和一笔人工清仓的大盈利强烈影响。以下把已证实事实、条件回放和待验证改动分开呈现。</p>'+ 
        '<p><strong>本次只新增研究脚本、数据和报告，没有更改交易策略、订单或运行中的服务。</strong></p>')
    add('覆盖范围与统计口径',
        '<p>读取三个当前模拟盘账本的全部958条记录，时间覆盖2026-04-14至2026-10-15；重建348个持仓周期。'+
        '其中333个有最终CLOSE，另排除标准盘1个数量不一致的周期后，干净完整样本为332个。三个预设共享行情和信号，不是332次独立实验。'+
        '备份用于账本溯源，不重复累计；空的Demo账本、停用的旧版账户不混入当前模拟盘。标准盘9月末已改为仅做多机械策略，不能当成全程相同AI策略的对照组。</p>'+
        '<p>本报告按开仓日期归属月份和策略时期；所有时间为北京时间。部分减仓合并至完整持仓，不当成独立成功交易。'+
        '网页绩效的251笔对应激进盘251条退出记录，而实际只有146个完整平仓周期。金额单位USDT，表中省略货币符号。</p>'+
        table(['账户','原始记录','完整且数量一致','完整周期毛盈亏','毛胜率','假设单边5bps后'],
              [[LABELS[n],data['manifest'][n]['raw_records'],x['clean_complete']['n'],money(x['clean_complete']['pnl']),str(x['clean_complete']['win_pct'])+'%',money(x['net_5bps']['pnl'])] for n,x in data['accounts'].items()]))
    add('1. 累计盈利并不代表当前自动策略稳定赚钱',
        f'<p>激进盘账本记录累计为{money(manifest["record_pnl"])}，状态字段为{money(manifest["saved_pnl"])}，两者有0.16的历史差额。'+
        f'与9月1日重算前备份按ID和时间配对，{rev["matched"]}条记录一致对应，其中12条数量、11条盈亏发生修改；'+
        f'这次修改使该段记录的总盈亏增加{money(rev["delta"])}。将匹配记录恢复为备份原始数量口径、其余后续记录保持不变，整本账累计为{money(rev["original_size_ledger_pnl"])}，仍未扣费用。</p>'+
        '<p>核心是8月18日开多、8月30日最终人工清仓的周期：开仓数量由0.007822 BTC变为0.03911 BTC，放大5倍；当前该周期盈利525.38。'+
        f'其余145个完整周期合计{money(a["without_manual"]["pnl"])}。这不表示大盈利应该删除，而是说明当前利润高度集中，且不能把事后重算账本当成未经修改的成交实绩。</p>'+
        '<p>建议：成交事件永久保留原始数量和费用；历史重算另存“重述口径”，页面分别展示原始实绩、重述结果、当前策略版本表现。不要覆盖或自动修补旧记录。</p>')
    add('2. 做空是跨时期、跨预设都出现的弱项',
        table(['账户','全历史多单毛盈亏','全历史空单毛盈亏','9月以来多单','9月以来空单'],
              [[LABELS[n],money(x['groups']['direction']['LONG']['pnl']),money(x['groups']['direction']['SHORT']['pnl']),money(x['risk_by_period']['late']['direction']['LONG']['pnl']),money(x['risk_by_period']['late']['direction']['SHORT']['pnl'])] for n,x in data['accounts'].items()])+
        '<p>激进盘9月以来空单9笔，2胜7负，合计亏175.38；多单10笔合计盈利66.78。9月以前的空单也为负。'+
        '这是优先拆分多空策略评价的证据，不是“BTC永远不能做空”的证明。时期行情、入场位置、信号延迟和退出规则均可能影响结果。</p>'+ 
        '<p>建议：做空单独记录准入与失效条件，在影子盘比较“回抽失败确认后入场”与当前直接顺势入场；确认样本外净期望前，不把多头侧表现当作空头侧有效性的证据。</p>')
    add('3. 仓位按保证金×杠杆定，止损风险没有统一预算',
        f'<p>激进盘9月前单笔初始止损金额中位数约4.65，9月以来约39.46，约增至8.5倍。日期前后策略和行情都在变，因此不能把亏损放大完全归因于杠杆。'+
        '但代码确实先用保证金比例×杠杆确定名义本金，再计算ATR止损；不是用允许亏损金额反推仓位。</p>'+
        table(['9月以来激进盘，保持原成交路径','毛盈亏','已实现周期回撤'],
              [['原始仓位',money(a['recent_5m_replays']['baseline']['all']['gross']['pnl']),185.96]]+
              [[f'每笔初始风险最多{cap}，只缩小不放大',money(x['pnl']),x['max_realized_dd_usd']] for cap,x in a['risk_caps'].items()])+
        '<p>这是按原成交结果缩放的敏感性分析，不含手续费、资金费和仓位改变后的决策反馈；不能据此声称10或20是最佳参数。'+
        '建议把最大允许亏损作为硬约束：数量上限＝风险预算÷入场到止损的距离，再与保证金、杠杆、最小下单量约束取交集。先确定风险预算，再决定杠杆。</p>')
    add('4. 盈利回吐存在，但固定小止盈会截断大赢家',
        '<p>9月以来激进盘19个完整周期中14个亏损；按完整5分钟K线和实际成交重建，至少8个亏损周期曾达到0.5R浮盈，至少3个曾达到1R。'+
        '这支持优化回吐管理，但剩余亏损不能靠“在没出现过的盈利点止盈”解决。MFE是已覆盖价格的下界，不能当成可提前知道的成交价。</p>'+
        table(['条件退出实验（同一19笔入场）','毛盈亏','假设单边5bps后','发生提前退出的周期'],
              [[VARIANTS[k],money(v['all']['gross']['pnl']),money(v['all']['net_5bps']['pnl']),v['all']['changed']] for k,v in a['recent_5m_replays'].items()])+
        '<p>回放在完整5分钟K线收盘观察信号并按该收盘价成交，不假设柱内触价成交；保留实验退出前的原始减仓，实验全平后忽略剩余原始退出。'+
        '不产生新入场、不重算后续AI、不复利、不修改实际历史资金占用。费用5bps/边仅为统一情景，不是账户实际费率；未包含滑点、延迟及资金费。'+
        '所以这些是条件回放，不是可交易策略的净收益承诺。</p>'+
        '<p>0.75R后回吐一半这一候选在激进盘改善明显，但同一规则在保守盘同期仍亏94.22，扣假设费用后亏145.27。'+
        '统一1R全平则让激进盘从亏108.60变成亏120.83，9月18日后尤其会截断大赢家。不能仅为了提高胜率设置很近的固定止盈。</p>')
    add('5. 快速重开值得控制，但冷却时间并未得到普遍验证',
        '<p>激进盘9月以来，盈利退出后1小时内同向重开的4笔全部亏损，合计亏133.12。若仅从既有记录中剔除这些样本，剩余15笔为盈利24.52，扣假设费用后仍亏12.68。'+
        '将全部1小时内重开的7笔剔除，剩余12笔毛盈利41.05，扣假设费用后盈利11.90。</p>'+
        '<p>但9月前“盈利后同向1小时重开”的样本合计为正；保守盘全历史的一刀切1小时冷却还会过滤掉主要大赢家。'+
        '因此本报告不把“删除亏损历史单”包装成冷却策略回测：真实跳过后，后续仓位、入场时刻、AI上下文都会变化。</p>'+
        '<p>建议：重新入场需要新结构或新确认，而不是只换一份研判ID；对“刚因反转/回吐退出，又同向进入”单独审查。固定1小时只是候选基线，需重放全部机会序列验证。</p>')
    add('6. 不能由最近一笔推导出的规则',
        '<p>“出现反向CVD背离就不入场”在本批完整历史样本上会删除大量盈利，包括近期盈利单；'+
        '按已收盘1小时K线重建的48小时区间位置过滤，也未呈现稳定改善。后者与线上4小时窗口、当根K线状态并不完全相同，不能据此断言现有护栏无效。'+
        '这两项结果说明：最近一笔存在背离或追空风险，只能用于个案解释，不能直接升级为全局硬门槛。</p>')
    add('策略版本与数据限制',
        table(['激进盘入场时期（提交日期近似）','完整周期','毛盈亏'],[[p,v['n'],money(v['pnl'])] for p,v in a['groups']['phase'].items()])+
        '<p>01：6月21日前；02：两层AI；03：8月29日至9月7日，仓位与阶梯退出变化；04：9月8日至17日，取消自动阶梯退出、AI移动止损；05：9月18日起增加保本与止损距离门槛。'+
        '提交时间不是精确部署时间；开仓跨版本的持仓可能混用退出规则。标准盘9月末切换机械策略另行解释。没有完整记录每个历史tick的策略版本，不能做严格因果归因。</p>'+
        '<p>完整历史小时级退出实验覆盖激进80/146、保守28/85、标准46/101个干净周期；短持仓常没有完整小时柱，不能把该子样本外推到全部交易。'+
        '近期5分钟实验覆盖9月以来全部干净周期19/19、19/19、13/13。仅使用持仓内完整柱，排除开平仓边界柱，避免将入场前或退出后的极值计入。'+
        '9月前后拆分是稳定性检查，不是真正未见过的留出集；候选规则是在查看历史后提出，有选择偏差，不给出显著性或未来收益结论。</p>'+
        '<p>15个周期没有最终CLOSE就出现下一次开仓：激进6、保守5、标准4；其中3笔旧激进仓只记了约一半退出，其余多为小额残仓。'+
        '保留已实现盈亏用于总账对账，但不纳入完整周期胜率或退出实验。标准盘另有1笔退出数量不一致。盈亏对账和异常清单均保存在JSON中。</p>')
    add('优化顺序与验收',
        '<ol><li><strong>先修评价基础：</strong>区分原始账本与重述账本；完整周期与成交腿分开；记录费用、资金费、MFE/MAE、策略版本、拒绝原因及最小残仓处置。</li>'+ 
        '<li><strong>统一单笔风险预算：</strong>按止损距离反推数量，AI只能在预算内选择；不因MODERATE信号反复改变承担的美元风险。</li>'+
        '<li><strong>拆开多空和重开验证：</strong>用相同费用和风险预算评价空头；重点对照结构确认、退出后重新确认与原始行为。</li>'+
        '<li><strong>影子验证盈利保护：</strong>冻结原始规则、1R基准、0.75R后回吐50%、1.5R后回撤0.6R几个候选；保留全部信号机会，重放跳过/提前退出后的后续入场，使用新的连续行情做前向比较。</li>'+
        '<li><strong>验收不只看胜率：</strong>净期望、最大回撤、成本敏感性、做空贡献、重新入场贡献，以及最大的几笔盈利是否被过早截断。新数据未证明改善前不自动推广参数。</li></ol>'+
        '<p>本次离线重建通过全账已实现盈亏对账；回放的部分减仓、时间顺序、空头符号、成本和回撤计算通过6项单元测试。未改动在线代码。</p>')
    add('可复核文件与复现',
        '<p><a href="round_trips.csv">全部持仓周期CSV</a> · <a href="history_audit.json">分组、回放和异常JSON</a> · '+
        '<a href="history_audit.svg">可缩放图表SVG</a> · <a href="history_audit.png">图表PNG</a></p>'+
        '<pre>python -m backtest.history_audit\npython -m unittest tests.test_history_audit\n.venv/bin/python -m backtest.history_audit_report</pre>'+
        '<p>行情：原本地BTCUSDT小时缓存，补充Binance公共市场数据接口 data-api.binance.vision 的小时和5分钟数据，'+
        '5分钟补充区间为2026-09-01 00:00至2026-10-15 16:00（北京时间）。所有模拟盘交易的入场价格与对应小时柱区间核对一致（容差0.1%）。'+
        '输入账本SHA-256保存在JSON；脚本读取当前账本，未来重跑会随账本新增而改变结果。</p>')
    doc='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>BTC 全历史交易审计</title><style>
    body{font:16px/1.85 system-ui,sans-serif;color:#172033;background:#f3f6fb;margin:0}main{max-width:1120px;margin:auto;padding:38px 24px}h1{font-size:32px;line-height:1.3}h2{font-size:22px;color:#193c70}section{background:white;border:1px solid #dce3ef;border-radius:12px;padding:22px 28px;margin:20px 0}p{margin:14px 0}table{border-collapse:collapse;width:100%;font-size:14px}th,td{padding:10px;text-align:left;border-bottom:1px solid #e2e8f0}th{background:#edf3fb;white-space:nowrap}.scroll{overflow:auto}img{width:100%;background:white;border-radius:12px}a{color:#1d4ed8}pre{background:#f1f5f9;padding:15px;overflow:auto}.sub{color:#64748b}li{margin:10px 0}@media print{body{background:white}section{break-inside:avoid}main{padding:0}}</style><main><h1>BTC 全历史交易审计与优化验证</h1><p class="sub">2026-10-15 · 以激进模拟盘为主，保守/标准预设交叉检查 · 研究报告，不修改交易规则</p>'''
    doc+=''.join(sections[:2])+'<img src="history_audit.png" alt="账本溯源、多空贡献、近期退出实验和浮盈回吐图表">'+''.join(sections[2:])+'</main></html>'
    (OUT/'history_audit.html').write_text(doc)
    print(OUT/'history_audit.html')


if __name__=='__main__':main()
