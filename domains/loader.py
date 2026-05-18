"""
业务域加载器

扫描 domains/ 目录，发现并加载各业务域的配置、服务注册表和业务规则。
支持热加载（reload）和按域名查询。
"""

import logging
import yaml
from pathlib import Path
from typing import Dict, List, Optional

from .base import DomainConfig
from core.registry import ServiceRegistry, load_registry_from_yaml

logger = logging.getLogger(__name__)


class DomainLoader:
    """
    业务域加载器

    使用方式：
        loader = DomainLoader("domains/")
        loader.load_all()

        # 获取特定域的注册表
        registry = loader.get_registry("go_member")

        # 获取特定域的业务规则
        rules = loader.get_rules("go_member")

        # 获取所有域合并的注册表（全局路由使用）
        merged = loader.get_merged_registry()
    """

    def __init__(self, domains_dir: str):
        self.domains_dir = Path(domains_dir)
        self._domains: Dict[str, DomainConfig] = {}
        self._registries: Dict[str, ServiceRegistry] = {}
        self._rules: Dict[str, List[Dict]] = {}

    # ------------------------------------------------------------------
    # 加载
    # ------------------------------------------------------------------

    def load_all(self) -> "DomainLoader":
        """扫描 domains/ 目录，加载所有启用的业务域。"""
        if not self.domains_dir.exists():
            return self

        for domain_dir in sorted(self.domains_dir.iterdir()):
            if not domain_dir.is_dir():
                continue
            if domain_dir.name.startswith(("_", ".")):
                continue
            domain_yaml = domain_dir / "domain.yaml"
            if domain_yaml.exists():
                self._load_domain(domain_dir)

        return self

    def _load_domain(self, domain_dir: Path):
        """加载单个业务域目录。"""
        domain_yaml = domain_dir / "domain.yaml"
        try:
            raw = yaml.safe_load(domain_yaml.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("跳过 %s：domain.yaml 解析失败 — %s", domain_dir.name, e)
            return

        domain = DomainConfig(
            name=raw.get("name", domain_dir.name),
            description=raw.get("description", ""),
            owner=raw.get("owner", ""),
            enabled=raw.get("enabled", True),
            weknora_kb_ids=raw.get("weknora_kb_ids", {}),
            database_key=raw.get("database_key", ""),
            domain_dir=str(domain_dir.resolve()),
        )

        if not domain.enabled:
            return

        self._domains[domain.name] = domain

        # 加载服务注册表
        services_yaml = domain_dir / "services.yaml"
        if services_yaml.exists():
            try:
                self._registries[domain.name] = load_registry_from_yaml(str(services_yaml))
            except Exception as e:
                logger.warning("%s: services.yaml 加载失败 — %s", domain.name, e)
                self._registries[domain.name] = ServiceRegistry()
        else:
            self._registries[domain.name] = ServiceRegistry()

        # 加载业务规则
        rules_yaml = domain_dir / "rules.yaml"
        if rules_yaml.exists():
            try:
                raw_rules = yaml.safe_load(rules_yaml.read_text(encoding="utf-8"))
                self._rules[domain.name] = raw_rules.get("rule_sets", [])
            except Exception as e:
                logger.warning("%s: rules.yaml 加载失败 — %s", domain.name, e)
                self._rules[domain.name] = []
        else:
            self._rules[domain.name] = []

    def reload(self) -> "DomainLoader":
        """清空并重新加载所有域（配置变更后调用）。"""
        self._domains.clear()
        self._registries.clear()
        self._rules.clear()
        return self.load_all()

    # ------------------------------------------------------------------
    # 查询接口
    # ------------------------------------------------------------------

    def list_domains(self) -> List[str]:
        """返回所有已加载的域名列表。"""
        return list(self._domains.keys())

    def get_domain(self, name: str) -> Optional[DomainConfig]:
        """获取域配置。"""
        return self._domains.get(name)

    def get_registry(self, domain_name: str) -> Optional[ServiceRegistry]:
        """获取指定域的服务注册表。"""
        return self._registries.get(domain_name)

    def get_rules(self, domain_name: str) -> List[Dict]:
        """获取指定域的业务规则集（rule_sets 列表）。"""
        return self._rules.get(domain_name, [])

    def get_merged_registry(self) -> ServiceRegistry:
        """
        合并所有域的服务注册表，用于全局路由。

        注意：不同域若注册了同名服务，后加载的域会覆盖先前的。
        """
        merged = ServiceRegistry()
        for domain_name, registry in self._registries.items():
            for service in registry.services.values():
                merged.register(service)
        return merged

    def get_kb_ids(self, domain_name: str) -> Dict[str, str]:
        """获取指定域的 WeKnora 知识库 ID 映射。"""
        domain = self._domains.get(domain_name)
        return domain.weknora_kb_ids if domain else {}

    def resolve_kb_ids(self, domain_name: Optional[str], global_kb_ids: Dict[str, str]) -> Dict[str, str]:
        """
        解析知识库 ID：域级非空配置优先，空值则回退到全局配置。

        Args:
            domain_name: 域名，None 时直接返回全局配置
            global_kb_ids: 全局 BugAnalysisConfig 中的 weknora_kb_ids
        """
        if not domain_name:
            return global_kb_ids or {}

        domain_kb_ids = self.get_kb_ids(domain_name)
        global_ids = global_kb_ids or {}

        # 域级非空值优先；域级为空时回退到全局
        merged = dict(global_ids)
        for key, value in domain_kb_ids.items():
            if value:
                merged[key] = value
        return merged

    def __repr__(self) -> str:
        domains = ", ".join(self._domains.keys()) or "（无）"
        return f"DomainLoader(domains_dir={self.domains_dir}, loaded=[{domains}])"
