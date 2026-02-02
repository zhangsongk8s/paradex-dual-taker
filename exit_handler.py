#!/usr/bin/env python3
"""
ExitHandler - 优雅退出处理器
统一管理程序退出，汇报交易统计信息
"""

from datetime import datetime, timedelta
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from enum import Enum
import logging


class ExitReason(Enum):
    """退出原因枚举"""
    USER_INTERRUPT = "user_interrupt"           # 用户中断 (Ctrl+C)
    FEE_DETECTED = "fee_detected"               # 检测到手续费
    SESSION_LIMIT = "session_limit"             # 达到会话交易限制
    POSITION_CLEARED = "position_cleared"       # 平仓完成
    BALANCE_LOW = "balance_low"                 # 余额不足
    POSITION_IMBALANCE = "position_imbalance"   # 持仓差异过大
    MANUAL_EXIT = "manual_exit"                 # 手动模式达到限制
    ERROR = "error"                             # 程序异常
    UNKNOWN = "unknown"                         # 未知原因


class ExitHandler:
    """
    优雅退出处理器
    
    功能：
    1. 记录退出原因
    2. 汇总交易统计信息
    3. 生成退出报告
    """
    
    def __init__(self, logger: logging.Logger = None):
        self.logger = logger or logging.getLogger('ExitHandler')
        self.console = Console()
        
        # 退出状态
        self.exit_reason: ExitReason = ExitReason.UNKNOWN
        self.exit_message: str = ""
        self.exit_time: datetime = None
        
        # 会话统计
        self.start_time: datetime = None
        self.trade_count: int = 0
        self.session_trades: int = 0
        self.successful_trades: int = 0
        self.failed_trades: int = 0
        
        # 账号信息
        self.account_a_name: str = "Account A"
        self.account_b_name: str = "Account B"
        self.group_identifier: str = ""
        
        # 持仓和余额
        self.final_position_a: float = None
        self.final_position_b: float = None
        self.final_balance_a: float = None
        self.final_balance_b: float = None
        self.direction_a: str = "none"
        self.direction_b: str = "none"
        
        # 其他统计
        self.fee_detected_value: str = None  # 检测到的手续费值
        
    def start_session(self, account_a: str, account_b: str, group_id: str):
        """开始会话，记录开始时间"""
        self.start_time = datetime.now()
        self.account_a_name = account_a
        self.account_b_name = account_b
        self.group_identifier = group_id
        self.logger.info(f"🚀 [ExitHandler] 会话开始: {self.group_identifier}")
        
    def update_stats(self, 
                     trade_count: int = None,
                     session_trades: int = None,
                     successful_trades: int = None,
                     failed_trades: int = None,
                     position_a: float = None,
                     position_b: float = None,
                     balance_a: float = None,
                     balance_b: float = None,
                     direction_a: str = None,
                     direction_b: str = None):
        """更新统计数据"""
        if trade_count is not None:
            self.trade_count = trade_count
        if session_trades is not None:
            self.session_trades = session_trades
        if successful_trades is not None:
            self.successful_trades = successful_trades
        if failed_trades is not None:
            self.failed_trades = failed_trades
        if position_a is not None:
            self.final_position_a = position_a
        if position_b is not None:
            self.final_position_b = position_b
        if balance_a is not None:
            self.final_balance_a = balance_a
        if balance_b is not None:
            self.final_balance_b = balance_b
        if direction_a is not None:
            self.direction_a = direction_a
        if direction_b is not None:
            self.direction_b = direction_b
    
    def set_exit(self, reason: ExitReason, message: str = "", fee_value: str = None):
        """设置退出原因"""
        self.exit_reason = reason
        self.exit_message = message
        self.exit_time = datetime.now()
        if fee_value:
            self.fee_detected_value = fee_value
        
        # 记录日志
        reason_text = self._get_reason_text()
        self.logger.info(f"🛑 [ExitHandler] 退出原因: {reason_text}")
        if message:
            self.logger.info(f"   详情: {message}")
    
    def _get_reason_text(self) -> str:
        """获取退出原因的中文描述"""
        reason_map = {
            ExitReason.USER_INTERRUPT: "👤 用户中断 (Ctrl+C)",
            ExitReason.FEE_DETECTED: f"💰 检测到手续费: {self.fee_detected_value or 'N/A'}",
            ExitReason.SESSION_LIMIT: "📊 达到会话交易限制",
            ExitReason.POSITION_CLEARED: "✅ 平仓完成",
            ExitReason.BALANCE_LOW: "💵 余额不足",
            ExitReason.POSITION_IMBALANCE: "⚠️ 持仓差异过大",
            ExitReason.MANUAL_EXIT: "🔧 手动模式达到限制",
            ExitReason.ERROR: "❌ 程序异常",
            ExitReason.UNKNOWN: "❓ 未知原因",
        }
        return reason_map.get(self.exit_reason, "❓ 未知原因")
    
    def _get_duration_str(self) -> str:
        """计算运行时长"""
        if not self.start_time:
            return "N/A"
        
        end_time = self.exit_time or datetime.now()
        duration = end_time - self.start_time
        
        hours, remainder = divmod(int(duration.total_seconds()), 3600)
        minutes, seconds = divmod(remainder, 60)
        
        if hours > 0:
            return f"{hours}h {minutes}m {seconds}s"
        elif minutes > 0:
            return f"{minutes}m {seconds}s"
        else:
            return f"{seconds}s"
    
    def generate_report(self) -> str:
        """生成退出报告（文本格式，用于日志）"""
        lines = [
            "=" * 60,
            "📊 交易会话结束报告",
            "=" * 60,
            f"账号组: {self.group_identifier}",
            f"开始时间: {self.start_time.strftime('%Y-%m-%d %H:%M:%S') if self.start_time else 'N/A'}",
            f"结束时间: {self.exit_time.strftime('%Y-%m-%d %H:%M:%S') if self.exit_time else 'N/A'}",
            f"运行时长: {self._get_duration_str()}",
            "-" * 60,
            f"退出原因: {self._get_reason_text()}",
        ]
        
        if self.exit_message:
            lines.append(f"详细信息: {self.exit_message}")
        
        lines.extend([
            "-" * 60,
            "📈 交易统计:",
            f"  本次运行交易数: {self.trade_count} 笔",
            f"  会话交易数: {self.session_trades} 笔",
        ])
        
        # 持仓信息
        lines.append("-" * 60)
        lines.append("📦 最终持仓:")
        
        dir_a = "📈多" if self.direction_a == "long" else "📉空" if self.direction_a == "short" else ""
        dir_b = "📈多" if self.direction_b == "long" else "📉空" if self.direction_b == "short" else ""
        
        pos_a_str = f"{self.final_position_a:.5f} BTC {dir_a}" if self.final_position_a is not None else "N/A"
        pos_b_str = f"{self.final_position_b:.5f} BTC {dir_b}" if self.final_position_b is not None else "N/A"
        
        lines.append(f"  {self.account_a_name}: {pos_a_str}")
        lines.append(f"  {self.account_b_name}: {pos_b_str}")
        
        # 余额信息
        if self.final_balance_a is not None or self.final_balance_b is not None:
            lines.append("-" * 60)
            lines.append("💰 最终余额:")
            bal_a_str = f"${self.final_balance_a:.2f}" if self.final_balance_a is not None else "N/A"
            bal_b_str = f"${self.final_balance_b:.2f}" if self.final_balance_b is not None else "N/A"
            lines.append(f"  {self.account_a_name}: {bal_a_str}")
            lines.append(f"  {self.account_b_name}: {bal_b_str}")
        
        lines.append("=" * 60)
        
        return "\n".join(lines)
    
    def print_report(self):
        """打印退出报告到控制台（美化版）"""
        # 创建统计表格
        stats_table = Table(show_header=False, box=None, padding=(0, 2))
        stats_table.add_column("Key", style="cyan")
        stats_table.add_column("Value", style="white")
        
        stats_table.add_row("账号组", self.group_identifier or "N/A")
        stats_table.add_row("运行时长", self._get_duration_str())
        stats_table.add_row("本次交易", f"{self.trade_count} 笔")
        stats_table.add_row("会话交易", f"{self.session_trades} 笔")
        
        # 持仓信息
        dir_a = "📈多" if self.direction_a == "long" else "📉空" if self.direction_a == "short" else ""
        dir_b = "📈多" if self.direction_b == "long" else "📉空" if self.direction_b == "short" else ""
        
        if self.final_position_a is not None:
            stats_table.add_row(f"{self.account_a_name} 持仓", f"{self.final_position_a:.5f} BTC {dir_a}")
        if self.final_position_b is not None:
            stats_table.add_row(f"{self.account_b_name} 持仓", f"{self.final_position_b:.5f} BTC {dir_b}")
        
        if self.final_balance_a is not None:
            stats_table.add_row(f"{self.account_a_name} 余额", f"${self.final_balance_a:.2f}")
        if self.final_balance_b is not None:
            stats_table.add_row(f"{self.account_b_name} 余额", f"${self.final_balance_b:.2f}")
        
        # 退出原因样式
        reason_text = self._get_reason_text()
        if self.exit_reason in [ExitReason.FEE_DETECTED, ExitReason.ERROR, ExitReason.BALANCE_LOW, ExitReason.POSITION_IMBALANCE]:
            reason_style = "bold red"
        elif self.exit_reason in [ExitReason.POSITION_CLEARED, ExitReason.SESSION_LIMIT]:
            reason_style = "bold green"
        else:
            reason_style = "bold yellow"
        
        # 创建面板
        self.console.print()
        self.console.print(Panel(
            stats_table,
            title="[bold cyan]📊 交易会话结束报告[/bold cyan]",
            subtitle=f"[{reason_style}]{reason_text}[/{reason_style}]",
            border_style="cyan"
        ))
        
        if self.exit_message:
            self.console.print(f"  💬 {self.exit_message}", style="dim")
        
        self.console.print()
    
    def log_report(self):
        """将报告写入日志"""
        report = self.generate_report()
        for line in report.split("\n"):
            self.logger.info(line)
