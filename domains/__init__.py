"""
domains — 业务域隔离层

每个子目录对应一条业务线，独立存放：
  - domain.yaml   : 域元信息
  - services.yaml : 服务/仓库注册表
  - rules.yaml    : 业务规则

通过 DomainLoader 统一扫描加载，供 BugAnalysisWorkflow 按域路由。
"""

from .base import DomainConfig
from .loader import DomainLoader

__all__ = ["DomainConfig", "DomainLoader"]
