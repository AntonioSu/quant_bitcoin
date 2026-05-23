#!/usr/bin/env python3
"""测试重构后的配置加载"""

import sys
import os
# tests/ -> quant_bitcoin/
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.scheduler import _load_binance_config
from utils import logger


def test_config_loading():
    """测试配置加载"""
    print("=" * 60)
    print("测试配置加载功能")
    print("=" * 60)
    
    # 测试 Demo 配置
    try:
        demo_cfg = _load_binance_config("binance_demo")
        print(f"\n✅ binance_demo 配置加载成功")
        print(f"   有 API Key: {bool(demo_cfg.get('api_key'))}")
        print(f"   有 Demo API Key: {bool(demo_cfg.get('demo_api_key'))}")
        print(f"   有代理配置: {bool(demo_cfg.get('proxy'))}")
    except Exception as e:
        print(f"\n❌ binance_demo 配置加载失败: {e}")
        return False
    
    # 测试主网配置 (预期会回退到 binance)
    try:
        live_cfg = _load_binance_config("binance_mainnet")
        print(f"\n⚠️  binance_mainnet 配置加载 (可能回退)")
        print(f"   有 API Key: {bool(live_cfg.get('api_key'))}")
    except Exception as e:
        print(f"\n⚠️  binance_mainnet 配置不存在 (预期行为): {e}")
    
    return True


def test_executor_creation():
    """测试执行器创建"""
    print("\n" + "=" * 60)
    print("测试执行器创建")
    print("=" * 60)
    
    try:
        from binance_utils import create_futures_executor
        
        # 加载 Demo 配置，使用 demo_api_key
        demo_cfg = _load_binance_config("binance_demo", use_demo_key=True)
        
        # 创建 Demo 执行器
        executor = create_futures_executor(
            binance_cfg=demo_cfg,
            assets_config={"bitcoin": {"symbol": "BTC/USDT:USDT", "coin": "BTC", "precision": 3}},
            demo=True,
            leverage=5,
        )
        
        print(f"\n✅ Demo Trading 执行器创建成功")
        print(f"   杠杆: {executor.default_leverage}x")
        
        # 测试获取持仓
        portfolio = executor.get_portfolio("bitcoin")
        print(f"   余额: ${portfolio.get('balance', 0):,.2f}")
        print(f"   仓位: {portfolio.get('direction', 'NONE')}")
        
        return True
        
    except Exception as e:
        print(f"\n❌ 执行器创建失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    print("\n🔧 开始测试重构后的配置系统...\n")
    
    config_ok = test_config_loading()
    executor_ok = test_executor_creation()
    
    print("\n" + "=" * 60)
    print("测试结果汇总")
    print("=" * 60)
    print(f"配置加载: {'✅ 通过' if config_ok else '❌ 失败'}")
    print(f"执行器创建: {'✅ 通过' if executor_ok else '❌ 失败'}")
    
    if config_ok and executor_ok:
        print("\n🎉 所有测试通过！")
        print("\n💡 配置系统已重构:")
        print("   - scheduler.py 根据 demo/live 模式自动选择配置")
        print("   - Demo 模式使用 binance_demo 配置")
        print("   - Live 模式使用 binance_mainnet 配置")
        print("   - binance_client.py 只负责连接，不关心配置来源")
        return 0
    else:
        print("\n⚠️  部分测试失败，请检查代码")
        return 1


if __name__ == "__main__":
    sys.exit(main())
