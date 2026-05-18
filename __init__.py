"""Evidence-first bug root-cause analysis workflow.

Typical usage:

    from bug_analysis_workflow import (
        AnalysisRequest,
        BugAnalysisWorkflow,
        load_config_from_yaml,
    )

    config = load_config_from_yaml("config/config.yaml")
    config.domains_dir = "domains"
    workflow = BugAnalysisWorkflow(config)

    result = workflow.analyze(AnalysisRequest(
        error_desc="会员开通失败，支付成功，但订单状态还是支付中",
        domain="go_member",
        repo_path="/path/to/go_member",
        expected_behavior="支付成功后订单状态应更新为 10",
        actual_behavior="payment_slip.state 仍为 7",
    ))
"""

try:
    from .core.models import (
        AnalysisRequest, AnalysisResult, ProblemType,
        CodeLocation, CodeModel, BugAnalysisConfig, ServiceInfo, DatabaseConfig,
    )
    from .core.workflow import BugAnalysisWorkflow, load_config_from_yaml
    from .core.registry import ServiceRegistry, load_registry_from_yaml
    from .domains import DomainConfig, DomainLoader
except ImportError:
    # When running from source tree (e.g. pytest), relative imports fail.
    # The package should be installed (pip install -e .) for these imports.
    pass

__version__ = "2.1.0"

__all__ = [
    # 工作流
    "BugAnalysisWorkflow",
    "load_config_from_yaml",
    # 模型
    "AnalysisRequest",
    "AnalysisResult",
    "ProblemType",
    "CodeLocation",
    "CodeModel",
    "BugAnalysisConfig",
    "DatabaseConfig",
    "ServiceInfo",
    # 注册表
    "ServiceRegistry",
    "load_registry_from_yaml",
    # 域
    "DomainConfig",
    "DomainLoader",
]
