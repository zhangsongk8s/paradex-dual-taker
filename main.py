#!/usr/bin/env python3
"""
Paradex Dual Taker - 价差监控触发交易系统
监控 BTC-USD-PERP 的价差，当价差 < 0.001% 时触发双账号并发交易
"""

import asyncio
import json
import os
import logging
import time
from pathlib import Path
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
from datetime import datetime, timedelta
import re
from rich.live import Live
from dashboard import Dashboard
from order_guard import OrderGuard
from exit_handler import ExitHandler, ExitReason


class ParadexDualTaker:
    def __init__(self):
        self.base_dir = Path(__file__).parent
        self.data_dir = self.base_dir / "data"
        
        # 账号组路径定义（启动时由用户选择）
        self.account_group_paths = {
            "group_a": {
                "main": self.data_dir / "auth_main.json",
                "hedge": self.data_dir / "auth_hedge.json",
                "name_a": "Shark 1",
                "name_b": "Shark 2"
            },
            "group_b": {
                "main": self.data_dir / "auth_shark3.json",
                "hedge": self.data_dir / "auth_shark4.json",
                "name_a": "Shark 3",
                "name_b": "Shark 4"
            },
            "group_c": {
                "main": self.data_dir / "auth_shark5.json",
                "hedge": self.data_dir / "auth_shark6.json",
                "name_a": "Shark 5",
                "name_b": "Shark 6"
            },
            "group_d": {
                "main": self.data_dir / "auth_shark7.json",
                "hedge": self.data_dir / "auth_shark8.json",
                "name_a": "Shark 7",
                "name_b": "Shark 8"
            }
        }
        
        # 当前使用的账号路径和名称（由 select_account_group 设置）
        self.auth_main_path = None  # 将在 select_account_group 中设置
        self.auth_hedge_path = None  # 将在 select_account_group 中设置
        self.account_a_name = "Account A"  # 默认值，将被动态更新
        self.account_b_name = "Account B"  # 默认值，将被动态更新
        
        self.trade_url = "https://app.paradex.trade/trade/BTC-USD-PERP"
        self.quantity = "0.01"
        self.spread_threshold = 0.001  # 0.001%
        self.min_available_balance = 300  # 最小可用余额阈值（USD），低于此值停止脚本
        self.min_depth = 0.030  # 最小盘口深度阈值（BTC），低于此值不交易 [平衡模式：低磨损+稳定]
        self.min_depth_spotter = 0.015  # Spotter 配平专用深度阈值（更低，确保能配平）
        self.browser = None
        self.context_a = None
        self.context_b = None
        self.page_a = None
        self.page_b = None
        # 交易计数器
        self.trade_count = 0
        self.max_trades = 1000
        self.force_exit_trades = 10  # 10次交易后强制退出（仅手动模式）
        self.reset_time = None
        
        # ✅ 数据文件路径（将在选择账号组后动态设置）
        self.trade_count_file = None
        self.order_guard = None
        self.group_identifier = None  # 账号组标识符，用于生成文件名
        # 持仓监控（只在交易成功时查询）
        self.position_cache = {"account_a": None, "account_b": None}
        self.direction_cache = {"account_a": "none", "account_b": "none"}  # 持仓方向缓存
        self.balance_cache = {"account_a": None, "account_b": None}  # 余额缓存
        # 文件保存队列（异步处理，不阻塞）
        self.save_queue = []
        self.save_pending = False
        # 交易模式：1=模式1(A多B空), 2=模式2(A空B多), 3=平仓模式
        self.trade_mode = 1
        # Spotter (观察手) 模式标志
        self.spotter_mode = False  # 是否处于配平模式
        
        # 🔄 自动轮转模式相关变量
        self.enable_auto_rotation = False  # 是否启用自动轮转模式
        self.last_open_mode = 1  # 上一次使用的开仓模式（1 或 2）
        self.TARGET_POSITION = 0.05  # 目标持仓阈值（BTC）- 5笔0.01交易后切换平仓
        
        # 💰 手续费检查相关变量
        self.FEE_CHECK_INTERVAL = 100  # 每100笔交易检查一次手续费
        self.last_fee_check_count = 0  # 上次检查手续费时的交易计数
        
        # 🛑 优雅退出处理器（稍后在日志初始化后设置 logger）
        self.exit_handler = None  # 将在 _setup_logging 之后初始化
        
        # 初始化日志系统
        self._setup_logging()
    
    def _setup_logging(self, group_identifier=None):
        """配置日志系统，将日志输出到文件
        
        Args:
            group_identifier: 账号组标识符（如 "shark1_2" 或 "shark3_4"），用于生成唯一的日志文件名
        """
        # 创建 logs 目录
        log_dir = self.base_dir / "logs"
        log_dir.mkdir(exist_ok=True)
        
        # 生成日志文件名（按日期和账号组）
        if group_identifier:
            log_filename = datetime.now().strftime(f"paradex_{group_identifier}_%Y%m%d.log")
        else:
            log_filename = datetime.now().strftime("paradex_dual_taker_%Y%m%d.log")
        log_path = log_dir / log_filename
        
        # 配置日志格式
        log_format = '%(asctime)s [%(levelname)s] %(message)s'
        date_format = '%Y-%m-%d %H:%M:%S'
        
        # 创建logger
        self.logger = logging.getLogger('ParadexDualTaker')
        self.logger.setLevel(logging.INFO)
        
        # 清除已有的handlers（避免重复）
        self.logger.handlers.clear()
        
        # 文件处理器（保存到文件）
        file_handler = logging.FileHandler(log_path, encoding='utf-8')
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(logging.Formatter(log_format, date_format))
        self.logger.addHandler(file_handler)
        
        # 控制台处理器（可选，用于调试）
        # console_handler = logging.StreamHandler()
        # console_handler.setLevel(logging.WARNING)
        # console_handler.setFormatter(logging.Formatter(log_format, date_format))
        # self.logger.addHandler(console_handler)
        
        self.logger.info("="*60)
        self.logger.info("Paradex Dual Taker 启动")
        self.logger.info(f"日志文件: {log_path}")
        self.logger.info("="*60)
    
    def _setup_data_files(self):
        """根据当前账号组，初始化数据文件路径（在选择账号组后调用）"""
        # 生成账号组标识符（用于文件名）
        # 例如：Shark 1 & Shark 2 → "shark1_2"
        # 例如：Shark 3 & Shark 4 → "shark3_4"
        name_a_num = ''.join(filter(str.isdigit, self.account_a_name))
        name_b_num = ''.join(filter(str.isdigit, self.account_b_name))
        self.group_identifier = f"shark{name_a_num}_{name_b_num}"
        
        # 设置交易计数文件路径
        self.trade_count_file = self.data_dir / f"trade_count_{self.group_identifier}.json"
        
        # 重新初始化 OrderGuard（24小时交易限制 + 会话限制）
        self.order_guard = OrderGuard(
            history_file=self.base_dir / f"trade_history_{self.group_identifier}.json",
            max_orders=1000,
            safety_threshold=950,
            session_limit=300  # 单次运行300笔后自动退出
        )
        
        # 重新配置日志系统（使用账号组标识符）
        self._setup_logging(group_identifier=self.group_identifier)
        
        # 🛑 初始化优雅退出处理器
        self.exit_handler = ExitHandler(logger=self.logger)
        self.exit_handler.start_session(
            account_a=self.account_a_name,
            account_b=self.account_b_name,
            group_id=self.group_identifier
        )
        
        self.logger.info(f"✅ 数据文件初始化完成:")
        self.logger.info(f"   📂 交易计数: {self.trade_count_file.name}")
        self.logger.info(f"   📂 交易历史: trade_history_{self.group_identifier}.json")
        self.logger.info(f"   📂 日志文件: paradex_{self.group_identifier}_*.log")
        
    def load_auth(self, auth_path):
        """加载账号认证信息"""
        if not auth_path.exists():
            raise FileNotFoundError(f"认证文件不存在: {auth_path}")
        with open(auth_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    async def init_browser(self):
        """初始化浏览器（优化版：屏蔽无关资源，极速加载）"""
        playwright = await async_playwright().start()
        self.browser = await playwright.chromium.launch(
            headless=True,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--no-sandbox',
                '--disable-gpu',
                '--disable-dev-shm-usage',
                '--disable-setuid-sandbox',
                # 复用连接，减少 DNS 和 TLS 握手
                '--enable-features=NetworkService,NetworkServiceInProcess',
            ]
        )
        
        # 🚀 定义资源拦截规则：屏蔽图片、字体、媒体，保留核心 JS/CSS
        async def route_intercept(route):
            """智能拦截：屏蔽无关资源，保留核心功能"""
            url = route.request.url
            resource_type = route.request.resource_type
            
            # 完全屏蔽的资源类型
            if resource_type in ["image", "media", "font"]:
                await route.abort()
                return
            
            # 屏蔽第三方分析/广告脚本
            blocked_domains = [
                "google-analytics.com",
                "googletagmanager.com",
                "mixpanel.com",
                "segment.com",
                "hotjar.com",
                "facebook.net",
            ]
            if any(domain in url for domain in blocked_domains):
                await route.abort()
                return
            
            # 保留核心功能的 JS 和 CSS
            await route.continue_()
        
        # 加载账号配置
        auth_main = self.load_auth(self.auth_main_path)  # Account_1 主账号
        auth_hedge = self.load_auth(self.auth_hedge_path)  # Account_2 对冲账号
        
        # 创建两个独立的上下文（账号 A 和 B）并挂载拦截器
        self.context_a = await self.browser.new_context(
            storage_state=auth_main if isinstance(auth_main, dict) and 'cookies' in auth_main else None,
            viewport={'width': 1920, 'height': 1080}
        )
        await self.context_a.route("**/*", route_intercept)  # 🚀 挂载拦截器
        
        self.context_b = await self.browser.new_context(
            storage_state=auth_hedge if isinstance(auth_hedge, dict) and 'cookies' in auth_hedge else None,
            viewport={'width': 1920, 'height': 1080}
        )
        await self.context_b.route("**/*", route_intercept)  # 🚀 挂载拦截器
        
        # 创建页面
        self.page_a = await self.context_a.new_page()
        self.page_b = await self.context_b.new_page()
        
        self.logger.info("🚀 浏览器初始化完成（已启用资源拦截优化）")
        
        return playwright
    
    async def setup_trading_page(self, page, account_name, dashboard=None):
        """设置交易页面：打开页面、切换到 Market 标签、输入数量"""
        try:
            if dashboard:
                dashboard.update(last_log=f"{account_name}: 正在打开交易页面...")
            await page.goto(self.trade_url, wait_until='networkidle', timeout=30000)
            await asyncio.sleep(2)  # 等待页面加载
            
            # Switch to Market tab
            if dashboard:
                dashboard.update(last_log=f"{account_name}: 切换到 Market 标签...")
            
            # Improved selectors for Market tab using text matching which is more robust
            market_tab_selectors = [
                # Text exact match (most reliable if text doesn't change)
                'span:text-is("Market")',
                'span:text-is("市场")',
                # Text contains match
                'span:has-text("Market")',
                'span:has-text("市场")',
                'button:has-text("Market")',
                'button:has-text("市场")',
                # Complex XPath for precision
                '//div[contains(@class, "Tab")]//span[contains(text(), "Market")]',
                '//div[contains(@class, "Tab")]//span[contains(text(), "市场")]',
            ]
            
            market_tab_clicked = False
            for selector in market_tab_selectors:
                try:
                    element = page.locator(selector).first
                    if await element.is_visible(timeout=1000):
                        await element.click(timeout=1000)
                        market_tab_clicked = True
                        if dashboard:
                            dashboard.update(last_log=f"{account_name}: Market 标签已切换")
                        break
                except:
                    continue
            
            if not market_tab_clicked:
                # Log usage warning but don't fail yet, maybe it's already on Market
                if dashboard:
                    dashboard.update(last_log=f"{account_name}: 未能自动切换到 Market 标签（可能已生效）")
            
            await asyncio.sleep(1)
            
            # Input Quantity
            if dashboard:
                dashboard.update(last_log=f"{account_name}: 输入数量 {self.quantity}...")
            
            await asyncio.sleep(2)
            
            # Enhanced Quantity Selectors
            quantity_selectors = [
                # 1. Label-based Proximity (Best for resistance to class changes)
                # Find "Size" or "大小" label, then find the input inside the same container or nearby
                '//label[contains(text(), "Size")]/..//input', 
                '//label[contains(text(), "大小")]/..//input',
                '//div[contains(text(), "Size")]/..//input',
                '//div[contains(text(), "大小")]/..//input',
                '//span[contains(text(), "Size")]/../following-sibling::div//input',
                '//span[contains(text(), "大小")]/../following-sibling::div//input',
                
                # 2. Attribute-based (High reliability)
                'input[aria-label="Size"]',
                'input[aria-label="大小"]',
                'input[placeholder="Size"]',
                'input[placeholder="大小"]',
                'input[placeholder="Amount"]',
                
                # 3. Class-based (Originals, preserved as fallback)
                'div.InputNumber__InputFieldWithInsideLabel-sc-1il2wqh-3 input[aria-label="大小"]',
                'input.InputNumber__NumberFormat-sc-1il2wqh-2',
                
                # 4. Generic Fallback - First visible number/text input in the order form
                'form input[type="text"]',
                '(//input[@type="text"])[1]', 
            ]
            
            quantity_input_filled = False
            last_error = None
            
            for selector in quantity_selectors:
                try:
                    # Handle XPath vs CSS
                    if selector.startswith('(') or selector.startswith('/'):
                        input_elem = page.locator(selector).first
                    else:
                        input_elem = page.locator(selector).first
                    
                    if await input_elem.count() > 0 and await input_elem.is_visible(timeout=1000):
                        # Focus and Clear
                        await input_elem.click(timeout=1000)
                        await asyncio.sleep(0.2)
                        await input_elem.press('Control+a')
                        await asyncio.sleep(0.1)
                        await input_elem.press('Delete')
                        
                        # Type Value
                        await input_elem.fill(self.quantity)
                        await asyncio.sleep(0.2)
                        
                        # Validate
                        val = await input_elem.input_value()
                        if self.quantity in val:
                            quantity_input_filled = True
                            if dashboard:
                                dashboard.update(last_log=f"{account_name}: 数量 {self.quantity} 输入成功")
                            break
                        else:
                            # Retry with typing
                            await input_elem.press('Control+a')
                            await input_elem.press('Delete')
                            await input_elem.type(self.quantity, delay=50) # Slow type
                            val = await input_elem.input_value()
                            if self.quantity in val:
                                quantity_input_filled = True
                                if dashboard:
                                    dashboard.update(last_log=f"{account_name}: 数量输入成功 (Slow Type)")
                                break
                except Exception as e:
                    last_error = str(e)
                    continue
            if not quantity_input_filled:
                # 🛑 Retry Strategy: Check if we are on the "Announcements" page or just lost
                # The debug dump showed we might be redirected to the Announcements page
                is_announcements = await page.locator("h1:has-text('Announcements')").count() > 0 or \
                                 await page.locator("h1:has-text('公告')").count() > 0
                
                if is_announcements:
                    if dashboard:
                        dashboard.update(last_log=f"{account_name}: 检测到公告页面，尝试点击 'Trade' 按钮...")
                    
                    # Try to click the Trade link in navigation
                    trade_nav = page.locator('a[href="/trade"]').first
                    if await trade_nav.is_visible():
                        await trade_nav.click()
                        await asyncio.sleep(3) # Wait for navigation
                        
                        # Recursive retry (one level deep) is risky, so let's just try to find selectors again here
                        # We just re-run the selector loop once more
                         
                        if dashboard:
                            dashboard.update(last_log=f"{account_name}: 已跳转，正在重试输入数量...")
                        for selector in quantity_selectors:
                            try:
                                if selector.startswith('(') or selector.startswith('/'):
                                    input_elem = page.locator(selector).first
                                else:
                                    input_elem = page.locator(selector).first
                                
                                if await input_elem.count() > 0 and await input_elem.is_visible(timeout=1000):
                                    await input_elem.click(timeout=1000)
                                    await input_elem.fill(self.quantity)
                                    if self.quantity in await input_elem.input_value():
                                        quantity_input_filled = True
                                        break
                            except:
                                continue
            if not quantity_input_filled:
                raise Exception(f"{account_name}: 无法找到或输入数量框 (即使尝试跳转后)")
            if dashboard:
                dashboard.update(last_log=f"{account_name}: 交易页面设置完成")
            
        except Exception as e:
            # 📸 Debug dump on failure
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            error_clean = str(e).replace(" ", "_")[:50]
            
            log_dir = self.base_dir / "logs"
            log_dir.mkdir(exist_ok=True)
            
            screenshot_path = log_dir / f"debug_failure_{timestamp}_{account_name.replace(' ', '_')}.png"
            html_path = log_dir / f"debug_failure_{timestamp}_{account_name.replace(' ', '_')}.html"
            
            try:
                await page.screenshot(path=str(screenshot_path))
                html_content = await page.content()
                with open(html_path, "w", encoding="utf-8") as f:
                    f.write(html_content)
                    
                self.logger.error(f"❌ 设置页面失败，已保存调试快照:\n   📸 {screenshot_path.name}\n   📄 {html_path.name}")
                if dashboard:
                    dashboard.update(last_log=f"{account_name}: 失败! 已保存调试快照到 logs 目录", status="🔴 错误")
            except Exception as dump_error:
                self.logger.error(f"❌ 保存调试快照失败: {dump_error}")
            if dashboard:
                dashboard.update(last_log=f"{account_name}: 设置交易页面失败: {e}", status="🔴 错误")
            raise
    
    async def get_order_book_prices(self, page):
        """从 Order Book 读取买一价 (Best Bid) 和卖一价 (Best Ask)"""
        try:
            # 定位 Order Book 容器
            order_book_container = page.locator('div[role="grid"].OrderBook__Container-h2hlxe-5').first
            
            if await order_book_container.count() == 0:
                # 备用：尝试其他可能的容器选择器
                order_book_container = page.locator('div[role="grid"][class*="OrderBook"]').first
                if await order_book_container.count() == 0:
                    return None, None
            
            best_ask = None
            best_bid = None
            
            # 方法1: 通过 aria-label 定位第一个 Ask 行和 Bid 行（最可靠）
            try:
                # 获取第一个 Ask 行（Best Ask - 卖一价）
                first_ask_row = order_book_container.locator('div[role="row"][aria-label^="Ask"]').first
                # 获取第一个 Bid 行（Best Bid - 买一价）
                first_bid_row = order_book_container.locator('div[role="row"][aria-label^="Bid"]').first
                
                if await first_ask_row.count() > 0 and await first_bid_row.count() > 0:
                    # 从第一个 Ask 行中读取价格按钮
                    ask_price_button = first_ask_row.locator('button[role="gridcell"][kind="ask"][aria-label="Price"]').first
                    # 从第一个 Bid 行中读取价格按钮
                    bid_price_button = first_bid_row.locator('button[role="gridcell"][kind="bid"][aria-label="Price"]').first
                    
                    if await ask_price_button.count() > 0 and await bid_price_button.count() > 0:
                        # 读取价格文本（格式：91,259）
                        ask_text = await ask_price_button.inner_text()
                        bid_text = await bid_price_button.inner_text()
                        
                        # 移除逗号并转换为浮点数
                        best_ask = float(ask_text.replace(',', '').strip())
                        best_bid = float(bid_text.replace(',', '').strip())
                        
                        # 验证：价格应该合理（买一价 < 卖一价）
                        if best_bid < best_ask:
                            return best_ask, best_bid
            except Exception as e:
                pass
            
            # 方法1b: 直接读取第一个 ask 和 bid 价格按钮（备用）
            try:
                # 获取第一个 ask 价格按钮（Best Ask - 卖一价）
                ask_price_button = order_book_container.locator(
                    'button[role="gridcell"][kind="ask"][aria-label="Price"]'
                ).first
                
                # 获取第一个 bid 价格按钮（Best Bid - 买一价）
                bid_price_button = order_book_container.locator(
                    'button[role="gridcell"][kind="bid"][aria-label="Price"]'
                ).first
                
                if await ask_price_button.count() > 0 and await bid_price_button.count() > 0:
                    # 读取价格文本（格式：91,259）
                    ask_text = await ask_price_button.inner_text()
                    bid_text = await bid_price_button.inner_text()
                    
                    # 移除逗号并转换为浮点数
                    best_ask = float(ask_text.replace(',', '').strip())
                    best_bid = float(bid_text.replace(',', '').strip())
                    
                    # 验证：价格应该合理（买一价 < 卖一价）
                    if best_bid < best_ask:
                        return best_ask, best_bid
            except Exception as e:
                pass
            
            # 方法2: 如果方法1失败，读取所有价格并选择最佳
            try:
                # 获取所有 ask 价格按钮
                ask_buttons = order_book_container.locator(
                    'button[role="gridcell"][kind="ask"][aria-label="Price"]'
                )
                # 获取所有 bid 价格按钮
                bid_buttons = order_book_container.locator(
                    'button[role="gridcell"][kind="bid"][aria-label="Price"]'
                )
                
                ask_count = await ask_buttons.count()
                bid_count = await bid_buttons.count()
                
                if ask_count > 0 and bid_count > 0:
                    ask_prices = []
                    bid_prices = []
                    
                    # 读取前5个 ask 价格（卖单，第一个应该是最佳卖一价）
                    for i in range(min(ask_count, 5)):
                        try:
                            button = ask_buttons.nth(i)
                            text = await button.inner_text()
                            price = float(text.replace(',', '').strip())
                            if 1000 < price < 200000:  # BTC 价格范围过滤
                                ask_prices.append(price)
                        except:
                            continue
                    
                    # 读取前5个 bid 价格（买单，第一个应该是最佳买一价）
                    for i in range(min(bid_count, 5)):
                        try:
                            button = bid_buttons.nth(i)
                            text = await button.inner_text()
                            price = float(text.replace(',', '').strip())
                            if 1000 < price < 200000:  # BTC 价格范围过滤
                                bid_prices.append(price)
                        except:
                            continue
                    
                    if ask_prices and bid_prices:
                        # 在 Order Book 中，第一个 ask 应该是最小的（Best Ask），第一个 bid 应该是最大的（Best Bid）
                        # 但如果顺序不对，我们取最小 ask 和最大 bid
                        best_ask = min(ask_prices)  # Best Ask = 最小的 ask（最接近中间价）
                        best_bid = max(bid_prices)  # Best Bid = 最大的 bid（最接近中间价）
                        
                        # 验证：价格应该合理（买一价 < 卖一价）
                        if best_bid < best_ask:
                            return best_ask, best_bid
            except Exception as e:
                pass
            
            # 方法2: 通过 class 选择器（备用方案）
            try:
                ask_buttons = order_book_container.locator(
                    'button.OrderBook__Price-h2hlxe-13[kind="ask"]'
                )
                bid_buttons = order_book_container.locator(
                    'button.OrderBook__Price-h2hlxe-13[kind="bid"]'
                )
                
                ask_count = await ask_buttons.count()
                bid_count = await bid_buttons.count()
                
                if ask_count > 0 and bid_count > 0:
                    ask_prices = []
                    bid_prices = []
                    
                    for i in range(min(ask_count, 10)):
                        try:
                            button = ask_buttons.nth(i)
                            text = await button.inner_text()
                            price = float(text.replace(',', '').strip())
                            if 1000 < price < 200000:
                                ask_prices.append(price)
                        except:
                            continue
                    
                    for i in range(min(bid_count, 10)):
                        try:
                            button = bid_buttons.nth(i)
                            text = await button.inner_text()
                            price = float(text.replace(',', '').strip())
                            if 1000 < price < 200000:
                                bid_prices.append(price)
                        except:
                            continue
                    
                    if ask_prices and bid_prices:
                        best_ask = min(ask_prices)
                        best_bid = max(bid_prices)
                        
                        if best_bid < best_ask:
                            return best_ask, best_bid
            except:
                pass
            
            # 方法3: 最后的备用方案 - 通过文本提取
            try:
                all_text = await order_book_container.inner_text()
                price_pattern = r'\d{1,3}(?:[,\s]\d{3})*(?:\.\d+)?'
                prices = re.findall(price_pattern, all_text.replace(',', '').replace(' ', ''))
                
                if len(prices) >= 2:
                    price_values = []
                    for p in prices:
                        try:
                            val = float(p)
                            if 1000 < val < 200000:  # BTC 价格范围过滤
                                price_values.append(val)
                        except:
                            continue
                    
                    if len(price_values) >= 2:
                        price_values = sorted(set(price_values), reverse=True)
                        best_ask = max(price_values)
                        best_bid = min(price_values)
                        
                        if best_bid < best_ask:
                            return best_ask, best_bid
            except:
                pass
            
            return None, None
            
        except Exception as e:
            return None, None
    
    async def get_order_book_with_depth(self, page):
        """
        读取订单簿的价格和数量（盘口深度）- 相对定位法
        返回: (best_ask, best_bid, ask_size, bid_size)
        如果读取失败，size 返回 -1（特殊标记）
        """
        try:
            result = await page.evaluate("""
            () => {
                try {
                    // 查找 Order Book 容器
                    const container = document.querySelector('div[class*="OrderBook"]');
                    if (!container) return null;
                    
                    // 查找价格按钮（这个已经验证可用）
                    const askButtons = container.querySelectorAll('button[kind="ask"]');
                    const bidButtons = container.querySelectorAll('button[kind="bid"]');
                    
                    if (askButtons.length === 0 || bidButtons.length === 0) return null;
                    
                    // ========== 读取 Best Ask（卖一） ==========
                    let bestAsk = null, askSize = -1, askRowHTML = '';
                    
                    const askBtn = askButtons[0];  // 第一个 ask 就是 Best Ask
                    const askPriceText = askBtn.innerText.replace(/,/g, '');
                    bestAsk = parseFloat(askPriceText);
                    
                    // 相对定位法：找父级容器（行）
                    let askRow = askBtn.parentElement;
                    if (askRow) {
                        askRowHTML = askRow.outerHTML;  // 保存 HTML 用于调试
                        
                        // 策略1: 查找第二个子元素
                        const children = Array.from(askRow.children);
                        if (children.length >= 2) {
                            const secondChild = children[1];
                            const sizeText = secondChild.innerText.replace(/,/g, '').trim();
                            const size = parseFloat(sizeText);
                            if (!isNaN(size) && size > 0 && size < 100) {
                                askSize = size;
                            }
                        }
                        
                        // 策略2: nextElementSibling（兄弟节点）
                        if (askSize === -1) {
                            const sibling = askBtn.nextElementSibling;
                            if (sibling) {
                                const sizeText = sibling.innerText.replace(/,/g, '').trim();
                                const size = parseFloat(sizeText);
                                if (!isNaN(size) && size > 0 && size < 100) {
                                    askSize = size;
                                }
                            }
                        }
                        
                        // 策略3: 从整行文本中提取（最后的备用方案）
                        if (askSize === -1) {
                            const rowText = askRow.innerText;
                            const nums = rowText.match(/\\d+\\.?\\d*/g);
                            if (nums && nums.length >= 2) {
                                const size = parseFloat(nums[1]);
                                if (!isNaN(size) && size > 0 && size < 100) {
                                    askSize = size;
                                }
                            }
                        }
                    }
                    
                    // ========== 读取 Best Bid（买一） ==========
                    let bestBid = null, bidSize = -1, bidRowHTML = '';
                    
                    const bidBtn = bidButtons[0];
                    const bidPriceText = bidBtn.innerText.replace(/,/g, '');
                    bestBid = parseFloat(bidPriceText);
                    
                    let bidRow = bidBtn.parentElement;
                    if (bidRow) {
                        bidRowHTML = bidRow.outerHTML;
                        
                        // 策略1: 第二个子元素
                        const children = Array.from(bidRow.children);
                        if (children.length >= 2) {
                            const secondChild = children[1];
                            const sizeText = secondChild.innerText.replace(/,/g, '').trim();
                            const size = parseFloat(sizeText);
                            if (!isNaN(size) && size > 0 && size < 100) {
                                bidSize = size;
                            }
                        }
                        
                        // 策略2: nextElementSibling
                        if (bidSize === -1) {
                            const sibling = bidBtn.nextElementSibling;
                            if (sibling) {
                                const sizeText = sibling.innerText.replace(/,/g, '').trim();
                                const size = parseFloat(sizeText);
                                if (!isNaN(size) && size > 0 && size < 100) {
                                    bidSize = size;
                                }
                            }
                        }
                        
                        // 策略3: 从整行提取
                        if (bidSize === -1) {
                            const rowText = bidRow.innerText;
                            const nums = rowText.match(/\\d+\\.?\\d*/g);
                            if (nums && nums.length >= 2) {
                                const size = parseFloat(nums[1]);
                                if (!isNaN(size) && size > 0 && size < 100) {
                                    bidSize = size;
                                }
                            }
                        }
                    }
                    
                    // 验证价格有效性
                    if (bestAsk && bestBid && bestAsk > 1000 && bestAsk < 200000 && 
                        bestBid > 1000 && bestBid < 200000 && bestBid < bestAsk) {
                        return {
                            ask: bestAsk,
                            bid: bestBid,
                            askSize: askSize,  // 可能是 -1
                            bidSize: bidSize,  // 可能是 -1
                            askRowHTML: askRowHTML,
                            bidRowHTML: bidRowHTML
                        };
                    }
                    
                    return null;
                    
                } catch (e) {
                    console.error('[Depth] JS Error:', e);
                    return null;
                }
            }
            """)
            
            if not result:
                return None, None, None, None
            
            ask = result.get('ask')
            bid = result.get('bid')
            ask_size = result.get('askSize', -1)
            bid_size = result.get('bidSize', -1)
            
            # 如果数量读取失败（-1），打印 HTML 调试信息
            if ask_size == -1 or bid_size == -1:
                self.logger.warning("⚠️ [Depth Debug] 数量读取失败，打印 HTML 结构用于调试：")
                if ask_size == -1 and 'askRowHTML' in result:
                    self.logger.warning(f"   Ask Row HTML: {result['askRowHTML'][:200]}...")
                if bid_size == -1 and 'bidRowHTML' in result:
                    self.logger.warning(f"   Bid Row HTML: {result['bidRowHTML'][:200]}...")
            
            return ask, bid, ask_size, bid_size
            
        except Exception as e:
            self.logger.error(f"❌ [Depth] 读取异常: {str(e)}")
            return None, None, None, None
    
    async def get_spread_from_middle(self, page):
        """从中间价差框读取点差率（用于验证）"""
        try:
            # 定位点差率元素：output.OrderBook__SpreadValue-h2hlxe-4
            spread_value = page.locator('output.OrderBook__SpreadValue-h2hlxe-4[aria-labelledby*="spread"]').first
            
            if await spread_value.count() > 0:
                spread_text = await spread_value.inner_text()
                # 移除 % 符号并转换为浮点数（格式：0.003%）
                spread_pct = float(spread_text.replace('%', '').strip())
                return spread_pct
            
            # 备用：通过 aria-labelledby 属性定位
            spread_value = page.locator('output[aria-labelledby*="spread"]').first
            if await spread_value.count() > 0:
                spread_text = await spread_value.inner_text()
                spread_pct = float(spread_text.replace('%', '').strip())
                return spread_pct
            
            return None
        except:
            return None
    
    async def check_trading_fee(self, page, dashboard=None) -> bool:
        """
        检查交易历史中的手续费是否为$0
        
        Returns:
            True: 费用为$0或读取失败（安全，继续交易）
            False: 检测到非零费用（需要退出）
        """
        try:
            self.logger.info("💰 [FeeCheck] 开始检查交易手续费...")
            
            if dashboard:
                dashboard.update(last_log="💰 手续费检查中，暂停交易...", status="🔍 检查中")
            
            # Step 1: 点击"交易历史"Tab
            trade_history_tab = page.locator('button[role="tab"]:has-text("交易历史")').first
            
            if await trade_history_tab.count() == 0:
                self.logger.warning("⚠️ [FeeCheck] 未找到交易历史Tab，跳过检查")
                return True
            
            await trade_history_tab.click()
            await asyncio.sleep(2)  # 等待表格加载
            
            # Step 2: 使用 JavaScript 读取第一行的费用列（第9列，索引8）
            fee_result = await page.evaluate("""
            () => {
                try {
                    // 查找交易历史表格（id="trade-history"）
                    const container = document.getElementById('trade-history');
                    if (!container) return { success: false, error: 'container not found' };
                    
                    const table = container.querySelector('table');
                    if (!table) return { success: false, error: 'table not found' };
                    
                    const tbody = table.querySelector('tbody');
                    if (!tbody) return { success: false, error: 'tbody not found' };
                    
                    const firstRow = tbody.querySelector('tr');
                    if (!firstRow) return { success: false, error: 'no rows' };
                    
                    // 获取所有 td 单元格
                    const cells = firstRow.querySelectorAll('td');
                    if (cells.length < 9) return { success: false, error: 'not enough columns: ' + cells.length };
                    
                    // 费用列是第9列（索引8）
                    const feeCell = cells[8];
                    const feeText = feeCell.innerText.trim();
                    
                    return { success: true, fee: feeText };
                } catch (e) {
                    return { success: false, error: e.toString() };
                }
            }
            """)
            
            if not fee_result or not fee_result.get('success'):
                error = fee_result.get('error', 'unknown') if fee_result else 'null result'
                self.logger.warning(f"⚠️ [FeeCheck] 读取费用失败: {error}，默认继续交易")
                return True
            
            fee_text = fee_result.get('fee', '')
            self.logger.info(f"💰 [FeeCheck] 读取到费用: {fee_text}")
            
            # Step 3: 判断费用是否为$0
            is_zero = fee_text in ['$0', '$0.00', '0', '$0.000']
            
            if is_zero:
                self.logger.info("✅ [FeeCheck] 费用为$0，继续交易")
                if dashboard:
                    dashboard.update(last_log=f"✅ 费用检查通过: {fee_text}", status="✅ 监控中")
                return True
            else:
                self.logger.error(f"🚨 [FeeCheck] 检测到非零费用: {fee_text}，需要退出程序！")
                if dashboard:
                    dashboard.update(last_log=f"🚨 检测到手续费: {fee_text}，程序即将退出", status="🔴 费用异常")
                return False
                
        except Exception as e:
            self.logger.warning(f"⚠️ [FeeCheck] 检查异常: {e}，默认继续交易")
            return True
    
    def graceful_exit(self, reason: ExitReason, message: str = "", fee_value: str = None):
        """
        优雅退出：更新 ExitHandler 状态、生成报告
        
        Args:
            reason: 退出原因 (ExitReason 枚举)
            message: 详细退出信息
            fee_value: 检测到的手续费值（仅 FEE_DETECTED 时使用）
        """
        if not self.exit_handler:
            self.logger.warning("⚠️ [GracefulExit] ExitHandler 未初始化")
            return
        
        # 获取会话交易数
        session_count = 0
        if self.order_guard:
            session_count, _ = self.order_guard.get_session_info()
        
        # 更新统计信息
        self.exit_handler.update_stats(
            trade_count=self.trade_count,
            session_trades=session_count,
            position_a=self.position_cache.get("account_a"),
            position_b=self.position_cache.get("account_b"),
            balance_a=self.balance_cache.get("account_a"),
            balance_b=self.balance_cache.get("account_b"),
            direction_a=self.direction_cache.get("account_a", "none"),
            direction_b=self.direction_cache.get("account_b", "none")
        )
        
        # 设置退出原因
        self.exit_handler.set_exit(reason, message, fee_value)
        
        # 打印和记录报告
        self.exit_handler.print_report()
        self.exit_handler.log_report()
    
    def load_trade_count(self):
        """加载交易计数器（同步，启动时调用）- 每次启动时强制退出次数重置为0"""
        try:
            # 每次启动时，强制退出次数（trade_count）重置为0
            self.trade_count = 0
            
            # ✅ 检查文件路径是否已初始化
            if self.trade_count_file is None:
                self.logger.warning("⚠️ 交易计数文件路径未初始化，请先选择账号组")
                return
            
            # 24小时重置逻辑保留（用于 max_trades，如果需要的话）
            if self.trade_count_file.exists():
                with open(self.trade_count_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    reset_time_str = data.get('reset_time', None)
                    if reset_time_str:
                        self.reset_time = datetime.fromisoformat(reset_time_str)
                    else:
                        self.reset_time = datetime.now()
                # 检查是否需要重置（24小时）- 这个逻辑保留用于其他用途
                if self.reset_time and (datetime.now() - self.reset_time).total_seconds() >= 86400:
                    self.reset_time = datetime.now()
                    self.save_trade_count_sync()
            else:
                self.reset_time = datetime.now()
                self.save_trade_count_sync()
        except:
            self.trade_count = 0
            self.reset_time = datetime.now()
    
    def save_trade_count_sync(self):
        """同步保存交易计数器（启动时使用）"""
        try:
            # ✅ 检查文件路径是否已初始化
            if self.trade_count_file is None:
                return
            
            data = {
                'count': self.trade_count,
                'reset_time': self.reset_time.isoformat() if self.reset_time else None
            }
            with open(self.trade_count_file, 'w', encoding='utf-8') as f:
                json.dump(data, f)
        except:
            pass
    
    async def save_trade_count_async(self):
        """异步保存交易计数器（不阻塞主循环）"""
        try:
            # ✅ 检查文件路径是否已初始化
            if self.trade_count_file is None:
                return
            
            data = {
                'count': self.trade_count,
                'reset_time': self.reset_time.isoformat() if self.reset_time else None
            }
            # 使用异步文件写入（不阻塞）
            def write_file():
                with open(self.trade_count_file, 'w', encoding='utf-8') as f:
                    json.dump(data, f)
            
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, write_file)
        except:
            pass
    
    def increment_trade_count(self):
        """增加交易计数（立即更新内存，异步保存文件）"""
        self.trade_count += 1
        # 异步保存，不阻塞
        asyncio.create_task(self.save_trade_count_async())
        return self.trade_count
    
    def print_exit_summary(self, dashboard, live, reason="用户中断"):
        """打印程序退出总结"""
        from datetime import datetime
        
        # 获取当前时间戳
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # 获取当前持仓
        pos_a = abs(self.position_cache.get("account_a", 0))
        pos_b = abs(self.position_cache.get("account_b", 0))
        dir_a = self.direction_cache.get("account_a", "none")
        dir_b = self.direction_cache.get("account_b", "none")
        
        # 计算持仓差异
        position_diff = abs(pos_a - pos_b)
        total_position = pos_a + pos_b
        
        # 获取余额
        balance_a = self.balance_cache.get("account_a", 0)
        balance_b = self.balance_cache.get("account_b", 0)
        
        # 获取24小时交易统计
        active_count, max_orders, _, _ = self.order_guard.get_status_info()
        
        print("\n" + "="*70)
        print("📊 程序运行总结")
        print("="*70)
        print(f"⏰ 退出时间: {timestamp}")
        print(f"🔴 退出原因: {reason}")
        print(f"📦 交易次数: 本次运行 {self.trade_count} 笔 | 今日总计 {active_count} 笔")
        print(f"")
        print(f"📈 当前持仓:")
        print(f"   {self.account_a_name}: {pos_a:.5f} BTC ({dir_a})")
        print(f"   {self.account_b_name}: {pos_b:.5f} BTC ({dir_b})")
        print(f"   持仓差异: {position_diff:.5f} BTC")
        print(f"   总持仓: {total_position:.5f} BTC")
        print(f"")
        print(f"💰 账户余额:")
        print(f"   {self.account_a_name}: ${balance_a:,.2f}")
        print(f"   {self.account_b_name}: ${balance_b:,.2f}")
        print(f"")
        print(f"⚠️ 需要注意:")
        
        # 检查项目
        warnings = []
        
        # 检查持仓差异
        if position_diff > 0.01:
            warnings.append(f"   ❌ 持仓不平衡: 差异 {position_diff:.5f} BTC > 0.01 BTC")
        elif position_diff > 0.001:
            warnings.append(f"   ⚠️ 持仓轻微不平衡: 差异 {position_diff:.5f} BTC")
        
        # 检查方向一致性
        if dir_a == dir_b and dir_a != "none":
            if pos_a >= 0.01 and pos_b >= 0.01:
                warnings.append(f"   ❌ 方向异常: 两账户都是 {dir_a}（大仓位），应该对冲")
            elif pos_a >= 0.001 or pos_b >= 0.001:
                warnings.append(f"   ⚠️ 方向一致: 两账户都是 {dir_a}（微仓位，可忽略）")
        
        # 检查余额
        if balance_a < self.min_available_balance:
            warnings.append(f"   ⚠️ {self.account_a_name} 余额不足: ${balance_a:.2f} < ${self.min_available_balance}")
        if balance_b < self.min_available_balance:
            warnings.append(f"   ⚠️ {self.account_b_name} 余额不足: ${balance_b:.2f} < ${self.min_available_balance}")
        
        # 检查持仓状态
        if total_position < 0.01:
            warnings.append(f"   ✅ 持仓已基本清空 (< 0.01 BTC)，可以安全退出")
        elif total_position < 0.001:
            warnings.append(f"   ✅ 持仓已完全清空 (< 0.001 BTC)")
        
        if warnings:
            for warning in warnings:
                print(warning)
        else:
            print("   ✅ 无异常，状态正常")
        
        print("="*70 + "\n")
    
    async def get_position_direction_by_color(self, page):
        """
        通过持仓文本的颜色判断持仓方向（使用 JavaScript 一次性获取，不影响性能）
        返回: "long" | "short" | "none"
        """
        try:
            result = await page.evaluate("""
                () => {
                    // 查找包含"当前持仓"的容器
                    const containers = document.querySelectorAll('div.Description__Container-fu5veb-0');
                    for (const container of containers) {
                        const text = container.innerText;
                        if (!text.includes('当前持仓') && !text.includes('Current Position')) continue;
                        
                        // 查找所有可能包含 BTC 数值的元素
                        const allElements = container.querySelectorAll('output, span, div, p');
                        for (const elem of allElements) {
                            const elemText = elem.innerText.trim();
                            // 匹配格式：0.13000 BTC 或 0.08000 BTC
                            if (/^\\d+\\.\\d+\\s*BTC$/i.test(elemText)) {
                                const style = window.getComputedStyle(elem);
                                const color = style.color;
                                const rgb = color.match(/\\d+/g);
                                if (rgb && rgb.length >= 3) {
                                    const r = parseInt(rgb[0]);
                                    const g = parseInt(rgb[1]);
                                    const b = parseInt(rgb[2]);
                                    
                                    // 判断颜色：红色系 = 空仓，绿色/青色系 = 多仓
                                    // 红色判断：R 明显大于 G 和 B
                                    // 绿色判断：G 明显大于 R
                                    if (r > g + 50 && r > b + 50) {
                                        return 'short';  // 红色 = 空仓
                                    } else if (g > r + 30 && g > b) {
                                        return 'long';   // 绿色/青色 = 多仓
                                    }
                                }
                            }
                        }
                    }
                    return null;
                }
            """)
            
            if result:
                return result
            return "none"
        except:
            return "none"
    
    async def get_position_and_balance(self, page, account_name=""):
        """从同一个大框体元素中同时获取持仓和余额（高效并发查询）"""
        try:
            # 定位包含持仓和余额信息的大框体容器
            # 通常这些信息都在同一个 Description 容器中
            container_selectors = [
                'div.Description__Container-fu5veb-0',
                'div:has-text("当前持仓"):has-text("可用于交易")',
                'div:has-text("Current Position"):has-text("Available")',
            ]
            
            position = None
            balance = None
            
            for container_selector in container_selectors:
                try:
                    container = page.locator(container_selector).first
                    if await container.is_visible(timeout=1000):
                        # 获取整个容器的文本内容
                        container_text = await container.inner_text()
                        
                        # 从同一文本中提取持仓
                        position_patterns = [
                            r'当前持仓[:\s]+([+-]?\d+\.?\d*)\s*BTC',
                            r'Current Position[:\s]+([+-]?\d+\.?\d*)\s*BTC',
                            r'持仓[:\s]+([+-]?\d+\.?\d*)\s*BTC',
                            r'Position[:\s]+([+-]?\d+\.?\d*)\s*BTC',
                            r'([+-]?\d+\.\d{5,})\s*BTC',  # 匹配5位以上小数的BTC数量
                        ]
                        
                        for pattern in position_patterns:
                            match = re.search(pattern, container_text, re.IGNORECASE)
                            if match:
                                position_str = match.group(1).strip()
                                position = float(position_str)
                                if -1000 < position < 1000:
                                    break
                        
                        # 从同一文本中提取余额
                        balance_patterns = [
                            r'可用于交易[:\s]*\$?\s*(-?\d[\d,]*\.?\d*)',
                            r'Available[:\s]*\$?\s*(-?\d[\d,]*\.?\d*)',
                            r'\$(-?\d[\d,]*\.?\d*)',  # 美元符号后的数字
                        ]
                        
                        for pattern in balance_patterns:
                            match = re.search(pattern, container_text)
                            if match:
                                balance_str = match.group(1).replace(',', '').strip()
                                try:
                                    balance = float(balance_str)
                                    # 验证余额值（应该是正数）
                                    if 0 <= balance < 1000000:
                                        break
                                except ValueError:
                                    continue
                        
                        # 如果都找到了，直接返回
                        if position is not None and balance is not None:
                            return position, balance
                except Exception as e:
                    continue
            
            # 如果从容器中没找到，尝试分别查找（备用方案）
            if position is None:
                position = await self.get_position_size(page)
            if balance is None:
                balance = await self.get_available_balance(page, account_name)
            
            return position, balance
        except Exception as e:
            # 如果合并查询失败，分别查询
            position = await self.get_position_size(page)
            balance = await self.get_available_balance(page, account_name)
            return position, balance
    
    async def get_position_direction_and_balance(self, page, account_name=""):
        """
        同时获取持仓、方向和余额（只在交易成功后调用，不影响主循环性能）
        返回: (position, direction, balance)
        direction: "long" | "short" | "none"
        """
        try:
            # 先获取持仓和余额（原有方法，性能不变）
            position, balance = await self.get_position_and_balance(page, account_name)
            
            # 如果持仓为0或None，直接返回
            if position is None or position == 0:
                return 0, "none", balance
            
            # 获取持仓方向（使用 JavaScript 一次性获取，性能影响最小）
            direction = await self.get_position_direction_by_color(page)
            
            # 如果颜色判断失败，根据数值判断（备用）
            if direction == "none":
                if position > 0:
                    direction = "long"
                elif position < 0:
                    direction = "short"
                    position = abs(position)  # 转换为正数显示
                else:
                    direction = "none"
                    position = 0
            else:
                # 如果通过颜色判断成功，确保持仓为正数
                position = abs(position)
            
            return position, direction, balance
        except Exception as e:
            # 备用方案：只根据数值判断
            try:
                position = await self.get_position_size(page)
                balance = await self.get_available_balance(page, account_name)
                
                if position is None or position == 0:
                    return 0, "none", balance
                elif position > 0:
                    return position, "long", balance
                else:
                    return abs(position), "short", balance
            except:
                return None, "none", None
    
    async def get_position_size(self, page):
        """获取当前持仓数量（改进版，更准确地提取持仓）"""
        try:
            # 扩展的选择器列表
            position_selectors = [
                'text="Current Position"',
                'text="当前持仓"',
                'text="Position"',
                'text="持仓"',
            ]
            
            for selector in position_selectors:
                try:
                    elem = page.locator(selector).first
                    if await elem.is_visible(timeout=1000):  # 增加超时时间
                        # 尝试多种方式获取父元素
                        parent = elem.locator('..')
                        text = await parent.inner_text()
                        
                        # 改进正则表达式，更准确地提取BTC数量
                        # 支持格式：0.39064 BTC, +0.39064 BTC, -0.39064 BTC, 0.39064BTC等
                        patterns = [
                            r'([+-]?\d+\.?\d*)\s*BTC',  # 标准格式
                            r'([+-]?\d+\.?\d*)\s*btc',  # 小写
                            r'Position[:\s]+([+-]?\d+\.?\d*)',  # Position: 0.39064
                            r'持仓[:\s]+([+-]?\d+\.?\d*)',  # 持仓: 0.39064
                        ]
                        
                        for pattern in patterns:
                            match = re.search(pattern, text, re.IGNORECASE)
                            if match:
                                position_str = match.group(1).strip()
                                position = float(position_str)
                                # 验证持仓值是否合理
                                if -1000 < position < 1000:  # BTC持仓应该在合理范围内
                                    return position
                        
                        # 如果正则匹配失败，尝试查找所有包含数字和BTC的元素
                        # 查找父元素内的所有文本节点
                        all_text = text
                        # 再次尝试更宽松的匹配
                        match = re.search(r'(\d+\.\d{5,})\s*BTC', all_text)  # 匹配5位以上小数的BTC数量
                        if match:
                            position = float(match.group(1))
                            if -1000 < position < 1000:
                                return position
                except Exception as e:
                    continue
            
            # 备用方法：直接搜索包含BTC数字的元素
            try:
                btc_elements = page.locator('text=/\\d+\\.\\d+\\s*BTC/i')
                count = await btc_elements.count()
                if count > 0:
                    for i in range(count):
                        elem = btc_elements.nth(i)
                        if await elem.is_visible(timeout=500):
                            text = await elem.inner_text()
                            match = re.search(r'([+-]?\d+\.?\d*)\s*BTC', text, re.IGNORECASE)
                            if match:
                                position = float(match.group(1))
                                if -1000 < position < 1000:
                                    return position
            except:
                pass
            
            return None
        except Exception as e:
            return None
    
    async def get_available_balance(self, page, account_name=""):
        """获取可用余额（USD）- 使用多种选择器定位（优化版，精确匹配）"""
        try:
            # 先等待页面稳定
            await asyncio.sleep(0.3)
            
            # 扩展的选择器列表，按精确度排序（最精确的优先）
            balance_selectors = [
                # 方法1: 通过Description容器定位（最精确，优先使用）
                ('div.Description__Container-fu5veb-0:has-text("可用于交易")', 'output.Description__Value-fu5veb-2'),
                # 方法2: 通过XPath精确定位"可用于交易"后的output元素
                ('xpath=//div[contains(text(), "可用于交易")]/following-sibling::output', None),
                ('xpath=//div[text()="可用于交易"]/following-sibling::output', None),
                # 方法3: 通过文本定位"可用于交易"（多种路径，但更精确）
                ('text="可用于交易"', 'xpath=./following-sibling::output'),
                ('text="可用于交易"', 'xpath=./parent::*/output[contains(@aria-label, "可用")]'),
                ('text="可用于交易"', '.. output[contains(@aria-label, "可用")]'),
                # 方法4: 通过aria-label精确定位（只匹配"可用于交易"相关）
                ('output[aria-label*="可用于交易"]', None),
                ('output[aria-label*="Available for Trading"]', None),
                ('output[aria-label*="Available Balance"]', None),
                # 方法5: 通过包含"可用于交易"的div定位output
                ('div:has-text("可用于交易"):has(output)', 'output'),
                # 方法6: 通过XPath查找包含"可用于交易"文本的容器，然后找output
                ('xpath=//div[contains(., "可用于交易") and contains(., "$")]//output', None),
                # 方法7: 最后才使用通用选择器（但需要验证）
                ('output[aria-label*="可用"]', None),
                ('output[aria-label*="Available"]', None),
            ]
            
            for container_selector, value_selector in balance_selectors:
                try:
                    if value_selector is None:
                        # 直接定位元素
                        if container_selector.startswith('xpath='):
                            elem = page.locator(container_selector).first
                        else:
                            elems = page.locator(container_selector)
                            count = await elems.count()
                            if count == 0:
                                continue
                            # 如果是通用的output选择器，需要筛选
                            if container_selector == 'output':
                                # 遍历所有output元素，找到包含"可用"或"Available"的
                                for i in range(count):
                                    elem = elems.nth(i)
                                    if await elem.is_visible(timeout=200):
                                        text = await elem.inner_text()
                                        text_lower = text.lower()
                                        # 确保是可用余额，不是持仓或其他金额
                                        if (('可用' in text or 'Available' in text) and '$' in text) and \
                                           not any(keyword in text_lower for keyword in ['持仓', 'position', '已用', 'used', 'margin']):
                                            balance_match = re.search(r'[\$]?\s*(-?\d[\d,]*\.?\d*)', text)
                                            if balance_match:
                                                balance = float(balance_match.group(1).replace(',', ''))
                                                # 只接受非负数或很小的负数
                                                if 0 <= balance < 1000000:
                                                    return balance
                                continue
                            else:
                                elem = elems.first
                    else:
                        # 先定位容器，再定位值元素
                        container = page.locator(container_selector).first
                        if not await container.is_visible(timeout=500):
                            continue
                        elem = container.locator(value_selector).first
                    
                    if await elem.is_visible(timeout=800):  # 增加超时时间
                        text = await elem.inner_text()
                        
                        # 验证文本是否包含"可用"相关关键词（确保是可用余额，不是其他金额）
                        text_lower = text.lower()
                        # 如果文本中明确包含"持仓"、"Position"、"已用"等，跳过（不是可用余额）
                        if any(keyword in text_lower for keyword in ['持仓', 'position', '已用', 'used', 'margin']):
                            continue
                        
                        # 提取数字（格式：$7.68 或 -48.41 或 7.68 或 $1,234.56）
                        # 改进正则表达式，更好地处理各种格式
                        # 支持多种格式：$1,234.56, -$123.45, 1234.56, $123等
                        patterns = [
                            r'[\$]?\s*(-?\d[\d,]*\.?\d*)',  # 标准格式
                            r'(-?\d[\d,]*\.?\d*)\s*USD',  # 带USD后缀
                            r'(-?\d[\d,]*\.?\d*)\s*\$',  # 带$后缀
                        ]
                        for pattern in patterns:
                            match = re.search(pattern, text.strip())
                            if match:
                                balance_str = match.group(1).replace(',', '').strip()
                                try:
                                    balance = float(balance_str)
                                    # 验证余额值是否合理（应该在合理范围内，且应该是正数或接近正数）
                                    # 可用余额通常应该是正数，负数可能是已用保证金
                                    if 0 <= balance < 1000000:  # 只接受非负数
                                        return balance
                                    # 如果是负数但绝对值很小，可能是显示问题，也接受
                                    elif -100 < balance < 0:
                                        return balance
                                except ValueError:
                                    continue
                except Exception as e:
                    continue
            
            return None
        except Exception as e:
            return None
    
    async def check_balance_sufficient(self, page, account_name, required_btc=0.01, current_price=None):
        """检查余额是否足够下单"""
        try:
            balance = await self.get_available_balance(page, account_name)
            if balance is None:
                # 如果无法获取余额，返回True（不阻止交易，但记录警告）
                return True, None, "无法获取余额信息（请检查页面元素）"
            
            # 如果余额已经是负数，直接判定不足
            if balance < 0:
                return False, balance, f"余额不足: {balance:.2f} USD（负数）"
            
            # 检查是否低于最小可用余额阈值（优先检查，避免继续交易）
            if balance < self.min_available_balance:
                return False, balance, f"可用余额过低: {balance:.2f} USD < 阈值: {self.min_available_balance} USD，停止交易（如实际余额正常，可能是读取错误）"
            
            # 如果无法获取当前价格，使用保守估计
            if current_price is None:
                current_price = 90000
            
            # 计算所需USD（保守估计：价格 * 数量 * 10%保证金）
            # 对于0.01 BTC，15x杠杆，大约需要 价格 * 0.01 / 15 ≈ 价格 * 0.00067
            # 但为了安全，使用更保守的估计：价格 * 0.01 * 0.1（10%保证金）
            required_usd = current_price * required_btc * 0.1
            
            if balance < required_usd:
                return False, balance, f"余额不足: {balance:.2f} USD < 所需: {required_usd:.2f} USD"
            
            return True, balance, None
        except Exception as e:
            # 检查失败不阻止交易，但记录
            return True, None, f"余额检查异常: {str(e)}"
    
    async def verify_quantity_once(self, page, account_name=""):
        """快速验证数量输入框是否有正确的值（只验证，不修改）"""
        try:
            quantity_selectors = [
                # 通过容器定位（最精确）
                'div.InputNumber__InputFieldWithInsideLabel-sc-1il2wqh-3 input[aria-label="大小"]',
                'div.InputNumber__InputFieldWithInsideLabel-sc-1il2wqh-3 input.InputNumber__NumberFormat-sc-1il2wqh-2',
                # 直接定位 input（精确）
                'input[aria-label="大小"]',
                'input.InputNumber__NumberFormat-sc-1il2wqh-2',
                'input[aria-label="大小"][inputmode="decimal"]',
            ]
            
            for selector in quantity_selectors:
                try:
                    input_elem = page.locator(selector).first
                    if await input_elem.is_visible(timeout=2000):
                        current_value = await input_elem.input_value()
                        if self.quantity in current_value or current_value == self.quantity:
                            return True
                except:
                    continue
            return False
        except:
            return False
    
    async def click_trade_button(self, page, account_name, action, dashboard=None):
        """
        🚀 [Turbo] 极速下单版：JS 注入直连内核
        跳过 Playwright 的所有可视化检查，直接触发 DOM 点击事件
        预期性能：从 300-500ms 降低到 10-30ms
        """
        try:
            action_text = "买入" if action == "buy" else "卖出"
            action_value = "BUY" if action == "buy" else "SELL"
            confirm_texts = ["确认购买", "确认出售", "Buy", "Sell", "Confirm"]
            
            # ============================================================
            # 🎯 第一步：极速切换交易方向（JS 注入）
            # ============================================================
            select_result = await page.evaluate(f"""
                () => {{
                    // 查找方向按钮（value="BUY" 或 "SELL"）
                    const btn = document.querySelector('button[value="{action_value}"][role="radio"]') ||
                                document.querySelector('button[value="{action_value}"]');
                    
                    if (!btn) return {{ success: false, reason: 'not_found' }};
                    if (btn.disabled) return {{ success: false, reason: 'disabled' }};
                    
                    // 检查是否已经是正确方向
                    const ariaChecked = btn.getAttribute('aria-checked');
                    if (ariaChecked === 'true') {{
                        return {{ success: true, already_selected: true }};
                    }}
                    
                    // 需要切换方向
                    btn.click();
                    return {{ success: true, already_selected: false }};
                }}
            """)
            
            if not select_result['success']:
                self.logger.error(f"{account_name}: ❌ 方向按钮问题 ({action_text}) - {select_result.get('reason')}")
                if dashboard:
                    dashboard.update(last_log=f"{account_name}: 未能选择 {action_text} 方向", status="🔴 错误")
                return False
            
            if select_result.get('already_selected'):
                self.logger.info(f"{account_name}: ✅ 方向已正确: {action_text}")
            else:
                self.logger.info(f"{account_name}: ✅ 方向已切换: {action_text}")
                # 等待 React/Vue 更新状态（关键！）
                await asyncio.sleep(0.02)  # 20ms 足够让前端框架更新
            
            # ============================================================
            # 🎯 第二步：极速点击确认按钮（JS 注入）
            # ============================================================
            confirm_result = await page.evaluate(f"""
                () => {{
                    const targets = {str(confirm_texts)};
                    const btns = Array.from(document.querySelectorAll('button[type="submit"]'));
                    
                    // 查找确认按钮（文本匹配 + 非禁用）
                    const targetBtn = btns.find(b => {{
                        const txt = b.innerText || b.textContent || '';
                        return targets.some(t => txt.includes(t)) && !b.disabled;
                    }});
                    
                    if (!targetBtn) return {{ success: false, reason: 'not_found' }};
                    if (targetBtn.disabled) return {{ success: false, reason: 'disabled' }};
                    
                    targetBtn.click();
                    return {{ success: true, buttonText: targetBtn.innerText }};
                }}
            """)
            
            if confirm_result['success']:
                self.logger.info(f"⚡ {account_name}: 极速下单成功 ({action_text})")
                if dashboard:
                    dashboard.update(last_log=f"⚡ {account_name}: {action_text} 下单成功")
                return True
            else:
                self.logger.error(f"{account_name}: ❌ 确认按钮问题 - {confirm_result.get('reason')}")
                if dashboard:
                    dashboard.update(last_log=f"{account_name}: 未能点击确认按钮", status="🔴 错误")
                return False
        
        except Exception as e:
            self.logger.error(f"{account_name}: 极速下单异常: {e}")
            if dashboard:
                dashboard.update(last_log=f"{account_name}: 下单异常 - {str(e)[:50]}", status="🔴 错误")
            return False
    
    async def execute_reduce_position(self, page, account_name, reduce_quantity, action, dashboard=None):
        """
        执行精确减仓操作（支持任意数量，不限于0.01）
        
        Args:
            page: 页面对象
            account_name: 账号名称
            reduce_quantity: 需要减仓的数量（BTC，如 0.02840）
            action: "buy" 或 "sell"
            dashboard: 仪表盘对象
        
        Returns:
            bool: 是否成功
        """
        try:
            # 格式化数量为字符串（保留5位小数）
            quantity_str = f"{reduce_quantity:.5f}".rstrip('0').rstrip('.')
            if not quantity_str or float(quantity_str) <= 0:
                quantity_str = "0.01"
            
            action_text = "买入" if action == "buy" else "卖出"
            
            if dashboard:
                dashboard.update(
                    last_log=f"{account_name}: 设置减仓数量 {quantity_str} BTC ({action_text})...",
                    status="⚖️ 平衡中"
                )
            
            # 第一步：等待页面稳定
            await asyncio.sleep(1)
            
            if dashboard:
                dashboard.update(
                    last_log=f"{account_name}: 开始查找输入框...",
                    status="🔍 查找中"
                )
            
            # 第二步：设置数量 - 使用更简单的选择器
            quantity_selectors = [
                'input[aria-label="大小"]',  # 最简单的选择器
                'input[type="text"][aria-label="大小"]',
                'input.InputNumber__NumberFormat-sc-1il2wqh-2',
            ]
            
            quantity_set = False
            for selector in quantity_selectors:
                try:
                    input_elem = page.locator(selector).first
                    # 增加等待时间到5秒，确保元素可见
                    if await input_elem.is_visible(timeout=5000):
                        # 先聚焦输入框（增加超时）
                        await input_elem.focus(timeout=2000)
                        await asyncio.sleep(0.5)
                        
                        # 清空输入框（使用多种方法，增加超时）
                        await input_elem.click(timeout=2000)
                        await asyncio.sleep(0.3)
                        await input_elem.press('Control+a', timeout=1000)
                        await asyncio.sleep(0.2)
                        await input_elem.press('Backspace', timeout=1000)
                        await asyncio.sleep(0.3)
                        
                        # 填入新数量（增加超时）
                        await input_elem.fill(quantity_str, timeout=3000)
                        await asyncio.sleep(0.8)  # 增加等待时间
                        
                        # 按 Enter 确认（增加超时）
                        await input_elem.press('Enter', timeout=1000)
                        await asyncio.sleep(0.5)
                        
                        # 验证数量是否设置成功
                        current_value = await input_elem.input_value()
                        # 比较数值（允许格式差异）
                        try:
                            current_float = float(current_value.replace(',', ''))
                            target_float = float(quantity_str)
                            if abs(current_float - target_float) < 0.00001:
                                quantity_set = True
                                if dashboard:
                                    dashboard.update(
                                        last_log=f"{account_name}: 减仓数量已设置为 {quantity_str} BTC",
                                        status="⚖️ 平衡中"
                                    )
                                break
                        except:
                            if quantity_str in current_value or current_value == quantity_str:
                                quantity_set = True
                                if dashboard:
                                    dashboard.update(
                                        last_log=f"{account_name}: 减仓数量已设置为 {quantity_str} BTC",
                                        status="⚖️ 平衡中"
                                    )
                                break
                except Exception as e:
                    # 添加详细调试日志
                    if dashboard:
                        error_msg = str(e)[:80]
                        dashboard.update(
                            last_log=f"{account_name}: 选择器失败 [{selector[:40]}...]: {error_msg}",
                            status="🟡 调试"
                        )
                    await asyncio.sleep(0.3)
                    continue
            
            if not quantity_set:
                if dashboard:
                    dashboard.update(
                        last_log=f"{account_name}: ⚠️ 所有选择器失败，等待5秒后最后尝试...",
                        status="🟡 等待中"
                    )
                
                # 最后尝试：等待更长时间，使用JavaScript直接操作
                await asyncio.sleep(5)
                
                try:
                    # 尝试使用 JavaScript 查找并设置输入框
                    js_result = await page.evaluate(f"""
                        () => {{
                            // 查找输入框
                            const inputs = document.querySelectorAll('input[aria-label="大小"]');
                            if (inputs.length > 0) {{
                                const input = inputs[0];
                                input.focus();
                                input.value = '{quantity_str}';
                                input.dispatchEvent(new Event('input', {{ bubbles: true }}));
                                input.dispatchEvent(new Event('change', {{ bubbles: true }}));
                                return {{ success: true, value: input.value }};
                            }}
                            return {{ success: false, error: 'No input found' }};
                        }}
                    """)
                    
                    if js_result.get('success'):
                        quantity_set = True
                        if dashboard:
                            dashboard.update(
                                last_log=f"{account_name}: ✅ JavaScript设置成功: {quantity_str} BTC",
                                status="⚖️ 平衡中"
                            )
                        await asyncio.sleep(1)
                    else:
                        if dashboard:
                            dashboard.update(
                                last_log=f"{account_name}: ❌ JavaScript也失败: {js_result.get('error', 'unknown')}",
                                status="🔴 错误"
                            )
                except Exception as e:
                    if dashboard:
                        dashboard.update(
                            last_log=f"{account_name}: ❌ JavaScript异常: {str(e)[:50]}",
                            status="🔴 错误"
                        )
                
                if not quantity_set:
                    return False
            
            # 第三步：选择交易方向
            action_value = "BUY" if action == "buy" else "SELL"
            direction_selectors = [
                f'button[value="{action_value}"]',
                f'button[value="{action_value}"][role="radio"]',
                f'button[role="radio"][value="{action_value}"]',
            ]
            
            direction_selected = False
            for selector in direction_selectors:
                try:
                    button = page.locator(selector).first
                    if await button.is_visible(timeout=2000):
                        await button.click(timeout=2000)
                        direction_selected = True
                        await asyncio.sleep(0.3)
                        break
                except:
                    continue
            
            if not direction_selected:
                if dashboard:
                    dashboard.update(
                        last_log=f"{account_name}: 未能选择 {action_text} 方向",
                        status="🔴 错误"
                    )
                return False
            
            # 第三步：点击确认按钮
            confirm_text = "确认购买" if action == "buy" else "确认出售"
            confirm_button_selectors = [
                f'button[type="submit"]:has-text("{confirm_text}")',
                f'button:has-text("{confirm_text}")',
                'button[type="submit"].SubmitOrder___StyledOrderButton-sc-1wo202o-0',
            ]
            
            confirm_clicked = False
            for selector in confirm_button_selectors:
                try:
                    button = page.locator(selector).first
                    if await button.is_visible(timeout=2000):
                        await button.click(timeout=2000)
                        confirm_clicked = True
                        if dashboard:
                            dashboard.update(
                                last_log=f"{account_name}: {action_text} {quantity_str} BTC 已执行",
                                status="⚖️ 平衡中"
                            )
                        return True
                except:
                    continue
            
            if not confirm_clicked:
                if dashboard:
                    dashboard.update(
                        last_log=f"{account_name}: 未能找到确认按钮",
                        status="🔴 错误"
                    )
                return False
            
            return False
        
        except Exception as e:
            if dashboard:
                dashboard.update(
                    last_log=f"{account_name}: 减仓操作异常: {str(e)}",
                    status="🔴 错误"
                )
            return False
    
    async def close_position_by_button(self, page, account_name, close_quantity, position_direction, dashboard=None):
        """
        通过专门的平仓按钮执行平仓操作（更准确、更安全）
        
        Args:
            page: 页面对象
            account_name: 账号名称
            close_quantity: 需要平仓的数量（BTC），每次最大0.01
            position_direction: "long" 或 "short"（持仓方向）
            dashboard: 仪表盘对象
        
        Returns:
            bool: 是否成功
        """
        try:
            # 限制每次平仓数量最大为0.01
            if close_quantity > 0.01:
                close_quantity = 0.01
            
            # 格式化数量
            quantity_str = f"{close_quantity:.5f}".rstrip('0').rstrip('.')
            if not quantity_str or float(quantity_str) <= 0:
                quantity_str = "0.01"
            
            direction_text = "多仓" if position_direction == "long" else "空仓"
            
            if dashboard:
                dashboard.update(
                    last_log=f"{account_name}: 准备平仓 {quantity_str} BTC ({direction_text})...",
                    status="⚖️ 平衡中"
                )
            
            # ============================================================
            # 第一步：点击"市场"平仓按钮 (增强版定位策略)
            # ============================================================
            
            # 策略：多重定位 + 页面滚动 + 更长等待时间
            self.logger.info(f"🔍 {account_name}: 开始定位'市场'平仓按钮...")
            
            # 先滚动到页面底部，确保持仓区域可见
            try:
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await asyncio.sleep(1)  # 等待滚动完成
                self.logger.info(f"📜 {account_name}: 已滚动到页面底部")
            except:
                pass
            
            market_clicked = False
            used_selector = None
            
            # 增强版选择器组：更多策略，更长超时
            market_selectors = [
                # === 精确定位策略 ===
                # 1. 通过文本定位（多语言支持）
                'button:has-text("市场")',
                'button:has-text("Market")',
                'button:has-text("市價")',  # 繁体中文
                
                # 2. 通过 XPath 定位：表格行中的按钮
                'xpath=//div[@role="row"]//button[contains(text(), "市场") or contains(text(), "Market")]',
                'xpath=//tr//button[contains(text(), "市场") or contains(text(), "Market")]',
                
                # 3. 通过 Class 定位
                'button[class*="MarketClose"]',
                'button[class*="market-close"]',
                'button.MarketCloseButton__ButtonSecondary-utl3l7-1',
                
                # === 宽泛定位策略 ===
                # 4. 持仓区域内的按钮
                'div[class*="Position"] button:has-text("市场")',
                'div[class*="Position"] button:has-text("Market")',
                'div[class*="position"] button:has-text("市场")',
                'div[class*="position"] button:has-text("Market")',
                
                # 5. 底部容器内的按钮
                'div.Description__Container-fu5veb-0 button:has-text("市场")',
                'div.Description__Container-fu5veb-0 button:has-text("Market")',
                
                # 6. 通过 data 属性定位
                'button[data-testid*="market"]',
                'button[data-testid*="close"]',
                
                # === 兜底策略 ===
                # 7. 所有市场按钮，取最后一个（通常平仓按钮在最后）
                'button:has-text("市场") >> nth=-1',
                'button:has-text("Market") >> nth=-1',
                
                # 8. 通过 JS 注入查找
                'xpath=//button[contains(translate(text(), "MARKET市场", "market市场"), "market") or contains(translate(text(), "MARKET市场", "market市场"), "市场")]',
            ]
            
            # 逐个尝试选择器
            for i, selector in enumerate(market_selectors):
                try:
                    self.logger.info(f"🔍 {account_name}: 尝试选择器 #{i+1}: {selector[:50]}...")
                    
                    # 根据选择器类型选择定位方式
                    if "nth=" in selector or ">>" in selector:
                        btn = page.locator(selector)
                    elif selector.startswith("xpath="):
                        btn = page.locator(selector).first
                    else:
                        btn = page.locator(selector).first
                    
                    # 增加等待时间到 3 秒
                    if await btn.is_visible(timeout=3000):
                        # 确保按钮可点击（不被遮挡）
                        try:
                            await btn.scroll_into_view_if_needed(timeout=1000)
                            await asyncio.sleep(0.3)
                        except:
                            pass
                        
                        # 点击按钮
                        await btn.click(timeout=2000)
                        market_clicked = True
                        used_selector = selector
                        
                        self.logger.info(f"✅ {account_name}: 成功点击'市场'按钮 (选择器 #{i+1})")
                        if dashboard:
                            dashboard.update(last_log=f"{account_name}: ✅ 点击平仓按钮成功")
                        
                        await asyncio.sleep(1.5)  # 等待弹窗动画
                        break
                        
                except Exception as e:
                    self.logger.debug(f"⚠️ {account_name}: 选择器 #{i+1} 失败: {str(e)[:100]}")
                    continue
            
            if not market_clicked:
                # 最后尝试：使用 JS 注入方式点击
                self.logger.warning(f"🔧 {account_name}: 常规选择器全部失败，尝试 JS 注入...")
                try:
                    js_result = await page.evaluate("""
                        () => {
                            // 查找所有包含"市场"或"Market"的按钮
                            const buttons = Array.from(document.querySelectorAll('button'));
                            const marketBtns = buttons.filter(btn => {
                                const text = btn.textContent || btn.innerText || '';
                                return text.includes('市场') || text.includes('Market') || text.includes('市價');
                            });
                            
                            // 过滤掉 Tab 按钮（通常有 role="tab"）
                            const closeButtons = marketBtns.filter(btn => {
                                return btn.getAttribute('role') !== 'tab' && !btn.closest('[role="tablist"]');
                            });
                            
                            if (closeButtons.length > 0) {
                                // 点击最后一个（通常平仓按钮在最后）
                                const targetBtn = closeButtons[closeButtons.length - 1];
                                targetBtn.click();
                                return true;
                            }
                            return false;
                        }
                    """)
                    
                    if js_result:
                        market_clicked = True
                        used_selector = "JS Injection"
                        self.logger.info(f"✅ {account_name}: 通过 JS 注入成功点击'市场'按钮")
                        if dashboard:
                            dashboard.update(last_log=f"{account_name}: ✅ 点击平仓按钮成功 (JS)")
                        await asyncio.sleep(1.5)
                except Exception as e:
                    self.logger.error(f"❌ {account_name}: JS 注入也失败: {str(e)}")
            
            if not market_clicked:
                error_msg = f"❌ 未找到'市场'平仓按钮（尝试了 {len(market_selectors)} 个选择器 + JS 注入）"
                self.logger.error(f"{account_name}: {error_msg}")
                
                # 保存截图用于调试
                try:
                    screenshot_path = f"logs/debug_market_button_{account_name}_{int(time.time())}.png"
                    await page.screenshot(path=screenshot_path)
                    self.logger.info(f"📸 {account_name}: 已保存调试截图: {screenshot_path}")
                except:
                    pass
                
                if dashboard:
                    dashboard.update(last_log=f"{account_name}: {error_msg}", status="🔴 错误")
                
                # 找不到按钮，返回 False 让 Spotter 下次重试
                return False
            
            self.logger.info(f"🎯 {account_name}: 使用的选择器: {used_selector}")

            # ============================================================
            # 第二步：操作弹窗 (清空 -> 输入)
            # ============================================================
            
            self.logger.info(f"🔍 {account_name}: 等待平仓弹窗出现...")
            
            # 弹窗输入框选择器
            input_selectors = [
                'div[role="dialog"] input[aria-label="大小"]', # 限定在 dialog (弹窗) 内
                'div[role="dialog"] input[type="text"]',
                'input.InputNumber__NumberFormat-sc-1il2wqh-2.eWZCdI', 
                'input[aria-label="大小"]', # 兜底
            ]
            
            input_found = None
            for idx, sel in enumerate(input_selectors, 1):
                try:
                    self.logger.debug(f"🔍 {account_name}: 尝试输入框选择器 #{idx}: {sel}...")
                    inp = page.locator(sel).first
                    if await inp.is_visible(timeout=3000):  # 增加到3秒
                        input_found = inp
                        self.logger.info(f"✅ {account_name}: 找到输入框 (选择器 #{idx})")
                        break
                except Exception as e:
                    self.logger.debug(f"⏭️ {account_name}: 选择器 #{idx} 失败: {str(e)[:50]}")
                    continue
            
            if not input_found:
                error_msg = f"❌ 弹窗未弹出 (找不到输入框，尝试了 {len(input_selectors)} 个选择器)"
                self.logger.error(f"{account_name}: {error_msg}")
                if dashboard:
                    dashboard.update(last_log=f"{account_name}: {error_msg}", status="🔴 错误")
                return False

            # 执行输入
            try:
                self.logger.info(f"✏️ {account_name}: 开始输入平仓数量: {quantity_str} BTC...")
                await input_found.click()
                await asyncio.sleep(0.1)
                await input_found.press('Control+a')
                await input_found.press('Delete')
                await asyncio.sleep(0.1)
                await input_found.fill(quantity_str)
                await asyncio.sleep(0.2)
                
                # 验证输入
                val = await input_found.input_value()
                self.logger.info(f"✅ {account_name}: 输入完成，当前值: {val}")
                if quantity_str not in val and val != quantity_str:
                    # 重试一次
                    self.logger.warning(f"⚠️ {account_name}: 输入值不匹配，重试...")
                    await input_found.fill(quantity_str)
                    val = await input_found.input_value()
                    self.logger.info(f"✅ {account_name}: 重试后的值: {val}")
            except Exception as e:
                error_msg = f"输入数量出错: {str(e)}"
                self.logger.error(f"❌ {account_name}: {error_msg}")
                if dashboard:
                     dashboard.update(last_log=f"{account_name}: {error_msg}", status="🔴 错误")
                return False

            # ============================================================
            # 第三步：点击确认 (平多/平空)
            # ============================================================
            
            target_text = "平多仓" if position_direction == "long" else "平空仓"
            english_text = "Close Long" if position_direction == "long" else "Close Short"
            
            self.logger.info(f"🔍 {account_name}: 查找确认按钮: {target_text}...")
            
            # 同样限定在 dialog 内查找按钮，防止点错
            confirm_selectors = [
                f'div[role="dialog"] button[type="submit"]:has-text("{target_text}")',
                f'div[role="dialog"] button:has-text("{target_text}")',
                f'button:has-text("{target_text}")', # 兜底
                # 英文兜底
                f'div[role="dialog"] button[type="submit"]:has-text("{english_text}")',
                f'div[role="dialog"] button:has-text("{english_text}")',
            ]
            
            confirm_clicked = False
            for idx, sel in enumerate(confirm_selectors, 1):
                try:
                    self.logger.debug(f"🔍 {account_name}: 尝试确认按钮选择器 #{idx}: {sel[:60]}...")

                    btn = page.locator(sel).first
                    if await btn.is_visible(timeout=2000):  # 增加到2秒
                        await btn.click()
                        confirm_clicked = True
                        self.logger.info(f"✅ {account_name}: 成功点击确认按钮 (选择器 #{idx})")
                        await asyncio.sleep(1)  # 等待操作完成
                        if dashboard:
                            dashboard.update(
                                last_log=f"{account_name}: ✅ 已执行 {target_text} {quantity_str} BTC",
                                status="⚖️ 平衡中"
                            )
                        return True
                except Exception as e:
                    self.logger.debug(f"⏭️ {account_name}: 选择器 #{idx} 失败: {str(e)[:50]}")
                    continue
            
            if not confirm_clicked:
                error_msg = f"❌ 找不到确认按钮 ({target_text}，尝试了 {len(confirm_selectors)} 个选择器)"
                self.logger.error(f"{account_name}: {error_msg}")
                if dashboard:
                    dashboard.update(last_log=f"{account_name}: {error_msg}", status="🔴 错误")
                return False

            return False
            
        except Exception as e:
            if dashboard:
                dashboard.update(last_log=f"{account_name}: 平仓异常: {str(e)}", status="🔴 错误")
            return False
    
    async def _balance_positions(self, pos_a, pos_b, dir_a, dir_b, dashboard, live):
        """
        执行持仓配平逻辑（Spotter 观察手专用）
        使用反向开单进行配平（更简单可靠）
        
        策略：
        - 如果 A 持仓多 → A 卖出 0.01 BTC
        - 如果 A 持仓空 → A 买入 0.01 BTC
        - 如果 B 持仓多 → B 卖出 0.01 BTC
        - 如果 B 持仓空 → B 买入 0.01 BTC
        - 残留小于 0.01 BTC 的持仓，忽略
        
        Args:
            pos_a: Account A 持仓
            pos_b: Account B 持仓
            dir_a: Account A 持仓方向
            dir_b: Account B 持仓方向
            dashboard: 仪表盘对象
            live: Live 上下文对象
        
        Returns:
            bool: 是否成功配平
        """
        max_attempts = 15  # 最多尝试15次
        max_duration = 45  # 最多运行45秒
        attempt = 0
        start_time = time.time()
        
        while attempt < max_attempts:
            # 检查总超时时间
            elapsed_time = time.time() - start_time
            if elapsed_time > max_duration:
                self.logger.warning(f"⏱️ [Spotter] 配平超时 ({elapsed_time:.1f}秒)，放弃配平")
                dashboard.update(
                    last_log=f"⏱️ [Spotter] 配平超时 ({elapsed_time:.1f}秒)，放弃配平",
                    status="🟡 超时"
                )
                live.update(dashboard.render())
                return False
            
            try:
                # 重新查询当前持仓
                query_a_task = self.get_position_direction_and_balance(self.page_a, self.account_a_name)
                query_b_task = self.get_position_direction_and_balance(self.page_b, self.account_b_name)
                result_a, result_b = await asyncio.gather(
                    query_a_task, query_b_task, return_exceptions=True
                )
                
                current_pos_a, current_dir_a, _ = (result_a if not isinstance(result_a, Exception) else (None, "none", None))
                current_pos_b, current_dir_b, _ = (result_b if not isinstance(result_b, Exception) else (None, "none", None))
                
                current_abs_a = current_pos_a if current_pos_a is not None else 0
                current_abs_b = current_pos_b if current_pos_b is not None else 0
                current_diff = current_abs_a - current_abs_b
                
                # 如果差异小于 0.01 BTC，视为已平衡（忽略残留小持仓）
                if abs(current_diff) < 0.01:
                    self.logger.info(f"🔭 [Spotter] 持仓差异 < 0.01 BTC ({abs(current_diff):.5f} BTC)，视为已平衡")
                    dashboard.update(
                        last_log=f"🔭 [Spotter] 持仓已平衡 (差异: {abs(current_diff):.5f} BTC)",
                        status="✅ 已平衡"
                    )
                    live.update(dashboard.render())
                    return True
                
                # 确定需要减仓的账号和方向
                if current_diff > 0:
                    # A 持仓多于 B，需要减少 A 的持仓
                    reduce_account = "A"
                    reduce_account_name = self.account_a_name
                    reduce_page = self.page_a
                    reduce_direction = current_dir_a
                    reduce_position = current_abs_a
                else:
                    # B 持仓多于 A，需要减少 B 的持仓
                    reduce_account = "B"
                    reduce_account_name = self.account_b_name
                    reduce_page = self.page_b
                    reduce_direction = current_dir_b
                    reduce_position = current_abs_b
                
                # 如果方向未知，无法配平
                if reduce_direction == "none":
                    self.logger.error(f"❌ [Spotter] Account {reduce_account} 持仓方向未知，无法配平")
                    dashboard.update(
                        last_log=f"❌ [Spotter] Account {reduce_account} 持仓方向未知",
                        status="🔴 错误"
                    )
                    live.update(dashboard.render())
                    return False
                
                # 检查要减仓的账户持仓是否太小
                if reduce_position < 0.01:
                    self.logger.info(f"🔭 [Spotter] Account {reduce_account} 持仓过小 ({reduce_position:.5f} BTC < 0.01)，忽略")
                    dashboard.update(
                        last_log=f"🔭 [Spotter] 残留持仓 < 0.01 BTC，忽略",
                        status="✅ 已平衡"
                    )
                    live.update(dashboard.render())
                    return True
                
                # 确定反向操作：多仓 → 卖出，空仓 → 买入
                if reduce_direction == "long":
                    action = "sell"
                    action_text = "卖出"
                elif reduce_direction == "short":
                    action = "buy"
                    action_text = "买入"
                else:
                    self.logger.error(f"❌ [Spotter] 未知的持仓方向: {reduce_direction}")
                    return False
                
                self.logger.info(f"🔭 [Spotter] 配平中 ({attempt+1}/{max_attempts})：{reduce_account_name} {action_text} 0.01 BTC (差异: {abs(current_diff):.5f} BTC)")
                
                # ========== 🔍 盘口深度检查（和 Sniper 逻辑一致）==========
                ask_price, bid_price, ask_size, bid_size = await self.get_order_book_with_depth(reduce_page)
                
                if ask_price is None or bid_price is None:
                    # 价格读取失败，跳过本次配平尝试
                    self.logger.warning("⚠️ [Spotter Depth] 无法读取价格，跳过本次配平尝试")
                    dashboard.update(
                        last_log="⚠️ [Spotter] 价格数据读取失败，等待重试",
                        status="🟡 数据异常"
                    )
                    live.update(dashboard.render())
                    await asyncio.sleep(2)
                    attempt += 1
                    continue
                
                # 处理数量读取失败的情况（-1）
                depth_check_passed = False
                
                if ask_size == -1 or bid_size == -1:
                    # 数量读取失败，打印警告但默认通过（配平优先）
                    self.logger.warning(
                        f"🟡 [Spotter Depth] 数量读取失败 (Ask:{ask_size}, Bid:{bid_size})，默认通过（配平优先）"
                    )
                    depth_check_passed = True  # 默认通过，不阻止配平
                else:
                    # 数量读取成功，检查深度（和 Sniper 一样的逻辑）
                    if action == "sell":
                        # 卖出时检查 Bid 深度
                        if bid_size < self.min_depth:
                            self.logger.warning(
                                f"⚠️ [Spotter Depth] Bid 深度不足 ({bid_size:.4f} BTC < {self.min_depth} BTC)，暂缓配平"
                            )
                            dashboard.update(
                                last_log=f"⚠️ [Spotter] Bid 深度不足 ({bid_size:.3f} < {self.min_depth})，等待",
                                status="🟡 深度不足"
                            )
                            live.update(dashboard.render())
                            await asyncio.sleep(2)
                            attempt += 1
                            continue
                        else:
                            self.logger.info(f"✅ [Spotter Depth] Bid 深度满足 ({bid_size:.4f} BTC >= {self.min_depth} BTC)")
                            depth_check_passed = True
                    else:  # buy
                        # 买入时检查 Ask 深度
                        if ask_size < self.min_depth:
                            self.logger.warning(
                                f"⚠️ [Spotter Depth] Ask 深度不足 ({ask_size:.4f} BTC < {self.min_depth} BTC)，暂缓配平"
                            )
                            dashboard.update(
                                last_log=f"⚠️ [Spotter] Ask 深度不足 ({ask_size:.3f} < {self.min_depth})，等待",
                                status="🟡 深度不足"
                            )
                            live.update(dashboard.render())
                            await asyncio.sleep(2)
                            attempt += 1
                            continue
                        else:
                            self.logger.info(f"✅ [Spotter Depth] Ask 深度满足 ({ask_size:.4f} BTC >= {self.min_depth} BTC)")
                            depth_check_passed = True
                
                # 深度检查通过，执行配平
                dashboard.update(
                    last_log=f"🔭 [Spotter] 深度检查通过，执行配平: {reduce_account_name} {action_text} 0.01 BTC",
                    status="🔭 Spotter Mode"
                )
                live.update(dashboard.render())
                
                # 使用反向开单进行配平（和 Sniper 交易一样）
                success = await self.click_trade_button(
                    reduce_page,
                    reduce_account_name,
                    action,
                    dashboard
                )
                
                if success:
                    self.logger.info(f"✅ [Spotter] 配平交易成功：{reduce_account_name} {action_text} 0.01 BTC")
                    attempt += 1
                    await asyncio.sleep(2)  # 等待交易完成
                else:
                    self.logger.warning(f"⚠️ [Spotter] 配平交易失败：{reduce_account_name} {action_text}")
                    attempt += 1
                    await asyncio.sleep(2)
                
            except Exception as e:
                self.logger.error(f"❌ [Spotter] 配平出错: {str(e)}")
                dashboard.update(
                    last_log=f"❌ [Spotter] 配平出错: {str(e)}",
                    status="🔴 错误"
                )
                live.update(dashboard.render())
                attempt += 1
                await asyncio.sleep(2)
        
        # 超过最大尝试次数
        self.logger.warning("⚠️ [Spotter] 配平达到最大尝试次数")
        return False
    
    async def monitor_spread(self):
        """监控价差的主循环 - Spotter (观察手) + Sniper (狙击手) 架构"""
        # 加载交易计数器
        self.load_trade_count()
        
        # 创建仪表盘
        dashboard = Dashboard(
            self.spread_threshold, 
            self.trade_mode, 
            self.min_available_balance,
            account_a_name=self.account_a_name,
            account_b_name=self.account_b_name,
            enable_auto_rotation=self.enable_auto_rotation
        )
        dashboard.set_force_exit_trades(self.force_exit_trades)
        dashboard.update(
            trade_count=self.trade_count,
            last_log="开始监控价差... | Spotter + Sniper 架构已启动"
        )
        
        # 在开始监控前，验证两个账号的数量是否已正确输入
        quantity_a_ok = await self.verify_quantity_once(self.page_a, self.account_a_name)
        quantity_b_ok = await self.verify_quantity_once(self.page_b, self.account_b_name)
        
        if not quantity_a_ok:
            dashboard.update(last_log=f"{self.account_a_name}: 数量验证失败，数量可能未正确输入", status="🟡 警告")
        if not quantity_b_ok:
            dashboard.update(last_log=f"{self.account_b_name}: 数量验证失败，数量可能未正确输入", status="🟡 警告")
        
        if quantity_a_ok and quantity_b_ok:
            dashboard.update(last_log="数量验证通过，开始实时监控... (按 Ctrl+C 退出)", status="🟢 监控中")
        else:
            dashboard.update(last_log="数量验证未完全通过，但继续监控... (按 Ctrl+C 退出)", status="🟡 警告")
        
        consecutive_errors = 0
        max_errors = 10
        
        # 初始化时查询一次持仓、方向和余额
        try:
            init_query_a = self.get_position_direction_and_balance(self.page_a, self.account_a_name)
            init_query_b = self.get_position_direction_and_balance(self.page_b, self.account_b_name)
            init_result_a, init_result_b = await asyncio.gather(
                init_query_a, init_query_b, return_exceptions=True
            )
            
            if not isinstance(init_result_a, Exception) and init_result_a is not None:
                pos_a, dir_a, bal_a = init_result_a
                if pos_a is not None:
                    self.position_cache["account_a"] = pos_a
                if dir_a is not None:
                    self.direction_cache["account_a"] = dir_a
                if bal_a is not None:
                    dashboard.update(balance_a=bal_a, direction_a=dir_a)
            
            if not isinstance(init_result_b, Exception) and init_result_b is not None:
                pos_b, dir_b, bal_b = init_result_b
                if pos_b is not None:
                    self.position_cache["account_b"] = pos_b
                if dir_b is not None:
                    self.direction_cache["account_b"] = dir_b
                if bal_b is not None:
                    dashboard.update(balance_b=bal_b, direction_b=dir_b)
        except Exception as e:
            # 初始化查询失败不影响启动
            pass
        
        # 使用 Live 上下文管理器来实时更新仪表盘
        with Live(dashboard.render(), refresh_per_second=10, screen=True) as live:
            while True:
                try:
                    # ========== 第一阶段：Spotter (观察手) - 绝对优先级 ==========
                    # 每次循环开始时检查持仓平衡，同时更新余额
                    try:
                        query_a_task = self.get_position_direction_and_balance(self.page_a, self.account_a_name)
                        query_b_task = self.get_position_direction_and_balance(self.page_b, self.account_b_name)
                        result_a, result_b = await asyncio.gather(
                            query_a_task, query_b_task, return_exceptions=True
                        )
                        
                        # 提取持仓、方向和余额（不再丢弃余额）
                        pos_a, dir_a, bal_a = (result_a if not isinstance(result_a, Exception) else (None, "none", None))
                        pos_b, dir_b, bal_b = (result_b if not isinstance(result_b, Exception) else (None, "none", None))
                        
                        # 更新缓存（包括余额）
                        if pos_a is not None:
                            self.position_cache["account_a"] = pos_a
                        if dir_a is not None:
                            self.direction_cache["account_a"] = dir_a
                        if bal_a is not None:
                            self.balance_cache["account_a"] = bal_a
                        if pos_b is not None:
                            self.position_cache["account_b"] = pos_b
                        if dir_b is not None:
                            self.direction_cache["account_b"] = dir_b
                        if bal_b is not None:
                            self.balance_cache["account_b"] = bal_b
                        
                        abs_pos_a = pos_a if pos_a is not None else 0
                        abs_pos_b = pos_b if pos_b is not None else 0
                        diff = abs(abs_pos_a - abs_pos_b)
                        
                        # ========== 🔭 二次确认防抖机制 (Double Check Debounce) ==========
                        # 如果初次检测到持仓不平衡，不要立即行动，而是等待并二次确认
                        # ✅ 修改阈值为 0.01，容忍小于 0.01 BTC 的持仓差异
                        if diff > 0.01:
                            # 第一步：初次检测 - 疑似不平衡
                            self.logger.warning(f"🔭 [Spotter] 初次检测: 疑似不平衡 | A={abs_pos_a:.5f} | B={abs_pos_b:.5f} | Diff={diff:.5f} BTC")
                            dashboard.update(
                                status="🟡 Spotter 等待确认",
                                last_log=f"🔭 [Spotter] 疑似不平衡 (Diff: {diff:.5f} BTC)，等待数据稳定...",
                            )
                            live.update(dashboard.render())
                            
                            # 第二步：防抖冷却 - 等待 UI 稳定
                            await asyncio.sleep(2.0)  # 关键：给 UI 2 秒的渲染时间
                            
                            # 第三步：二次读取 - 重新查询最新数据（包括余额）
                            self.logger.info("🔭 [Spotter] 二次确认: 重新读取持仓和余额数据...")
                            try:
                                query_a_retry = self.get_position_direction_and_balance(self.page_a, self.account_a_name)
                                query_b_retry = self.get_position_direction_and_balance(self.page_b, self.account_b_name)
                                result_a_retry, result_b_retry = await asyncio.gather(
                                    query_a_retry, query_b_retry, return_exceptions=True
                                )
                                
                                # 提取持仓、方向和余额（不丢弃余额）
                                pos_a_retry, dir_a_retry, bal_a_retry = (result_a_retry if not isinstance(result_a_retry, Exception) else (None, "none", None))
                                pos_b_retry, dir_b_retry, bal_b_retry = (result_b_retry if not isinstance(result_b_retry, Exception) else (None, "none", None))
                                
                                # 更新余额缓存
                                if bal_a_retry is not None:
                                    self.balance_cache["account_a"] = bal_a_retry
                                if bal_b_retry is not None:
                                    self.balance_cache["account_b"] = bal_b_retry
                                
                                abs_pos_a_retry = pos_a_retry if pos_a_retry is not None else 0
                                abs_pos_b_retry = pos_b_retry if pos_b_retry is not None else 0
                                diff_retry = abs(abs_pos_a_retry - abs_pos_b_retry)
                                
                                # 第四步：最终决策 - 根据二次读取结果判断
                                # ✅ 修改阈值为 0.01，容忍小于 0.01 BTC 的持仓差异
                                if diff_retry > 0.01:
                                    # ✅ 二次确认：确实不平衡，进入 Spotter Mode
                                    self.spotter_mode = True
                                    self.logger.error(f"❗ [Spotter] 二次确认: 持仓确实不平衡 | A={abs_pos_a_retry:.5f} | B={abs_pos_b_retry:.5f} | Diff={diff_retry:.5f} BTC")
                                    dashboard.update(
                                        status="🔭 Spotter Mode",
                                        last_log=f"🔭 [Spotter] 二次确认不平衡 (Diff: {diff_retry:.5f} BTC)，执行配平...",
                                    )
                                    live.update(dashboard.render())
                                    
                                    # 执行配平逻辑
                                    balance_success = await self._balance_positions(
                                        abs_pos_a_retry, abs_pos_b_retry, dir_a_retry, dir_b_retry, dashboard, live
                                    )
                                    
                                    if balance_success:
                                        self.spotter_mode = False
                                        self.logger.info("✅ [Spotter] 持仓配平成功，恢复 Sniper 模式")
                                        dashboard.update(
                                            status="🟢 Sniper Mode",
                                            last_log="✅ 持仓已平衡，恢复监控",
                                        )
                                        live.update(dashboard.render())
                                    else:
                                        self.logger.error("❌ [Spotter] 持仓配平失败")
                                        dashboard.update(
                                            status="🔴 Spotter Mode (配平失败)",
                                            last_log="⚠️ 配平失败，继续尝试...",
                                        )
                                        live.update(dashboard.render())
                                    
                                    # 配平后强制跳回循环开头，再次检查是否干净
                                    await asyncio.sleep(0.1)
                                    continue
                                else:
                                    # ✅ 虚惊一场：二次读取已平衡（UI延迟导致）
                                    self.logger.info(f"✅ [Spotter] 虚惊一场 (UI延迟) | 二次读取: A={abs_pos_a_retry:.5f} | B={abs_pos_b_retry:.5f} | Diff={diff_retry:.5f} BTC")
                                    dashboard.update(
                                        status="🟢 Sniper Mode",
                                        last_log=f"✅ [Spotter] 虚惊一场 (UI延迟)，持仓正常",
                                    )
                                    live.update(dashboard.render())
                                    # 继续执行 Sniper 逻辑（不 continue）
                                    
                            except Exception as e:
                                # 二次读取失败，保守起见，跳过本轮
                                self.logger.error(f"❌ [Spotter] 二次读取失败: {str(e)[:50]}")
                                dashboard.update(
                                    status="🟡 数据异常",
                                    last_log="⚠️ 二次读取失败，跳过本轮",
                                )
                                live.update(dashboard.render())
                                await asyncio.sleep(0.5)
                                continue
                        else:
                            # 持仓平衡，确保不在 Spotter Mode
                            if self.spotter_mode:
                                self.spotter_mode = False
                                dashboard.update(status="🟢 Sniper Mode")
                    
                    except Exception as e:
                        # 持仓查询失败不影响主循环
                        if self.spotter_mode:
                            self.spotter_mode = False
                        pass
                    
                    # ========== 🔄 自动轮转状态机 (Auto Rotation State Machine) ==========
                    # 只在自动模式下执行，手动模式跳过此逻辑
                    if self.enable_auto_rotation and not self.spotter_mode:
                        try:
                            # 获取当前持仓（使用缓存，避免额外查询）
                            pos_a = self.position_cache.get("account_a", 0)
                            pos_b = self.position_cache.get("account_b", 0)
                            abs_pos_a = abs(pos_a if pos_a else 0)
                            abs_pos_b = abs(pos_b if pos_b else 0)
                            # 使用单边最大持仓（对冲策略应检查单边）
                            max_single_position = max(abs_pos_a, abs_pos_b)
                            
                            # 状态机逻辑
                            if self.trade_mode in [1, 2]:  # 当前是开仓模式
                                # 检查是否达到目标持仓（检查单边最大持仓）
                                if max_single_position >= self.TARGET_POSITION:
                                    self.logger.info(f"🔄 [Auto Rotation] 持仓达标 (单边最大={max_single_position:.5f} >= {self.TARGET_POSITION} BTC)，切换到平仓模式")
                                    self.last_open_mode = self.trade_mode  # 记录当前开仓模式
                                    self.trade_mode = 3  # 切换到平仓模式
                                    dashboard.update(
                                        trade_mode=self.trade_mode,
                                        last_log=f"🔄 自动切换: 开仓 → 平仓 (A={abs_pos_a:.5f}, B={abs_pos_b:.5f} BTC)",
                                        status="🔄 模式切换"
                                    )
                                    live.update(dashboard.render())
                                    await asyncio.sleep(1)
                            
                            elif self.trade_mode == 3:  # 当前是平仓模式
                                # 检查是否平仓完成（检查单边最大持仓）
                                # ✅ 修改阈值为 0.01，容忍微仓位，避免死循环
                                if max_single_position < 0.01:  # 容忍微仓位
                                    # 切换到另一个开仓模式（1→2 或 2→1）
                                    new_mode = 2 if self.last_open_mode == 1 else 1
                                    self.logger.info(f"🔄 [Auto Rotation] 平仓完成 (A={abs_pos_a:.5f}, B={abs_pos_b:.5f} BTC < 0.01)，切换到模式{new_mode}")
                                    
                                    # 如果有微仓位残留，记录提示
                                    if max_single_position > 0.0001:
                                        self.logger.info(f"ℹ️ [Auto Rotation] 残留微仓位 {max_single_position:.5f} BTC，已忽略")
                                    self.trade_mode = new_mode
                                    self.last_open_mode = new_mode  # 更新记录
                                    dashboard.update(
                                        trade_mode=self.trade_mode,
                                        last_log=f"🔄 自动切换: 平仓 → 模式{new_mode} (A={abs_pos_a:.5f}, B={abs_pos_b:.5f} BTC)",
                                        status="🔄 模式切换"
                                    )
                                    live.update(dashboard.render())
                                    await asyncio.sleep(1)
                        except Exception as e:
                            self.logger.error(f"❌ [Auto Rotation] 状态机异常: {str(e)}")
                    
                    # ========== 第二阶段：Sniper (狙击手) - 待命射击 ==========
                    # 只有 Spotter 通过（持仓平衡）才进入此阶段
                    if not self.spotter_mode:
                        # 检查交易计数器限制（24小时重置）
                        if self.trade_count >= self.max_trades:
                            if self.reset_time and (datetime.now() - self.reset_time).total_seconds() >= 86400:
                                # 重置计数器
                                self.trade_count = 0
                                self.reset_time = datetime.now()
                                await self.save_trade_count_async()
                                dashboard.update(
                                    trade_count=self.trade_count,
                                    last_log="交易计数器已重置（24小时）"
                                )
                                live.update(dashboard.render())
                            else:
                                # 等待到重置时间（每60秒检查一次，不阻塞）
                                next_reset = self.reset_time + timedelta(hours=24)
                                wait_seconds = (next_reset - datetime.now()).total_seconds()
                                if wait_seconds > 0:
                                    dashboard.update(
                                        trade_count=self.trade_count,
                                        last_log=f"等待重置 | 重置时间: {next_reset.strftime('%H:%M:%S')}",
                                        status="⏳ 等待重置"
                                    )
                                    live.update(dashboard.render())
                                    await asyncio.sleep(60)  # 每60秒检查一次
                                    continue
                        
                        # 直接读取中间价差框的价差（最快速、最准确）
                        spread_pct = await self.get_spread_from_middle(self.page_a)
                        
                        if spread_pct is None:
                            consecutive_errors += 1
                            if consecutive_errors >= max_errors:
                                dashboard.update(
                                    last_log=f"连续 {max_errors} 次读取失败，请检查页面状态",
                                    status="🔴 错误"
                                )
                                live.update(dashboard.render())
                                consecutive_errors = 0
                            await asyncio.sleep(0.1)  # 快速重试
                            continue
                        
                        consecutive_errors = 0  # 重置错误计数
                        
                        # 读取价格用于显示
                        best_ask, best_bid = await self.get_order_book_prices(self.page_a)
                        
                        # 获取24小时额度信息
                        active_count, max_orders, is_safe, status_text = self.order_guard.get_status_info()
                        
                        # 更新仪表盘（包含持仓、方向和余额缓存）
                        dashboard.update(
                            bid=best_bid,
                            ask=best_ask,
                            spread=spread_pct,
                            pos_a=self.position_cache.get("account_a"),
                            pos_b=self.position_cache.get("account_b"),
                            direction_a=self.direction_cache.get("account_a"),
                            direction_b=self.direction_cache.get("account_b"),
                            balance_a=self.balance_cache.get("account_a"),
                            balance_b=self.balance_cache.get("account_b"),
                            trade_count=self.trade_count,
                            order_guard_count=active_count,
                            order_guard_max=max_orders,
                            order_guard_status=status_text,
                            last_log=f"🟢 [Sniper] 环境安全，正在搜寻猎物 | Spread: {spread_pct:.4f}%",
                            status="🔫 Sniper Mode"
                        )
                        live.update(dashboard.render())
                    
                    # 检查触发条件：直接使用中间价差框的价差
                    # ✅ 修复：添加 spread_pct 有效性检查，允许 0 点差（最佳套利机会）
                    if spread_pct is not None and spread_pct >= 0 and spread_pct < self.spread_threshold:
                        # ========== 🔍 盘口深度检查（防止薄单滑点）==========
                        # 读取订单簿深度（价格 + 数量）
                        ask_price, bid_price, ask_size, bid_size = await self.get_order_book_with_depth(self.page_a)
                        
                        if ask_price is None or bid_price is None:
                            # 价格读取完全失败（严重错误），跳过本次交易
                            self.logger.warning("⚠️ [Depth Check] 无法读取价格，跳过本次交易")
                            dashboard.update(
                                last_log="⚠️ 价格数据读取失败，跳过",
                                status="🟡 数据异常"
                            )
                            live.update(dashboard.render())
                            await asyncio.sleep(0.5)
                            continue
                        
                        # 处理数量读取失败的情况（-1）
                        depth_check_passed = False
                        
                        if ask_size == -1 or bid_size == -1:
                            # 数量读取失败，打印警告但默认通过（激进策略）
                            self.logger.warning(
                                f"🟡 [Depth Check] 数量读取失败 (Ask:{ask_size}, Bid:{bid_size})，采用激进策略：默认通过"
                            )
                            depth_check_passed = True  # 默认通过，不阻止交易
                        else:
                            # 数量读取成功，进行正常的深度判断
                            if ask_size < self.min_depth or bid_size < self.min_depth:
                                # 深度不足，跳过交易
                                self.logger.warning(
                                    f"⚠️ [Depth Check] 深度不足 (Ask:{ask_size:.4f} BTC, Bid:{bid_size:.4f} BTC < {self.min_depth} BTC)，跳过"
                                )
                                dashboard.update(
                                    last_log=f"⚠️ 深度不足 (A:{ask_size:.3f}/B:{bid_size:.3f} < {self.min_depth})，跳过",
                                    status="🟡 深度不足"
                                )
                                live.update(dashboard.render())
                                await asyncio.sleep(0.2)
                                continue
                            else:
                                # 深度满足，通过检查
                                self.logger.info(
                                    f"✅ [Depth Check] 深度满足 (Ask:{ask_size:.4f} BTC, Bid:{bid_size:.4f} BTC >= {self.min_depth} BTC)"
                                )
                                depth_check_passed = True
                        
                        # 如果深度检查未通过，已经在上面 continue 了
                        # 这里只有通过的情况才会继续执行
                        
                        # ========== 📊 24小时额度统计（仅计数，不干预交易）==========
                        # 注：OrderGuard 仅作为统计工具，不阻断交易流程
                        active_count, max_orders, _, status_text = self.order_guard.get_status_info()
                        if active_count >= self.order_guard.safety_threshold:
                            self.logger.info(f"📊 [OrderGuard] 24h交易统计: {active_count}/{max_orders} 笔 (已超过阈值 {self.order_guard.safety_threshold}，但不干预交易)")
                        
                        # ========== 根据模式生成日志文本 ==========
                        if self.trade_mode == 1:
                            mode_text = "模式1 (A买B卖/A多B空)"
                        elif self.trade_mode == 2:
                            mode_text = "模式2 (A卖B买/A空B多)"
                        elif self.trade_mode == 3:
                            mode_text = "平仓模式 (自动检测)"
                        else:
                            mode_text = f"未知模式 ({self.trade_mode})"
                        
                        dashboard.update(
                            last_log=f"🔫 [Sniper] 锁定目标，开火！({mode_text}) | Spread: {spread_pct:.4f}% < {self.spread_threshold}%",
                            status="🚀 正在下单..."
                        )
                        live.update(dashboard.render())
                        
                        # ========== 根据模式执行买卖操作（重构：支持3种模式）==========
                        if self.trade_mode == 1:
                            # 模式1 (A多B空)：A买 B卖
                            self.logger.info(f"🔫 [Sniper] 模式1执行: {self.account_a_name} 买入, {self.account_b_name} 卖出")
                            task_a = self.click_trade_button(self.page_a, self.account_a_name, "buy", dashboard)
                            task_b = self.click_trade_button(self.page_b, self.account_b_name, "sell", dashboard)
                        
                        elif self.trade_mode == 2:
                            # 模式2 (A空B多)：A卖 B买
                            self.logger.info(f"🔫 [Sniper] 模式2执行: {self.account_a_name} 卖出, {self.account_b_name} 买入")
                            task_a = self.click_trade_button(self.page_a, self.account_a_name, "sell", dashboard)
                            task_b = self.click_trade_button(self.page_b, self.account_b_name, "buy", dashboard)
                        
                        elif self.trade_mode == 3:
                            # ========== 平仓模式：根据当前持仓方向决定操作 ==========
                            # 获取当前持仓方向（从缓存中读取，如果缓存为空则查询）
                            dir_a = self.direction_cache.get("account_a", "none")
                            dir_b = self.direction_cache.get("account_b", "none")
                            pos_a = self.position_cache.get("account_a", 0)
                            pos_b = self.position_cache.get("account_b", 0)
                            
                            # 如果方向未知，快速查询（不阻塞，使用缓存值）
                            if dir_a == "none" or dir_b == "none":
                                try:
                                    # 快速查询方向（不查询余额，只查询方向）
                                    quick_query_a = self.get_position_direction_by_color(self.page_a)
                                    quick_query_b = self.get_position_direction_by_color(self.page_b)
                                    quick_dir_a, quick_dir_b = await asyncio.gather(
                                        quick_query_a, quick_query_b, return_exceptions=True
                                    )
                                    if not isinstance(quick_dir_a, Exception) and quick_dir_a != "none":
                                        dir_a = quick_dir_a
                                    if not isinstance(quick_dir_b, Exception) and quick_dir_b != "none":
                                        dir_b = quick_dir_b
                                except:
                                    pass  # 如果查询失败，使用缓存值或默认逻辑
                            
                            # ========== 🛡️ 无持仓保护机制 (Critical Protection) ==========
                            # 检查：如果两个账户都无持仓（或持仓极小），不执行平仓操作
                            if dir_a == "none" and dir_b == "none":
                                self.logger.warning(f"⚠️ [Sniper] 平仓模式检测到双方无持仓 (A={pos_a:.5f}, B={pos_b:.5f})，跳过本次交易")
                                dashboard.update(
                                    last_log="⚠️ 平仓完毕，无持仓可平",
                                    status="🟢 Sniper Mode"
                                )
                                live.update(dashboard.render())
                                await asyncio.sleep(1)
                                continue  # 跳过本次交易
                            
                            # 检查：如果持仓已经很小（< 0.01 BTC），处理模式切换/退出
                            # ✅ 修改阈值为 0.01，容忍微仓位，避免死循环
                            total_position = abs(pos_a if pos_a else 0) + abs(pos_b if pos_b else 0)
                            if total_position < 0.01:
                                if self.enable_auto_rotation:
                                    # 自动模式：切换回开仓模式
                                    self.logger.info(f"🔄 [Auto] 持仓已基本清空 (总持仓={total_position:.5f} BTC < 0.01 BTC)，自动切换回开仓模式")
                                    
                                    # 如果有微仓位残留，记录提示
                                    if total_position > 0.0001:
                                        self.logger.info(f"ℹ️ [Auto] 残留微仓位 {total_position:.5f} BTC，已忽略")
                                    
                                    self.trade_mode = 1
                                    dashboard.update(
                                        trade_mode=self.trade_mode,
                                        last_log=f"🔄 持仓已清空 (总持仓={total_position:.5f} BTC)，自动切换回开仓模式",
                                        status="🟢 Sniper Mode"
                                    )
                                    live.update(dashboard.render())
                                    await asyncio.sleep(1)
                                    continue
                                else:
                                    # 手动模式：平仓完毕后退出程序
                                    self.logger.info(f"✅ [手动模式] 持仓已基本清空 (总持仓={total_position:.5f} BTC < 0.01 BTC)，平仓任务完成，程序退出")
                                    
                                    # 如果有微仓位残留，记录提示
                                    if total_position > 0.0001:
                                        self.logger.info(f"ℹ️ 残留微仓位 {total_position:.5f} BTC，可忽略")
                                    
                                    dashboard.update(
                                        last_log=f"✅ 平仓任务完成 (剩余持仓={total_position:.5f} BTC)，程序退出",
                                        status="🟢 完成"
                                    )
                                    live.update(dashboard.render())
                                    await asyncio.sleep(2)  # 让用户看到最终状态
                                    
                                    # 显示退出总结
                                    self.print_exit_summary(dashboard, live, reason="平仓任务完成")
                                    return  # 退出 monitor_spread，结束程序
                            
                            # ========== 平仓方向判断 ==========
                            # 根据持仓方向决定平仓操作
                            # 多仓（long）：卖出（sell）来平仓
                            # 空仓（short）：买入（buy）来平仓
                            # ⚠️ 无持仓（none）：跳过该账户，只平另一方
                            
                            skip_a = False
                            skip_b = False
                            
                            # 检查单边持仓是否太小（< 0.01 BTC），太小则跳过（容忍微仓位）
                            # ✅ 修改阈值为 0.01，避免微仓位死循环
                            if pos_a < 0.01:
                                skip_a = True
                                action_a = None
                                self.logger.info(f"ℹ️ [Sniper] Account A 微仓位 ({pos_a:.5f} BTC < 0.01)，已跳过平仓")
                            elif dir_a == "long":
                                action_a = "sell"  # 多仓 → 卖出平仓
                            elif dir_a == "short":
                                action_a = "buy"   # 空仓 → 买入平仓
                            else:
                                # ⚠️ 无持仓：不执行 A 的操作
                                skip_a = True
                                action_a = None
                                self.logger.warning(f"⚠️ [Sniper] Account A 无持仓 (dir={dir_a})，跳过 A 的平仓操作")
                            
                            if pos_b < 0.01:
                                skip_b = True
                                action_b = None
                                self.logger.info(f"ℹ️ [Sniper] Account B 微仓位 ({pos_b:.5f} BTC < 0.01)，已跳过平仓")
                            elif dir_b == "long":
                                action_b = "sell"  # 多仓 → 卖出平仓
                            elif dir_b == "short":
                                action_b = "buy"   # 空仓 → 买入平仓
                            else:
                                # ⚠️ 无持仓：不执行 B 的操作
                                skip_b = True
                                action_b = None
                                self.logger.warning(f"⚠️ [Sniper] Account B 无持仓 (dir={dir_b})，跳过 B 的平仓操作")
                            
                            # 如果两边都要跳过，直接 continue
                            if skip_a and skip_b:
                                self.logger.error("❌ [Sniper] 双方都无持仓，无法执行平仓")
                                dashboard.update(
                                    last_log="❌ 无持仓可平，请检查持仓状态",
                                    status="🟡 警告"
                                )
                                live.update(dashboard.render())
                                await asyncio.sleep(2)
                                continue
                            
                            # 记录平仓操作信息（用于调试）
                            log_msg = f"平仓模式：A({dir_a})→{action_a if not skip_a else 'SKIP'}, B({dir_b})→{action_b if not skip_b else 'SKIP'}"
                            self.logger.info(f"🔫 [Sniper] {log_msg}")
                            dashboard.update(
                                last_log=log_msg,
                                status="🚀 正在下单..."
                            )
                            live.update(dashboard.render())
                            
                            # 执行平仓操作（跳过无持仓的账户）
                            if not skip_a and not skip_b:
                                # 双方都有持仓，执行双边平仓
                                task_a = self.click_trade_button(self.page_a, self.account_a_name, action_a, dashboard)
                                task_b = self.click_trade_button(self.page_b, self.account_b_name, action_b, dashboard)
                            elif skip_a:
                                # 只平 B
                                task_a = asyncio.sleep(0)  # 占位任务
                                task_b = self.click_trade_button(self.page_b, self.account_b_name, action_b, dashboard)
                            else:
                                # 只平 A
                                task_a = self.click_trade_button(self.page_a, self.account_a_name, action_a, dashboard)
                                task_b = asyncio.sleep(0)  # 占位任务
                        
                        else:
                            # 未知模式，报错并跳过
                            self.logger.error(f"❌ [Sniper] 未知的交易模式: {self.trade_mode}")
                            dashboard.update(
                                last_log=f"❌ 未知交易模式 ({self.trade_mode})，请重新选择",
                                status="🔴 错误"
                            )
                            live.update(dashboard.render())
                            await asyncio.sleep(2)
                            continue
                        
                        # 并发执行买卖操作
                        # 等待两个操作完成
                        results = await asyncio.gather(task_a, task_b, return_exceptions=True)
                        
                        # 检查交易结果
                        action_a_success = results[0] if not isinstance(results[0], Exception) else False
                        action_b_success = results[1] if not isinstance(results[1], Exception) else False
                        
                        # 记录交易结果
                        if action_a_success and action_b_success:
                            self.logger.info(f"✅ [Sniper] 双边交易成功 | {mode_text} | Spread: {spread_pct:.4f}%")
                        elif action_a_success:
                            self.logger.warning(f"⚠️ [Sniper] 单边交易 (A成功, B失败) | {mode_text}")
                        elif action_b_success:
                            self.logger.warning(f"⚠️ [Sniper] 单边交易 (A失败, B成功) | {mode_text}")
                        else:
                            self.logger.error(f"❌ [Sniper] 双边交易失败 | {mode_text}")
                        
                        # 只要有一个成功就继续（不要求两个都成功，避免A失败导致整体失败）
                        if action_a_success or action_b_success:
                            # 增加交易计数（异步保存，不阻塞）
                            self.increment_trade_count()
                            # 添加订单记录到滑动窗口计数器（交易成功后）
                            self.order_guard.add_order()
                            # 睡后模式：增加周期计数器
                            if self.auto_mode:
                                self.cycle_trade_count += 1
                            
                            # 等待页面更新（确保持仓信息已刷新）- 增加到5秒，防止UI延迟导致的幻读
                            self.logger.info("⏳ [Sniper] 等待 5 秒，确保 UI 完全刷新...")
                            await asyncio.sleep(5.0)  # ⚠️ 关键：从 1秒 → 3.5秒 → 5秒，彻底解决 UI 延迟
                            
                            # 并发查询持仓、方向和余额（只在交易成功后查询，不影响主循环性能）
                            query_a_task = self.get_position_direction_and_balance(self.page_a, self.account_a_name)
                            query_b_task = self.get_position_direction_and_balance(self.page_b, self.account_b_name)
                            result_a, result_b = await asyncio.gather(
                                query_a_task, query_b_task, return_exceptions=True
                            )
                            
                            # 处理查询结果
                            if isinstance(result_a, Exception) or result_a is None:
                                position_a, direction_a, balance_a = None, "none", None
                            else:
                                position_a, direction_a, balance_a = result_a
                            
                            if isinstance(result_b, Exception) or result_b is None:
                                position_b, direction_b, balance_b = None, "none", None
                            else:
                                position_b, direction_b, balance_b = result_b
                            
                            # 更新持仓、方向和余额缓存
                            if position_a is not None:
                                self.position_cache["account_a"] = position_a
                            if direction_a is not None:
                                self.direction_cache["account_a"] = direction_a
                            if balance_a is not None:
                                self.balance_cache["account_a"] = balance_a
                            if position_b is not None:
                                self.position_cache["account_b"] = position_b
                            if direction_b is not None:
                                self.direction_cache["account_b"] = direction_b
                            if balance_b is not None:
                                self.balance_cache["account_b"] = balance_b
                            
                            # 检查持仓差异，如果大于0.05 BTC，强制退出程序
                            if position_a is not None and position_b is not None:
                                abs_pos_a = abs(position_a) if position_a is not None else 0
                                abs_pos_b = abs(position_b) if position_b is not None else 0
                                position_diff = abs(abs_pos_a - abs_pos_b)
                                
                                if position_diff > 0.05:  # 持仓差异大于0.05 BTC
                                    dashboard.update(
                                        last_log=f"⚠️ 持仓差异过大：A={abs_pos_a:.5f} BTC, B={abs_pos_b:.5f} BTC，差异={position_diff:.5f} BTC > 0.05 BTC，强制退出程序",
                                        status="🔴 强制退出"
                                    )
                                    live.update(dashboard.render())
                                    await asyncio.sleep(3)  # 显示退出信息
                                    return  # 强制退出监控循环
                            
                            # 检查余额是否低于阈值
                            balance_warning = ""
                            if balance_a is not None and balance_a < self.min_available_balance:
                                balance_warning += f" | A余额: ${balance_a:.2f} < 阈值"
                            if balance_b is not None and balance_b < self.min_available_balance:
                                balance_warning += f" | B余额: ${balance_b:.2f} < 阈值"
                            
                            # 更新仪表盘
                            pos_info = ""
                            if self.position_cache["account_a"] is not None:
                                dir_symbol_a = "📈多" if self.direction_cache["account_a"] == "long" else "📉空" if self.direction_cache["account_a"] == "short" else ""
                                pos_info += f" | A: {self.position_cache['account_a']:.5f} {dir_symbol_a}"
                            if self.position_cache["account_b"] is not None:
                                dir_symbol_b = "📈多" if self.direction_cache["account_b"] == "long" else "📉空" if self.direction_cache["account_b"] == "short" else ""
                                pos_info += f" | B: {self.position_cache['account_b']:.5f} {dir_symbol_b}"
                            
                            # 显示交易状态（A成功/B成功/都成功）
                            trade_status = ""
                            if action_a_success and action_b_success:
                                trade_status = "✅ 交易执行成功（A+B）"
                            elif action_a_success:
                                trade_status = "⚠️ 交易部分成功（A成功，B失败）"
                            elif action_b_success:
                                trade_status = "⚠️ 交易部分成功（B成功，A失败）"
                            
                            log_msg = f"{trade_status} | 计数: {self.trade_count}/{self.force_exit_trades}"
                            log_msg += f"{pos_info}{balance_warning}"
                            
                            dashboard.update(
                                trade_count=self.trade_count,
                                pos_a=self.position_cache["account_a"],
                                pos_b=self.position_cache["account_b"],
                                direction_a=self.direction_cache["account_a"],
                                direction_b=self.direction_cache["account_b"],
                                balance_a=balance_a,
                                balance_b=balance_b,
                                last_log=log_msg,
                                status="✅ 交易完成"
                            )
                            live.update(dashboard.render())
                            
                            # 🛡️ 如果余额低于阈值，停止脚本（平仓模式除外，避免死锁）
                            # 平仓模式（mode 3）跳过余额检查，因为平仓是为了释放保证金
                            if self.trade_mode != 3:
                                if (balance_a is not None and balance_a < self.min_available_balance) or \
                                   (balance_b is not None and balance_b < self.min_available_balance):
                                    dashboard.update(
                                        last_log=f"可用余额低于阈值 {self.min_available_balance} USD，停止交易",
                                        status="🔴 余额不足"
                                    )
                                    live.update(dashboard.render())
                                    self.graceful_exit(ExitReason.BALANCE_LOW, f"余额低于 {self.min_available_balance} USD")
                                    await asyncio.sleep(2)
                                    return  # 退出监控循环
                            else:
                                # 平仓模式：忽略余额检查，记录日志
                                if (balance_a is not None and balance_a < self.min_available_balance) or \
                                   (balance_b is not None and balance_b < self.min_available_balance):
                                    self.logger.info(f"⚠️ [平仓模式] 余额低于阈值，但平仓模式允许继续执行")
                            
                            # 检查是否达到强制退出次数（手动模式下生效，自动模式跳过）
                            if not self.enable_auto_rotation and self.trade_count >= self.force_exit_trades:
                                self.logger.info(f"🛑 [手动模式] 已达到强制退出次数 {self.force_exit_trades}，程序退出")
                                dashboard.update(
                                    last_log=f"已达到强制退出次数 {self.force_exit_trades}，程序退出",
                                    status="🔴 退出"
                                )
                                live.update(dashboard.render())
                                self.graceful_exit(ExitReason.MANUAL_EXIT, f"手动模式达到 {self.force_exit_trades} 笔交易")
                                await asyncio.sleep(2)  # 显示退出信息
                                return  # 退出监控循环
                            elif self.enable_auto_rotation and self.trade_count >= self.force_exit_trades:
                                # 自动模式下只记录日志，不退出
                                self.logger.info(f"📊 [自动模式] 已完成 {self.trade_count} 笔交易（无退出限制）")
                            
                            # 检查会话交易限制（自动退出）
                            session_count, session_limit = self.order_guard.get_session_info()
                            
                            # 每10笔交易打印一次进度
                            if session_count % 10 == 0:
                                self.logger.info(f"📊 [会话进度] {session_count}/{session_limit} 笔交易")
                            
                            if self.order_guard.should_exit():
                                self.logger.info(f"🎯 [自动退出] 已完成 {session_count}/{session_limit} 笔交易，程序自动退出")
                                dashboard.update(
                                    last_log=f"✅ 任务完成：{session_count} 笔交易", 
                                    status="🎉 完成"
                                )
                                live.update(dashboard.render())
                                self.graceful_exit(ExitReason.SESSION_LIMIT, f"完成 {session_count}/{session_limit} 笔交易")
                                await asyncio.sleep(2)
                                return  # 退出监控循环
                            
                            # 💰 每100笔交易检查一次手续费（独立检查，不影响主策略）
                            if session_count > 0 and \
                               session_count % self.FEE_CHECK_INTERVAL == 0 and \
                               session_count != self.last_fee_check_count:
                                
                                self.last_fee_check_count = session_count  # 标记已检查，避免重复
                                self.logger.info(f"💰 [FeeCheck] 达到 {session_count} 笔交易，执行手续费检查...")
                                
                                live.update(dashboard.render())
                                
                                fee_is_zero = await self.check_trading_fee(self.page_a, dashboard)
                                live.update(dashboard.render())
                                
                                if not fee_is_zero:
                                    # 检测到非零手续费，安全退出
                                    self.logger.error(f"🚨 [FeeCheck] 检测到非零手续费，程序安全退出！")
                                    dashboard.update(
                                        last_log=f"🚨 检测到非零手续费，程序退出（{session_count} 笔交易）",
                                        status="🔴 费用异常"
                                    )
                                    live.update(dashboard.render())
                                    self.graceful_exit(ExitReason.FEE_DETECTED, f"检测到非零手续费（{session_count} 笔交易）")
                                    await asyncio.sleep(3)
                                    return  # 安全退出
                                else:
                                    self.logger.info(f"✅ [FeeCheck] 手续费检查通过，继续交易")
                            
                            # 每50单截图一次（后台处理，不阻塞）
                            if self.trade_count % 50 == 0:
                                async def save_screenshot():
                                    try:
                                        screenshot_path = self.base_dir / f"success_trade_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                                        await self.page_a.screenshot(path=str(screenshot_path), full_page=True)
                                        dashboard.update(last_log=f"第 {self.trade_count} 单截图已保存: {screenshot_path}")
                                    except Exception as e:
                                        pass
                                
                                # 创建后台任务，不等待完成
                                asyncio.create_task(save_screenshot())
                        else:
                            # 交易失败信息
                            mode_text = "A买B卖" if self.trade_mode == 1 else "A卖B买"
                            dashboard.update(
                                last_log=f"交易可能失败 | 模式: {mode_text} | A: {action_a_success}, B: {action_b_success}",
                                status="🟡 警告"
                            )
                            live.update(dashboard.render())
                        
                        # 开仓后自然回到循环开头（让 Spotter 检查持仓）
                        # 不需要 continue，因为已经在循环内，会自然回到开头
                    
                    # 短暂休眠，优化读取速度
                    await asyncio.sleep(0.05)  # 50ms 间隔，约 20 次/秒
                    
                except PlaywrightTimeoutError:
                    consecutive_errors += 1
                    if consecutive_errors < max_errors:
                        await asyncio.sleep(0.1)
                        continue
                except KeyboardInterrupt:
                    dashboard.update(last_log="用户中断程序", status="🔴 退出")
                    live.update(dashboard.render())
                    await asyncio.sleep(1)
                    raise
                except Exception as e:
                    consecutive_errors += 1
                    if consecutive_errors >= max_errors:
                        dashboard.update(
                            last_log=f"监控循环异常: {e}",
                            status="🔴 错误"
                        )
                        live.update(dashboard.render())
                        consecutive_errors = 0
                    await asyncio.sleep(0.1)
                    continue
    
    def select_trade_mode(self):
        """
        选择交易模式（已废弃，使用 select_trade_mode_with_position 替代）
        保留此方法仅作为备用
        """
        from rich.console import Console
        console = Console()
        
        console.print("\n" + "=" * 60, style="cyan")
        console.print("请选择交易模式：", style="bold")
        console.print("  1. 开仓模式：Account A 买入，Account B 卖出")
        console.print("  2. 平仓模式：Account A 卖出，Account B 买入（镜像）")
        console.print("=" * 60, style="cyan")
        
        while True:
            try:
                choice = input("请输入模式序号 (1 或 2): ").strip()
                if choice == "1":
                    self.trade_mode = 1
                    return
                elif choice == "2":
                    self.trade_mode = 2
                    return
                else:
                    console.print("[Error] 无效选择，请输入 1 或 2", style="red")
            except (EOFError, KeyboardInterrupt):
                console.print("\n[Exit] 用户取消", style="yellow")
                raise
    
    def select_account_group(self):
        """选择交易账号组"""
        from rich.console import Console
        from rich.panel import Panel
        from rich.table import Table
        
        console = Console()
        
        # 创建账号组选择表格
        table = Table(title="🦈 选择交易账号组", show_header=True, header_style="bold cyan")
        table.add_column("选项", style="cyan", width=8)
        table.add_column("账号组", style="green", width=30)
        table.add_column("认证文件", style="yellow")
        
        table.add_row(
            "[1]",
            "🦈 Group A (Shark 1 & Shark 2)",
            "auth_main.json & auth_hedge.json"
        )
        table.add_row(
            "[2]",
            "🦈 Group B (Shark 3 & Shark 4)",
            "auth_shark3.json & auth_shark4.json"
        )
        table.add_row(
            "[3]",
            "🦈 Group C (Shark 5 & Shark 6)",
            "auth_shark5.json & auth_shark6.json"
        )
        table.add_row(
            "[4]",
            "🦈 Group D (Shark 7 & Shark 8)",
            "auth_shark7.json & auth_shark8.json"
        )
        
        console.print("\n")
        console.print(table)
        console.print("\n")
        
        while True:
            try:
                choice = input("请选择账号组 [1/2/3/4]: ").strip()
                
                if choice == "1":
                    # Group A: Shark 1 & Shark 2
                    group_config = self.account_group_paths["group_a"]
                    self.auth_main_path = group_config["main"]
                    self.auth_hedge_path = group_config["hedge"]
                    self.account_a_name = group_config["name_a"]
                    self.account_b_name = group_config["name_b"]
                    
                    console.print(f"\n✅ 已选择: [bold green]Group A[/bold green]", style="green")
                    console.print(f"   📌 账号 A: [bold cyan]{self.account_a_name}[/bold cyan]")
                    console.print(f"   📌 账号 B: [bold cyan]{self.account_b_name}[/bold cyan]")
                    
                    # ✅ 初始化数据文件（根据账号组生成唯一文件名）
                    self._setup_data_files()
                    break
                    
                elif choice == "2":
                    # Group B: Shark 3 & Shark 4
                    group_config = self.account_group_paths["group_b"]
                    self.auth_main_path = group_config["main"]
                    self.auth_hedge_path = group_config["hedge"]
                    self.account_a_name = group_config["name_a"]
                    self.account_b_name = group_config["name_b"]
                    
                    console.print(f"\n✅ 已选择: [bold green]Group B[/bold green]", style="green")
                    console.print(f"   📌 账号 A: [bold cyan]{self.account_a_name}[/bold cyan]")
                    console.print(f"   📌 账号 B: [bold cyan]{self.account_b_name}[/bold cyan]")
                    
                    # ✅ 初始化数据文件（根据账号组生成唯一文件名）
                    self._setup_data_files()
                    break
                    
                elif choice == "3":
                    # Group C: Shark 5 & Shark 6
                    group_config = self.account_group_paths["group_c"]
                    self.auth_main_path = group_config["main"]
                    self.auth_hedge_path = group_config["hedge"]
                    self.account_a_name = group_config["name_a"]
                    self.account_b_name = group_config["name_b"]
                    
                    console.print(f"\n✅ 已选择: [bold green]Group C[/bold green]", style="green")
                    console.print(f"   📌 账号 A: [bold cyan]{self.account_a_name}[/bold cyan]")
                    console.print(f"   📌 账号 B: [bold cyan]{self.account_b_name}[/bold cyan]")
                    
                    # ✅ 初始化数据文件（根据账号组生成唯一文件名）
                    self._setup_data_files()
                    break
                    
                elif choice == "4":
                    # Group D: Shark 7 & Shark 8
                    group_config = self.account_group_paths["group_d"]
                    self.auth_main_path = group_config["main"]
                    self.auth_hedge_path = group_config["hedge"]
                    self.account_a_name = group_config["name_a"]
                    self.account_b_name = group_config["name_b"]
                    
                    console.print(f"\n✅ 已选择: [bold green]Group D[/bold green]", style="green")
                    console.print(f"   📌 账号 A: [bold cyan]{self.account_a_name}[/bold cyan]")
                    console.print(f"   📌 账号 B: [bold cyan]{self.account_b_name}[/bold cyan]")
                    
                    # ✅ 初始化数据文件（根据账号组生成唯一文件名）
                    self._setup_data_files()
                    break
                    
                else:
                    console.print("[Error] 无效选择，请输入 1、2、3 或 4", style="red")
                    
            except (EOFError, KeyboardInterrupt):
                console.print("\n[Exit] 用户取消", style="yellow")
                raise
    
    async def select_trade_mode_with_position(self):
        """选择交易模式（显示当前持仓和方向）"""
        from rich.console import Console
        from rich.table import Table
        console = Console()
        
        console.print("\n" + "=" * 60, style="cyan")
        console.print("正在查询当前持仓信息...", style="yellow")
        
        # 查询两个账号的持仓、方向和余额
        try:
            query_a_task = self.get_position_direction_and_balance(self.page_a, self.account_a_name)
            query_b_task = self.get_position_direction_and_balance(self.page_b, self.account_b_name)
            result_a, result_b = await asyncio.gather(
                query_a_task, query_b_task, return_exceptions=True
            )
            
            # 处理查询结果
            if isinstance(result_a, Exception) or result_a is None:
                pos_a, dir_a, bal_a = None, "none", None
            else:
                pos_a, dir_a, bal_a = result_a
            
            if isinstance(result_b, Exception) or result_b is None:
                pos_b, dir_b, bal_b = None, "none", None
            else:
                pos_b, dir_b, bal_b = result_b
            
            # 显示持仓信息表格
            console.print("\n" + "=" * 60, style="cyan")
            console.print("当前持仓信息：", style="bold")
            
            position_table = Table(show_header=True, header_style="bold cyan")
            position_table.add_column("账号", style="cyan", justify="center")
            position_table.add_column("持仓数量", justify="center")
            position_table.add_column("持仓方向", justify="center")
            position_table.add_column("可用余额", justify="center")
            
            # 格式化持仓显示
            def format_pos_display(pos, direction):
                if pos is None or pos == 0 or direction == "none":
                    return "无持仓"
                if direction == "long":
                    return f"[green]📈 {pos:.5f} BTC[/green]"
                elif direction == "short":
                    return f"[red]📉 {pos:.5f} BTC[/red]"
                else:
                    return f"{pos:.5f} BTC"
            
            def format_dir_display(direction):
                if direction == "long":
                    return "[green]多仓[/green]"
                elif direction == "short":
                    return "[red]空仓[/red]"
                else:
                    return "[dim]无持仓[/dim]"
            
            pos_a_display = format_pos_display(pos_a, dir_a)
            pos_b_display = format_pos_display(pos_b, dir_b)
            dir_a_display = format_dir_display(dir_a)
            dir_b_display = format_dir_display(dir_b)
            bal_a_display = f"${bal_a:,.2f}" if bal_a is not None else "N/A"
            bal_b_display = f"${bal_b:,.2f}" if bal_b is not None else "N/A"
            
            position_table.add_row(self.account_a_name, pos_a_display, dir_a_display, bal_a_display)
            position_table.add_row(self.account_b_name, pos_b_display, dir_b_display, bal_b_display)
            
            console.print(position_table)
            console.print("=" * 60, style="cyan")
            
        except Exception as e:
            console.print(f"[yellow]警告：无法查询持仓信息: {e}[/yellow]")
            console.print("=" * 60, style="cyan")
        
        # ========== 第一步：选择运行方式（自动 vs 手动）==========
        console.print("\n请选择运行方式：", style="bold cyan")
        console.print("  [cyan]1. 🔄 自动狙击模式[/cyan] (Auto Rotation 1-3-2-3 Loop)")
        console.print("     → 自动在 Mode 1→3→2→3 之间循环，根据持仓量智能切换")
        console.print("  [cyan]2. 🖐️ 手动狙击模式[/cyan] (Manual Single Mode)")
        console.print("     → 手动选择并固定在某个模式（1/2/3）")
        console.print("=" * 60, style="cyan")
        
        while True:
            try:
                mode_choice = input("\n请输入序号 (1 或 2): ").strip()
                
                if mode_choice == "1":
                    # ========== 自动模式 ==========
                    self.enable_auto_rotation = True
                    self.trade_mode = 1  # 默认从模式1开始
                    self.last_open_mode = 1
                    self.logger.info("🔄 [Auto Rotation] 已启用自动轮转模式")
                    console.print("\n[bold green]✅ 已启动自动轮转模式[/bold green]")
                    console.print(f"[cyan]目标持仓阈值: {self.TARGET_POSITION} BTC[/cyan]")
                    console.print("[cyan]轮转逻辑: 模式1 (开仓) → 模式3 (平仓) → 模式2 (开仓) → 模式3 (平仓) → ...[/cyan]")
                    return
                
                elif mode_choice == "2":
                    # ========== 手动模式 ==========
                    self.enable_auto_rotation = False
                    console.print("\n[bold green]✅ 已选择手动狙击模式[/bold green]")
                    
                    # 显示模式选择（3种模式）
                    console.print("\n请选择交易模式：", style="bold")
                    console.print("  [cyan]1. 模式1 (A多B空)[/cyan]：Account A 买入 (做多)，Account B 卖出 (做空)")
                    console.print("  [cyan]2. 模式2 (A空B多)[/cyan]：Account A 卖出 (做空)，Account B 买入 (做多)")
                    console.print("  [cyan]3. 平仓模式[/cyan]：自动检测持仓方向，反向平仓")
                    console.print("=" * 60, style="cyan")
                    
                    while True:
                        try:
                            choice = input("请输入模式序号 (1, 2 或 3): ").strip()
                            if choice == "1":
                                self.trade_mode = 1
                                console.print("[green]✅ 已选择：模式1 (A多B空)[/green]")
                                return
                            elif choice == "2":
                                self.trade_mode = 2
                                console.print("[green]✅ 已选择：模式2 (A空B多)[/green]")
                                return
                            elif choice == "3":
                                self.trade_mode = 3
                                console.print("[green]✅ 已选择：平仓模式[/green]")
                                return
                            else:
                                console.print("[Error] 无效选择，请输入 1, 2 或 3", style="red")
                        except (EOFError, KeyboardInterrupt):
                            console.print("\n[Exit] 用户取消", style="yellow")
                            raise
                else:
                    console.print("[yellow]⚠️ 无效选择，请输入 1 或 2[/yellow]")
                    
            except (EOFError, KeyboardInterrupt):
                console.print("\n[Exit] 用户取消", style="yellow")
                raise
    
    async def run(self):
        """主运行函数"""
        from rich.console import Console
        console = Console()
        
        playwright = None
        try:
            self.logger.info("="*60)
            self.logger.info("系统启动中...")
            self.logger.info("="*60)
            
            console.print("=" * 60, style="cyan")
            console.print("Paradex Dual Taker - 价差监控触发交易系统", style="bold cyan")
            console.print("=" * 60, style="cyan")
            
            # 🦈 第一步：选择账号组
            self.select_account_group()
            
            # 先初始化浏览器（需要先初始化才能查询持仓）
            self.logger.info("正在初始化浏览器...")
            playwright = await self.init_browser()
            self.logger.info("浏览器初始化完成")
            
            # 设置两个账号的交易页面（临时设置，用于查询持仓）
            console.print("\n正在初始化交易页面...", style="yellow")
            temp_dashboard = Dashboard(
                self.spread_threshold, 
                1, 
                self.min_available_balance,
                account_a_name=self.account_a_name,
                account_b_name=self.account_b_name,
                enable_auto_rotation=self.enable_auto_rotation
            )  # 临时模式
            
            await asyncio.gather(
                self.setup_trading_page(self.page_a, self.account_a_name, temp_dashboard),
                self.setup_trading_page(self.page_b, self.account_b_name, temp_dashboard)
            )
            
            await asyncio.sleep(2)  # 等待页面完全加载
            
            # 选择交易模式
            await self.select_trade_mode_with_position()
            
            # 根据模式设置账号标签（重构：支持3种模式）
            if self.trade_mode == 1:
                # 模式1：A买 B卖（A多B空）
                account_a_label = f"{self.account_a_name} (Buy/Long)"
                account_b_label = f"{self.account_b_name} (Sell/Short)"
                mode_display = f"模式1 ({self.account_a_name}买{self.account_b_name}卖)"
            elif self.trade_mode == 2:
                # 模式2：A卖 B买（A空B多）
                account_a_label = f"{self.account_a_name} (Sell/Short)"
                account_b_label = f"{self.account_b_name} (Buy/Long)"
                mode_display = f"模式2 ({self.account_a_name}卖{self.account_b_name}买)"
            elif self.trade_mode == 3:
                # 平仓模式：自动检测
                account_a_label = f"{self.account_a_name} (Auto Close)"
                account_b_label = f"{self.account_b_name} (Auto Close)"
                mode_display = "平仓模式 (自动检测)"
            else:
                # 未知模式
                account_a_label = self.account_a_name
                account_b_label = self.account_b_name
                mode_display = f"未知模式 ({self.trade_mode})"
            
            console.print(f"\n已选择: {mode_display}", style="bold green")
            console.print("开始监控价差...\n", style="yellow")
            
            # 开始监控循环（内部会创建新的 Live 仪表盘）
            await self.monitor_spread()
            
        except KeyboardInterrupt:
            self.logger.info("用户中断程序 (Ctrl+C)")
            console.print(f"\n[Exit] 用户中断程序", style="yellow")
        except Exception as e:
            self.logger.error(f"程序异常: {str(e)}", exc_info=True)
            console.print(f"[Error] 程序异常: {e}", style="red")
            import traceback
            traceback.print_exc()
        finally:
            # 清理资源
            self.logger.info("正在清理资源...")
            if self.browser:
                await self.browser.close()
            if playwright:
                await playwright.stop()
            self.logger.info("="*60)
            self.logger.info("系统已退出")
            self.logger.info("="*60)
            console.print(f"[Exit] 程序已退出", style="dim")


async def main():
    """入口函数"""
    trader = ParadexDualTaker()
    await trader.run()


if __name__ == "__main__":
    asyncio.run(main())

