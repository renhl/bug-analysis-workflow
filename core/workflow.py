"""
Bug Analysis Workflow - 主工作流
"""

import os
import re
import json as json_module
import tempfile
import subprocess
from typing import List, Dict, Optional
from datetime import datetime
from pathlib import Path

import logging
from .models import (
    AnalysisRequest, AnalysisResult, RouteResult,
    CodeModel, CodeLocation, ProblemType, LogEvent,
    BugAnalysisConfig, DatabaseConfig, ContextPackage, DEFAULT_AGENT_ID,
)
from .registry import ServiceRegistry, load_registry_from_yaml
from .routers import CompositeRouter
from .constants import (
    CONFIDENCE_HIGH_THRESHOLD, CONFIDENCE_TOP_K_SEARCH,
    CONFIDENCE_LOW_THRESHOLD, CASE_RESULT_CONFIDENCE,
    CHANGED_FILES_BASE_BRANCHES, CHANGED_FILES_TIMEOUT, CHANGED_FILES_EXCLUDED_MODULES,
    CHANGED_FILE_EXTENSIONS, CHANGED_FILES_MAX_HINTS,
    CROSS_SERVICE_CONFIDENCE,
    AI_MAX_TOKENS, AI_CONFIDENCE_BOOST_VERIFIED, AI_CONFIDENCE_PENALTY_NO_LOCATIONS,
)

logger = logging.getLogger(__name__)

# 导入连接器
from connectors.weknora import WeKnoraConnector, WeKnoraConfig
from connectors.aliyun_sls import AliyunSLSConnector
from connectors.mysql import MySQLConnector, MySQLConfig

# 导入适配器
from adapters.java_adapter import JavaAdapter
from adapters.go_adapter import GoAdapter


class _FallbackAnalyzer:
    """Low-confidence analyzer used when AI/skill analysis is unavailable.

    The original rule-based analyzers have been removed. This fallback keeps
    the public AnalysisResult contract stable and tells the caller which
    evidence is needed for a stronger result.
    """

    def __init__(self, problem_type: ProblemType = ProblemType.C1):
        self.problem_type = problem_type

    def analyze(self, request, code_model=None, logs=None, similar_cases=None):
        guidance = {
            ProblemType.A1: "Stack trace detected, but no AI/skill analyzer is available to read the crash line. "
                            "Enable AI (ai_enabled=true) or use the bug-analyzer skill for deep analysis.",
            ProblemType.A2: "Error log detected, but no AI/skill analyzer is available to trace the error origin. "
                            "Enable AI (ai_enabled=true) or use the bug-analyzer skill for deep analysis.",
            ProblemType.B1: "Data anomaly detected, but no AI/skill analyzer is available to trace data flow. "
                            "Check DB state directly or enable AI for analysis.",
            ProblemType.B2: "Business anomaly detected, but no AI/skill analyzer is available to compare rules. "
                            "Enable AI (ai_enabled=true) or use the bug-analyzer skill for deep analysis.",
            ProblemType.C1: "Logic deviation detected, but no AI/skill analyzer is available to compare expected and actual behavior. "
                            "This type of bug requires AI reasoning. Enable AI (ai_enabled=true) "
                            "or use the bug-analyzer skill which performs evidence-based code reading "
                            "and call-chain tracing.",
        }

        return AnalysisResult(
            problem_type=self.problem_type,
            root_cause=guidance.get(self.problem_type, guidance[ProblemType.C1]),
            code_locations=[],
            fix_suggestion=(
                "Enable AI analysis (ai_enabled=true), pass --ai, or use the bug-analyzer skill. "
                "Provide --repo plus --expected/--actual for logic bugs to raise confidence."
            ),
            confidence=0.1,
        )


def load_config_from_yaml(config_path: str) -> BugAnalysisConfig:
    """
    从 YAML 文件加载 BugAnalysisConfig

    支持格式参见 config/config.yaml.example
    """
    import yaml
    with open(config_path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    # 解析多业务线数据库配置
    databases: Dict[str, DatabaseConfig] = {}
    for key, db_raw in (raw.get("databases") or {}).items():
        databases[key] = DatabaseConfig(
            host=db_raw.get("host") or "",
            port=int(db_raw.get("port") or 3306),
            user=db_raw.get("user") or "",
            password=str(db_raw.get("password") or ""),
            db_name=db_raw.get("db_name") or "",
        )

    return BugAnalysisConfig(
        weknora_base_url=raw.get("weknora_base_url", ""),
        weknora_api_key=raw.get("weknora_api_key", ""),
        weknora_kb_ids=raw.get("weknora_kb_ids") or {},
        domains_dir=raw.get("domains_dir"),
        registry_path=raw.get("registry_path"),
        databases=databases,
        sls_access_key=raw.get("sls_access_key"),
        sls_secret=raw.get("sls_secret"),
        sls_endpoint=raw.get("sls_endpoint"),
        sls_project=raw.get("sls_project"),
        confidence_threshold=raw.get("confidence_threshold", 0.5),
        max_related_repos=raw.get("max_related_repos", 5),
        enable_auto_save_case=raw.get("enable_auto_save_case", True),
        ai_enabled=raw.get("ai_enabled", False),
        ai_api_key=raw.get("anthropic_api_key"),
        ai_model=raw.get("ai_model", "claude-sonnet-4-20250514"),
        ai_timeout=float(raw.get("ai_timeout", 30.0)),
        ai_fallback_to_skill=raw.get("ai_fallback_to_skill", True),
    )


class BugAnalysisWorkflow:
    """
    智能问题分析工作流

    支持两种初始化模式：
    1. 域模式（推荐）：domains_dir 指向 domains/ 目录，自动发现各业务域
    2. 传统模式：registry_path 指向单一服务注册表 YAML

    核心流程:
    1. 路由确定仓库（支持按域限定范围）
    2. 预处理（代码解析、日志获取）
    3. 知识增强（WeKnora，支持按域 KB 配置）
    4. 分析引擎（根据问题类型选择）
    5. 输出结果
    6. 知识闭环
    """

    def __init__(self, config: BugAnalysisConfig):
        self.config = config

        # 域加载器（域模式）
        self.domain_loader = self._init_domain_loader(config)

        # 服务注册表（合并所有域或传统单注册表）
        self.registry = self._init_registry(config)

        # 初始化连接器
        self.weknora = self._init_weknora(config)
        self.sls = self._init_sls(config)

        # 多业务线 MySQL 连接器（按域名索引，延迟初始化）
        self._mysql_connectors: Dict[str, MySQLConnector] = {}

        # 全局路由器（使用合并注册表）
        self.router = CompositeRouter(
            registry=self.registry,
            sls_connector=self.sls,
            weknora_connector=self.weknora
        )

        # 域级路由器缓存（按域名索引，域模式下限定路由范围）
        self._domain_routers: Dict[str, CompositeRouter] = {}

        # 语言适配器
        self.adapters = {
            'java': JavaAdapter(),
            'go': GoAdapter(),
        }

        # AI 分析器（延迟初始化）
        self.ai_analyzer = None

        # 基础分析器（不含域特定规则）
        # Type-B 和 Type-C 的域特定版本在 _get_analyzer() 中按需创建并缓存
        self.analyzers = {
            ProblemType.A1: _FallbackAnalyzer(ProblemType.A1),
            ProblemType.A2: _FallbackAnalyzer(ProblemType.A2),
        }

        # 域级分析器缓存（含域关键词 / 规则 / KB IDs）
        self._domain_analyzers: Dict[str, Dict] = {}  # {domain_key: {B1, B2, C1}}

        # 代码模型缓存
        self.code_models: Dict[str, CodeModel] = {}

        # 语言检测缓存（避免重复 rglob 遍历）
        self._language_cache: Dict[str, Optional[str]] = {}

        # 跨服务调用图缓存
        self.service_call_graph: Dict[str, List[str]] = {}
        self._build_service_call_graph()

    # ----------------------------------------------------------------
    # 初始化方法
    # ----------------------------------------------------------------

    def _init_domain_loader(self, config: BugAnalysisConfig):
        """初始化域加载器（仅域模式）"""
        if not config.domains_dir:
            return None
        from domains.loader import DomainLoader
        return DomainLoader(config.domains_dir).load_all()

    def _init_registry(self, config: BugAnalysisConfig) -> ServiceRegistry:
        """初始化服务注册表"""
        if self.domain_loader:
            return self.domain_loader.get_merged_registry()
        if config.registry_path:
            return load_registry_from_yaml(config.registry_path)
        return ServiceRegistry()

    def _init_weknora(self, config: BugAnalysisConfig) -> Optional[WeKnoraConnector]:
        """初始化 WeKnora 连接器"""
        if (
            config.weknora_base_url
            and config.weknora_api_key
            and "your-" not in config.weknora_base_url
            and not config.weknora_api_key.startswith("sk-your")
        ):
            return WeKnoraConnector(WeKnoraConfig(
                base_url=config.weknora_base_url,
                api_key=config.weknora_api_key,
                default_agent_id=DEFAULT_AGENT_ID
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

    def get_mysql(self, domain_name: str) -> Optional[MySQLConnector]:
        """
        获取指定业务域的 MySQL 连接器（延迟初始化）

        连接配置来自 config.databases[domain.database_key]
        """
        if domain_name in self._mysql_connectors:
            return self._mysql_connectors[domain_name]

        # 从域配置获取 database_key
        database_key = domain_name
        if self.domain_loader:
            domain = self.domain_loader.get_domain(domain_name)
            if domain and domain.database_key:
                database_key = domain.database_key

        db_config = self.config.databases.get(database_key)
        if not db_config:
            return None

        try:
            connector = MySQLConnector(MySQLConfig(
                host=db_config.host,
                port=db_config.port,
                user=db_config.user,
                password=db_config.password,
                database=db_config.db_name,
            ))
            self._mysql_connectors[domain_name] = connector
            return connector
        except Exception as e:
            logger.error("[MySQL] 初始化 %s 连接失败: %s", domain_name, e)
            return None

    def _resolve_kb_ids(self, domain_name: Optional[str]) -> Dict[str, str]:
        """解析当前域的知识库 ID（域级优先，全局兜底）"""
        if self.domain_loader and domain_name:
            return self.domain_loader.resolve_kb_ids(domain_name, self.config.weknora_kb_ids)
        return self.config.weknora_kb_ids or {}

    def _get_domain_rules(self, domain_name: Optional[str]) -> List[Dict]:
        """获取指定域的业务规则（用于 Type-C 分析）"""
        if self.domain_loader and domain_name:
            return self.domain_loader.get_rules(domain_name)
        return []

    def _get_domain_router(self, domain_name: Optional[str]) -> CompositeRouter:
        """获取指定域的路由器（域模式下使用域内注册表，限定路由范围）"""
        if not domain_name or not self.domain_loader:
            return self.router

        if domain_name not in self._domain_routers:
            domain_registry = self.domain_loader.get_registry(domain_name)
            if domain_registry:
                self._domain_routers[domain_name] = CompositeRouter(
                    registry=domain_registry,
                    sls_connector=self.sls,
                    weknora_connector=self.weknora
                )
            else:
                self._domain_routers[domain_name] = self.router

        return self._domain_routers[domain_name]

    # ----------------------------------------------------------------
    # 主分析入口
    # ----------------------------------------------------------------

    def analyze(self, request: AnalysisRequest) -> AnalysisResult:
        """
        主分析入口

        当 request.domain 指定时，路由、规则、KB、数据库均限定在该域内。
        输出模型保持稳定：始终返回 AnalysisResult，CLI/API 上层不需要跟随流程演进改字段。
        """

        # Guard: validate required inputs
        if not request.error_desc:
            return AnalysisResult(
                problem_type=ProblemType.C1,
                root_cause="未提供报错信息",
                code_locations=[],
                fix_suggestion="请提供完整的报错文本或问题描述",
                confidence=0.0,
            )

        # ========== Step 1: 先分类，再路由 ==========
        # 文本本身包含大量信号：堆栈、NPE、基础设施错误、预期/实际偏差。
        # 先做轻量分类，可以避免基础设施问题被误导去读业务代码。
        initial_problem_type = self._classify_problem(request, logs=[])
        if self._is_infrastructure_issue(request):
            return self._infra_result(request)

        # ========== Step 2: 路由确定仓库 ==========
        kb_ids = list(self._resolve_kb_ids(request.domain).values())
        # 域模式下使用域内注册表路由器，限定搜索范围
        active_router = self._get_domain_router(request.domain)
        route_result = active_router.route(request, kb_ids=kb_ids)

        # 若请求中明确指定了 repo_path，直接使用
        if request.repo_path:
            route_result.needs_user_input = False
            route_result.primary_repo = request.repo_path

        # 合并 CLI 传入的关联仓库
        if request.related_repos:
            route_result.related_repos = list(dict.fromkeys(
                route_result.related_repos + request.related_repos
            ))

        if route_result.needs_user_input:
            return AnalysisResult(
                problem_type=ProblemType.C1,
                root_cause="需要更多信息",
                code_locations=[],
                fix_suggestion=route_result.question,
                confidence=0.0
            )

        repo_path = request.repo_path or route_result.primary_repo

        if not repo_path:
            return self._no_repo_result(request, initial_problem_type)

        # ========== Step 3: 多服务调用链分析 ==========
        call_chain = route_result.call_chain
        if not call_chain and request.trace_id:
            call_chain = self._get_call_chain_from_trace(request.trace_id)

        if call_chain and len(call_chain) > 1:
            return self._analyze_cross_service(request, call_chain, route_result)

        # ========== Step 4: 预处理（单服务分析） ==========

        changed_files = request.changed_files or self._get_changed_files(repo_path, request.base_branch)
        if changed_files:
            request = self._enrich_request_with_diff(request, changed_files, repo_path)

        code_model = self._parse_code(repo_path)
        logs = self._get_logs(request, route_result)
        problem_type = self._classify_problem(request, logs, fallback=initial_problem_type)

        # ========== Step 5: 知识增强 ==========
        similar_cases = self._search_similar_cases(request)

        if similar_cases and self._is_exact_match(similar_cases[0], request.error_desc):
            return self._build_result_from_case(similar_cases[0], request)

        # ========== Step 6: Analysis Engine ==========
        # Use AI if enabled
        if self.config.ai_enabled:
            context = self._build_context_package(
                request, route_result, code_model, logs, similar_cases, problem_type
            )
            result = self._analyze_with_ai(context)
            if result is None:
                # AI failed, fall back to low-confidence guidance
                logger.info("AI analysis failed or unavailable, falling back to guidance result")
                analyzer = self._get_analyzer(problem_type, request.domain)
                result = analyzer.analyze(
                    request=request, code_model=code_model, logs=logs, similar_cases=similar_cases
                )
        else:
            # AI disabled, use low-confidence guidance
            analyzer = self._get_analyzer(problem_type, request.domain)
            result = analyzer.analyze(
                request=request, code_model=code_model, logs=logs, similar_cases=similar_cases
            )

        # ========== Step 7: 结果验证 ==========
        if result.code_locations and code_model:
            result.code_locations = self._verify_locations(result.code_locations, code_model)

        # ========== Step 8: 知识闭环 ==========
        if self.config.enable_auto_save_case and result.confidence > CONFIDENCE_HIGH_THRESHOLD:
            self._save_case(result, request)

        return result

    # ----------------------------------------------------------------
    # 服务调用图
    # ----------------------------------------------------------------

    def _build_service_call_graph(self):
        """构建跨服务调用图（从服务注册表依赖推断）"""
        for service_name, service_info in self.registry.services.items():
            for dep in service_info.dependencies:
                if service_name not in self.service_call_graph:
                    self.service_call_graph[service_name] = []
                self.service_call_graph[service_name].append(dep)

    # ----------------------------------------------------------------
    # 跨服务分析
    # ----------------------------------------------------------------

    def _analyze_cross_service(self,
                               request: AnalysisRequest,
                               call_chain: List[str],
                               route_result: RouteResult) -> AnalysisResult:
        """跨服务调用链分析"""

        service_logs: Dict[str, List[LogEvent]] = {}
        service_models: Dict[str, CodeModel] = {}

        for service in call_chain:
            repo = self.registry.get_repo(service)
            if not repo:
                continue

            if request.trace_id and self.sls:
                logs = self.sls.query_logs(query=f"service: {service} AND trace_id: {request.trace_id}")
            else:
                logs = []
            service_logs[service] = logs

            if repo in self.code_models:
                service_models[service] = self.code_models[repo]
            else:
                model = self._parse_code(repo)
                if model:
                    service_models[service] = model
                    self.code_models[repo] = model

        consistency_issues = self._check_cross_service_consistency(call_chain, service_logs, request)

        error_service = None
        for issue in consistency_issues:
            if issue['type'] == 'state_inconsistency':
                error_service = issue['service']
                break

        if error_service and error_service in service_models:
            error_model = service_models[error_service]
            error_logs = service_logs.get(error_service, [])
            analyzer = _FallbackAnalyzer()
            result = analyzer.analyze(
                request=AnalysisRequest(
                    error_desc=f"[跨服务问题] {request.error_desc}，问题出现在 {error_service}",
                    domain=request.domain,
                    repo_path=self.registry.get_repo(error_service),
                    trace_id=request.trace_id,
                    time_range=request.time_range
                ),
                code_model=error_model,
                logs=error_logs,
                similar_cases=[]
            )
            result.root_cause = f"[跨服务调用链] {call_chain}\n\n{result.root_cause}"
            return result

        return AnalysisResult(
            problem_type=ProblemType.B1,
            root_cause=f"涉及多服务调用链: {call_chain}\n请检查各服务的状态同步逻辑",
            code_locations=[],
            fix_suggestion="建议按调用链顺序检查:\n" + '\n'.join(f"  - {s}" for s in call_chain),
            confidence=CROSS_SERVICE_CONFIDENCE,
            timeline=self._build_cross_service_timeline(call_chain, service_logs)
        )

    def _check_cross_service_consistency(self,
                                          call_chain: List[str],
                                          service_logs: Dict[str, List[LogEvent]],
                                          request: AnalysisRequest) -> List[Dict]:
        """检查跨服务状态一致性"""
        issues = []

        for i in range(len(call_chain) - 1):
            caller = call_chain[i]
            callee = call_chain[i + 1]

            caller_info = self.registry.get_service(caller)
            if caller_info and callee in caller_info.dependencies:
                shared_tables = set(caller_info.db_tables) & set(
                    self.registry.get_service(callee).db_tables if self.registry.get_service(callee) else []
                )
                if shared_tables:
                    issues.append({
                        'type': 'shared_data',
                        'caller': caller,
                        'callee': callee,
                        'tables': list(shared_tables),
                        'description': f"{caller} 和 {callee} 共享数据表 {shared_tables}，可能存在状态不一致"
                    })

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
        """构建跨服务调用时间线"""
        all_logs = []
        for service, logs in service_logs.items():
            for log in logs:
                all_logs.append((service, log))

        all_logs.sort(key=lambda x: x[1].timestamp)

        return [
            {
                'timestamp': str(log.timestamp),
                'service': service,
                'operation': log.message[:100],
                'status': log.level,
                'trace_id': log.trace_id
            }
            for service, log in all_logs
        ]

    def _get_call_chain_from_trace(self, trace_id: str) -> List[str]:
        """从 traceId 获取调用链"""
        if not self.sls:
            return []
        logs = self.sls.extract_trace_chain(trace_id)
        chain = []
        for log in logs:
            if log.service and log.service not in chain:
                chain.append(log.service)
        return chain

    # ----------------------------------------------------------------
    # 代码解析
    # ----------------------------------------------------------------

    def _parse_code(self, repo_path: str) -> Optional[CodeModel]:
        """解析代码（带缓存）"""
        if repo_path in self.code_models:
            return self.code_models[repo_path]

        language = self._detect_language(repo_path)
        if not language:
            return None

        adapter = self.adapters.get(language)
        if not adapter:
            return None

        try:
            code_model = adapter.parse_repo(repo_path)
            self.code_models[repo_path] = code_model
            return code_model
        except Exception as e:
            logger.warning("代码解析失败: %s", e)
            return None

    def _detect_language(self, repo_path: str) -> Optional[str]:
        """检测仓库语言（带缓存，避免重复 rglob 遍历）"""
        if repo_path in self._language_cache:
            return self._language_cache[repo_path]

        repo = Path(repo_path)

        for service in self.registry.services.values():
            if service.repo_url == repo_path or repo_path in service.repo_url:
                self._language_cache[repo_path] = service.language
                return service.language

        java_count = len(list(repo.rglob("*.java")))
        go_count = len(list(repo.rglob("*.go")))
        ts_count = len(list(repo.rglob("*.ts"))) + len(list(repo.rglob("*.tsx")))

        counts = {'java': java_count, 'go': go_count, 'typescript': ts_count}
        max_lang = max(counts, key=counts.get)
        result = max_lang if counts[max_lang] > 0 else None
        self._language_cache[repo_path] = result
        return result

    def _get_changed_files(self, repo_path: str, base_branch: Optional[str] = None) -> List[str]:
        """自动获取 git diff 变更文件列表"""
        try:
            candidates = [base_branch] if base_branch else list(CHANGED_FILES_BASE_BRANCHES)
            for base in candidates:
                result = subprocess.run(
                    ['git', 'diff', f'{base}...HEAD', '--name-only', '--diff-filter=AM'],
                    cwd=repo_path,
                    capture_output=True,
                    text=True,
                    timeout=CHANGED_FILES_TIMEOUT,
                )
                if result.returncode == 0 and result.stdout.strip():
                    files = [f.strip() for f in result.stdout.strip().splitlines() if f.endswith(CHANGED_FILE_EXTENSIONS)]
                    if files:
                        return files
        except subprocess.TimeoutExpired:
            logger.debug("git diff timed out for %s", repo_path)
        except Exception as e:
            logger.debug("git diff failed for %s: %s", repo_path, e)
        return []

    def _enrich_request_with_diff(self,
                                   request: AnalysisRequest,
                                   changed_files: List[str],
                                   repo_path: str) -> AnalysisRequest:
        """用 git diff 信息增强请求"""
        from dataclasses import replace

        module_hints = set()
        for f in changed_files[:20]:
            stem = Path(f).stem
            if stem not in CHANGED_FILES_EXCLUDED_MODULES:
                module_hints.add(stem)

        hint_str = ' '.join(sorted(module_hints)[:CHANGED_FILES_MAX_HINTS])
        enriched_desc = request.error_desc
        if hint_str and hint_str not in request.error_desc:
            enriched_desc = f"[变更模块: {hint_str}] {request.error_desc}"

        return replace(request, changed_files=changed_files, error_desc=enriched_desc)

    # ----------------------------------------------------------------
    # 日志与分类
    # ----------------------------------------------------------------

    def _get_logs(self, request: AnalysisRequest, route_result: RouteResult) -> List[LogEvent]:
        """获取日志"""
        if not self.sls:
            return []
        if request.trace_id:
            return self.sls.extract_trace_chain(request.trace_id)
        if request.time_range:
            keywords = route_result.matched_keywords or []
            return self.sls.find_error_events(time_range=request.time_range, keywords=keywords)
        return []

    def _classify_problem(self,
                          request: AnalysisRequest,
                          logs: List[LogEvent],
                          fallback: Optional[ProblemType] = None) -> ProblemType:
        """问题分类。

        先看运行时证据（日志/堆栈），再看用户文本。这样即使没有接入 SLS，
        CLI 直接粘贴 panic/NPE/Traceback 也能走 A1，而不是被归到 B2/C1。
        """
        if logs and any(l.stack_trace for l in logs):
            return ProblemType.A1
        if logs and any(l.level == "ERROR" for l in logs):
            return ProblemType.A2

        full_text = " ".join(filter(None, [
            request.error_desc,
            request.expected_behavior,
            request.actual_behavior,
        ]))

        stack_patterns = (
            r'panic:',
            r'nil pointer dereference',
            r'NullPointerException',
            r'Traceback \(most recent call last\)',
            r'Cannot read (?:properties|property) of undefined',
            r'\S+\.(?:go|java|py|ts|tsx|js|jsx):\d+',
            r'\.py", line \d+',
        )
        if any(re.search(pattern, full_text, re.IGNORECASE) for pattern in stack_patterns):
            return ProblemType.A1

        data_keywords = (
            "状态不一致", "数据不一致", "金额不对", "数量不对", "缺数据",
            "未更新", "重复扣", "重复支付", "订单状态", "会员状态",
        )
        if any(kw in full_text for kw in data_keywords):
            return ProblemType.B1

        # C1 关键词：包含「应当/应该/预期」等期望词，或「不符合/不符/不对」等否定词，
        # 以及「没有/未/没」等缺失词（X 成功但 Y 没发生 = 逻辑偏差典型模式）
        c1_keywords = (
            "预期", "应该", "应当", "不符合", "不符", "不对",
            "没有", "未", "没回", "没调", "没成", "没更", "没执",
        )
        if any(kw in full_text for kw in c1_keywords):
            return ProblemType.C1

        error_keywords = ("ERROR", "Exception", "失败", "报错", "错误", "error")
        if any(kw.lower() in full_text.lower() for kw in error_keywords):
            return ProblemType.A2

        return fallback or ProblemType.B2

    def _is_infrastructure_issue(self, request: AnalysisRequest) -> bool:
        """识别网络、DNS、证书、下游不可用等非业务代码优先的问题。"""
        text = " ".join(filter(None, [
            request.error_desc,
            request.expected_behavior,
            request.actual_behavior,
        ])).lower()
        patterns = (
            "connection refused", "connection reset", "i/o timeout", "deadline exceeded",
            "no route to host", "dns", "tls handshake", "certificate",
            "502", "503", "504", "gateway timeout", "service unavailable",
        )
        return any(p in text for p in patterns)

    # ----------------------------------------------------------------
    # 知识增强
    # ----------------------------------------------------------------

    def _search_similar_cases(self, request: AnalysisRequest) -> List[Dict]:
        """搜索相似历史案例（使用域级 KB 配置）"""
        if not self.weknora:
            return []

        kb_ids = list(self._resolve_kb_ids(request.domain).values())
        if not kb_ids:
            return []

        try:
            return self.weknora.search_knowledge(
                query=request.error_desc,
                kb_ids=kb_ids,
                top_k=CONFIDENCE_TOP_K_SEARCH
            )
        except Exception as e:
            logger.debug("相似案例查询失败，跳过知识增强: %s", e)
            return []

    def _is_exact_match(self, case: Dict, desc: str) -> bool:
        """判断是否完全匹配历史案例。

        关键修复：知识库返回的 score 是语义相关度分数（通常全是 1.0），
        不代表案例内容与用户描述相同。必须检查案例内容本身是否与 desc 匹配。

        中文场景下使用编辑距离相似度 + 模糊子串匹配 + 关键标识符校验。
        """
        case_content = case.get('content', '')
        if not desc or not case_content:
            return False

        # 策略1：如果 desc 几乎完整地包含在 case_content 中（模糊子串匹配）
        fuzzy_substring_sim = self._fuzzy_substring_similarity(desc, case_content)
        if fuzzy_substring_sim >= 0.9:
            if not self._has_conflicting_identifiers(desc, case_content):
                return True

        # 策略2：如果两个字符串长度接近且整体相似（编辑距离）
        len_ratio = min(len(desc), len(case_content)) / max(len(desc), len(case_content)) if max(len(desc), len(case_content)) > 0 else 0
        if len_ratio >= 0.8:
            edit_sim = self._edit_distance_similarity(desc, case_content)
            if edit_sim >= 0.9 and not self._has_conflicting_identifiers(desc, case_content):
                return True

        return False

    def _fuzzy_substring_similarity(self, pattern: str, text: str) -> float:
        """计算 pattern 在 text 中的最佳模糊子串匹配相似度。

        用滑动窗口在 text 上移动，对每个与 pattern 长度接近的子串计算编辑距离，
        返回最高相似度。适用于"描述是案例内容的前缀"的场景。
        """
        if not pattern:
            return 1.0
        if not text:
            return 0.0
        if pattern in text:
            return 1.0
        m, n = len(pattern), len(text)
        if m > n:
            pattern, text, m, n = text, pattern, n, m
        best_score = 0.0
        window = m * 2  # 搜索范围：pattern 长度的 2 倍
        if window > n:
            window = n
        for i in range(n - window + 1):
            sub = text[i:i + window]
            sim = self._edit_distance_similarity(pattern, sub)
            if sim > best_score:
                best_score = sim
                if best_score >= 0.95:
                    break
        return best_score

    def _edit_distance_similarity(self, a: str, b: str) -> float:
        """计算两字符串的编辑距离相似度（0~1，1=完全相同）。"""
        if not a and not b:
            return 1.0
        if not a or not b:
            return 0.0
        m, n = len(a), len(b)
        if m == n and a == b:
            return 1.0
        # Standard Levenshtein with O(min(m,n)) space
        if m < n:
            a, b, m, n = b, a, n, m
        prev = list(range(n + 1))
        curr = [0] * (n + 1)
        for i in range(1, m + 1):
            curr[0] = i
            for j in range(1, n + 1):
                if a[i - 1] == b[j - 1]:
                    curr[j] = prev[j - 1]
                else:
                    curr[j] = 1 + min(prev[j], curr[j - 1], prev[j - 1])
            prev, curr = curr, prev
        distance = prev[n]
        return 1.0 - distance / max(m, n)

    def _has_conflicting_identifiers(self, a: str, b: str) -> bool:
        """检查两个描述中是否有冲突的关键标识符（如 type=14 vs type=11）。"""
        nums_a = set(re.findall(r'(?:type|Type|类型|type=)(\d+)', a, re.IGNORECASE))
        nums_b = set(re.findall(r'(?:type|Type|类型|type=)(\d+)', b, re.IGNORECASE))
        if nums_a and nums_b and nums_a != nums_b:
            return True
        return False

    def _build_result_from_case(self, case: Dict, request: AnalysisRequest) -> AnalysisResult:
        """从历史案例构建结果"""
        content = case.get('content', '')
        root_cause = self._extract_from_case(content, '根因', '问题原因')
        file = self._extract_from_case(content, '文件', 'File')
        line = self._extract_from_case(content, '行号', 'Line')
        fix = self._extract_from_case(content, '修复', '解决方案')

        code_locations = []
        if file and line:
            line_match = re.search(r'(\d+)', str(line))
            line_num = int(line_match.group(1)) if line_match else 0
            code_locations.append(CodeLocation(file=file, line=line_num, verified=False))

        return AnalysisResult(
            problem_type=ProblemType.A1,
            root_cause=root_cause or "历史案例匹配",
            code_locations=code_locations,
            fix_suggestion=fix or "请参考历史案例处理",
            confidence=CASE_RESULT_CONFIDENCE,
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

    # ----------------------------------------------------------------
    # 分析器选择
    # ----------------------------------------------------------------

    def _get_domain_keywords(self, domain_name: Optional[str]) -> List[str]:
        """Collect domain keywords from service registry (cached for future use)."""
        if not self.domain_loader or not domain_name:
            return []
        registry = self.domain_loader.get_registry(domain_name)
        if not registry:
            return []
        keywords = []
        for svc in registry.services.values():
            keywords.extend(svc.keywords or [])
        return list(dict.fromkeys(keywords))  # deduplicate preserving order

    def _get_analyzer(self, problem_type: ProblemType, domain_name: Optional[str] = None):
        """
        Get analyzer for problem type and domain.

        All problem types now use _FallbackAnalyzer unless AI/skill analysis is
        available. The fallback preserves output compatibility.
        """
        if problem_type in (ProblemType.A1, ProblemType.A2):
            return self.analyzers.get(problem_type, _FallbackAnalyzer(problem_type))

        cache_key = domain_name or "__global__"
        if cache_key not in self._domain_analyzers:
            self._domain_analyzers[cache_key] = {}

        if problem_type in (ProblemType.B1, ProblemType.B2):
            if problem_type not in self._domain_analyzers[cache_key]:
                self._domain_analyzers[cache_key][problem_type] = _FallbackAnalyzer(problem_type)
            return self._domain_analyzers[cache_key][problem_type]

        if problem_type == ProblemType.C1:
            if ProblemType.C1 not in self._domain_analyzers[cache_key]:
                self._domain_analyzers[cache_key][ProblemType.C1] = _FallbackAnalyzer(ProblemType.C1)
            return self._domain_analyzers[cache_key][ProblemType.C1]

        return _FallbackAnalyzer(problem_type)

    # ----------------------------------------------------------------
    # AI Analysis Engine (new)
    # ----------------------------------------------------------------

    def _build_context_package(self,
                               request: AnalysisRequest,
                               route_result: RouteResult,
                               code_model: Optional[CodeModel],
                               logs: List[LogEvent],
                               similar_cases: List[Dict],
                               problem_type: ProblemType) -> ContextPackage:
        """Build ContextPackage for AI analysis from pipeline results."""
        # Extract crash file locations from stack trace
        crash_files = self._extract_crash_locations(request, code_model)

        # Read actual code at crash locations (±30 lines)
        code_snippets = self._read_code_snippets(
            crash_files,
            request.repo_path or route_result.primary_repo
        )

        # Get git diff
        changed_files = request.changed_files or self._get_changed_files(
            request.repo_path or route_result.primary_repo or "", request.base_branch
        )

        return ContextPackage(
            error_desc=request.error_desc,
            repo_path=request.repo_path or route_result.primary_repo,
            crash_files=crash_files,
            code_snippets=code_snippets,
            call_graph=self._get_call_chain(request, code_model),
            changed_files=changed_files,
            similar_cases=similar_cases,
            domain_rules=self._get_domain_rules(request.domain),
            expected_behavior=request.expected_behavior,
            actual_behavior=request.actual_behavior,
            problem_type=problem_type.value,
        )

    def _analyze_with_ai(self, context: ContextPackage) -> Optional[AnalysisResult]:
        """
        Analyze bug using AI with fallback chain.

        Priority: API → Skill → Rule Engine (fallback is handled by caller)
        """
        # Try API first
        api_result = self._try_api_analysis(context)
        if api_result and api_result.confidence >= 0.5:
            return api_result

        # API failed or low confidence → try skill (writes context file, user runs skill)
        if self.config.ai_fallback_to_skill:
            skill_result = self._try_skill_analysis(context)
            if skill_result:
                return skill_result

        return None  # Caller falls back to low-confidence guidance

    def _try_api_analysis(self, context: ContextPackage) -> Optional[AnalysisResult]:
        """Try AI analysis via Anthropic API."""
        try:
            import anthropic

            api_key = self.config.ai_api_key or os.environ.get("ANTHROPIC_API_KEY")
            if not api_key:
                logger.warning("No Anthropic API key available for AI analysis")
                return None

            client = anthropic.Anthropic(api_key=api_key)

            prompt = self._build_analysis_prompt(context)

            message = client.messages.create(
                model=self.config.ai_model,
                max_tokens=AI_MAX_TOKENS,
                messages=[{"role": "user", "content": prompt}],
                timeout=int(self.config.ai_timeout),
            )

            response_text = message.content[0].text
            result = self._parse_ai_response(response_text, context)
            logger.info("AI analysis complete: confidence=%.2f, locations=%d",
                       result.confidence, len(result.code_locations))
            return result

        except ImportError:
            logger.warning("anthropic package not installed, cannot use AI API analysis")
            return None
        except Exception as e:
            logger.warning("AI API analysis failed: %s", e)
            return None

    def _try_skill_analysis(self, context: ContextPackage) -> Optional[AnalysisResult]:
        """Write context to file for the bug-analyzer skill."""
        try:
            context_file = Path(tempfile.gettempdir()) / "bug_analysis_context.json"
            context_dict = {
                "error_desc": context.error_desc,
                "repo_path": context.repo_path,
                "crash_files": context.crash_files,
                "code_snippets": context.code_snippets,
                "changed_files": context.changed_files,
                "similar_cases": context.similar_cases,
                "expected_behavior": context.expected_behavior,
                "actual_behavior": context.actual_behavior,
                "problem_type": context.problem_type,
            }
            with open(context_file, 'w') as f:
                json_module.dump(context_dict, f, ensure_ascii=False, indent=2)
            logger.info("Context written to %s for skill analysis", context_file)
            # Note: skill must be triggered manually or by parent process
            return None  # Skill result must be fed back separately
        except Exception as e:
            logger.warning("Failed to write skill context: %s", e)
            return None

    def _build_analysis_prompt(self, context: ContextPackage) -> str:
        """Build analysis prompt for AI model."""
        parts = []
        parts.append(
            "You are a senior SRE analyzing a production bug. "
            "You have access to code snippets, git history, logs, and knowledge base cases. "
            "Provide a structured analysis in JSON format only (no other text)."
        )
        parts.append(f"\n## Bug Description\n{context.error_desc}")

        if context.code_snippets:
            parts.append("\n## Code at Crash Location")
            for file_path, snippet in context.code_snippets.items():
                parts.append(f"\n### {file_path}\n```\n{snippet}\n```")

        if context.call_graph:
            parts.append("\n## Call Graph\n" + "\n".join(context.call_graph))

        if context.changed_files:
            parts.append("\n## Recent Changes\n" + "\n".join(context.changed_files[:20]))

        if context.similar_cases:
            parts.append("\n## Similar Cases")
            for i, case in enumerate(context.similar_cases[:3]):
                content = case.get('content', '')[:300]
                parts.append(f"\nCase {i+1}:\n{content}")

        if context.domain_rules:
            parts.append("\n## Business Rules")
            for rule in context.domain_rules:
                if isinstance(rule, dict):
                    parts.append(f"- {rule.get('name', '')}: {rule.get('description', '')}")
                else:
                    parts.append(f"- {rule}")

        if context.expected_behavior:
            parts.append(f"\n## Expected Behavior\n{context.expected_behavior}")
        if context.actual_behavior:
            parts.append(f"\n## Actual Behavior\n{context.actual_behavior}")

        parts.append("""
## Task
1. Classify the bug before concluding.
2. Identify the ROOT CAUSE (not symptom).
3. Pinpoint exact CODE LOCATIONS (file:line:function).
4. Propose a MINIMAL FIX. Keep the output compatible with the schema below.
5. Assign CONFIDENCE (0.0-1.0) based on evidence strength.

Evidence rules:
- If no code snippet supports the root cause, confidence must be <= 0.30.
- If expected behavior is missing for a logic bug, confidence must be <= 0.55.
- Do not recommend business code changes for infrastructure errors.

## Output Format (JSON only, no markdown fences)
{
  "root_cause": "string",
  "code_locations": [{"file": "string", "line": int, "function": "string"}],
  "fix_suggestion": "string",
  "confidence": float,
  "thinking": "your reasoning process",
  "problem_type": "stack_trace|error_log|data_anomaly|business_anomaly|logic_error"
}
""")
        return "\n".join(parts)

    def _parse_ai_response(self, response_text: str, context: ContextPackage) -> AnalysisResult:
        """Parse AI JSON response into AnalysisResult."""
        text = response_text.strip()
        # Strip markdown fences if present
        if text.startswith("```"):
            lines = text.split("\n")
            # Remove opening fence (```json or ```) and closing fence
            if len(lines) >= 3 and lines[-1].strip().startswith("```"):
                text = "\n".join(lines[1:-1])
            else:
                text = "\n".join(lines[1:])

        try:
            data = json_module.loads(text)
        except json_module.JSONDecodeError:
            # Try to extract JSON from text
            match = re.search(r'\{[\s\S]*\}', text)
            if match:
                try:
                    data = json_module.loads(match.group(0))
                except json_module.JSONDecodeError:
                    logger.warning("Failed to parse AI response as JSON")
                    raise
            else:
                raise

        code_locations = []
        for loc in data.get("code_locations", []):
            code_locations.append(CodeLocation(
                file=loc.get("file", ""),
                line=loc.get("line", 0),
                function=loc.get("function"),
                verified=False,
            ))

        problem_type_str = data.get("problem_type", "logic_error")
        try:
            problem_type = ProblemType(problem_type_str)
        except ValueError:
            problem_type = ProblemType.C1

        confidence = data.get("confidence", 0.5)

        # Boost confidence if we have verified locations matching crash files
        if code_locations and context.crash_files:
            confidence = min(confidence + AI_CONFIDENCE_BOOST_VERIFIED, 1.0)

        # Penalty if no code locations found
        if not code_locations:
            confidence = max(confidence - AI_CONFIDENCE_PENALTY_NO_LOCATIONS, 0.0)

        return AnalysisResult(
            problem_type=problem_type,
            root_cause=data.get("root_cause", "AI analysis completed but no root cause identified"),
            code_locations=code_locations,
            fix_suggestion=data.get("fix_suggestion", ""),
            confidence=confidence,
            thinking=data.get("thinking"),
        )

    def _extract_crash_locations(self,
                                  request: AnalysisRequest,
                                  code_model: Optional[CodeModel]) -> List[Dict]:
        """Extract crash file locations from stack trace / error description."""
        locations = []
        desc = request.error_desc

        # Pattern: file.go:123 or file.java:456 or path/to/file.go:123
        pattern = r'(\S+\.(?:go|java|py|ts|tsx|js|jsx)):(\d+)'
        for match in re.finditer(pattern, desc):
            locations.append({
                'file': match.group(1),
                'line': int(match.group(2)),
                'function': None,
            })

        # If we have a code model, try to match file paths and find enclosing functions
        if code_model and locations:
            for loc in locations:
                for file_model in code_model.files:
                    if file_model.path.endswith(loc['file']) or loc['file'] in file_model.path:
                        loc['file'] = file_model.path
                        # Find function containing this line
                        for func in file_model.functions:
                            if func.start_line <= loc['line'] <= func.end_line:
                                loc['function'] = func.name
                                break
                        break

        return locations

    def _read_code_snippets(self,
                            crash_files: List[Dict],
                            repo_path: Optional[str]) -> Dict[str, str]:
        """Read actual code at crash locations (±30 lines context)."""
        snippets: Dict[str, str] = {}
        if not repo_path:
            return snippets

        context_lines = 30
        for loc in crash_files:
            file_rel = loc.get('file', '')
            if not file_rel:
                continue

            file_path = Path(repo_path) / file_rel
            if not file_path.exists():
                # Try as absolute path
                file_path = Path(file_rel)
                if not file_path.exists():
                    continue

            try:
                content = file_path.read_text(encoding='utf-8', errors='replace')
                lines = content.split('\n')
                line_num = loc['line']
                start = max(0, line_num - context_lines - 1)
                end = min(len(lines), line_num + context_lines)

                snippet_lines = []
                for i in range(start, end):
                    marker = ">>>" if i == line_num - 1 else "   "
                    snippet_lines.append(f"{marker} {i+1}: {lines[i]}")

                key = f"{file_rel}:{loc['line']}"
                if loc.get('function'):
                    key += f" ({loc['function']})"
                snippets[key] = "\n".join(snippet_lines)
            except Exception as e:
                logger.debug("Failed to read %s: %s", file_path, e)

        return snippets

    def _get_call_chain(self,
                         request: AnalysisRequest,
                         code_model: Optional[CodeModel]) -> List[str]:
        """Extract call chain from code model based on error description."""
        if not code_model:
            return []

        chain = []
        desc = request.error_desc

        # Find functions mentioned in error description
        for file_model in code_model.files:
            for func in file_model.functions:
                if func.name in desc:
                    # Trace callers
                    chain.append(f"{func.name} ({func.file}:{func.start_line})")
                    for caller in func.called_by[:5]:
                        chain.append(f"  <- {caller}")

        return chain

    # ----------------------------------------------------------------
    # 结果验证与保存
    # ----------------------------------------------------------------

    def _verify_locations(self,
                          locations: List[CodeLocation],
                          code_model: CodeModel) -> List[CodeLocation]:
        """Verify code locations pass-through (Claude analysis handles verification)."""
        return locations

    def _save_case(self, result: AnalysisResult, request: AnalysisRequest):
        """保存分析结果为知识库案例（知识闭环）"""
        if not self.weknora:
            return

        kb_ids = self._resolve_kb_ids(request.domain)
        kb_id = kb_ids.get('bug_cases')
        if not kb_id:
            return

        case_doc = self._generate_case_document(result, request)
        try:
            self.weknora.upload_text(
                kb_id=kb_id,
                content=case_doc,
                title=f"Bug案例: {result.root_cause[:50]}",
                metadata={
                    'type': 'bug_case',
                    'domain': request.domain or 'global',
                    'confidence': result.confidence,
                    'problem_type': result.problem_type.value,
                    'created_at': datetime.now().isoformat()
                }
            )
        except Exception as e:
            logger.warning("保存案例失败: %s", e)

    def _generate_case_document(self, result: AnalysisResult, request: AnalysisRequest) -> str:
        """生成 Bug 案例文档（Markdown 格式）"""
        doc = f"""# Bug案例分析报告

## 问题描述
{request.error_desc}

## 业务域
{request.domain or '未指定'}

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
        if result.timeline:
            doc += "\n## 事件时间线\n"
            for event in result.timeline:
                doc += f"- [{event.get('timestamp', '')}] {event.get('service', '')}: {event.get('operation', '')}\n"

        return doc

    def _infra_result(self, request: AnalysisRequest) -> AnalysisResult:
        """基础设施类问题短路返回，保持 AnalysisResult 输出兼容。"""
        return AnalysisResult(
            problem_type=ProblemType.A2,
            root_cause=(
                "更像基础设施或下游依赖问题，而不是已定位的业务代码逻辑 bug。"
                "当前输入包含网络、DNS、证书、网关或下游不可用信号。"
            ),
            code_locations=[],
            fix_suggestion=(
                "按顺序检查：1) 目标服务健康状态；2) host/port/DNS 是否可达；"
                "3) TLS 证书和网关配置；4) 连接池、超时、限流、熔断配置；"
                "5) 若提供 traceId/time_range，再补充日志确认失败发生在哪个下游。"
            ),
            confidence=0.4,
        )

    def _no_repo_result(self,
                        request: AnalysisRequest,
                        problem_type: Optional[ProblemType] = None) -> AnalysisResult:
        """无仓库时的结果"""
        return AnalysisResult(
            problem_type=problem_type or ProblemType.C1,
            root_cause="无法确定代码仓库",
            code_locations=[],
            fix_suggestion=(
                "请提供 --repo 代码仓库路径，或配置 --domain / config/domains.yaml 让系统自动路由。"
                "如果是逻辑偏差，建议同时提供 --expected 和 --actual。"
            ),
            confidence=0.2 if problem_type else 0.0
        )
