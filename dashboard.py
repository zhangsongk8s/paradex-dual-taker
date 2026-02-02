#!/usr/bin/env python3
"""
Dashboard - 基于 rich 库的固定 TUI 仪表盘
用于显示量化交易机器人的实时状态
"""

from rich.panel import Panel
from rich.layout import Layout
from rich.table import Table
from rich.text import Text
from rich.console import Group
from rich import box
from rich.rule import Rule
from typing import Optional


class Dashboard:
    """固定 TUI 仪表盘类"""
    
    def __init__(
        self, 
        spread_threshold: float, 
        trade_mode: int = 1, 
        min_available_balance: float = 200,
        account_a_name: str = "Account A",
        account_b_name: str = "Account B",
        enable_auto_rotation: bool = False
    ):
        """
        初始化仪表盘
        
        Args:
            spread_threshold: 价差阈值（百分比）
            trade_mode: 交易模式（1=开仓, 2=平仓）
            min_available_balance: 最小可用余额阈值（USD）
            account_a_name: 账号A名称
            account_b_name: 账号B名称
            enable_auto_rotation: 是否启用自动轮转模式
        """
        self.spread_threshold = spread_threshold
        self.trade_mode = trade_mode
        self.min_available_balance = min_available_balance
        self.account_a_name = account_a_name
        self.account_b_name = account_b_name
        self.enable_auto_rotation = enable_auto_rotation
        
        # 状态数据
        self.status = "🟢 监控中"
        self.bid_price: Optional[float] = None
        self.ask_price: Optional[float] = None
        self.spread_pct: Optional[float] = None
        self.position_a: Optional[float] = None
        self.position_b: Optional[float] = None
        self.direction_a: Optional[str] = None  # "long" | "short" | "none"
        self.direction_b: Optional[str] = None  # "long" | "short" | "none"
        self.balance_a: Optional[float] = None  # Account A 可用余额
        self.balance_b: Optional[float] = None  # Account B 可用余额
        self.trade_count: int = 0
        self.max_trades: int = 1000
        self.force_exit_trades: int = 50
        self.order_guard_count: Optional[int] = None  # 24小时额度当前数量
        self.order_guard_max: Optional[int] = None  # 24小时额度最大值
        self.order_guard_status: Optional[str] = None  # 24小时额度状态
        self.rpi_hit_count: int = 0  # 🎯 RPI 零点差捕获次数
        self.last_log: str = "系统初始化中..."
        
        # 创建布局
        self.layout = self._create_layout()
    
    def _create_layout(self) -> Layout:
        """创建界面布局"""
        layout = Layout()
        
        # 创建主面板内容
        content = self._create_content()
        
        # 🔫 RPI Sniper 专属皮肤：Bold Red 警示配色
        panel = Panel(
            content,
            title="[bold red]🔫 SNIPER BOT | 零点差狙击模式 (RPI Hunter)[/bold red]",
            border_style="red",
            padding=(1, 2)
        )
        
        layout.update(panel)
        return layout
    
    def _create_content(self):
        """创建仪表盘内容（竖向对比矩阵布局）"""
        # ===== 主容器 =====
        main_table = Table(
            show_header=False,
            box=box.ROUNDED,
            padding=(0, 0),
            expand=True,
            border_style="cyan",
            pad_edge=False
        )
        main_table.add_column("Content", ratio=1)
        
        # ===== Row 1: 顶部状态栏 =====
        # 组合显示：全局模式 + 当前执行阶段（两种模式都显示当前阶段）
        
        # 1. 定义主标题 (全局模式)
        if self.enable_auto_rotation:
            prefix = "🔄 自动轮转 (Auto)"
            mode_style = "bold cyan"
        else:
            prefix = "🖐️ 手动 (Manual)"
            mode_style = "bold yellow"
        
        # 2. 定义当前阶段（根据 trade_mode 动态变化，自动和手动都显示）
        if self.trade_mode == 1:
            status = "🚀 阶段: A多/B空 (Long A)"
        elif self.trade_mode == 2:
            status = "🚀 阶段: A空/B多 (Long B)"
        elif self.trade_mode == 3:
            status = "🧹 阶段: 平仓中 (Closing All)"
        else:
            status = "⚠️ 阶段: 未知状态"
        
        # 3. 组合显示：全局模式 | 当前阶段
        mode_text = f"{prefix} | {status}"
        
        status_line = Text(
            f"  {mode_text} | 目标价差阈值: {self.spread_threshold:.4f}%  ",
            style=mode_style,
            justify="center"
        )
        main_table.add_row(status_line)
        
        # 分割线
        main_table.add_row(Rule(style="dim"))
        
        # ===== Row 2: 核心对比表格（3列：指标 | 账号A | 账号B）=====
        grid = Table(
            box=None,
            expand=True,
            padding=(0, 1),
            show_header=False
        )
        
        # 列定义
        grid.add_column(justify="right", style="bold cyan", ratio=2)  # 指标名
        grid.add_column(justify="center", ratio=3)  # 账号A数据
        grid.add_column(justify="center", ratio=3)  # 账号B数据
        
        # 表头行：账号名称（左上角显示实时点差）
        if self.spread_pct is not None:
            spread_style = "bold green" if self.spread_pct < self.spread_threshold else "bold red"
            spread_render = Text(f"📊 点差: {self.spread_pct:.4f}%", style=spread_style)
        else:
            spread_render = Text("📊 点差: N/A", style="dim")
        
        grid.add_row(
            spread_render,
            Text(f"🦈 {self.account_a_name}", style="bold yellow"),
            Text(f"🦈 {self.account_b_name}", style="bold yellow")
        )
        
        # 空行分隔
        grid.add_row("", "", "")
        
        # === 数据行1: 权益 (Equity) ===
        balance_a_str = f"${self.balance_a:,.2f}" if self.balance_a is not None else "N/A"
        balance_b_str = f"${self.balance_b:,.2f}" if self.balance_b is not None else "N/A"
        
        # 余额警告
        if self.balance_a is not None and self.balance_a < self.min_available_balance:
            balance_a_display = Text(f"💵 {balance_a_str} ⚠️", style="red bold")
        else:
            balance_a_display = Text(f"💵 {balance_a_str}", style="green")
        
        if self.balance_b is not None and self.balance_b < self.min_available_balance:
            balance_b_display = Text(f"💵 {balance_b_str} ⚠️", style="red bold")
        else:
            balance_b_display = Text(f"💵 {balance_b_str}", style="green")
        
        grid.add_row("💵 权益", balance_a_display, balance_b_display)
        
        # === 数据行2: 持仓 (Position) ===
        def format_position(pos, direction):
            """格式化持仓显示"""
            if pos is None or pos == 0 or direction == "none":
                return Text("--", style="dim")
            
            if direction == "long":
                return Text(f"🟢 {pos:.5f} BTC", style="green bold")
            elif direction == "short":
                return Text(f"🔴 {pos:.5f} BTC", style="red bold")
            else:
                return Text(f"{pos:.5f} BTC", style="white")
        
        pos_a_display = format_position(self.position_a, self.direction_a)
        pos_b_display = format_position(self.position_b, self.direction_b)
        
        grid.add_row("📈 持仓", pos_a_display, pos_b_display)
        
        # === 数据行3: 盘口 (Market) - 根据模式显示对应价格 ===
        # Mode 1: A买B卖 -> A看买价，B看卖价
        # Mode 2: A卖B买 -> A看卖价，B看买价
        # Mode 3: 平仓模式 -> 根据持仓方向显示
        
        if self.trade_mode == 1:  # A买B卖
            market_a_str = f"Buy: {self.bid_price:,.2f}" if self.bid_price is not None else "N/A"
            market_b_str = f"Sell: {self.ask_price:,.2f}" if self.ask_price is not None else "N/A"
        elif self.trade_mode == 2:  # A卖B买
            market_a_str = f"Sell: {self.ask_price:,.2f}" if self.ask_price is not None else "N/A"
            market_b_str = f"Buy: {self.bid_price:,.2f}" if self.bid_price is not None else "N/A"
        else:  # 平仓模式
            market_a_str = f"Buy: {self.bid_price:,.2f}" if self.bid_price is not None else "N/A"
            market_b_str = f"Sell: {self.ask_price:,.2f}" if self.ask_price is not None else "N/A"
        
        grid.add_row(
            "💰 盘口",
            Text(market_a_str, style="cyan"),
            Text(market_b_str, style="magenta")
        )
        
        main_table.add_row(grid)
        
        # 分割线
        main_table.add_row(Rule(style="dim"))
        
        # ===== Row 3: 底部统计栏（3列：交易数 | RPI Hits | Total）=====
        stats_table = Table.grid(padding=(0, 2), expand=True)
        stats_table.add_column(justify="center", ratio=1)
        stats_table.add_column(justify="center", ratio=1)
        stats_table.add_column(justify="center", ratio=1)
        
        # 本次运行交易数
        trade_info = Text(f"📦 本次运行: {self.trade_count}", style="green bold")
        
        # 🎯 RPI Hits 计数器（Sniper 专属）
        rpi_hits_info = Text(f"🎯 RPI Hits: {self.rpi_hit_count}", style="bold magenta")
        
        # Total 额度信息
        if self.order_guard_count is not None and self.order_guard_max is not None:
            status_color = "green" if self.order_guard_status == "安全" else "yellow" if self.order_guard_status == "接近上限" else "red"
            quota_info = Text.from_markup(
                f"🕒 [{status_color}]Total: {self.order_guard_count}[/{status_color}]"
            )
        else:
            quota_info = Text("🕒 Total: --", style="dim")
        
        stats_table.add_row(trade_info, rpi_hits_info, quota_info)
        main_table.add_row(stats_table)
        
        # 分割线
        main_table.add_row(Rule(style="dim"))
        
        # ===== Row 4: 底部日志 =====
        log_line = Text.from_markup(f"  [dim]📝 最后日志:[/dim] {self.last_log}  ")
        main_table.add_row(log_line)
        
        return main_table
    
    def update(
        self,
        bid: Optional[float] = None,
        ask: Optional[float] = None,
        spread: Optional[float] = None,
        pos_a: Optional[float] = None,
        pos_b: Optional[float] = None,
        direction_a: Optional[str] = None,
        direction_b: Optional[str] = None,
        balance_a: Optional[float] = None,
        balance_b: Optional[float] = None,
        trade_count: Optional[int] = None,
        rpi_hit_count: Optional[int] = None,
        auto_mode: Optional[bool] = None,
        cycle_trade_count: Optional[int] = None,
        auto_mode_trades_per_cycle: Optional[int] = None,
        order_guard_count: Optional[int] = None,
        order_guard_max: Optional[int] = None,
        order_guard_status: Optional[str] = None,
        last_log: Optional[str] = None,
        status: Optional[str] = None,
        account_a_name: Optional[str] = None,
        account_b_name: Optional[str] = None,
        enable_auto_rotation: Optional[bool] = None,
        trade_mode: Optional[int] = None
    ):
        """
        更新仪表盘数据
        
        Args:
            bid: 买一价
            ask: 卖一价
            spread: 当前价差（百分比）
            pos_a: Account A 持仓
            pos_b: Account B 持仓
            direction_a: Account A 持仓方向 ("long" | "short" | "none")
            direction_b: Account B 持仓方向 ("long" | "short" | "none")
            trade_count: 交易计数
            order_guard_count: 24小时额度当前数量
            order_guard_max: 24小时额度最大值
            order_guard_status: 24小时额度状态
            last_log: 最后一条日志
            status: 状态文本
            account_a_name: 账号A名称
            account_b_name: 账号B名称
            enable_auto_rotation: 是否启用自动轮转模式
            trade_mode: 当前交易模式（1/2/3）
        """
        if bid is not None:
            self.bid_price = bid
        if ask is not None:
            self.ask_price = ask
        if spread is not None:
            self.spread_pct = spread
        if pos_a is not None:
            self.position_a = pos_a
        if pos_b is not None:
            self.position_b = pos_b
        if direction_a is not None:
            self.direction_a = direction_a
        if direction_b is not None:
            self.direction_b = direction_b
        if balance_a is not None:
            self.balance_a = balance_a
        if balance_b is not None:
            self.balance_b = balance_b
        if trade_count is not None:
            self.trade_count = trade_count
        if rpi_hit_count is not None:
            self.rpi_hit_count = rpi_hit_count
        if order_guard_count is not None:
            self.order_guard_count = order_guard_count
        if order_guard_max is not None:
            self.order_guard_max = order_guard_max
        if order_guard_status is not None:
            self.order_guard_status = order_guard_status
        if last_log is not None:
            self.last_log = last_log
        if status is not None:
            self.status = status
        if account_a_name is not None:
            self.account_a_name = account_a_name
        if account_b_name is not None:
            self.account_b_name = account_b_name
        if enable_auto_rotation is not None:
            self.enable_auto_rotation = enable_auto_rotation
        if trade_mode is not None:
            self.trade_mode = trade_mode
        
        # 更新布局内容
        self.layout.update(self._create_content())
    
    def render(self) -> Layout:
        """返回当前布局（用于 Live 更新）"""
        return self.layout
    
    def set_trade_mode(self, mode: int):
        """设置交易模式"""
        self.trade_mode = mode
        self.layout.update(self._create_content())
    
    def set_force_exit_trades(self, count: int):
        """设置强制退出交易次数"""
        self.force_exit_trades = count
        self.layout.update(self._create_content())
    
    def set_auto_rotation(self, enabled: bool):
        """设置自动轮转模式"""
        self.enable_auto_rotation = enabled
        self.layout.update(self._create_content())
