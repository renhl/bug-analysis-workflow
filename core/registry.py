"""
服务注册表 - 业务关键词 → 服务名 → 仓库地址
"""

import yaml
from pathlib import Path
from typing import List, Optional, Dict
from .models import ServiceInfo


class ServiceRegistry:
    """
    服务注册表
    
    核心数据源，用于问题路由
    """
    
    def __init__(self):
        self.services: Dict[str, ServiceInfo] = {}
        self.keyword_index: Dict[str, List[str]] = {}
        self.db_table_index: Dict[str, List[str]] = {}
    
    def register(self, service: ServiceInfo):
        """
        注册服务
        """
        self.services[service.name] = service
        
        # 建立关键词索引
        for keyword in service.keywords:
            keyword_lower = keyword.lower()
            if keyword_lower not in self.keyword_index:
                self.keyword_index[keyword_lower] = []
            if service.name not in self.keyword_index[keyword_lower]:
                self.keyword_index[keyword_lower].append(service.name)
        
        # 建立数据库表索引
        for table in service.db_tables:
            if table not in self.db_table_index:
                self.db_table_index[table] = []
            self.db_table_index[table].append(service.name)
    
    def get_service(self, name: str) -> Optional[ServiceInfo]:
        """
        获取服务信息
        """
        return self.services.get(name)
    
    def get_repo(self, service_name: str) -> Optional[str]:
        """
        获取服务的仓库地址
        """
        service = self.services.get(service_name)
        return service.repo_url if service else None
    
    def search_by_keyword(self, keyword: str) -> List[str]:
        """
        根据关键词搜索服务
        """
        return self.keyword_index.get(keyword.lower(), [])
    
    def search_by_keywords(self, keywords: List[str]) -> Dict[str, int]:
        """
        多关键词搜索，返回服务得分
        """
        scores: Dict[str, int] = {}
        for keyword in keywords:
            services = self.search_by_keyword(keyword)
            for service in services:
                scores[service] = scores.get(service, 0) + 1
        return scores
    
    def search_by_db_table(self, table: str) -> List[str]:
        """
        根据数据库表搜索服务
        """
        return self.db_table_index.get(table, [])
    
    def all_services(self) -> List[str]:
        """
        获取所有服务名
        """
        return list(self.services.keys())
    
    def all_keywords(self) -> List[str]:
        """
        获取所有关键词
        """
        return list(self.keyword_index.keys())
    
    def get_dependencies(self, service_name: str) -> List[str]:
        """
        获取服务的依赖
        """
        service = self.services.get(service_name)
        return service.dependencies if service else []


def load_registry_from_yaml(path: str) -> ServiceRegistry:
    """
    从 YAML 文件加载服务注册表
    
    这是最简单的初始化方式
    """
    registry = ServiceRegistry()
    
    config = yaml.safe_load(Path(path).read_text())
    
    for svc in config.get("services", []):
        registry.register(ServiceInfo(
            name=svc["name"],
            repo_url=svc["repo_url"],
            language=svc.get("language", "unknown"),
            keywords=svc.get("keywords", []),
            dependencies=svc.get("dependencies", []),
            db_tables=svc.get("db_tables", []),
            api_endpoints=svc.get("api_endpoints", []),
            team=svc.get("team", ""),
            owner=svc.get("owner", ""),
            docs_url=svc.get("docs_url", ""),
        ))
    
    return registry


def save_registry_to_yaml(registry: ServiceRegistry, path: str):
    """
    保存服务注册表到 YAML
    """
    services = []
    for name, info in registry.services.items():
        services.append({
            "name": info.name,
            "repo_url": info.repo_url,
            "language": info.language,
            "keywords": info.keywords,
            "dependencies": info.dependencies,
            "db_tables": info.db_tables,
            "api_endpoints": info.api_endpoints,
            "team": info.team,
            "owner": info.owner,
            "docs_url": info.docs_url,
        })
    
    yaml.dump({"services": services}, Path(path).open("w"))