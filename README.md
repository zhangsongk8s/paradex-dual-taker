# Paradex Dual Taker (双账号策略交易机器人)

这是一个针对 Paradex 永续合约交易所的高频价差监控与自动交易机器人。它使用双账号（Taker）策略，通过 WebSocket 实时监控盘口，在捕捉到预设价差时进行零延迟交易。

## 📂 项目核心文件

本项目包含以下 6 个核心脚本：

| 文件名 | 说明 |
| :--- | :--- |
| **`main.py`** | **主程序**。负责核心交易逻辑、WebSocket 监听、多账号协调。 |
| **`dashboard.py`** | **监控面板**。基于终端（TUI）的实时状态显示界面。 |
| **`exit_handler.py`** | **退出句柄**。处理程序优雅退出和状态清理。 |
| **`order_guard.py`** | **风控守卫**。限制最大交易次数，防止过度交易。 |
| **`network_diagnostic.py`** | **网络诊断**。测试本机到 Paradex 的延迟和连通性。 |
| **`get_auth.py`** | **登录提取**。用于提取账号登录状态，保存为 JSON 文件。 |

## 🚀 快速开始

### 1. 环境要求
*   Python 3.10+
*   Chrome 浏览器 (用于 Playwright 自动化)

### 2. 安装依赖
```bash
pip install -r requirements.txt
playwright install chromium
```

### 3. 获取账号认证文件
使用 `get_auth.py` 脚本提取账号登录状态（需要在有图形界面的环境下运行）：
```bash
python3 get_auth.py
```
按照提示在浏览器中登录账号，脚本会自动将认证信息保存到 `data/` 目录。

### 4. 运行
**启动交易机器人：**
```bash
python3 main.py
```

**测试网络环境：**
```bash
python3 network_diagnostic.py
```

## ⚠️ 免责声明
本项目仅供技术研究和教育用途。使用本软件产生的任何盈亏由使用者自行承担。
