"""
Paradex 账号登录状态提取脚本

用于提取多个 Paradex 账号的登录状态，
保存为 JSON 文件到 data/ 目录下。

使用方法：
1. 在 ACCOUNTS 列表中添加/修改账号配置
2. 运行脚本，按顺序登录各个账号
"""

from playwright.sync_api import sync_playwright
import os

# ==================== 配置区域 ====================
# 在这里添加或修改需要提取的账号
# 格式: {"display_name": "显示名称", "filename": "保存的文件名"}

ACCOUNTS = [
    # Shark 1-6 已提取，跳过
    # {"display_name": "🦈 Shark 3 (Group A - 主账号)", "filename": "auth_shark3.json"},
    # {"display_name": "🦈 Shark 4 (Group A - 对冲账号)", "filename": "auth_shark4.json"},
    # {"display_name": "🦈 Shark 5 (Group B - 主账号)", "filename": "auth_shark5.json"},
    # {"display_name": "🦈 Shark 6 (Group B - 对冲账号)", "filename": "auth_shark6.json"},
    
    # 新增 Group C
    {"display_name": "🦈 Shark 7 (Group C - 主账号)", "filename": "auth_shark7.json"},
    {"display_name": "🦈 Shark 8 (Group C - 对冲账号)", "filename": "auth_shark8.json"},
    
    # 如需添加更多账号，在此处添加新行：
    # {"display_name": "🦈 Shark 9 (Group D - 主账号)", "filename": "auth_shark9.json"},
]

# ==================================================

# 确保 data 目录存在，方便主程序读取
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")

if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)
    print(f"创建目录: {DATA_DIR}")

def login_and_save(account_display_name: str, filename: str):
    """
    启动浏览器，等待用户手动登录，然后保存登录状态
    """
    file_path = os.path.join(DATA_DIR, filename)
    
    print(f"\n{'='*60}")
    print(f"当前任务: {account_display_name}")
    print(f"保存路径: {file_path}")
    print(f"{'='*60}\n")
    
    with sync_playwright() as p:
        # 启动浏览器 (有头模式)
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        
        print(f"正在打开 Paradex 网站...")
        try:
            page.goto("https://app.paradex.trade", timeout=60000)
        except Exception as e:
            print(f"加载页面超时，请检查网络: {e}")

        # 提示用户操作
        print(f"\n{'='*60}")
        print(f"请在弹出的浏览器中操作：")
        print(f"  1. 连接钱包 ({account_display_name})")
        print(f"  2. 完成签名认证")
        print(f"  3. 等待页面加载完毕 (看到资产/持仓界面)")
        print(f"{'='*60}\n")
        
        input(f" >>> 登录成功后，请按回车键保存并继续... <<< ")
        
        # 保存状态
        print(f"\n正在保存...")
        context.storage_state(path=file_path)
        print(f"✓ 成功! 文件已保存: {filename}\n")
        
        browser.close()
        print(f"✓ 浏览器已关闭\n")

def main():
    print("\n" + "="*60)
    print("Paradex 多账号登录状态提取工具")
    print("="*60)
    print(f"文件将保存到: {DATA_DIR}")
    print(f"本次需要处理 {len(ACCOUNTS)} 个账号\n")
    
    # 显示账号列表
    for idx, account in enumerate(ACCOUNTS, 1):
        print(f"  {idx}. {account['display_name']} → {account['filename']}")
    
    print("\n" + "="*60)
    input("按回车键开始提取... ")
    
    # 循环处理每个账号
    for idx, account in enumerate(ACCOUNTS, 1):
        print(f"\n[进度: {idx}/{len(ACCOUNTS)}]")
        login_and_save(
            account_display_name=account["display_name"],
            filename=account["filename"]
        )
        
        # 在账号之间添加分隔提示
        if idx < len(ACCOUNTS):
            print("="*60)
            print(f"准备下一个账号 ({idx+1}/{len(ACCOUNTS)})...")
            print("="*60)
    
    # 完成提示
    print("\n" + "="*60)
    print("🎉 所有账号提取完成！")
    print("="*60)
    print("已保存的文件：")
    for account in ACCOUNTS:
        file_path = os.path.join(DATA_DIR, account["filename"])
        exists = "✓" if os.path.exists(file_path) else "✗"
        print(f"  {exists} {account['filename']}")
    print("="*60 + "\n")

if __name__ == "__main__":
    main()