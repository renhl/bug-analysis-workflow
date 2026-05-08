"""
Bug Analysis Workflow - 主工作流
"""

import re
from typing import List, Dict, Optional
from datetime import datetime

from .models import (
    AnalysisRequest, AnalysisResult, RouteResult,
    CodeModel, CodeLocation, ProblemType, LogEvent
)
from .registry import ServiceRegistry, load_registry_from_yaml
from .routers import CompositeRouter


class BugAnalysisWorkflow:
    """
    智能问题分析工作流
    
    核心流程:
    1. 路由确定仓库
    2. 预处理（代码解析、日志获取）
    3. 知识增强（WeKnora）
    4. 分析引擎（根据问题类型）
    5. 输出结果
    6. 知识闭环
    """
    
    def __init__(self, config):
        self.config = config
        
        # 服务注册表
        self.registry = load_registry_from_yaml(config.registry_path)
        
        # 路由器
        self.router = CompositeRouter(
            registry=self.registry,
            sls_connector=None,  # TODO: 初始化连接器
            weknora_connector=None
        )
        
        # 语言适配器
        self.adapters = {}  # TODO: 初始化适配器
        
        # 分析器
        self.analyzers = {}  # TODO: 初始化分析器
    
    def analyze(self, request: AnalysisRequest) -> AnalysisResult:
        """
        主分析入口
        """
        
        # ========== Step 1: 路由确定仓库 ==========
        route_result = self.router.route(
            request, 
            kb_ids=[self.config.weknora_kb_ids.get("system_docs")]
        )
        
        # 如果需要用户确认
        if route_result.needs_user_input:
            return AnalysisResult(
                problem_type=ProblemType.C1,
                root_cause="需要更多信息",
                code_locations=[],
                fix_suggestion=route_result.question,
                confidence=0.0
            )
        
        # 确定分析仓库
        repo_path = request.repo_path or route_result.primary_repo
        if not repo_path:
            return self._no_repo_result(request)
        
        # ========== Step 2: 预处理 ==========
        
        # 代码解析
        code_model = self._parse_code(repo_path)
        
        # 获取日志
        logs = self._get_logs(request)
        
        # 问题分类
        problem_type = self._classify_problem(request, logs)
        
        # ========== Step 3: 知识增强 ==========
        similar_cases = self._search_similar_cases(request)
        
        # 如果找到完全匹配的历史案例
        if similar_cases and self._is_exact_match(similar_cases[0], request.error_desc):
            return self._build_result_from_case(similar_cases[0])
        
        # ========== Step 4: 分析引擎 ==========
        analyzer = self._get_analyzer(problem_type)
        
        result = analyzer.analyze(
            request=request,
            code_model=code_model,
            logs=logs,
            similar_cases=similar_cases
        )
        
        # ========== Step 5: 结果验证 ==========
        if result.code_locations:
            result.code_locations = self._verify_locations(
                result.code_locations, 
                code_model
            )
        
        # ========== Step 6: 知识闭环 ==========
        if self.config.enable_auto_save_case and result.confidence > 0.7:
            self._save_case(result, request)
        
        return result
    
    def _parse_code(self, repo_path: str) -> Optional[CodeModel]:
        """
        解析代码
        """
        # TODO: 实现代码解析
        # 1. 检测语言
        # 2. 选择适配器
        # 3. 解析 AST
        return None
    
    def _get_logs(self, request: AnalysisRequest) -> List[LogEvent]:
        """
        获取日志
        """
        # TODO: 实现日志获取
        # 1. 如果有 traceId，查询调用链
        # 2. 如果有时间范围，查询错误事件
        return []
    
    def _classify_problem(self, request: AnalysisRequest, logs: List[LogEvent]) -> ProblemType:
        """
        问题分类
        """
        
        # Type A1: 有堆栈
        if logs and any(l.stack_trace for l in logs):
            return ProblemType.A1
        
        # Type A2: 有 ERROR 日志
        if logs and any(l.level == "ERROR" for l in logs):
            return ProblemType.A2
        
        # Type B: 业务/数据异常
        if "预期" in request.error_desc or "应该" in request.error_desc or "不符合" in request.error_desc:
            return ProblemType.C1
        
        # 默认 Type B
        return ProblemType.B2
    
    def _search_similar_cases(self, request: AnalysisRequest) -> List[Dict]:
        """
        搜索相似历史案例
        """
        # TODO: WeKnora 知识库搜索
        return []
    
    def _is_exact_match(self, case: Dict, desc: str) -> bool:
        """
        判断是否完全匹配
        """
        # TODO: 实现匹配判断
        return False
    
    def _build_result_from_case(self, case: Dict) -> AnalysisResult:
        """
        从历史案例构建结果
        """
        return AnalysisResult(
            problem_type=ProblemType.A1,
            root_cause=case.get("root_cause", ""),
            code_locations=[
                CodeLocation(
                    file=case.get("file", ""),
                    line=case.get("line", 0)
                )
            ],
            fix_suggestion=case.get("fix_suggestion", ""),
            confidence=0.95,
            matched_cases=[case]
        )
    
    def _get_analyzer(self, problem_type: ProblemType):
        """
        根据问题类型选择分析器
        """
        # TODO: 返回对应的分析器
        return StackTraceAnalyzer()  # 默认
    
    def _verify_locations(self, 
                          locations: List[CodeLocation],
                          code_model: CodeModel) -> List[CodeLocation]:
        """
        验证代码位置
        """
        verified = []
        
        for loc in locations:
            # 检查文件是否在代码模型中
            for file in code_model.files if code_model else []:
                if loc.file in file.path:
                    # 检查行号是否在函数范围内
                    for func in file.functions:
                        if func.start_line <= loc.line <= func.end_line:
                            loc.function = func.name
                            loc.verified = True
                            verified.append(loc)
                            break
        
        return verified or locations
    
    def _save_case(self, result: AnalysisResult, request: AnalysisRequest):
        """
        保存分析结果为知识库案例
        """
        # TODO: WeKnora 上传文档
        pass
    
    def _no_repo_result(self, request: AnalysisRequest) -> AnalysisResult:
        """
        无仓库时的结果
        """
        return AnalysisResult(
            problem_type=ProblemType.C1,
            root_cause="无法确定代码仓库",
            code_locations=[],
            fix_suggestion="请提供代码仓库地址或更详细的问题描述",
            confidence=0.0
        )


class StackTraceAnalyzer:
    """
    堆栈分析器 (Type A)
    """
    
    def analyze(self, request, code_model, logs, similar_cases) -> AnalysisResult:
        """
        分析堆栈
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
    
    def _extract_stack_trace(self, logs) -> List[str]:
        """
        从日志提取堆栈
        """
        for log in logs:
            if log.stack_trace:
                return log.stack_trace
        return []
    
    def _is_business_code(self, frame: str, code_model) -> bool:
        """
        判断是否业务代码
        """
        # 过滤框架: java.lang, org.springframework, etc.
        frameworks = ["java.lang", "org.springframework", "org.apache", "javax."]
        return not any(f in frame for f in frameworks)
    
    def _parse_frame(self, frame: str):
        """
        解析堆栈帧，提取文件和行号
        
        格式: at com.example.OrderService.processOrder(OrderService.java:245)
        """
        match = re.search(r'at\s+[\w.]+\(([\w.]+):(\d+)\)', frame)
        if match:
            return match.group(1), int(match.group(2))
        return None, None
    
    def _analyze_root_cause(self, frames, request) -> str:
        """
        分析根因
        """
        if frames:
            top_frame = frames[0]
            return f"错误发生在: {top_frame}"
        return request.error_desc
    
    def _fallback_analysis(self, request, code_model) -> AnalysisResult:
        """
        无堆栈时的退化分析
        """
        return AnalysisResult(
            problem_type=ProblemType.A2,
            root_cause=request.error_desc,
            code_locations=[],
            fix_suggestion="无法定位具体代码，请提供更多信息（如堆栈、traceId）",
            confidence=0.3
        )


class DataChainAnalyzer:
    """
    数据链分析器 (Type B)
    """
    
    def analyze(self, request, code_model, logs, similar_cases) -> AnalysisResult:
        """
        分析数据链
        """
        # TODO: 实现数据链分析
        # 1. 检测数据异常
        # 2. 重建时间线
        # 3. 找到异常操作
        # 4. 映射到代码
        
        return AnalysisResult(
            problem_type=ProblemType.B1,
            root_cause="数据分析待实现",
            code_locations=[],
            fix_suggestion="",
            confidence=0.0
        )


class LogicInferenceAnalyzer:
    """
    逻辑推理分析器 (Type C)
    """
    
    def analyze(self, request, code_model, logs, similar_cases) -> AnalysisResult:
        """
        逻辑推理分析
        """
        # TODO: 实现逻辑推理
        # 1. 提取业务规则
        # 2. 分析代码逻辑
        # 3. 找偏差点
        
        return AnalysisResult(
            problem_type=ProblemType.C1,
            root_cause="逻辑推理待实现",
            code_locations=[],
            fix_suggestion="",
            confidence=0.0
        )