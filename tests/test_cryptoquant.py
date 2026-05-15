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
print("测试 CryptoQuant API")
print("=" * 60)

api_key = os.getenv("CRYPTOQUANT_API_KEY")
print(f"\nAPI Key: {api_key[:20]}...{api_key[-10:] if api_key else 'None'}")

whale = WhaleAlert(provider=WhaleDataProvider.CRYPTOQUANT, api_key=api_key)

try:
    print("\n正在获取数据...")
    data = whale.fetch()
    summary = whale.get_flow_summary()
    
    print(f"\n✅ 成功获取数据!")
    print(f"  净流入: {data.value:+.2f} BTC")
    print(f"  流入: {summary['inflow']:.2f} BTC")
    print(f"  流出: {summary['outflow']:.2f} BTC")
    print(f"  时间: {data.timestamp}")
    print(f"  卖压信号 (>2000): {whale.is_selling_pressure()}")
    print(f"  囤币信号 (<-2000): {whale.is_accumulation()}")
    
except Exception as e:
    print(f"\n❌ 获取失败: {e}")
    import traceback
    traceback.print_exc()
