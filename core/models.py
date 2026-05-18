"""
Bug Analysis Workflow - 核心数据模型
"""

from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any
from datetime import datetime
from enum import Enum

# ── Default agent ID (shared across connectors) ──
DEFAULT_AGENT_ID = "builtin-smart-reasoning"


class ProblemType(Enum):
    """问题类型分类"""
    A1 = "stack_trace"      # 有堆栈，直接定位
    A2 = "error_log"        # 有 ERROR 日志，需分析
    B1 = "data_anomaly"     # 数据异常
    B2 = "business_anomaly" # 业务异常
    C1 = "logic_error"      # 逻辑偏差


@dataclass
class AnalysisRequest:
    """分析请求"""
    error_desc: str                                # 问题描述（必填）
    domain: Optional[str] = None                  # 业务域名称（限定分析范围，如 go_member）
    repo_path: Optional[str] = None               # 代码仓库路径（可选，路由自动确定）
    trace_id: Optional[str] = None                # traceId（可选）
    time_range: Optional[tuple] = None            # 时间范围（可选）
    db_query: Optional[str] = None                # 数据库查询（可选）
    expected_behavior: Optional[str] = None       # 预期行为（可选，用于 Type C）
    actual_behavior: Optional[str] = None         # 实际行为（可选，用于 Type C）
    changed_files: Optional[List[str]] = None     # 变更文件列表（可选，git diff 结果）
    base_branch: Optional[str] = None             # 基准分支（可选，用于自动获取 diff）
    related_repos: Optional[List[str]] = None     # 关联仓库路径列表（可选，跨服务分析）


@dataclass
class CodeLocation:
    """代码位置"""
    file: str
    line: int
    function: Optional[str] = None
    code_snippet: Optional[str] = None
    verified: bool = False


@dataclass
class AnalysisResult:
    """分析结果"""
    problem_type: ProblemType
    root_cause: str
    code_locations: List[CodeLocation]
    fix_suggestion: str
    confidence: float

    # 可选字段
    thinking: Optional[str] = None            # Agent 思考过程
    tool_calls: Optional[List[Dict]] = None   # 工具调用记录
    references: Optional[List[Dict]] = None   # 知识库引用
    timeline: Optional[List[Dict]] = None     # 时间线（Type B）

    # 原始数据
    raw_answer: Optional[str] = None
    matched_cases: Optional[List[Dict]] = None


@dataclass
class RouteResult:
    """路由结果"""
    primary_repo: Optional[str] = None
    related_repos: List[str] = field(default_factory=list)
    confidence: float = 0.0

    # 可选字段
    call_chain: Optional[List[str]] = None
    matched_keywords: Optional[List[str]] = None
    knowledge_context: Optional[Dict] = None
    needs_user_input: bool = False
    question: Optional[str] = None


@dataclass
class LogEvent:
    """日志事件"""
    timestamp: datetime
    level: str           # INFO/WARN/ERROR
    trace_id: str
    service: str
    message: str
    location: Optional[str] = None   # "OrderService.java:245"
    stack_trace: Optional[List[str]] = None
    extra: Dict = field(default_factory=dict)


@dataclass
class CodeModel:
    """统一代码模型"""
    language: str
    repo_path: str
    files: List['FileModel']
    call_graph: Dict[str, List[str]]  # 函数 → 调用的函数
    entry_points: List[str]           # API 入口点
    index_time: datetime

    def search_function(self, name: str) -> List['FunctionModel']:
        """搜索函数"""
        results = []
        for file in self.files:
            for func in file.functions:
                if name in func.name:
                    results.append(func)
        return results

    def search_by_keyword(self, keyword: str) -> List['FunctionModel']:
        """按关键词搜索"""
        results = []
        for file in self.files:
            for func in file.functions:
                snippet = func.code_snippet or ""
                if keyword in snippet or keyword in func.name:
                    results.append(func)
        return results


@dataclass
class FileModel:
    """文件模型"""
    path: str
    functions: List['FunctionModel']
    classes: List['ClassModel']
    imports: List[str]


@dataclass
class FunctionModel:
    """函数模型"""
    name: str
    file: str
    start_line: int
    end_line: int
    parameters: List[str]
    return_type: str
    calls: List[str]              # 调用的其他函数
    called_by: List[str]          # 被谁调用
    error_handling: List[str]     # try-catch / if err != nil
    db_operations: List[str]      # SQL/ORM 操作
    external_calls: List[str]     # HTTP/RPC 调用
    code_snippet: Optional[str] = None


@dataclass
class ClassModel:
    """类模型"""
    name: str
    file: str
    start_line: int
    end_line: int
    methods: List[str]
    annotations: List[str]


@dataclass
class ServiceInfo:
    """服务信息"""
    name: str                      # 服务名: order-service
    repo_url: str                  # 仓库地址
    language: str                  # 语言: java / go
    keywords: List[str]            # 业务关键词
    dependencies: List[str] = field(default_factory=list)        # 依赖的服务
    db_tables: List[str] = field(default_factory=list)           # 数据库表
    api_endpoints: List[str] = field(default_factory=list)       # API 端点

    # 可选
    team: Optional[str] = None
    owner: Optional[str] = None
    docs_url: Optional[str] = None


@dataclass
class DataAnomaly:
    """数据异常"""
    type: str                      # status_inconsistency, value_out_of_bounds, etc.
    description: str
    fields: List[str]
    severity: str = "medium"       # low/medium/high


@dataclass
class DatabaseConfig:
    """单个数据库连接配置"""
    host: str
    port: int
    user: str
    password: str
    db_name: str
    charset: str = "utf8mb4"
    connect_timeout: int = 10


@dataclass
class ContextPackage:
    """Complete context for AI analysis (new)"""
    error_desc: str
    repo_path: Optional[str] = None
    crash_files: List[Dict] = field(default_factory=list)
    code_snippets: Dict[str, str] = field(default_factory=dict)  # {file: content}
    call_graph: List[str] = field(default_factory=list)
    changed_files: List[str] = field(default_factory=list)
    similar_cases: List[Dict] = field(default_factory=list)
    domain_rules: List[Dict] = field(default_factory=list)
    expected_behavior: Optional[str] = None
    actual_behavior: Optional[str] = None
    problem_type: Optional[str] = None


@dataclass
class BugAnalysisConfig:
    """
    全局分析配置

    支持两种模式：
    1. 域模式（推荐）：设置 domains_dir，各业务域在 domains/<name>/ 下独立配置
    2. 传统模式：设置 registry_path，单一服务注册表

    多业务线数据库通过 databases 字段配置，key 为业务域名（如 go_member），
    value 为 DatabaseConfig。域的 domain.yaml 中通过 database_key 引用。
    """

    # WeKnora 知识库（平台级，各业务域可在 domain.yaml 中覆盖）
    weknora_base_url: str = ""
    weknora_api_key: str = ""
    weknora_kb_ids: Dict[str, str] = field(default_factory=dict)

    # 域模式（推荐）
    domains_dir: Optional[str] = None

    # 传统模式（向后兼容）
    registry_path: Optional[str] = None

    # 多业务线数据库配置（key = 业务域名，value = DatabaseConfig）
    databases: Dict[str, DatabaseConfig] = field(default_factory=dict)

    # 阿里云 SLS 日志（可选）
    sls_access_key: Optional[str] = None
    sls_secret: Optional[str] = None
    sls_endpoint: Optional[str] = None
    sls_project: Optional[str] = None

    # 分析配置
    confidence_threshold: float = 0.5
    max_related_repos: int = 5
    enable_auto_save_case: bool = True

    # AI Analysis Configuration
    ai_enabled: bool = False
    ai_api_key: Optional[str] = None
    ai_model: str = "claude-sonnet-4-20250514"
    ai_timeout: float = 30.0
    ai_fallback_to_skill: bool = True
