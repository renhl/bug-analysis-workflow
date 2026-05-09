"""
Bug Analysis Workflow - 知识库增强的代码级问题定位系统

一套完整的问题分析 Workflow，能够定位问题到代码行级别，
支持 Java/Go/前端项目，集成 WeKnora 知识库实现知识积累和相似案例匹配
"""

from .core.models import (
    AnalysisRequest, AnalysisResult, ProblemType,
    CodeLocation, CodeModel, BugAnalysisConfig
)
from .core.workflow import BugAnalysisWorkflow
from .core.registry import ServiceRegistry, load_registry_from_yaml

__version__ = "1.0.0"
__all__ = [
    "BugAnalysisWorkflow",
    "AnalysisRequest",
    "AnalysisResult",
    "ProblemType",
    "CodeLocation",
    "CodeModel",
    "BugAnalysisConfig",
    "ServiceRegistry",
    "load_registry_from_yaml",
]