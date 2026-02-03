#!/usr/bin/env python3
"""
🔬 Paradex 网络诊断工具 (精简版)
专为浏览器自动化策略优化，测试 VPS 到 Paradex Web 的连接质量
"""

import asyncio
import time
import socket
import requests
import json
from datetime import datetime
from playwright.async_api import async_playwright
from rich.console import Console
from rich.table import Table

console = Console()

class NetworkDiagnostic:
    def __init__(self):
        self.results = {}
        self.trade_url = "https://app.paradex.trade/trade/BTC-USD-PERP"
        self.web_url = "https://app.paradex.trade"
        
    def print_header(self):
        """打印诊断工具标题"""
        console.print("\n" + "="*60, style="bold blue")
        console.print("🔬 Paradex 网络诊断工具 (浏览器策略版)", style="bold blue", justify="center")
        console.print(f"⏰ 测试时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", justify="center")
        console.print("="*60 + "\n", style="bold blue")
    
    def test_dns_resolution(self):
        """测试 DNS 解析 (仅 Web 端)"""
        console.print("📍 [1/4] DNS 解析测试", style="bold yellow")
        
        domain = "app.paradex.trade"
        try:
            start = time.time()
            ip = socket.gethostbyname(domain)
            duration = (time.time() - start) * 1000
            self.results['dns'] = {
                "domain": domain,
                "ip": ip,
                "time": f"{duration:.2f}ms",
                "status": "✅"
            }
            console.print(f"  ✅ {domain} → {ip} ({duration:.2f}ms)", style="green")
        except Exception as e:
            self.results['dns'] = {
                "domain": domain,
                "ip": "N/A",
                "time": "N/A",
                "status": "❌"
            }
            console.print(f"  ❌ {domain} → 解析失败: {e}", style="red")
        
        console.print()
    
    def test_web_connectivity(self):
        """测试 Web 页面连接"""
        console.print("🌐 [2/4] Web 连接测试", style="bold yellow")
        
        try:
            start = time.time()
            headers = {
                'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36'
            }
            response = requests.get(self.web_url, timeout=10, headers=headers)
            duration = (time.time() - start) * 1000
            
            self.results['web'] = {
                "url": self.web_url,
                "status_code": response.status_code,
                "time": f"{duration:.2f}ms",
                "status": "✅" if response.status_code == 200 else "⚠️"
            }
            console.print(f"  ✅ Paradex Web: {response.status_code} ({duration:.2f}ms)", style="green")
        except Exception as e:
            self.results['web'] = {
                "url": self.web_url,
                "status_code": "N/A",
                "time": "N/A",
                "status": "❌"
            }
            console.print(f"  ❌ Paradex Web: 连接失败 - {e}", style="red")
        
        console.print()
    
    async def test_browser_loading(self):
        """测试浏览器页面加载（核心测试）"""
        console.print("🚀 [3/4] 浏览器加载测试 (Playwright)", style="bold yellow")
        
        try:
            async with async_playwright() as p:
                console.print("  🔧 启动 Chromium 浏览器...")
                browser = await p.chromium.launch(
                    headless=True,
                    args=[
                        '--disable-blink-features=AutomationControlled',
                        '--no-sandbox',
                        '--disable-gpu',
                        '--disable-dev-shm-usage',
                    ]
                )
                
                context = await browser.new_context(
                    viewport={'width': 1920, 'height': 1080}
                )
                
                page = await context.new_page()
                
                # 资源拦截（与 main.py 保持一致）
                async def route_intercept(route):
                    resource_type = route.request.resource_type
                    if resource_type in ["image", "media", "font"]:
                        await route.abort()
                        return
                    await route.continue_()
                
                await page.route("**/*", route_intercept)
                
                # 测试页面加载
                console.print(f"  📄 加载页面: {self.trade_url}")
                start = time.time()
                
                await page.goto(self.trade_url, wait_until='domcontentloaded', timeout=30000)
                load_time = (time.time() - start) * 1000
                console.print(f"  ✅ 页面加载成功: {load_time:.2f}ms", style="green")
                
                # 检测关键元素
                console.print("  🔍 检测关键交易元素...")
                
                elements_check = []
                
                # 检测 Market 标签
                try:
                    await page.wait_for_selector('span:has-text("Market")', timeout=5000)
                    console.print("    ✅ Market 标签", style="green")
                    elements_check.append(("Market 标签", "✅"))
                except:
                    console.print("    ⚠️ Market 标签未找到", style="yellow")
                    elements_check.append(("Market 标签", "⚠️"))
                
                # 检测数量输入框
                try:
                    await page.wait_for_selector('input[type="text"]', timeout=5000)
                    console.print("    ✅ 数量输入框", style="green")
                    elements_check.append(("数量输入框", "✅"))
                except:
                    console.print("    ⚠️ 数量输入框未找到", style="yellow")
                    elements_check.append(("数量输入框", "⚠️"))
                
                # 检测 Order Book
                try:
                    await page.wait_for_selector('div[class*="OrderBook"]', timeout=5000)
                    console.print("    ✅ Order Book 盘口", style="green")
                    elements_check.append(("Order Book", "✅"))
                except:
                    console.print("    ⚠️ Order Book 未找到", style="yellow")
                    elements_check.append(("Order Book", "⚠️"))
                
                # 测试 JS 执行
                js_start = time.time()
                result = await page.evaluate("1 + 1")
                js_time = (time.time() - js_start) * 1000
                console.print(f"  ✅ JS 执行正常: {js_time:.2f}ms", style="green")
                
                self.results['browser'] = {
                    "load_time": f"{load_time:.2f}ms",
                    "js_time": f"{js_time:.2f}ms",
                    "elements": elements_check,
                    "status": "✅"
                }
                
                await browser.close()
                
        except Exception as e:
            console.print(f"  ❌ 浏览器测试失败: {e}", style="red")
            self.results['browser'] = {"status": "❌", "error": str(e)}
        
        console.print()
    
    def test_geo_location(self):
        """测试地理位置"""
        console.print("🌍 [4/4] VPS 地理位置", style="bold yellow")
        
        try:
            response = requests.get("https://ipinfo.io/json", timeout=5)
            if response.status_code == 200:
                data = response.json()
                location = f"{data.get('city', 'N/A')}, {data.get('country', 'N/A')}"
                console.print(f"  📍 位置: {location}", style="cyan")
                console.print(f"  🌐 IP: {data.get('ip', 'N/A')}", style="cyan")
                console.print(f"  🏢 ISP: {data.get('org', 'N/A')}", style="cyan")
                
                self.results['geo'] = {
                    "ip": data.get('ip', 'N/A'),
                    "location": location,
                    "isp": data.get('org', 'N/A')
                }
            else:
                console.print("  ⚠️ 无法获取地理位置信息", style="yellow")
                self.results['geo'] = {"status": "⚠️"}
        except Exception as e:
            console.print(f"  ❌ 地理位置测试失败: {e}", style="red")
            self.results['geo'] = {"status": "❌"}
        
        console.print()
    
    def print_summary(self):
        """打印诊断摘要"""
        console.print("\n" + "="*60, style="bold blue")
        console.print("📊 诊断摘要", style="bold blue", justify="center")
        console.print("="*60 + "\n", style="bold blue")
        
        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("测试项目", style="cyan", width=25)
        table.add_column("状态", justify="center", width=10)
        table.add_column("详情", width=25)
        
        # DNS
        dns = self.results.get('dns', {})
        table.add_row(
            "DNS 解析",
            dns.get('status', '❌'),
            dns.get('time', 'N/A')
        )
        
        # Web
        web = self.results.get('web', {})
        table.add_row(
            "Web 连接",
            web.get('status', '❌'),
            web.get('time', 'N/A')
        )
        
        # Browser
        browser = self.results.get('browser', {})
        table.add_row(
            "浏览器加载",
            browser.get('status', '❌'),
            browser.get('load_time', 'N/A')
        )
        
        # Geo
        geo = self.results.get('geo', {})
        table.add_row(
            "VPS 位置",
            "✅" if 'location' in geo else "⚠️",
            geo.get('location', 'N/A')
        )
        
        console.print(table)
        
        # 总体评估
        console.print("\n" + "="*60, style="bold blue")
        
        all_ok = (
            dns.get('status') == '✅' and
            web.get('status') == '✅' and
            browser.get('status') == '✅'
        )
        
        if all_ok:
            console.print("✅ 网络状态良好，可以正常运行交易脚本", style="bold green", justify="center")
        else:
            console.print("⚠️ 检测到问题，请检查网络配置", style="bold yellow", justify="center")
            if dns.get('status') != '✅':
                console.print("  • DNS 解析异常，尝试使用 8.8.8.8", style="yellow")
            if browser.get('status') != '✅':
                console.print("  • 浏览器加载失败，检查 Playwright 安装", style="yellow")
        
        console.print("="*60 + "\n", style="bold blue")
    
    def save_results(self):
        """保存诊断结果"""
        filename = f"network_diagnostic_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump({
                "timestamp": datetime.now().isoformat(),
                "results": self.results
            }, f, indent=2, ensure_ascii=False)
        
        console.print(f"💾 诊断结果已保存: {filename}", style="bold green")

async def main():
    """主函数"""
    diag = NetworkDiagnostic()
    
    diag.print_header()
    
    # 执行测试（精简版：4项核心测试）
    diag.test_dns_resolution()
    diag.test_web_connectivity()
    await diag.test_browser_loading()
    diag.test_geo_location()
    
    # 打印摘要
    diag.print_summary()
    
    # 保存结果
    diag.save_results()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        console.print("\n\n⚠️ 测试已中断", style="yellow")
    except Exception as e:
        console.print(f"\n\n❌ 测试出错: {e}", style="red")
