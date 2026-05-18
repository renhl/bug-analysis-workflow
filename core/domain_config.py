"""Multi-domain configuration loader."""
import yaml
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass, field

@dataclass
class DomainRepo:
    path: str
    language: str
    url: Optional[str] = None

@dataclass
class DomainConfig:
    name: str
    display: str
    database: Optional[str] = None
    repos: List[DomainRepo] = field(default_factory=list)
    dependencies: List[str] = field(default_factory=list)
    keywords: List[str] = field(default_factory=list)
    tables: List[str] = field(default_factory=list)
    apis: List[str] = field(default_factory=list)

class DomainConfigLoader:
    """Load multi-domain configuration from YAML."""

    def __init__(self, config_path: str):
        self.config_path = Path(config_path)
        self.domains: Dict[str, DomainConfig] = {}
        self._load()

    def _load(self):
        with open(self.config_path) as f:
            raw = yaml.safe_load(f) or {}
        for name, cfg in (raw.get('domains') or {}).items():
            repos = [
                DomainRepo(
                    path=str(Path(r.get('path', '')).expanduser()),
                    language=r.get('language', ''),
                    url=r.get('url'),
                )
                for r in cfg.get('repos', [])
            ]
            self.domains[name] = DomainConfig(
                name=name,
                display=cfg.get('display', name),
                database=cfg.get('database'),
                repos=repos,
                dependencies=cfg.get('dependencies', []),
                keywords=self._flatten_list(cfg.get('keywords', [])),
                tables=self._flatten_list(cfg.get('tables', [])),
                apis=self._flatten_list(cfg.get('apis', [])),
            )

    @staticmethod
    def _flatten_list(items):
        """Flatten comma-separated items in a list."""
        result = []
        for item in items:
            for part in str(item).split(','):
                stripped = part.strip()
                if stripped:
                    result.append(stripped)
        return result

    def get_domain(self, name: str) -> Optional[DomainConfig]:
        return self.domains.get(name)

    def list_domains(self) -> List[str]:
        return list(self.domains.keys())

    def get_all_repos(self) -> List[str]:
        """Get all repo paths across all domains."""
        paths = []
        for domain in self.domains.values():
            for repo in domain.repos:
                if repo.path not in paths:
                    paths.append(repo.path)
        return paths
