#!/bin/bash
# cleanup_for_github.sh
# 严格清理脚本：只保留用户指定的5个核心文件和必要的项目描述文件

echo "🧹 正在执行 GitHub 发布前的清理工作 (严格模式)..."

# 1. 核心保留名单
# main.py, dashboard.py, exit_handler.py, order_guard.py, network_diagnostic.py
# requirements.txt, README.md, cleanup_for_github.sh

# 2. 删除数据和敏感信息
echo "🔥 删除所有数据文件 (data/*, trade_history*, auth*)..."
rm -rf data
rm -f trade_history_*.json
rm -f *.json

# 3. 删除日志
echo "🔥 清空日志目录..."
rm -rf logs
mkdir logs
touch logs/.gitkeep

# 4. 删除文档和其他脚本 (只保留指定的5个)
echo "🔥 删除非核心脚本和文档..."
rm -rf docs
rm -f paradex_bot.py
rm -f test_vps_comprehensive.py
rm -f ping.py
rm -f cleanup_logs.sh
rm -f setup_auto_cleanup.sh
rm -f *.bak

# 5. 清理 Python 缓存
echo "🧹 清理 Python 编译缓存..."
find . -type d -name "__pycache__" -exec rm -rf {} +
find . -name "*.pyc" -delete

echo "✨ 清理完成！"
echo "📂 当前目录剩余文件 (确认仅包含核心文件):"
ls -lh
