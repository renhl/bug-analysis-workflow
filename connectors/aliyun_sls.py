"""
阿里云 SLS 日志连接器
"""

import httpx
from typing import List, Optional, Dict
from datetime import datetime
from ..models import LogEvent


class AliyunSLSConnector:
    """
    阿里云 SLS 日志查询
    
    用于获取 traceId 调用链、错误事件等
    """
    
    def __init__(self, access_key: str, secret: str, 
                 endpoint: str, project: str):
        self.access_key = access_key
        self.secret = secret
        self.endpoint = endpoint
        self.project = project
    
    def query_logs(self, 
                   query: str,
                   logstore: str = None,
                   time_range: tuple = None,
                   limit: int = 100) -> List[LogEvent]:
        """
        查询日志
        
        Args:
            query: SLS 查询语句
            logstore: 日志库名
            time_range: (start_time, end_time) 时间范围
            limit: 返回条数限制
        
        Returns:
            结构化的日志事件列表
        """
        # TODO: 实现阿里云 SLS API 调用
        # 需要使用阿里云 SDK 或直接调用 API
        
        return []
    
    def extract_trace_chain(self, trace_id: str, 
                            logstore: str = None) -> List[LogEvent]:
        """
        按 traceId 提取完整调用链
        """
        return self.query_logs(
            query=f"trace_id: {trace_id}",
            logstore=logstore
        )
    
    def find_error_events(self,
                          time_range: tuple,
                          keywords: List[str] = None,
                          logstore: str = None,
                          level: str = "ERROR") -> List[LogEvent]:
        """
        查找错误事件
        """
        query = f"level: {level}"
        if keywords:
            query += f" AND ({' OR '.join(keywords)})"
        
        return self.query_logs(query, logstore, time_range)
    
    def get_log_context(self, log_event: LogEvent, 
                        before: int = 10, after: int = 10) -> List[LogEvent]:
        """
        获取日志上下文（前后N条）
        """
        # TODO: 实现日志上下文查询
        return []
    
    def parse_log_level(self, raw_log: Dict) -> str:
        """
        解析日志级别
        """
        # 不同的日志格式可能有不同的字段名
        level_fields = ["level", "Level", "LEVEL", "log_level", "status"]
        
        for field in level_fields:
            if field in raw_log:
                return raw_log[field]
        
        return "INFO"  # 默认
    
    def parse_log_location(self, raw_log: Dict) -> Optional[str]:
        """
        解析日志中的代码位置
        
        格式可能是: OrderService.java:245 或 at xxx(OrderService.java:245)
        """
        location_fields = ["location", "file", "caller", "source"]
        
        for field in location_fields:
            if field in raw_log:
                return raw_log[field]
        
        # 从 message 中提取
        message = raw_log.get("message", "")
        match = self._extract_location_from_message(message)
        if match:
            return match
        
        return None
    
    def _extract_location_from_message(self, message: str) -> Optional[str]:
        """
        从日志消息中提取位置
        """
        # Java 格式
        import re
        match = re.search(r'at\s+[\w.]+\(([\w.]+):(\d+)\)', message)
        if match:
            return f"{match.group(1)}:{match.group(2)}"
        
        # Go 格式
        match = re.search(r'([\w/]+\.go):(\d+)', message)
        if match:
            return f"{match.group(1)}:{match.group(2)}"
        
        return None
    
    def parse_stack_trace(self, raw_log: Dict) -> Optional[List[str]]:
        """
        解析堆栈信息
        """
        stack_fields = ["stack_trace", "stacktrace", "exception", "error_stack"]
        
        for field in stack_fields:
            if field in raw_log and raw_log[field]:
                # 堆栈可能是字符串，需要按行分割
                stack = raw_log[field]
                if isinstance(stack, str):
                    return stack.split('\n')
                return stack
        
        return None