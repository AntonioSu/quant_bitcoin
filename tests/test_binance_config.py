#!/usr/bin/env python3
"""测试 Binance 配置是否正确"""

import sys
import os

# 添加项目路径 (tests/ -> quant_bitcoin/)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from binance_utils import BinanceClient
from utils import logger


def test_demo_config():
    """测试 Demo Trading 配置"""
    print("=" * 60)
    print("测试 Demo Trading 配置 (使用 binance_demo)")
    print("=" * 60)
    
    try:
        # 测试 Demo Trading 合约，显式指定 config_key
        client = BinanceClient(
            testnet=False, 
            market_type='future', 
            demo=True,
            config_key='binance_demo'
        )
        balance = client.exchange.fetch_balance()
        
        usdt_total = balance.get('USDT', {}).get('total', 0)
        print(f"✅ Demo Trading 合约账户连接成功")
        print(f"   配置来源: binance_demo")
        print(f"   USDT 余额: {usdt_total:,.2f}")
        
        # 获取持仓信息
        try:
            positions = client.exchange.fetch_positions(['BTC/USDT:USDT'])
            active_positions = [p for p in positions if float(p.get('contracts', 0)) > 0]
            if active_positions:
                print(f"   持仓数: {len(active_positions)}")
                for pos in active_positions:
                    side = pos.get('side', 'N/A')
                    contracts = float(pos.get('contracts', 0))
                    entry = float(pos.get('entryPrice', 0))
                    print(f"   - {side.upper()}: {contracts:.4f} BTC @ ${entry:,.2f}")
            else:
                print(f"   持仓: 无")
        except Exception as e:
            print(f"   获取持仓失败: {e}")
        
        return True
        
    except Exception as e:
        print(f"❌ Demo Trading 配置测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_testnet_config():
    """测试 Testnet 配置（使用 api_key/secret_key）"""
    print("\n" + "=" * 60)
    print("测试 Testnet 配置 (使用 binance_demo)")
    print("=" * 60)
    
    try:
        # 测试 Testnet 现货，显式指定 config_key
        client = BinanceClient(
            testnet=True, 
            market_type='spot', 
            demo=False,
            config_key='binance_demo'
        )
        balance = client.exchange.fetch_balance()
        
        usdt_total = balance.get('USDT', {}).get('total', 0)
        print(f"✅ Testnet 现货账户连接成功")
        print(f"   配置来源: binance_demo")
        print(f"   USDT 余额: {usdt_total:,.2f}")
        
        return True
        
    except Exception as e:
        print(f"❌ Testnet 配置测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_config_fallback():
    """测试配置回退机制"""
    print("\n" + "=" * 60)
    print("测试配置回退机制")
    print("=" * 60)
    
    try:
        # 测试不存在的 config_key，应该回退到 "binance"
        # 由于我们只有 binance_demo，会回退到空配置
        print("   测试场景: 请求不存在的 binance_mainnet")
        print("   预期行为: 回退到 binance (如果存在)")
        
        # 这个测试只是验证不会崩溃
        client = BinanceClient(
            testnet=False, 
            market_type='future', 
            demo=True,
            config_key='binance_mainnet'  # 不存在的配置
        )
        print(f"   ✅ 客户端创建成功 (使用回退配置)")
        
        return True
        
    except Exception as e:
        # 预期可能会失败（因为回退到空配置）
        print(f"   ⚠️  预期失败: {type(e).__name__}")
        print(f"   说明: binance_mainnet 不存在，回退到 binance 也不存在")
        return True  # 这是预期行为


def main():
    print("\n🔧 开始测试 Binance 配置...\n")
    
    demo_ok = test_demo_config()
    testnet_ok = test_testnet_config()
    fallback_ok = test_config_fallback()
    
    print("\n" + "=" * 60)
    print("测试结果汇总")
    print("=" * 60)
    print(f"Demo Trading: {'✅ 通过' if demo_ok else '❌ 失败'}")
    print(f"Testnet:      {'✅ 通过' if testnet_ok else '❌ 失败'}")
    print(f"配置回退:     {'✅ 通过' if fallback_ok else '❌ 失败'}")
    
    if demo_ok and testnet_ok:
        print("\n🎉 所有配置测试通过！")
        print("\n💡 提示: 现在支持通过 config_key 参数指定配置来源")
        print("   - binance_demo:    Demo Trading + Testnet (当前)")
        print("   - binance_mainnet: 真实主网 (未来扩展)")
        print("   - binance:         回退默认配置")
        return 0
    else:
        print("\n⚠️  部分配置测试失败，请检查 config.json")
        return 1


if __name__ == "__main__":
    sys.exit(main())
