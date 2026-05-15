import sys
sys.path.insert(0, '/data1/suwenyuan/agent')

from stock_btc.data_sources.funding_rate import FundingRate

# 测试 U本位合约 (与币安截图一致)
print('=== U本位合约 (BTCUSDT) ===')
fr_usdt = FundingRate(symbol='BTCUSDT', use_coin_m=False)
data = fr_usdt.fetch()
print(f'当前费率: {data.value:.5f}%')
print(f'年化收益: {fr_usdt.get_annual_yield():.2%}')

print('\n历史费率 (最近5次):')
for h in fr_usdt.fetch_history(5):
    print(f'  {h["time"]}: {h["rate_pct"]:.5f}%')

print('\n=== 币本位合约 (BTCUSD_PERP) ===')
fr_coin = FundingRate(symbol='BTCUSD_PERP', use_coin_m=True)
data = fr_coin.fetch()
print(f'当前费率: {data.value:.5f}%')

print('\n历史费率 (最近5次):')
for h in fr_coin.fetch_history(5):
    print(f'  {h["time"]}: {h["rate_pct"]:.5f}%')
