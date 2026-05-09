"""
语言适配器基类 - 多语言 AST 解析抽象
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Optional
from dataclasses import dataclass
from pathlib import Path

from core.models import CodeModel, FileModel, FunctionModel, ClassModel


@dataclass
class ParsedFunction:
    """解析出的函数信息"""
    name: str
    start_line: int
    end_line: int
    parameters: List[str]
    return_type: str
    calls: List[str]           # 调用的函数名
    error_handling: List[str]  # try-catch / if err != nil 等
    db_operations: List[str]   # SQL 操作 / ORM 调用
    external_calls: List[str]  # HTTP/RPC/MQ 调用
    code_snippet: str


@dataclass
class ParsedClass:
    """解析出的类信息"""
    name: str
    start_line: int
    end_line: int
    methods: List[str]
    annotations: List[str]


class LanguageAdapter(ABC):
    """
    语言适配器抽象基类
    
    负责解析特定语言的代码，提取函数、类、调用关系等
    """
    
    @abstractmethod
    def parse_file(self, file_path: str) -> List[ParsedFunction]:
        """解析单个文件，提取函数列表"""
        pass
    
    @abstractmethod
    def parse_class(self, file_path: str) -> List[ParsedClass]:
        """解析单个文件，提取类列表"""
        pass
    
    @abstractmethod
    def get_language(self) -> str:
        """返回语言名称"""
        pass
    
    @abstractmethod
    def is_business_code(self, func_name: str, class_name: str = None) -> bool:
        """判断是否是业务代码（过滤框架代码）"""
        pass
    
    def parse_repo(self, repo_path: str, 
                   file_patterns: List[str] = None) -> CodeModel:
        """
        解析整个仓库
        
        Args:
            repo_path: 仓库路径
            file_patterns: 要解析的文件模式，如 ['*.java', '*.go']
        """
        from datetime import datetime
        
        files = []
        call_graph = {}  # 函数名 -> 调用的函数列表
        
        # 查找所有匹配的文件
        repo = Path(repo_path)
        patterns = file_patterns or self.get_default_patterns()
        
        for pattern in patterns:
            for file_path in repo.rglob(pattern):
                # 跳过测试文件、配置文件等
                if self._should_skip(file_path):
                    continue
                
                try:
                    parsed_funcs = self.parse_file(str(file_path))
                    parsed_classes = self.parse_class(str(file_path))
                    
                    # 转换为模型
                    functions = []
                    for pf in parsed_funcs:
                        func = FunctionModel(
                            name=pf.name,
                            file=str(file_path.relative_to(repo)),
                            start_line=pf.start_line,
                            end_line=pf.end_line,
                            parameters=pf.parameters,
                            return_type=pf.return_type,
                            calls=pf.calls,
                            called_by=[],  # 后续填充
                            error_handling=pf.error_handling,
                            db_operations=pf.db_operations,
                            external_calls=pf.external_calls,
                            code_snippet=pf.code_snippet
                        )
                        functions.append(func)
                        
                        # 构建调用图
                        full_name = self._get_full_func_name(pf.name, file_path)
                        call_graph[full_name] = pf.calls
                    
                    classes = []
                    for pc in parsed_classes:
                        classes.append(ClassModel(
                            name=pc.name,
                            file=str(file_path.relative_to(repo)),
                            start_line=pc.start_line,
                            end_line=pc.end_line,
                            methods=pc.methods,
                            annotations=pc.annotations
                        ))
                    
                    if functions or classes:
                        files.append(FileModel(
                            path=str(file_path.relative_to(repo)),
                            functions=functions,
                            classes=classes,
                            imports=self._extract_imports(file_path)
                        ))
                        
                except Exception as e:
                    # 解析失败，跳过
                    print(f"Parse error: {file_path}: {e}")
                    continue
        
        # 反向填充 called_by
        for file in files:
            for func in file.functions:
                caller_name = self._get_full_func_name(func.name, Path(repo_path) / func.file)
                for called in func.calls:
                    # 找到被调用的函数，记录调用者
                    for f in files:
                        for f_func in f.functions:
                            if called in f_func.name or f_func.name in called:
                                f_func.called_by.append(caller_name)
        
        return CodeModel(
            language=self.get_language(),
            repo_path=repo_path,
            files=files,
            call_graph=call_graph,
            entry_points=self._find_entry_points(files),
            index_time=datetime.now()
        )
    
    @abstractmethod
    def get_default_patterns(self) -> List[str]:
        """返回默认的文件匹配模式"""
        pass
    
    @abstractmethod
    def _extract_imports(self, file_path: Path) -> List[str]:
        """提取导入语句"""
        pass
    
    def _should_skip(self, file_path: Path) -> bool:
        """判断是否应该跳过该文件"""
        skip_patterns = [
            'test', 'Test', '_test', 'spec', 'Spec',
            'config', 'Config', 'conf',
            'vendor', 'node_modules', 'target', 'build'
        ]
        return any(p in str(file_path) for p in skip_patterns)
    
    @abstractmethod
    def _get_full_func_name(self, func_name: str, file_path: Path) -> str:
        """获取函数的全限定名"""
        pass
    
    @abstractmethod
    def _find_entry_points(self, files: List[FileModel]) -> List[str]:
        """找出 API 入口点"""
        pass