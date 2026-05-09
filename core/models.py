"""
Bug Analysis Workflow - 核心模块
"""

from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any
from datetime import datetime
from enum import Enum


class ProblemType(Enum):
    """问题类型分类"""
    A1 = "stack_trace"      # 有堆栈，直接定位
    A2 = "error_log"        # 有ERROR日志，需分析
    B1 = "data_anomaly"     # 数据异常
    B2 = "business_anomaly" # 业务异常
    C1 = "logic_error"      # 逻辑偏差


@dataclass
class AnalysisRequest:
    """分析请求"""
    error_desc: str                           # 问题描述（必填）
    repo_path: Optional[str] = None           # 代码仓库路径（可选，路由自动确定）
    trace_id: Optional[str] = None            # traceId（可选）
    time_range: Optional[tuple] = None        # 时间范围（可选）
    db_query: Optional[str] = None            # 数据库查询（可选）
    expected_behavior: Optional[str] = None   # 预期行为（可选，用于 Type C）


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
                if keyword in func.code_snippet or keyword in func.name:
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
    language: str                  # 语言: java
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
class BugAnalysisConfig:
    """配置"""
    # 服务注册表
    registry_path: str
    
    # WeKnora
    weknora_base_url: str
    weknora_api_key: str
    weknora_kb_ids: Dict[str, str]
    
    # 阿里云 SLS
    sls_access_key: Optional[str] = None
    sls_secret: Optional[str] = None
    sls_endpoint: Optional[str] = None
    sls_project: Optional[str] = None
    
    # 数据库
    db_host: Optional[str] = None
    db_port: Optional[int] = None
    db_user: Optional[str] = None
    db_password: Optional[str] = None
    db_name: Optional[str] = None
    
    # 其他
    confidence_threshold: float = 0.5
    max_related_repos: int = 5
    enable_auto_save_case: bool = True