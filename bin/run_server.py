#!/usr/bin/env python3
"""
启动 BTC 神盾-长矛监控服务器

用法:
    python run_server.py [--port 8088] [--host 0.0.0.0]

访问:
    http://localhost:8088
"""

import argparse
import sys
import os
from pathlib import Path

# 添加路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 加载 .env 文件
def load_env():
    env_file = Path(__file__).parent.parent / ".env"
    if env_file.exists():
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    os.environ.setdefault(key.strip(), value.strip())

load_env()

# 代理配置 (访问 Binance 等境外 API)
_PROXY = os.getenv("HTTPS_PROXY", "http://gfw.in.zhihu.com:18080")
os.environ.setdefault("HTTPS_PROXY", _PROXY)
os.environ.setdefault("https_proxy", _PROXY)


def main():
    parser = argparse.ArgumentParser(description="BTC 神盾-长矛监控服务器")
    parser.add_argument("--host", default="0.0.0.0", help="绑定地址")
    parser.add_argument("--port", type=int, default=8088, help="端口号")
    parser.add_argument("--reload", action="store_true", help="开发模式(自动重载)")
    
    # 三种运行模式 (独立开关，可以同时启用)
    # 模拟盘开关
    parser.add_argument("--no-sim", action="store_true",
                        help="禁用模拟盘 (默认启用)")
    
    # Demo Trading 开关 + 预设
    parser.add_argument("--demo", action="store_true",
                        help="启用 Demo Trading (Binance Demo API，虚拟资金)")
    parser.add_argument("--demo-preset", type=str, default="aggressive", metavar="PRESET",
                        help="Demo Trading 使用的预设 (默认 aggressive)")
    
    # 真实主网开关 + 预设
    parser.add_argument("--live", action="store_true",
                        help="启用真实主网 (Binance Mainnet API，真实资金)")
    parser.add_argument("--live-preset", type=str, default="aggressive", metavar="PRESET",
                        help="真实主网使用的预设 (默认 aggressive)")
    
    parser.add_argument("--max-capital", type=float, default=500.0,
                        help="Demo/实盘资金上限 (默认$500)")
    parser.add_argument("--mainnet", action="store_true",
                        help="确认使用真实主网交易 (需要与--live一起使用)")
    
    args = parser.parse_args()
    
    use_sim = not args.no_sim
    
    # 安全检查：真实主网需要显式确认
    if args.live and not args.mainnet:
        print("❌ 错误: 真实主网模式需要显式指定 --mainnet 参数以确认风险")
        print("   如果要使用 Demo Trading，请使用 --demo 参数")
        sys.exit(1)
    
    import uvicorn

    # 启动完整服务 (API + 调度器)
    mode_lines = []
    
    if use_sim:
        mode_lines.append(f"║  📊 模拟盘:   [全部] (conservative/standard/aggressive)       ║")
    
    if args.demo:
        mode_lines.append(f"║  🟡 Demo盘:   [{args.demo_preset}] (Binance Demo API, 虚拟资金)      ║")
    
    if args.live:
        mode_lines.append(f"║  🔴 真实主网: [{args.live_preset}] (⚠️ Binance Mainnet, 真实资金!)   ║")
    
    if not mode_lines:
        mode_lines.append(f"║  ⚠️  警告: 所有调度器均已禁用                                 ║")
    
    mode_info = "\n".join(mode_lines)
        
    print(f"""
╔══════════════════════════════════════════════════════════════╗
║       🛡️ BTC 神盾-长矛 监控系统                              ║
╠══════════════════════════════════════════════════════════════╣
║  📊 监控面板: http://localhost:{args.port}                         ║
║  📖 API 文档: http://localhost:{args.port}/docs                     ║
║  🔄 后台调度: 已启用 (每60秒检查信号)                         ║
╠══════════════════════════════════════════════════════════════╣
{mode_info}
║  💰 资金上限: ${args.max_capital:,.0f}                                      ║
║  ⏹️  停止服务: Ctrl+C                                         ║
╚══════════════════════════════════════════════════════════════╝
    """)
        
    # 真实主网二次确认
    if args.live:
        print("""
⚠️  ═══════════════════════════════════════════════════════════ ⚠️
    警告: 即将启动 BINANCE MAINNET 真实交易!
    这将使用真实资金进行交易，可能导致损失！
⚠️  ═══════════════════════════════════════════════════════════ ⚠️
""")
        confirm = input("请输入 'YES' 确认继续: ")
        if confirm != "YES":
            print("❌ 已取消")
            sys.exit(0)
    
    from server.scheduler import create_integrated_app
    app = create_integrated_app(
        use_sim=use_sim,
        use_demo=args.demo,
        use_live=args.live,
        demo_preset=args.demo_preset,
        live_preset=args.live_preset,
        max_capital=args.max_capital,
    )
    
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
    )


if __name__ == "__main__":
    main()
