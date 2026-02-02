#!/usr/bin/env python3
"""
🔬 Paradex 网络诊断工具
全面测试 VPS 到 Paradex 的网络连接质量，排查可能的网络问题
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
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn

console = Console()

class NetworkDiagnostic:
    def __init__(self):
        self.results = {}
        self.trade_url = "https://app.paradex.trade/trade/BTC-USD-PERP"
        self.api_url = "https://api.paradex.trade/v1/system/config"
        self.ws_url = "wss://ws.prod.paradex.trade/v1/ws"
        
    def print_header(self):
        """打印诊断工具标题"""
        console.print("\n" + "="*70, style="bold blue")
        console.print("🔬 Paradex 网络诊断工具", style="bold blue", justify="center")
        console.print(f"⏰ 测试时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", justify="center")
        console.print("="*70 + "\n", style="bold blue")
    
    def test_dns_resolution(self):
        """测试 DNS 解析"""
        console.print("📍 [1/8] DNS 解析测试", style="bold yellow")
        
        domains = [
            "app.paradex.trade",
            "api.paradex.trade",
            "ws.prod.paradex.trade"
        ]
        
        results = []
        for domain in domains:
            try:
                start = time.time()
                ip = socket.gethostbyname(domain)
                duration = (time.time() - start) * 1000
                results.append({
                    "domain": domain,
                    "ip": ip,
                    "time": f"{duration:.2f}ms",
                    "status": "✅"
                })
                console.print(f"  ✅ {domain} → {ip} ({duration:.2f}ms)", style="green")
            except Exception as e:
                results.append({
                    "domain": domain,
                    "ip": "N/A",
                    "time": "N/A",
                    "status": "❌"
                })
                console.print(f"  ❌ {domain} → 解析失败: {e}", style="red")
        
        self.results['dns'] = results
        console.print()
    
    def test_http_connectivity(self):
        """测试 HTTP/HTTPS 连接"""
        console.print("🌐 [2/8] HTTP/HTTPS 连接测试", style="bold yellow")
        
        endpoints = [
            ("Paradex API", self.api_url),
            ("Paradex Web", "https://app.paradex.trade"),
            ("Google (对照)", "https://www.google.com"),
            ("Cloudflare (对照)", "https://www.cloudflare.com")
        ]
        
        results = []
        for name, url in endpoints:
            try:
                start = time.time()
                headers = {
                    'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36'
                }
                response = requests.get(url, timeout=10, headers=headers)
                duration = (time.time() - start) * 1000
                
                results.append({
                    "name": name,
                    "status_code": response.status_code,
                    "time": f"{duration:.2f}ms",
                    "status": "✅" if response.status_code == 200 else "⚠️"
                })
                
                status_style = "green" if response.status_code == 200 else "yellow"
                console.print(f"  ✅ {name}: {response.status_code} ({duration:.2f}ms)", style=status_style)
            except Exception as e:
                results.append({
                    "name": name,
                    "status_code": "N/A",
                    "time": "N/A",
                    "status": "❌"
                })
                console.print(f"  ❌ {name}: 连接失败 - {e}", style="red")
        
        self.results['http'] = results
        console.print()
    
    def test_api_latency(self):
        """测试 API 延迟（多次采样）"""
        console.print("⚡ [3/8] API 延迟测试（10次采样）", style="bold yellow")
        
        latencies = []
        success_count = 0
        
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console
        ) as progress:
            task = progress.add_task("测试中...", total=10)
            
            for i in range(10):
                try:
                    start = time.time()
                    response = requests.get(self.api_url, timeout=5)
                    duration = (time.time() - start) * 1000
                    
                    if response.status_code == 200:
                        latencies.append(duration)
                        success_count += 1
                except:
                    pass
                
                progress.update(task, advance=1)
                time.sleep(0.2)
        
        if latencies:
            avg = sum(latencies) / len(latencies)
            min_lat = min(latencies)
            max_lat = max(latencies)
            
            console.print(f"  📊 成功率: {success_count}/10 ({success_count*10}%)", style="green")
            console.print(f"  📈 平均延迟: {avg:.2f}ms", style="cyan")
            console.print(f"  ⬇️  最低延迟: {min_lat:.2f}ms", style="green")
            console.print(f"  ⬆️  最高延迟: {max_lat:.2f}ms", style="yellow")
            
            self.results['api_latency'] = {
                "success_rate": f"{success_count}/10",
                "avg": f"{avg:.2f}ms",
                "min": f"{min_lat:.2f}ms",
                "max": f"{max_lat:.2f}ms"
            }
        else:
            console.print("  ❌ 所有请求均失败", style="red")
            self.results['api_latency'] = {"status": "failed"}
        
        console.print()
    
    async def test_browser_loading(self):
        """测试浏览器页面加载（模拟真实交易环境）"""
        console.print("🌍 [4/8] 浏览器页面加载测试（Playwright）", style="bold yellow")
        
        try:
            async with async_playwright() as p:
                # 启动浏览器（与 main.py 相同配置）
                console.print("  🚀 启动 Chromium 浏览器...")
                browser = await p.chromium.launch(
                    headless=True,
                    args=[
                        '--disable-blink-features=AutomationControlled',
                        '--no-sandbox',
                        '--disable-gpu',
                        '--disable-dev-shm-usage',
                        '--disable-setuid-sandbox',
                        '--enable-features=NetworkService,NetworkServiceInProcess',
                    ]
                )
                
                context = await browser.new_context(
                    viewport={'width': 1920, 'height': 1080},
                    user_agent='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36'
                )
                
                page = await context.new_page()
                
                # 资源拦截（与 main.py 相同）
                async def route_intercept(route):
                    resource_type = route.request.resource_type
                    if resource_type == "image":
                        await route.abort()
                        return
                    await route.continue_()
                
                await page.route("**/*", route_intercept)
                
                # 测试页面加载
                console.print(f"  📄 加载页面: {self.trade_url}")
                start = time.time()
                
                try:
                    await page.goto(self.trade_url, wait_until='domcontentloaded', timeout=30000)
                    load_time = (time.time() - start) * 1000
                    console.print(f"  ✅ 页面加载成功: {load_time:.2f}ms", style="green")
                    
                    # 等待关键元素加载
                    console.print("  🔍 检测关键元素...")
                    
                    elements_to_check = [
                        ('button[type="submit"]', "下单按钮"),
                        ('input[type="text"]', "输入框"),
                        ('div', "页面容器")
                    ]
                    
                    element_results = []
                    for selector, name in elements_to_check:
                        try:
                            await page.wait_for_selector(selector, timeout=5000)
                            console.print(f"    ✅ {name} 已加载", style="green")
                            element_results.append({"element": name, "status": "✅"})
                        except:
                            console.print(f"    ⚠️  {name} 未找到", style="yellow")
                            element_results.append({"element": name, "status": "⚠️"})
                    
                    # 测试 JS 执行
                    console.print("  ⚙️  测试 JavaScript 执行...")
                    js_start = time.time()
                    result = await page.evaluate("1 + 1")
                    js_time = (time.time() - js_start) * 1000
                    
                    if result == 2:
                        console.print(f"  ✅ JS 执行正常: {js_time:.2f}ms", style="green")
                    
                    self.results['browser'] = {
                        "load_time": f"{load_time:.2f}ms",
                        "js_time": f"{js_time:.2f}ms",
                        "elements": element_results,
                        "status": "✅"
                    }
                    
                except Exception as e:
                    console.print(f"  ❌ 页面加载失败: {e}", style="red")
                    self.results['browser'] = {"status": "❌", "error": str(e)}
                
                await browser.close()
                
        except Exception as e:
            console.print(f"  ❌ 浏览器测试失败: {e}", style="red")
            self.results['browser'] = {"status": "❌", "error": str(e)}
        
        console.print()
    
    def test_bandwidth(self):
        """测试带宽（下载测试）"""
        console.print("📡 [5/8] 带宽测试", style="bold yellow")
        
        try:
            # 下载一个小文件测试带宽
            test_url = "https://app.paradex.trade"
            
            console.print("  📥 下载测试文件...")
            start = time.time()
            response = requests.get(test_url, timeout=10, stream=True)
            
            total_size = 0
            for chunk in response.iter_content(chunk_size=8192):
                total_size += len(chunk)
            
            duration = time.time() - start
            speed_mbps = (total_size * 8) / (duration * 1_000_000)
            
            console.print(f"  ✅ 下载完成: {total_size} bytes", style="green")
            console.print(f"  ⚡ 速度: {speed_mbps:.2f} Mbps", style="cyan")
            
            self.results['bandwidth'] = {
                "size": f"{total_size} bytes",
                "speed": f"{speed_mbps:.2f} Mbps",
                "status": "✅"
            }
        except Exception as e:
            console.print(f"  ❌ 带宽测试失败: {e}", style="red")
            self.results['bandwidth'] = {"status": "❌"}
        
        console.print()
    
    def test_packet_loss(self):
        """测试丢包率（多次 ping）"""
        console.print("📶 [6/8] 丢包率测试（20次请求）", style="bold yellow")
        
        success = 0
        total = 20
        latencies = []
        
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console
        ) as progress:
            task = progress.add_task("测试中...", total=total)
            
            for i in range(total):
                try:
                    start = time.time()
                    response = requests.get(self.api_url, timeout=3)
                    duration = (time.time() - start) * 1000
                    
                    if response.status_code == 200:
                        success += 1
                        latencies.append(duration)
                except:
                    pass
                
                progress.update(task, advance=1)
                time.sleep(0.1)
        
        loss_rate = ((total - success) / total) * 100
        
        console.print(f"  📊 成功: {success}/{total}", style="green")
        console.print(f"  📉 丢包率: {loss_rate:.1f}%", style="yellow" if loss_rate > 0 else "green")
        
        if latencies:
            jitter = max(latencies) - min(latencies)
            console.print(f"  📈 抖动: {jitter:.2f}ms", style="cyan")
            
            self.results['packet_loss'] = {
                "success": f"{success}/{total}",
                "loss_rate": f"{loss_rate:.1f}%",
                "jitter": f"{jitter:.2f}ms"
            }
        else:
            self.results['packet_loss'] = {"status": "❌"}
        
        console.print()
    
    def test_ssl_certificate(self):
        """测试 SSL 证书"""
        console.print("🔒 [7/8] SSL 证书验证", style="bold yellow")
        
        try:
            response = requests.get(self.trade_url, timeout=5)
            console.print("  ✅ SSL 证书有效", style="green")
            console.print(f"  🔐 HTTPS 协议: {response.url.startswith('https')}", style="green")
            
            self.results['ssl'] = {"status": "✅", "https": True}
        except requests.exceptions.SSLError as e:
            console.print(f"  ❌ SSL 证书错误: {e}", style="red")
            self.results['ssl'] = {"status": "❌", "error": str(e)}
        except Exception as e:
            console.print(f"  ⚠️  连接错误: {e}", style="yellow")
            self.results['ssl'] = {"status": "⚠️", "error": str(e)}
        
        console.print()
    
    def test_geo_location(self):
        """测试地理位置和路由"""
        console.print("🌍 [8/8] 地理位置和路由测试", style="bold yellow")
        
        try:
            # 获取 VPS IP 信息
            response = requests.get("https://ipinfo.io/json", timeout=5)
            if response.status_code == 200:
                data = response.json()
                console.print(f"  📍 VPS 位置: {data.get('city', 'N/A')}, {data.get('country', 'N/A')}", style="cyan")
                console.print(f"  🌐 IP 地址: {data.get('ip', 'N/A')}", style="cyan")
                console.print(f"  🏢 ISP: {data.get('org', 'N/A')}", style="cyan")
                
                self.results['geo'] = {
                    "ip": data.get('ip', 'N/A'),
                    "location": f"{data.get('city', 'N/A')}, {data.get('country', 'N/A')}",
                    "isp": data.get('org', 'N/A')
                }
            else:
                console.print("  ⚠️  无法获取地理位置信息", style="yellow")
                self.results['geo'] = {"status": "⚠️"}
        except Exception as e:
            console.print(f"  ❌ 地理位置测试失败: {e}", style="red")
            self.results['geo'] = {"status": "❌"}
        
        console.print()
    
    def print_summary(self):
        """打印诊断摘要"""
        console.print("\n" + "="*70, style="bold blue")
        console.print("📊 诊断摘要", style="bold blue", justify="center")
        console.print("="*70 + "\n", style="bold blue")
        
        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("测试项目", style="cyan", width=30)
        table.add_column("状态", justify="center", width=10)
        table.add_column("详情", width=30)
        
        # DNS
        dns_ok = all(r['status'] == '✅' for r in self.results.get('dns', []))
        table.add_row(
            "DNS 解析",
            "✅" if dns_ok else "❌",
            f"{len([r for r in self.results.get('dns', []) if r['status'] == '✅'])}/3 域名解析成功"
        )
        
        # HTTP
        http_ok = all(r['status'] in ['✅', '⚠️'] for r in self.results.get('http', []))
        table.add_row(
            "HTTP/HTTPS 连接",
            "✅" if http_ok else "❌",
            f"{len([r for r in self.results.get('http', []) if r['status'] == '✅'])}/4 端点可达"
        )
        
        # API Latency
        api_lat = self.results.get('api_latency', {})
        if 'avg' in api_lat:
            table.add_row(
                "API 延迟",
                "✅",
                f"平均 {api_lat['avg']}"
            )
        else:
            table.add_row("API 延迟", "❌", "测试失败")
        
        # Browser
        browser = self.results.get('browser', {})
        table.add_row(
            "浏览器加载",
            browser.get('status', '❌'),
            browser.get('load_time', 'N/A')
        )
        
        # Packet Loss
        pkt = self.results.get('packet_loss', {})
        table.add_row(
            "丢包率",
            "✅" if pkt.get('loss_rate', '100%') == '0.0%' else "⚠️",
            pkt.get('loss_rate', 'N/A')
        )
        
        # SSL
        ssl = self.results.get('ssl', {})
        table.add_row(
            "SSL 证书",
            ssl.get('status', '❌'),
            "证书有效" if ssl.get('status') == '✅' else "验证失败"
        )
        
        console.print(table)
        
        # 总体评估
        console.print("\n" + "="*70, style="bold blue")
        
        issues = []
        if not dns_ok:
            issues.append("DNS 解析异常")
        if not http_ok:
            issues.append("HTTP 连接异常")
        if api_lat.get('status') == 'failed':
            issues.append("API 不可达")
        if browser.get('status') == '❌':
            issues.append("浏览器加载失败")
        if pkt.get('loss_rate', '0%') != '0.0%':
            issues.append(f"存在丢包 ({pkt.get('loss_rate', 'N/A')})")
        
        if not issues:
            console.print("✅ 网络状态良好，无明显问题", style="bold green", justify="center")
        else:
            console.print("⚠️  检测到以下问题:", style="bold yellow")
            for issue in issues:
                console.print(f"  • {issue}", style="yellow")
        
        console.print("="*70 + "\n", style="bold blue")
        
        # 建议
        console.print("💡 建议:", style="bold cyan")
        if not dns_ok:
            console.print("  • 检查 DNS 配置，尝试使用 8.8.8.8 或 1.1.1.1", style="cyan")
        if pkt.get('loss_rate', '0%') != '0.0%':
            console.print("  • 网络不稳定，考虑更换 VPS 或联系服务商", style="cyan")
        if browser.get('status') == '❌':
            console.print("  • 检查 Playwright 安装: playwright install chromium", style="cyan")
        if not issues:
            console.print("  • 网络连接正常，可以正常运行交易脚本", style="green")
        
        console.print()
    
    def save_results(self):
        """保存诊断结果到文件"""
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
    
    # 执行所有测试
    diag.test_dns_resolution()
    diag.test_http_connectivity()
    diag.test_api_latency()
    await diag.test_browser_loading()
    diag.test_bandwidth()
    diag.test_packet_loss()
    diag.test_ssl_certificate()
    diag.test_geo_location()
    
    # 打印摘要
    diag.print_summary()
    
    # 保存结果
    diag.save_results()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        console.print("\n\n⚠️  测试已中断", style="yellow")
    except Exception as e:
        console.print(f"\n\n❌ 测试出错: {e}", style="red")
