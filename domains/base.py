"""
业务域基础数据类

每个业务域（domain）对应一个独立目录，包含：
- domain.yaml  : 域元信息（名称、负责人、开关等）
- services.yaml: 该域下的服务/仓库列表
- rules.yaml   : 该域特有的业务规则（用于 Type-C 逻辑推理）

数据库配置说明：
- domain.yaml 中通过 database_key 指定使用哪个数据库
- 实际连接信息在 config/config.yaml 的 databases 字段中按 key 索引
- 这样敏感信息（密码）只在 config/config.yaml（已 gitignore），域配置文件可安全提交
"""

from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass
class DomainConfig:
    """业务域配置"""

    # 域唯一标识（对应目录名）
    name: str

    # 人类可读描述
    description: str = ""

    # 负责团队或负责人
    owner: str = ""

    # 是否启用，False 时跳过加载
    enabled: bool = True

    # 该域对应的 WeKnora 知识库 ID 映射
    # 例如: {"bug_cases": "kb-00000001", "business_rules": "kb-00000002"}
    # 留空的 key 会自动回退到 config.yaml 中的全局配置
    weknora_kb_ids: Dict[str, str] = field(default_factory=dict)

    # 数据库配置 key，引用 config/config.yaml 中 databases.<key> 的连接信息
    # 例如: database_key: go_member  → 使用 config.databases["go_member"]
    # 留空则不启用数据库查询能力
    database_key: str = ""

    # 域目录的绝对路径（由 DomainLoader 自动填充）
    domain_dir: str = ""

    @property
    def services_yaml(self) -> Optional[str]:
        """服务注册表文件路径"""
        if not self.domain_dir:
            return None
        from pathlib import Path
        p = Path(self.domain_dir) / "services.yaml"
        return str(p) if p.exists() else None

    @property
    def rules_yaml(self) -> Optional[str]:
        """业务规则文件路径"""
        if not self.domain_dir:
            return None
        from pathlib import Path
        p = Path(self.domain_dir) / "rules.yaml"
        return str(p) if p.exists() else None
