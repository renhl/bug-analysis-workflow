"""
Go 语言适配器 - 使用正则/AST 解析 Go 代码
"""

import re
from typing import List, Optional
from pathlib import Path

try:
    import tree_sitter_go as ts_go
    from tree_sitter import Language, Parser
    TREE_SITTER_AVAILABLE = True
except ImportError:
    TREE_SITTER_AVAILABLE = False

from .base import LanguageAdapter, ParsedFunction, ParsedClass
from core.models import FileModel


class GoAdapter(LanguageAdapter):
    """
    Go 语言适配器
    
    提取:
    - 函数定义
    - error 处理 (if err != nil)
    - SQL 操作
    - HTTP 调用
    - RPC/GRPC 调用
    """
    
    def __init__(self):
        if TREE_SITTER_AVAILABLE:
            self.parser = Parser(Language(ts_go.language()))
        else:
            self.parser = None
    
    def get_language(self) -> str:
        return "go"
    
    def get_default_patterns(self) -> List[str]:
        return ["*.go"]
    
    def parse_file(self, file_path: str) -> List[ParsedFunction]:
        """解析 Go 文件"""
        
        if self.parser:
            return self._parse_with_tree_sitter(file_path)
        else:
            return self._parse_with_regex(file_path)
    
    def _parse_with_regex(self, file_path: str) -> List[ParsedFunction]:
        """正则解析 Go 函数"""
        
        content = Path(file_path).read_text()
        functions = []
        
        # Go 函数定义: func name(params) (returns) { body }
        func_pattern = r'''
            func\s+
            (?:(\w+)\.)?       # 方法所属类型（可选）
            (\w+)\s*           # 函数名
            \(([^)]*)\)\s*     # 参数
            (?:\(([^)]*)\))?   # 返回值（可选）
            \{                 # 函数体开始
        '''
        
        for match in re.finditer(func_pattern, content, re.VERBOSE | re.MULTILINE):
            receiver = match.group(1)  # 方法接收者
            name = match.group(2)
            params_str = match.group(3)
            returns_str = match.group(4) or ""
            
            # 计算行号
            start_line = content[:match.start()].count('\n') + 1
            
            # 找到函数体结束
            brace_count = 1
            pos = match.end()
            while brace_count > 0 and pos < len(content):
                if content[pos] == '{':
                    brace_count += 1
                elif content[pos] == '}':
                    brace_count -= 1
                pos += 1
            
            end_line = content[:pos].count('\n') + 1
            body = content[match.end():pos-1]
            
            # 提取参数
            parameters = []
            for param in params_str.split(','):
                param = param.strip()
                if param:
                    # Go 参数格式: name type 或 type
                    parts = param.split()
                    if len(parts) >= 2:
                        parameters.append(parts[-1])  # 类型
                    elif len(parts) == 1:
                        parameters.append(parts[0])
            
            # 返回类型
            return_type = returns_str.strip() or "void"
            if return_type.startswith('(') and return_type.endswith(')'):
                return_type = return_type[1:-1].strip()
            
            # 提取函数体信息
            full_name = f"{receiver}.{name}" if receiver else name
            
            functions.append(ParsedFunction(
                name=full_name,
                start_line=start_line,
                end_line=end_line,
                parameters=parameters,
                return_type=return_type,
                calls=self._extract_calls_from_body(body),
                error_handling=self._extract_error_handling(body),
                db_operations=self._extract_db_operations(body),
                external_calls=self._extract_external_calls(body),
                code_snippet=body[:500]
            ))
        
        return functions
    
    def _parse_with_tree_sitter(self, file_path: str) -> List[ParsedFunction]:
        """使用 tree-sitter 解析"""
        
        source = Path(file_path).read_bytes()
        tree = self.parser.parse(source)
        
        functions = []
        
        # TODO: 实现完整的 tree-sitter 解析
        # 当前退化到正则
        return self._parse_with_regex(file_path)
    
    def _extract_calls_from_body(self, body: str) -> List[str]:
        """提取函数调用"""
        calls = []
        
        # 匹配函数调用: pkg.Func() 或 Func()
        for match in re.finditer(r'(\w+)\.(\w+)\s*\(', body):
            pkg = match.group(1)
            func = match.group(2)
            # 排除内置包
            if pkg not in ['fmt', 'log', 'strings', 'strconv', 'json', 'time', 'context', 'errors']:
                calls.append(f"{pkg}.{func}")
        
        for match in re.finditer(r'(\w+)\s*\([^)]*\)', body):
            name = match.group(1)
            if name not in ['if', 'for', 'switch', 'select', 'return', 'go', 'defer', 'func']:
                calls.append(name)
        
        return calls
    
    def _extract_error_handling(self, body: str) -> List[str]:
        """提取 error 处理"""
        handlers = []
        
        # Go 特有的 error 处理模式
        patterns = [
            r'if\s+err\s*!=\s*nil',              # if err != nil
            r'if\s+errors\.Is',                  # errors.Is
            r'if\s+errors\.As',                  # errors.As
            r'return\s+err',                     # return err
            r'fmt\.Errorf',                      # fmt.Errorf
            r'errors\.New',                      # errors.New
            r'panic\(',                          # panic
        ]
        
        for pattern in patterns:
            if re.search(pattern, body):
                handlers.append(pattern)
        
        return handlers
    
    def _extract_db_operations(self, body: str) -> List[str]:
        """提取数据库操作"""
        ops = []
        
        # GORM
        patterns = [
            r'\.Create\(', r'\.First\(', r'\.Find\(', r'\.Save\(',
            r'\.Update\(', r'\.Delete\(', r'\.Where\(', r'\.Raw\('
        ]
        
        for pattern in patterns:
            for match in re.finditer(pattern, body):
                ops.append(f"gorm:{match.group(0)}")
        
        # database/sql
        patterns = [
            r'db\.Query', r'db\.Exec', r'db\.Prepare',
            r'rows\.Next', r'rows\.Scan'
        ]
        
        for pattern in patterns:
            if re.search(pattern, body):
                ops.append(f"sql:{pattern}")
        
        return ops
    
    def _extract_external_calls(self, body: str) -> List[str]:
        """提取外部服务调用"""
        calls = []
        
        # HTTP 调用
        patterns = [
            r'http\.Get', r'http\.Post', r'http\.Client',
            r'resty\.R', r'client\.R',
            r'httpclient\.'
        ]
        
        for pattern in patterns:
            for match in re.finditer(pattern, body):
                calls.append(f"http:{match.group(0)}")
        
        # RPC/GRPC
        patterns = [
            r'grpc\.', r'rpc\.',
            r'\.Call\(', r'\.Invoke\('
        ]
        
        for pattern in patterns:
            if re.search(pattern, body):
                calls.append(f"rpc:{pattern}")
        
        # Service 调用（Go 项目常见命名）
        for match in re.finditer(r'(\w+Service)\.(\w+)', body):
            calls.append(f"service:{match.group(1)}.{match.group(2)}")
        
        return calls
    
    def parse_class(self, file_path: str) -> List[ParsedClass]:
        """解析 Go struct（相当于类）"""
        
        content = Path(file_path).read_text()
        classes = []
        
        # Go struct 定义
        struct_pattern = r'''
            type\s+
            (\w+)\s*           # struct 名
            struct\s*          # struct 关键字
            \{                  # struct 体开始
        '''
        
        for match in re.finditer(struct_pattern, content, re.VERBOSE):
            name = match.group(1)
            start_line = content[:match.start()].count('\n') + 1
            
            # 找到 struct 体结束
            brace_count = 1
            pos = match.end()
            while brace_count > 0 and pos < len(content):
                if content[pos] == '{':
                    brace_count += 1
                elif content[pos] == '}':
                    brace_count -= 1
                pos += 1
            
            end_line = content[:pos].count('\n') + 1
            
            # 提取方法（通过方法接收者判断）
            methods = []
            method_pattern = rf'func\s+\({name}\s*\*?{name}\)\s+(\w+)\s*\('
            for method_match in re.finditer(method_pattern, content):
                methods.append(method_match.group(1))
            
            classes.append(ParsedClass(
                name=name,
                start_line=start_line,
                end_line=end_line,
                methods=methods,
                annotations=[]  # Go 没有 Java 式注解
            ))
        
        return classes
    
    def is_business_code(self, func_name: str, class_name: str = None) -> bool:
        """判断是否业务代码"""
        
        # Go 标准库排除
        stdlib = [
            'fmt', 'log', 'strings', 'strconv', 'json', 'time',
            'context', 'errors', 'sync', 'io', 'os', 'net', 'http',
            'database', 'sql', 'regexp', 'math', 'rand', 'crypto'
        ]
        
        # 检查是否调用标准库
        if '.' in func_name:
            pkg = func_name.split('.')[0]
            if pkg in stdlib:
                return False
        
        # 业务关键词
        business_patterns = [
            'Order', 'Payment', 'User', 'Product', 'Inventory', 'Cart',
            'Trade', 'Account', 'Transaction', 'Invoice', 'Refund',
            'Service', 'Handler', 'Controller', 'Manager', 'Processor'
        ]
        
        for pattern in business_patterns:
            if pattern in func_name or (class_name and pattern in class_name):
                return True
        
        return True  # Go 默认认为是业务代码（除非明确是标准库）
    
    def _extract_imports(self, file_path: Path) -> List[str]:
        """提取 import 语句"""
        content = file_path.read_text()
        imports = []
        
        # 单行 import
        for match in re.finditer(r'import\s+"([^"]+)"', content):
            imports.append(match.group(1))
        
        # 多行 import
        import_block_pattern = r'import\s+\(([^)]+)\)'
        for match in re.finditer(import_block_pattern, content):
            block = match.group(1)
            for imp_match in re.finditer(r'"([^"]+)"', block):
                imports.append(imp_match.group(1))
        
        return imports
    
    def _get_full_func_name(self, func_name: str, file_path: Path) -> str:
        """获取全限定名"""
        # Go: pkg.FuncName
        package = self._extract_package(file_path)
        return f"{package}.{func_name}"
    
    def _extract_package(self, file_path: Path) -> str:
        """提取 package 名"""
        content = file_path.read_text()
        match = re.search(r'package\s+(\w+)', content)
        return match.group(1) if match else "main"
    
    def _find_entry_points(self, files: List[FileModel]) -> List[str]:
        """找出 API 入口点（HTTP Handler）"""
        entry_points = []
        
        for file in files:
            for func in file.functions:
                # HTTP Handler
                if 'Handler' in func.name or 'Handle' in func.name:
                    entry_points.append(func.name)
                
                # main 函数
                if func.name == 'main':
                    entry_points.append(func.name)
                
                # 或者有 HTTP 调用的函数
                if 'http.' in func.code_snippet:
                    entry_points.append(func.name)
        
        return entry_points