"""
Bug Analysis Workflow - 主工作流
"""

import re
import json
from typing import List, Dict, Optional
from datetime import datetime
from pathlib import Path

from .models import (
    AnalysisRequest, AnalysisResult, RouteResult,
    CodeModel, CodeLocation, ProblemType, LogEvent, BugAnalysisConfig
)
from .registry import ServiceRegistry, load_registry_from_yaml
from .routers import CompositeRouter

# 导入分析器
from analyzers.stack_trace_analyzer import StackTraceAnalyzer
from analyzers.data_chain_analyzer import DataChainAnalyzer
from analyzers.logic_inference_analyzer import LogicInferenceAnalyzer

# 导入连接器
from connectors.weknora import WeKnoraConnector, WeKnoraConfig
from connectors.aliyun_sls import AliyunSLSConnector

# 导入适配器
from adapters.java_adapter import JavaAdapter
from adapters.go_adapter import GoAdapter


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
    
    支持多服务互调分析：
    - 从 traceId 获取调用链
    - 从服务注册表推断调用关系
    - 跨服务状态一致性检查
    """
    
    def __init__(self, config: BugAnalysisConfig):
        self.config = config
        
        # 服务注册表
        self.registry = load_registry_from_yaml(config.registry_path)
        
        # 初始化连接器
        self.weknora = self._init_weknora(config)
        self.sls = self._init_sls(config)
        
        # 路由器
        self.router = CompositeRouter(
            registry=self.registry,
            sls_connector=self.sls,
            weknora_connector=self.weknora
        )
        
        # 语言适配器
        self.adapters = {
            'java': JavaAdapter(),
            'go': GoAdapter(),
            # 'typescript': TypeScriptAdapter(),  # TODO
        }
        
        # 分析器
        self.analyzers = {
            ProblemType.A1: StackTraceAnalyzer(),
            ProblemType.A2: StackTraceAnalyzer(),  # A2 也用堆栈分析，退化处理
            ProblemType.B1: DataChainAnalyzer(self.sls, None, self.weknora),
            ProblemType.B2: DataChainAnalyzer(self.sls, None, self.weknora),
            ProblemType.C1: LogicInferenceAnalyzer(self.weknora),
        }
        
        # 代码模型缓存
        self.code_models: Dict[str, CodeModel] = {}
        
        # 跨服务调用图缓存
        self.service_call_graph: Dict[str, List[str]] = {}
        self._build_service_call_graph()
    
    def _init_weknora(self, config: BugAnalysisConfig) -> Optional[WeKnoraConnector]:
        """初始化 WeKnora 连接器"""
        if config.weknora_base_url and config.weknora_api_key:
            return WeKnoraConnector(WeKnoraConfig(
                base_url=config.weknora_base_url,
                api_key=config.weknora_api_key,
                default_agent_id="builtin-smart-reasoning"
            ))
        return None
    
    def _init_sls(self, config: BugAnalysisConfig) -> Optional[AliyunSLSConnector]:
        """初始化阿里云 SLS 连接器"""
        if config.sls_access_key and config.sls_secret:
            return AliyunSLSConnector(
                access_key=config.sls_access_key,
                secret=config.sls_secret,
                endpoint=config.sls_endpoint,
                project=config.sls_project
            )
        return None
    
    def _build_service_call_graph(self):
        """
        构建跨服务调用图
        
        从服务注册表的 dependencies 推断调用关系
        """
        for service_name, service_info in self.registry.services.items():
            # 服务依赖 → 可能的调用关系
            for dep in service_info.dependencies:
                if service_name not in self.service_call_graph:
                    self.service_call_graph[service_name] = []
                self.service_call_graph[service_name].append(dep)
    
    def analyze(self, request: AnalysisRequest) -> AnalysisResult:
        """
        主分析入口
        
        支持多服务互调分析
        """
        
        # ========== Step 1: 路由确定仓库 ==========
        kb_ids = list(self.config.weknora_kb_ids.values()) if self.config.weknora_kb_ids else []
        route_result = self.router.route(request, kb_ids=kb_ids)
        
        # 如果需要用户确认
        if route_result.needs_user_input:
            return AnalysisResult(
                problem_type=ProblemType.C1,
                root_cause="需要更多信息",
                code_locations=[],
                fix_suggestion=route_result.question,
                confidence=0.0
            )
        
        # 确定分析仓库（支持多仓库）
        repo_path = request.repo_path or route_result.primary_repo
        related_repos = route_result.related_repos
        
        if not repo_path:
            return self._no_repo_result(request)
        
        # ========== Step 2: 多服务调用链分析 ==========
        call_chain = route_result.call_chain
        if not call_chain and request.trace_id:
            # 从 traceId 获取调用链
            call_chain = self._get_call_chain_from_trace(request.trace_id)
        
        # 如果问题涉及多个服务，进行跨服务分析
        if call_chain and len(call_chain) > 1:
            return self._analyze_cross_service(request, call_chain, route_result)
        
        # ========== Step 3: 预处理（单服务分析） ==========
        
        # 代码解析
        code_model = self._parse_code(repo_path)
        
        # 获取日志
        logs = self._get_logs(request, route_result)
        
        # 问题分类
        problem_type = self._classify_problem(request, logs)
        
        # ========== Step 4: 知识增强 ==========
        similar_cases = self._search_similar_cases(request)
        
        # 如果找到完全匹配的历史案例
        if similar_cases and self._is_exact_match(similar_cases[0], request.error_desc):
            return self._build_result_from_case(similar_cases[0])
        
        # ========== Step 5: 分析引擎 ==========
        analyzer = self._get_analyzer(problem_type)
        
        result = analyzer.analyze(
            request=request,
            code_model=code_model,
            logs=logs,
            similar_cases=similar_cases
        )
        
        # ========== Step 6: 结果验证 ==========
        if result.code_locations and code_model:
            result.code_locations = self._verify_locations(
                result.code_locations, 
                code_model
            )
        
        # ========== Step 7: 知识闭环 ==========
        if self.config.enable_auto_save_case and result.confidence > 0.7:
            self._save_case(result, request)
        
        return result
    
    def _analyze_cross_service(self,
                               request: AnalysisRequest,
                               call_chain: List[str],
                               route_result: RouteResult) -> AnalysisResult:
        """
        跨服务调用链分析
        
        当问题涉及多个服务时，分析跨服务的状态一致性
        """
        
        # 获取所有涉及的仓库
        services_in_chain = call_chain
        repos_to_analyze = []
        
        for service in services_in_chain:
            repo = self.registry.get_repo(service)
            if repo:
                repos_to_analyze.append((service, repo))
        
        # 为每个服务获取日志和代码模型
        service_logs = {}
        service_models = {}
        
        for service, repo in repos_to_analyze:
            # 日志（按 traceId 或服务名）
            if request.trace_id and self.sls:
                logs = self.sls.query_logs(query=f"service: {service} AND trace_id: {request.trace_id}")
            else:
                logs = []
            service_logs[service] = logs
            
            # 代码模型
            if repo in self.code_models:
                service_models[service] = self.code_models[repo]
            else:
                model = self._parse_code(repo)
                if model:
                    service_models[service] = model
                    self.code_models[repo] = model
        
        # 分析跨服务状态一致性
        consistency_issues = self._check_cross_service_consistency(
            call_chain, service_logs, request
        )
        
        # 定位不一致的服务
        error_service = None
        for issue in consistency_issues:
            if issue['type'] == 'state_inconsistency':
                error_service = issue['service']
                break
        
        # 如果找到错误服务，深入分析该服务
        if error_service and error_service in service_models:
            error_model = service_models[error_service]
            error_logs = service_logs.get(error_service, [])
            
            # 使用数据链分析器分析该服务
            analyzer = DataChainAnalyzer(self.sls, None, self.weknora)
            
            # 构建增强的请求
            enhanced_request = AnalysisRequest(
                error_desc=f"[跨服务问题] {request.error_desc}，问题出现在 {error_service}",
                repo_path=self.registry.get_repo(error_service),
                trace_id=request.trace_id,
                time_range=request.time_range
            )
            
            result = analyzer.analyze(
                request=enhanced_request,
                code_model=error_model,
                logs=error_logs,
                similar_cases=[]
            )
            
            # 添加跨服务上下文
            result.root_cause = f"[跨服务调用链] {call_chain}\n\n{result.root_cause}"
            
            return result
        
        # 如果没有找到明确的不一致，返回调用链信息
        return AnalysisResult(
            problem_type=ProblemType.B1,
            root_cause=f"涉及多服务调用链: {call_chain}\n请检查各服务的状态同步逻辑",
            code_locations=[],
            fix_suggestion=f"建议按调用链顺序检查:\n" + '\n'.join(f"  - {s}" for s in call_chain),
            confidence=0.5,
            timeline=self._build_cross_service_timeline(call_chain, service_logs)
        )
    
    def _check_cross_service_consistency(self,
                                          call_chain: List[str],
                                          service_logs: Dict[str, List[LogEvent]],
                                          request: AnalysisRequest) -> List[Dict]:
        """
        检查跨服务状态一致性
        
        检查：
        1. 状态更新是否同步
        2. 回调是否正确处理
        3. 数据是否一致
        """
        
        issues = []
        
        # 从日志检查状态变更
        state_changes = {}
        
        for service, logs in service_logs.items():
            for log in logs:
                # 检查状态变更日志
                if '状态' in log.message or 'status' in log.message.lower():
                    state_changes[service] = log
        
        # 检查依赖服务之间的状态一致性
        for i in range(len(call_chain) - 1):
            caller = call_chain[i]
            callee = call_chain[i + 1]
            
            # 从服务注册表检查依赖关系
            caller_info = self.registry.get_service(caller)
            if caller_info and callee in caller_info.dependencies:
                # 检查共享的数据表
                shared_tables = set(caller_info.db_tables) & set(
                    self.registry.get_service(callee).db_tables if self.registry.get_service(callee) else []
                )
                
                if shared_tables:
                    # 共享数据表，检查一致性
                    issues.append({
                        'type': 'shared_data',
                        'caller': caller,
                        'callee': callee,
                        'tables': list(shared_tables),
                        'description': f"{caller} 和 {callee} 共享数据表 {shared_tables}，可能存在状态不一致"
                    })
        
        # 检查回调处理
        for service, logs in service_logs.items():
            for log in logs:
                if '回调' in log.message and log.level == 'ERROR':
                    issues.append({
                        'type': 'callback_error',
                        'service': service,
                        'description': f"{service} 回调处理失败"
                    })
                
                if '同步' in log.message and '失败' in log.message:
                    issues.append({
                        'type': 'sync_error',
                        'service': service,
                        'description': f"{service} 状态同步失败"
                    })
        
        return issues
    
    def _build_cross_service_timeline(self,
                                       call_chain: List[str],
                                       service_logs: Dict[str, List[LogEvent]]) -> List[Dict]:
        """
        构建跨服务调用时间线
        """
        
        timeline = []
        
        # 收集所有日志，按时间排序
        all_logs = []
        for service, logs in service_logs.items():
            for log in logs:
                all_logs.append((service, log))
        
        all_logs.sort(key=lambda x: x[1].timestamp)
        
        for service, log in all_logs:
            timeline.append({
                'timestamp': str(log.timestamp),
                'service': service,
                'operation': log.message[:100],
                'status': log.level,
                'trace_id': log.trace_id
            })
        
        return timeline
    
    def _get_call_chain_from_trace(self, trace_id: str) -> List[str]:
        """
        从 traceId 获取调用链
        
        如果有 SLS 连接器，查询 traceId 的日志
        """
        
        if not self.sls:
            return []
        
        logs = self.sls.extract_trace_chain(trace_id)
        
        # 提取服务顺序
        chain = []
        for log in logs:
            if log.service and log.service not in chain:
                chain.append(log.service)
        
        return chain
    
    def _parse_code(self, repo_path: str) -> Optional[CodeModel]:
        """
        解析代码
        
        1. 检测语言
        2. 选择适配器
        3. 解析 AST
        """
        
        # 检查缓存
        if repo_path in self.code_models:
            return self.code_models[repo_path]
        
        # 检测语言
        language = self._detect_language(repo_path)
        
        if not language:
            return None
        
        # 选择适配器
        adapter = self.adapters.get(language)
        
        if not adapter:
            return None
        
        # 解析
        try:
            code_model = adapter.parse_repo(repo_path)
            self.code_models[repo_path] = code_model
            return code_model
        except Exception as e:
            print(f"代码解析失败: {e}")
            return None
    
    def _detect_language(self, repo_path: str) -> Optional[str]:
        """
        检测仓库语言
        
        通过文件扩展名或服务注册表判断
        """
        
        repo = Path(repo_path)
        
        # 检查服务注册表
        for service in self.registry.services.values():
            if service.repo_url == repo_path or repo_path in service.repo_url:
                return service.language
        
        # 检查文件扩展名
        java_count = len(list(repo.rglob("*.java")))
        go_count = len(list(repo.rglob("*.go")))
        ts_count = len(list(repo.rglob("*.ts"))) + len(list(repo.rglob("*.tsx")))
        
        counts = {'java': java_count, 'go': go_count, 'typescript': ts_count}
        
        if counts:
            return max(counts, key=counts.get)
        
        return None
    
    def _get_logs(self, request: AnalysisRequest, route_result: RouteResult) -> List[LogEvent]:
        """
        获取日志
        
        1. 如果有 traceId，查询调用链
        2. 如果有时间范围，查询错误事件
        """
        
        if not self.sls:
            return []
        
        if request.trace_id:
            return self.sls.extract_trace_chain(request.trace_id)
        
        if request.time_range:
            keywords = route_result.matched_keywords or []
            return self.sls.find_error_events(
                time_range=request.time_range,
                keywords=keywords
            )
        
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
        
        使用 WeKnora 知识库搜索
        """
        
        if not self.weknora:
            return []
        
        kb_ids = list(self.config.weknora_kb_ids.values()) if self.config.weknora_kb_ids else []
        
        if not kb_ids:
            return []
        
        results = self.weknora.search_knowledge(
            query=request.error_desc,
            kb_ids=kb_ids,
            top_k=5
        )
        
        return results
    
    def _is_exact_match(self, case: Dict, desc: str) -> bool:
        """
        判断是否完全匹配
        
        相似度 > 0.95 时认为是完全匹配
        """
        
        # WeKnora 返回的相似度分数
        score = case.get('score', 0)
        
        if score >= 0.95:
            return True
        
        # 也可以检查关键词匹配度
        case_content = case.get('content', '')
        
        # 如果案例描述与问题描述高度相似
        common_words = set(desc.lower().split()) & set(case_content.lower().split())
        overlap_ratio = len(common_words) / len(desc.split()) if desc.split() else 0
        
        return overlap_ratio >= 0.7
    
    def _build_result_from_case(self, case: Dict) -> AnalysisResult:
        """
        从历史案例构建结果
        """
        
        content = case.get('content', '')
        
        # 从案例内容提取信息
        root_cause = self._extract_from_case(content, '根因', '问题原因')
        file = self._extract_from_case(content, '文件', 'File')
        line = self._extract_from_case(content, '行号', 'Line')
        fix = self._extract_from_case(content, '修复', '解决方案')
        
        code_locations = []
        if file and line:
            # 尝试解析行号
            line_match = re.search(r'(\d+)', str(line))
            line_num = int(line_match.group(1)) if line_match else 0
            
            code_locations.append(CodeLocation(
                file=file,
                line=line_num,
                verified=False
            ))
        
        return AnalysisResult(
            problem_type=ProblemType.A1,
            root_cause=root_cause or "历史案例匹配",
            code_locations=code_locations,
            fix_suggestion=fix or "请参考历史案例处理",
            confidence=0.95,
            matched_cases=[case]
        )
    
    def _extract_from_case(self, content: str, *keywords) -> Optional[str]:
        """从案例内容提取特定字段"""
        
        for keyword in keywords:
            pattern = rf'{keyword}[：:]\s*(.+)'
            match = re.search(pattern, content)
            if match:
                return match.group(1).strip()
        
        return None
    
    def _get_analyzer(self, problem_type: ProblemType):
        """
        根据问题类型选择分析器
        """
        
        return self.analyzers.get(problem_type, StackTraceAnalyzer())
    
    def _verify_locations(self,
                          locations: List[CodeLocation],
                          code_model: CodeModel) -> List[CodeLocation]:
        """
        验证代码位置
        
        检查文件是否存在、行号是否在函数范围内
        """
        
        verified = []
        
        for loc in locations:
            # 检查文件是否在代码模型中
            for file in code_model.files if code_model else []:
                if loc.file in file.path or file.path in loc.file:
                    # 检查行号是否在函数范围内
                    for func in file.functions:
                        if func.start_line <= loc.line <= func.end_line:
                            loc.function = func.name
                            loc.verified = True
                            verified.append(loc)
                            break
                    
                    # 如果不在函数范围内，但文件存在
                    if not loc.verified:
                        loc.verified = True
                        verified.append(loc)
                    break
        
        # 返回验证过的，或原始的（如果验证失败）
        return verified or locations
    
    def _save_case(self, result: AnalysisResult, request: AnalysisRequest):
        """
        保存分析结果为知识库案例
        
        实现知识闭环
        """
        
        if not self.weknora:
            return
        
        kb_id = self.config.weknora_kb_ids.get('bug_cases')
        
        if not kb_id:
            return
        
        # 生成案例文档
        case_doc = self._generate_case_document(result, request)
        
        # 上传到知识库
        try:
            self.weknora.upload_text(
                kb_id=kb_id,
                content=case_doc,
                title=f"Bug案例: {result.root_cause[:50]}",
                metadata={
                    'type': 'bug_case',
                    'confidence': result.confidence,
                    'problem_type': result.problem_type.value,
                    'created_at': datetime.now().isoformat()
                }
            )
        except Exception as e:
            print(f"保存案例失败: {e}")
    
    def _generate_case_document(self, result: AnalysisResult, request: AnalysisRequest) -> str:
        """
        生成 Bug 案例文档
        
        格式化的 Markdown 文档，包含问题描述、根因、修复建议等
        """
        
        doc = f"""# Bug案例分析报告

## 问题描述
{request.error_desc}

## 问题类型
{result.problem_type.value}

## 根因定位
{result.root_cause}

## 代码位置
"""
        
        if result.code_locations:
            for loc in result.code_locations:
                doc += f"- 文件: {loc.file}\n"
                doc += f"- 行号: {loc.line}\n"
                if loc.function:
                    doc += f"- 函数: {loc.function}\n"
        else:
            doc += "未能定位具体代码位置\n"
        
        doc += f"""
## 修复建议
{result.fix_suggestion}

## 置信度
{result.confidence:.2f}

## 分析时间
{datetime.now().isoformat()}
"""
        
        # 添加时间线（如果有）
        if result.timeline:
            doc += "\n## 事件时间线\n"
            for event in result.timeline:
                doc += f"- [{event.get('timestamp', '')}] {event.get('service', '')}: {event.get('operation', '')}\n"
        
        return doc
    
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


# 保留 StackTraceAnalyzer 类（用于 Type A 问题）
class StackTraceAnalyzer:
    """
    堆栈分析器 (Type A)
    
    处理有堆栈信息的错误
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
        
        过滤框架层
        """
        
        # 框架包名（Java）
        frameworks = [
            "java.lang", "java.util", "org.springframework",
            "org.apache", "javax.", "com.fasterxml",
            "net.", "io.", "reactor."
        ]
        
        # Go 框架
        go_frameworks = [
            "net/http", "encoding/", "strings", "fmt",
            "log", "errors", "sync"
        ]
        
        # 检查是否框架代码
        for fw in frameworks + go_frameworks:
            if fw in frame:
                return False
        
        return True
    
    def _parse_frame(self, frame: str):
        """
        解析堆栈帧，提取文件和行号
        
        Java 格式: at com.example.OrderService.processOrder(OrderService.java:245)
        Go 格式: order/service.go:45
        """
        
        # Java 格式
        match = re.search(r'at\s+[\w.]+\(([\w.]+):(\d+)\)', frame)
        if match:
            return match.group(1), int(match.group(2))
        
        # Go 格式
        match = re.search(r'([\w/]+\.go):(\d+)', frame)
        if match:
            return match.group(1), int(match.group(2))
        
        return None, None
    
    def _analyze_root_cause(self, frames, request) -> str:
        """
        分析根因
        
        从堆栈顶层的业务代码推断
        """
        
        if frames:
            top_frame = frames[0]
            
            # 提取异常类型（如果有）
            exception_type = None
            for frame in frames:
                if 'Exception' in frame or 'Error' in frame:
                    match = re.search(r'(\w+Exception|\w+Error)', frame)
                    if match:
                        exception_type = match.group(1)
                        break
            
            if exception_type:
                return f"错误类型: {exception_type}\n发生在: {top_frame}"
            
            return f"错误发生在: {top_frame}"
        
        return request.error_desc
    
    def _fallback_analysis(self, request, code_model) -> AnalysisResult:
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
                fix_suggestion="请提供完整堆栈或更多上下文",
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
            'create', 'Create', '创建',
            'update', 'Update', '更新',
            'delete', 'Delete', '删除',
        ]
        
        for word in business_words:
            if word in desc:
                keywords.append(word)
        
        return keywords


# 保留 DataChainAnalyzer 和 LogicInferenceAnalyzer 的类定义（已在单独文件中）
# 这里仅作为导入使用