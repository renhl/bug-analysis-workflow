"""
堆栈分析器 (Type A) - 从堆栈信息定位代码位置
"""

import re
from typing import List, Optional

from core.models import (
    AnalysisRequest, AnalysisResult, ProblemType,
    CodeLocation, CodeModel, LogEvent
)


class StackTraceAnalyzer:
    """
    堆栈分析器
    
    处理 Type A 问题：
    - A1: 有堆栈，直接定位
    - A2: 有 ERROR 日志，需分析
    
    分析流程：
    1. 从日志提取堆栈信息
    2. 过滤框架层堆栈
    3. 定位业务代码行
    4. 分析根因
    """
    
    def analyze(self, 
                request: AnalysisRequest,
                code_model: CodeModel,
                logs: List[LogEvent],
                similar_cases: List) -> AnalysisResult:
        """
        主分析入口
        """
        
        # 从日志提取堆栈
        stack_frames = self._extract_stack_trace(logs)
        
        if not stack_frames:
            # 无堆栈，退化到关键词分析
            return self._fallback_analysis(request, code_model)
        
        # 过滤框架堆栈，只保留业务代码
        business_frames = [
            f for f in stack_frames
            if self._is_business_code(f, code_model)
        ]
        
        # 定位到源码
        code_locations = []
        for frame in business_frames[:5]:  # 只取前5层
            file, line = self._parse_frame(frame)
            if file and line:
                code_locations.append(CodeLocation(
                    file=file,
                    line=line,
                    verified=False
                ))
        
        # 分析根因
        root_cause = self._analyze_root_cause(business_frames, request)
        
        return AnalysisResult(
            problem_type=ProblemType.A1,
            root_cause=root_cause,
            code_locations=code_locations,
            fix_suggestion="请检查堆栈中的代码位置",
            confidence=0.85
        )
    
    def _extract_stack_trace(self, logs: List[LogEvent]) -> List[str]:
        """
        从日志提取堆栈
        
        支持多种格式的堆栈
        """
        
        for log in logs:
            if log.stack_trace:
                return log.stack_trace
            
            # 也从 message 中提取堆栈
            if 'at ' in log.message or 'Exception' in log.message:
                stack = self._extract_stack_from_message(log.message)
                if stack:
                    return stack
        
        return []
    
    def _extract_stack_from_message(self, message: str) -> List[str]:
        """从日志消息提取堆栈"""
        
        lines = message.split('\n')
        stack = []
        
        for line in lines:
            if 'at ' in line or '.java:' in line or '.go:' in line:
                stack.append(line.strip())
        
        return stack
    
    def _is_business_code(self, frame: str, code_model: CodeModel) -> bool:
        """
        判断是否业务代码
        
        过滤框架层堆栈帧
        """
        
        # 框架包名（Java）
        frameworks = [
            "java.lang", "java.util", "org.springframework",
            "org.apache", "javax.", "com.fasterxml",
            "net.", "io.", "reactor.", "lombok", "slf4j"
        ]
        
        # Go 框架
        go_frameworks = [
            "net/http", "encoding/", "strings", "fmt",
            "log", "errors", "sync", "runtime/", "reflect"
        ]
        
        # 检查是否框架代码
        for fw in frameworks + go_frameworks:
            if fw in frame:
                return False
        
        # 如果有代码模型，检查是否在模型中
        if code_model:
            for file in code_model.files:
                # 检查堆栈帧是否包含该文件
                if file.path in frame:
                    return True
        
        return True  # 默认认为是业务代码
    
    def _parse_frame(self, frame: str):
        """
        解析堆栈帧，提取文件和行号
        
        支持格式：
        - Java: at com.example.OrderService.processOrder(OrderService.java:245)
        - Go: order/service.go:45
        - TypeScript: at OrderService.processOrder (order.service.ts:45)
        """
        
        # Java 格式
        match = re.search(r'at\s+[\w.]+\(([\w.]+):(\d+)\)', frame)
        if match:
            return match.group(1), int(match.group(2))
        
        # Go 格式
        match = re.search(r'([\w/]+\.go):(\d+)', frame)
        if match:
            return match.group(1), int(match.group(2))
        
        # TypeScript 格式
        match = re.search(r'([\w/.]+\.ts):(\d+)', frame)
        if match:
            return match.group(1), int(match.group(2))
        
        return None, None
    
    def _analyze_root_cause(self, frames: List[str], request: AnalysisRequest) -> str:
        """
        分析根因
        
        从堆栈顶层的业务代码推断错误类型
        """
        
        if not frames:
            return request.error_desc
        
        top_frame = frames[0]
        
        # 提取异常类型（如果有）
        exception_type = None
        
        # 从完整堆栈中搜索异常类型
        for frame in frames:
            if 'Exception' in frame or 'Error' in frame:
                match = re.search(r'(\w+Exception|\w+Error)', frame)
                if match:
                    exception_type = match.group(1)
                    break
        
        # 构建根因描述
        if exception_type:
            return f"错误类型: {exception_type}\n发生位置: {top_frame}"
        
        return f"错误发生在: {top_frame}"
    
    def _fallback_analysis(self, 
                          request: AnalysisRequest,
                          code_model: CodeModel) -> AnalysisResult:
        """
        无堆栈时的退化分析
        
        尝试从代码关键词定位
        """
        
        # 提取关键词
        keywords = self._extract_keywords(request.error_desc)
        
        # 从代码模型搜索
        code_locations = []
        
        if code_model:
            for kw in keywords:
                matched = code_model.search_by_keyword(kw)
                for func in matched[:3]:
                    code_locations.append(CodeLocation(
                        file=func.file,
                        line=func.start_line,
                        function=func.name,
                        verified=True
                    ))
        
        if code_locations:
            return AnalysisResult(
                problem_type=ProblemType.A2,
                root_cause=f"无堆栈信息，通过关键词定位: {keywords}",
                code_locations=code_locations,
                fix_suggestion="请提供完整堆栈或更多上下文以提高定位精度",
                confidence=0.3
            )
        
        return AnalysisResult(
            problem_type=ProblemType.A2,
            root_cause=request.error_desc,
            code_locations=[],
            fix_suggestion="无法定位具体代码，请提供更多信息（如堆栈、traceId）",
            confidence=0.3
        )
    
    def _extract_keywords(self, desc: str) -> List[str]:
        """从描述提取关键词"""
        
        keywords = []
        
        # 业务关键词
        business_words = [
            'order', 'Order', '订单',
            'payment', 'Payment', '支付',
            'inventory', 'Inventory', '库存',
            'user', 'User', '用户',
            'product', 'Product', '商品',
            'create', 'Create', '创建',
            'update', 'Update', '更新',
            'delete', 'Delete', '删除',
            'process', 'Process', '处理',
        ]
        
        for word in business_words:
            if word in desc:
                keywords.append(word)
        
        return keywords