#!/usr/bin/env python3
"""统一测试入口：发现并运行 tests/ 下所有 test_* 函数。

每个测试文件只需定义 test_* 函数，不需要自己写 sys.path 引导和 main 运行器。

用法:
    python tests/run_all.py              # 运行全部
    python tests/run_all.py capital_guard # 只跑名字里含该关键字的模块
"""

import importlib
import inspect
import os
import sys
import traceback

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(TESTS_DIR)

# 测试模块用顶层包路径导入 (core.*, server.* ...)，因此项目根目录必须在 sys.path 首位
sys.path.insert(0, ROOT_DIR)


def discover_modules(keyword: str = ""):
    names = sorted(
        f[:-3] for f in os.listdir(TESTS_DIR)
        if f.startswith("test_") and f.endswith(".py")
    )
    return [n for n in names if keyword in n]


def run() -> int:
    keyword = sys.argv[1] if len(sys.argv) > 1 else ""
    passed, failures = 0, []

    for mod_name in discover_modules(keyword):
        try:
            module = importlib.import_module(f"tests.{mod_name}")
        except Exception:
            failures.append((mod_name, "<import>", traceback.format_exc()))
            print(f"❌ {mod_name} 导入失败")
            continue

        test_fns = [
            fn for name, fn in inspect.getmembers(module, inspect.isfunction)
            if name.startswith("test_") and fn.__module__ == module.__name__
        ]
        print(f"\n── {mod_name} ({len(test_fns)} tests)")

        for fn in test_fns:
            try:
                fn()
                passed += 1
                print(f"  ✅ {fn.__name__}")
            except Exception:
                failures.append((mod_name, fn.__name__, traceback.format_exc()))
                print(f"  ❌ {fn.__name__}")

    print("\n" + "=" * 60)
    for mod_name, fn_name, tb in failures:
        print(f"\n❌ {mod_name}::{fn_name}\n{tb}")
    print(f"结果: {passed} passed, {len(failures)} failed")
    print("=" * 60)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(run())
