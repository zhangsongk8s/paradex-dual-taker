# 更新日志 (Changelog)

## v1.1.0 (2026-02-03)

### 新增功能
- **`get_auth.py`** - 账号登录状态提取工具
  - 支持批量提取多个 Paradex 账号的登录状态
  - 自动保存认证信息到 `data/` 目录
  - 交互式操作，支持在浏览器中手动登录后自动保存
  - 使用方法：在有图形界面的环境下运行 `python3 get_auth.py`

- **`network_diagnostic.py`** (精简版)
  - 专为浏览器自动化策略优化的网络诊断工具
  - 移除了无用的 API 延迟、丢包率等测试项
  - 保留 4 项核心测试：
    1. DNS 解析 (app.paradex.trade)
    2. Web 连接测试
    3. 浏览器加载测试 (Playwright + 关键元素检测)
    4. VPS 地理位置信息
  - 输出更加简洁直观

### 改进
- 更新 `README.md`，添加新脚本的使用说明
- 添加 `.gitignore`，忽略运行时生成的临时文件

---

## v1.0.0 (2026-02-02)

### 初始版本
- **`main.py`** - 核心交易逻辑，双账号价差监控与自动交易
- **`dashboard.py`** - 基于 Rich 的终端监控面板
- **`exit_handler.py`** - 优雅退出处理器
- **`order_guard.py`** - 24小时交易限制风控守卫
- **`requirements.txt`** - 项目依赖
