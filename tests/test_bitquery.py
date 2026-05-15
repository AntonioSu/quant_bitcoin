import sys
import os
sys.path.insert(0, '/data1/suwenyuan/agent')

# 加载 .env
from pathlib import Path
env_file = Path('/data1/suwenyuan/agent/stock_btc/.env')
if env_file.exists():
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ[key.strip()] = value.strip()

from stock_btc.data_sources import WhaleAlert, WhaleDataProvider

print("=" * 60)
print("测试 Bitquery API")
print("=" * 60)

# 提示用户输入 API Key
api_key = input("\n请输入你的 Bitquery API Key (如果还没有，请访问 https://bitquery.io/ 注册): ").strip()

if not api_key:
    print("\n❌ 未提供 API Key，无法测试")
    print("\n📝 注册步骤:")
    print("1. 访问 https://bitquery.io/")
    print("2. 点击 'Sign Up' 注册账号")
    print("3. 登录后，在 Dashboard 找到 API Key")
    print("4. 免费版提供: 1000点数, 10请求/分钟")
    sys.exit(1)

print(f"\nAPI Key: {api_key[:20]}...{api_key[-10:]}")

whale = WhaleAlert(provider=WhaleDataProvider.BITQUERY, api_key=api_key)

try:
    print("\n正在获取币安交易所 BTCB 流动数据...")
    print(f"监控地址数量: {len(whale.BINANCE_ADDRESSES)}")
    print(f"监控地址: {whale.BINANCE_ADDRESSES[0][:10]}... 等")
    
    data = whale.fetch()
    summary = whale.get_flow_summary()
    
    print(f"\n✅ 成功获取数据!")
    print(f"  净流入: {data.value:+.2f} BTCB (24小时)")
    print(f"  流入: {summary['inflow']:.2f} BTCB")
    print(f"  流出: {summary['outflow']:.2f} BTCB")
    print(f"  时间: {data.timestamp}")
    print(f"  数据更新次数: {data.raw.get('updates_count', 0)}")
    print(f"  卖压信号 (>2000): {whale.is_selling_pressure()}")
    print(f"  囤币信号 (<-2000): {whale.is_accumulation()}")
    
    print("\n💡 说明:")
    print("  - 监控 BSC 链上币安交易所地址的 BTCB (Binance-Peg Bitcoin) 流动")
    print("  - 正值 = 净流入 (币安收到更多币，可能有卖压)")
    print("  - 负值 = 净流出 (币安流出更多币，可能在囤币)")
    
except Exception as e:
    print(f"\n❌ 获取失败: {e}")
    import traceback
    traceback.print_exc()
    
    print("\n🔍 可能的原因:")
    print("1. API Key 无效或已过期")
    print("2. 免费额度已用完 (1000点数)")
    print("3. 请求频率超限 (10请求/分钟)")
    print("4. Bitquery 服务暂时不可用")
