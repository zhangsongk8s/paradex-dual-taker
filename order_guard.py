#!/usr/bin/env python3
"""
OrderGuard - 滑动窗口交易计数器
用于跟踪24小时内的交易次数，防止超过 Paradex Retail 1000笔/24小时限制
"""

import json
from pathlib import Path
from datetime import datetime, timedelta
from typing import List


class OrderGuard:
    """滑动窗口交易计数器"""
    
    def __init__(self, history_file: Path, max_orders: int = 1000, safety_threshold: int = 950, session_limit: int = 300):
        """
        初始化 OrderGuard
        
        Args:
            history_file: 交易历史文件路径（JSON）
            max_orders: 24小时内最大订单数（默认1000）
            safety_threshold: 安全阈值，超过此值将阻止下单（默认950，留50笔缓冲）
            session_limit: 单次会话交易限制（默认300笔，达到后自动退出）
        """
        self.history_file = history_file
        self.max_orders = max_orders
        self.safety_threshold = safety_threshold
        
        # 会话交易限制（单次运行）
        self.session_limit = session_limit
        self.session_count = 0
        
        # 确保文件目录存在
        self.history_file.parent.mkdir(parents=True, exist_ok=True)
        
        # 初始化文件（如果不存在）
        if not self.history_file.exists():
            self._write_history([])
    
    def _read_history(self) -> List[str]:
        """读取交易历史（时间戳列表）"""
        try:
            if not self.history_file.exists():
                return []
            with open(self.history_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data.get('timestamps', [])
        except (json.JSONDecodeError, KeyError, IOError):
            # 文件损坏或不存在，返回空列表
            return []
    
    def _write_history(self, timestamps: List[str]):
        """写入交易历史"""
        try:
            data = {
                'timestamps': timestamps,
                'last_updated': datetime.now().isoformat()
            }
            with open(self.history_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except IOError:
            # 写入失败，但不影响主流程
            pass
    
    def _clean_old_orders(self, timestamps: List[str]) -> List[str]:
        """
        清理超过24小时的时间戳
        
        Args:
            timestamps: 时间戳列表（ISO格式字符串）
            
        Returns:
            清理后的时间戳列表
        """
        now = datetime.now()
        cutoff_time = now - timedelta(hours=24)
        
        active_timestamps = []
        for ts_str in timestamps:
            try:
                ts = datetime.fromisoformat(ts_str)
                if ts > cutoff_time:
                    active_timestamps.append(ts_str)
            except (ValueError, TypeError):
                # 无效的时间戳，跳过
                continue
        
        return active_timestamps
    
    def get_active_count(self) -> int:
        """
        获取24小时内的活跃订单数（自动清理过期订单）
        
        Returns:
            活跃订单数量
        """
        timestamps = self._read_history()
        active_timestamps = self._clean_old_orders(timestamps)
        
        # 如果清理后有变化，更新文件
        if len(active_timestamps) != len(timestamps):
            self._write_history(active_timestamps)
        
        return len(active_timestamps)
    
    def is_safe(self) -> bool:
        """
        检查是否可以安全下单
        
        Returns:
            True 如果活跃订单数 < safety_threshold，否则 False
        """
        active_count = self.get_active_count()
        return active_count < self.safety_threshold
    
    def add_order(self):
        """
        添加一笔订单记录（交易成功后调用）
        将当前时间戳写入历史文件
        """
        timestamps = self._read_history()
        now_iso = datetime.now().isoformat()
        timestamps.append(now_iso)
        
        # 清理过期订单后再写入
        active_timestamps = self._clean_old_orders(timestamps)
        self._write_history(active_timestamps)
        
        # 增加会话计数
        self.session_count += 1
    
    def get_status_info(self) -> tuple:
        """
        获取状态信息（用于 Dashboard 显示）
        
        Returns:
            (active_count, max_orders, is_safe, status_text)
        """
        active_count = self.get_active_count()
        is_safe = active_count < self.safety_threshold
        
        if active_count >= self.max_orders:
            status_text = "额度耗尽"
        elif active_count >= self.safety_threshold:
            status_text = "接近上限"
        else:
            status_text = "安全"
        
        return active_count, self.max_orders, is_safe, status_text
    
    def needs_manual_confirmation(self) -> bool:
        """
        检查是否需要人工确认（接近安全阈值时）
        
        Returns:
            True 如果活跃订单数 >= safety_threshold，需要人工确认
        """
        active_count = self.get_active_count()
        return active_count >= self.safety_threshold
    
    def should_exit(self) -> bool:
        """
        检查是否应该退出程序（达到会话交易限制）
        
        Returns:
            True 如果会话交易数 >= session_limit，否则 False
        """
        return self.session_count >= self.session_limit
    
    def get_session_info(self) -> tuple:
        """
        获取会话信息
        
        Returns:
            (session_count, session_limit) 当前会话交易数和限制
        """
        return self.session_count, self.session_limit

