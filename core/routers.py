"""
问题路由器 - 确定问题涉及的代码仓库
"""

import re
from typing import List, Dict, Optional
from .models import RouteResult, AnalysisRequest
from .registry import ServiceRegistry
from .constants import (
    KEYWORD_ROUTE_BASE_CONFIDENCE, KEYWORD_ROUTE_PER_KEYWORD_BONUS,
    KEYWORD_ROUTE_MAX_CONFIDENCE, TRACE_ROUTE_CONFIDENCE,
    KB_ROUTE_CONFIDENCE, ROUTE_MERGE_BONUS, RELATED_REPO_WEIGHT,
    MAX_RELATED_REPOS, TRACE_ROUTE_THRESHOLD,
    CONFIDENCE_LOW_THRESHOLD,
)


class KeywordRouter:
    """
    关键词路由器
    
    从问题描述中提取业务关键词，匹配到服务
    """
    
    def __init__(self, registry: ServiceRegistry):
        self.registry = registry
    
    def route(self, problem_desc: str) -> RouteResult:
        """
        从问题描述提取关键词并路由
        """
        
        # Step 1: 提取关键词
        keywords = self._extract_keywords(problem_desc)
        
        # Step 2: 多关键词搜索，得到服务得分
        service_scores = self.registry.search_by_keywords(keywords)
        
        # Step 3: 按得分排序
        sorted_services = sorted(
            service_scores.items(),
            key=lambda x: x[1],
            reverse=True
        )
        
        # Step 4: 映射到仓库
        repositories = []
        for service_name, score in sorted_services:
            repo = self.registry.get_repo(service_name)
            if repo:
                repositories.append((service_name, repo, score))
        
        # Step 5: 确定主仓库和关联仓库
        if repositories:
            primary_service, primary_repo, primary_score = repositories[0]
            related_repos = [r[1] for r in repositories[1:]] if len(repositories) > 1 else []
        else:
            primary_repo = None
            related_repos = []
        
        # Step 6: 计算置信度
        if repositories:
            max_score = repositories[0][2]
            confidence = min(KEYWORD_ROUTE_BASE_CONFIDENCE + max_score * KEYWORD_ROUTE_PER_KEYWORD_BONUS, KEYWORD_ROUTE_MAX_CONFIDENCE)
        else:
            confidence = 0.0
        
        return RouteResult(
            primary_repo=primary_repo,
            related_repos=related_repos,
            confidence=confidence,
            matched_keywords=keywords
        )
    
    def _extract_keywords(self, desc: str) -> List[str]:
        """
        提取业务关键词
        """
        keywords = []
        if not desc:
            return keywords
        desc_lower = desc.lower()
        
        # 直接匹配注册表中的关键词
        for keyword in self.registry.all_keywords():
            if keyword in desc_lower:
                keywords.append(keyword)
        
        return keywords


class LogRouter:
    """
    日志路由器
    
    从 traceId 提取完整调用链，自动识别涉及的服务
    """
    
    def __init__(self, registry: ServiceRegistry, sls_connector=None):
        self.registry = registry
        self.sls_connector = sls_connector
    
    def route(self, trace_id: str) -> RouteResult:
        """
        从阿里云 SLS 查询 traceId 的完整链路
        """
        
        if not self.sls_connector:
            return RouteResult(confidence=0.0)
        
        # Step 1: 查询 traceId 的所有日志
        logs = self.sls_connector.query_logs(
            query=f"trace_id: {trace_id}"
        )
        
        if not logs:
            return RouteResult(confidence=0.0)
        
        # Step 2: 提取涉及的服务
        services = set()
        for log in logs:
            if log.service:
                services.add(log.service)
        
        # Step 3: 构建调用链
        call_chain = self._build_call_chain(logs)
        
        # Step 4: 找到错误点（如果有的话）
        error_service = self._find_error_service(logs)
        primary_service = error_service or (call_chain[-1] if call_chain else None)
        
        # Step 5: 映射到仓库
        repositories = []
        for service in services:
            repo = self.registry.get_repo(service)
            if repo:
                repositories.append((service, repo))
        
        primary_repo = None
        related_repos = []
        for service, repo in repositories:
            if service == primary_service:
                primary_repo = repo
            else:
                related_repos.append(repo)
        
        return RouteResult(
            primary_repo=primary_repo,
            related_repos=related_repos,
            confidence=TRACE_ROUTE_CONFIDENCE,  # traceId 路由置信度最高
            call_chain=call_chain
        )
    
    def _build_call_chain(self, logs) -> List[str]:
        """
        从日志重建调用链
        """
        # 按时间排序
        sorted_logs = sorted(logs, key=lambda l: l.timestamp)
        
        # 提取服务调用顺序
        chain = []
        for log in sorted_logs:
            if log.service and log.service not in chain:
                chain.append(log.service)
        
        return chain
    
    def _find_error_service(self, logs) -> Optional[str]:
        """
        找到出错的服务
        """
        for log in logs:
            if log.level == "ERROR" or log.stack_trace:
                return log.service
        return None


class KnowledgeBaseRouter:
    """
    知识库路由器
    
    利用 WeKnora 知识库理解业务上下文
    """
    
    def __init__(self, registry: ServiceRegistry, weknora_connector=None):
        self.registry = registry
        self.weknora = weknora_connector
    
    def route(self, problem_desc: str, kb_ids: List[str] = None) -> RouteResult:
        """
        查询知识库获取业务上下文
        """
        
        if not self.weknora:
            return RouteResult(confidence=0.0)
        
        kb_ids = kb_ids or []

        # Step 1: 搜索系统架构文档
        try:
            system_docs = self.weknora.search_knowledge(
                query=problem_desc,
                kb_ids=kb_ids,
                top_k=3
            )
        except Exception:
            return RouteResult(confidence=0.0)
        
        # Step 2: 从文档中提取服务名
        services = self._extract_services_from_docs(system_docs)
        
        # Step 3: 映射到仓库
        repositories = []
        for service in services:
            repo = self.registry.get_repo(service)
            if repo and repo not in [r[1] for r in repositories]:
                repositories.append((service, repo))
        
        primary_repo = repositories[0][1] if repositories else None
        related_repos = [r[1] for r in repositories[1:]] if len(repositories) > 1 else []
        
        return RouteResult(
            primary_repo=primary_repo,
            related_repos=related_repos,
            confidence=KB_ROUTE_CONFIDENCE,
            knowledge_context={"system_docs": system_docs}
        )
    
    def _extract_services_from_docs(self, docs) -> List[str]:
        """
        从文档中提取服务名
        """
        services = []
        
        for doc in docs:
            content = doc.get("content", "")
            
            # 搜索文档中提到的服务名
            for service in self.registry.all_services():
                if service in content:
                    services.append(service)
        
        return services


class CompositeRouter:
    """
    组合路由器
    
    按优先级尝试三种策略，合并结果
    """
    
    def __init__(self, registry: ServiceRegistry, 
                 sls_connector=None, weknora_connector=None):
        self.registry = registry
        self.log_router = LogRouter(registry, sls_connector)
        self.keyword_router = KeywordRouter(registry)
        self.kb_router = KnowledgeBaseRouter(registry, weknora_connector)
    
    def route(self, request: AnalysisRequest, kb_ids: List[str] = None) -> RouteResult:
        """
        路由决策流程
        """
        
        # 策略1: 如果有 traceId，优先用日志路由（最准确）
        if request.trace_id:
            result = self.log_router.route(request.trace_id)
            if result.confidence > TRACE_ROUTE_THRESHOLD:
                return result
        
        # 策略2: 关键词路由
        keyword_result = self.keyword_router.route(request.error_desc)

        # 策略3: 知识库路由
        try:
            kb_result = self.kb_router.route(request.error_desc, kb_ids)
        except Exception:
            kb_result = RouteResult(confidence=0.0)

        # 策略4: 合并结果
        merged = self._merge_results(keyword_result, kb_result)

        # 策略5: 如果仍有不确定性，生成问题
        if merged.confidence < CONFIDENCE_LOW_THRESHOLD or not merged.primary_repo:
            merged.needs_user_input = True
            merged.question = self._generate_question(merged)
        
        return merged
    
    def _merge_results(self, r1: RouteResult, r2: RouteResult) -> RouteResult:
        """
        合并两个路由结果
        """
        
        # 仓库得分累加
        repo_scores: Dict[str, float] = {}
        
        if r1.primary_repo:
            repo_scores[r1.primary_repo] = repo_scores.get(r1.primary_repo, 0) + r1.confidence
        for repo in r1.related_repos:
            repo_scores[repo] = repo_scores.get(repo, 0) + r1.confidence * RELATED_REPO_WEIGHT
        
        if r2.primary_repo:
            repo_scores[r2.primary_repo] = repo_scores.get(r2.primary_repo, 0) + r2.confidence
        for repo in r2.related_repos:
            repo_scores[repo] = repo_scores.get(repo, 0) + r2.confidence * RELATED_REPO_WEIGHT
        
        # 排序
        sorted_repos = sorted(
            repo_scores.items(),
            key=lambda x: x[1],
            reverse=True
        )
        
        # 取最高得分作为主仓库
        primary_repo = sorted_repos[0][0] if sorted_repos else None
        related_repos = [r[0] for r in sorted_repos[1:]] if len(sorted_repos) > 1 else []
        
        # 合置信度（两方都为 0 时跳过 bonus，避免虚假信号）
        base = (r1.confidence + r2.confidence) / 2
        confidence = base + ROUTE_MERGE_BONUS if base > 0 else 0.0
        confidence = min(confidence, 1.0)
        
        return RouteResult(
            primary_repo=primary_repo,
            related_repos=related_repos[:MAX_RELATED_REPOS],  # 最多 MAX_RELATED_REPOS 个关联仓库
            confidence=confidence,
            matched_keywords=r1.matched_keywords,
            knowledge_context=r2.knowledge_context
        )
    
    def _generate_question(self, result: RouteResult) -> str:
        """
        生成用户确认问题
        """
        related = result.related_repos or ["未检测到"]
        
        return f"""
检测到以下可能相关的服务/仓库:
{chr(10).join(f'- {r}' for r in related)}

无法确定主要问题所在的仓库，请提供:
1. 代码仓库地址（本地路径或 Git URL）
2. 或者 traceId（可以从日志中获取）

如果有其他信息（如服务名、具体报错），也可以补充。
"""