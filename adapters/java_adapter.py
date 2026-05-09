"""
Java 语言适配器 - 使用 tree-sitter-java 解析
"""

import re
from typing import List, Optional
from pathlib import Path

try:
    import tree_sitter_java as tsjava
    from tree_sitter import Language, Parser
    TREE_SITTER_AVAILABLE = True
except ImportError:
    TREE_SITTER_AVAILABLE = False

from .base import LanguageAdapter, ParsedFunction, ParsedClass
from core.models import FileModel


class JavaAdapter(LanguageAdapter):
    """
    Java 语言适配器
    
    提取:
    - 类/方法定义
    - try-catch 异常处理
    - SQL 操作 (JDBC/MyBatis)
    - HTTP 调用 (RestTemplate/HttpClient)
    - Spring 注解
    """
    
    def __init__(self):
        if TREE_SITTER_AVAILABLE:
            self.parser = Parser(Language(tsjava.language()))
        else:
            self.parser = None
    
    def get_language(self) -> str:
        return "java"
    
    def get_default_patterns(self) -> List[str]:
        return ["*.java"]
    
    def parse_file(self, file_path: str) -> List[ParsedFunction]:
        """解析 Java 文件，提取方法"""
        
        if self.parser:
            return self._parse_with_tree_sitter(file_path)
        else:
            return self._parse_with_regex(file_path)
    
    def _parse_with_tree_sitter(self, file_path: str) -> List[ParsedFunction]:
        """使用 tree-sitter 解析"""
        
        source = Path(file_path).read_bytes()
        tree = self.parser.parse(source)
        
        functions = []
        
        # 遍历语法树
        for node in self._walk_tree(tree.root_node):
            if node.type == "method_declaration":
                func = self._extract_method(node, source)
                if func:
                    functions.append(func)
        
        return functions
    
    def _walk_tree(self, node):
        """递归遍历语法树"""
        yield node
        for child in node.children:
            yield from self._walk_tree(child)
    
    def _extract_method(self, node, source) -> Optional[ParsedFunction]:
        """从 AST 节点提取方法信息"""
        
        name = None
        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        parameters = []
        return_type = "void"
        calls = []
        error_handling = []
        db_operations = []
        external_calls = []
        
        for child in node.children:
            # 方法名
            if child.type == "identifier":
                name = source[child.start_byte:child.end_byte].decode()
            
            # 返回类型
            if child.type in ["type_identifier", "integral_type", "floating_point_type", "boolean_type", "void_type"]:
                return_type = source[child.start_byte:child.end_byte].decode()
            
            # 参数
            if child.type == "formal_parameters":
                for param in child.children:
                    if param.type == "formal_parameter":
                        for p_child in param.children:
                            if p_child.type in ["type_identifier", "integral_type"]:
                                param_type = source[p_child.start_byte:p_child.end_byte].decode()
                                parameters.append(param_type)
            
            # 方法体
            if child.type == "block":
                body = source[child.start_byte:child.end_byte].decode()
                
                # 提取调用
                calls = self._extract_calls_from_body(body)
                
                # 提取异常处理
                error_handling = self._extract_error_handling(body)
                
                # 提取数据库操作
                db_operations = self._extract_db_operations(body)
                
                # 提取外部调用
                external_calls = self._extract_external_calls(body)
        
        if not name:
            return None
        
        return ParsedFunction(
            name=name,
            start_line=start_line,
            end_line=end_line,
            parameters=parameters,
            return_type=return_type,
            calls=calls,
            error_handling=error_handling,
            db_operations=db_operations,
            external_calls=external_calls,
            code_snippet=source[node.start_byte:node.end_byte].decode()[:500]
        )
    
    def _parse_with_regex(self, file_path: str) -> List[ParsedFunction]:
        """正则表达式备用解析（无 tree-sitter 时）"""
        
        content = Path(file_path).read_text()
        functions = []
        
        # 匹配方法定义
        method_pattern = r'''
            (?:public|private|protected|static)?\s+
            (\w+)\s+           # 返回类型
            (\w+)\s*           # 方法名
            \(([^)]*)\)\s*     # 参数
            (?:throws\s+[\w,]+)?\s*
            \{                 # 方法体开始
        '''
        
        for match in re.finditer(method_pattern, content, re.VERBOSE):
            return_type = match.group(1)
            name = match.group(2)
            params_str = match.group(3)
            
            # 计算行号
            start_line = content[:match.start()].count('\n') + 1
            
            # 找到方法体结束
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
            
            functions.append(ParsedFunction(
                name=name,
                start_line=start_line,
                end_line=end_line,
                parameters=[p.strip().split()[-1] for p in params_str.split(',') if p.strip()],
                return_type=return_type,
                calls=self._extract_calls_from_body(body),
                error_handling=self._extract_error_handling(body),
                db_operations=self._extract_db_operations(body),
                external_calls=self._extract_external_calls(body),
                code_snippet=body[:500]
            ))
        
        return functions
    
    def _extract_calls_from_body(self, body: str) -> List[str]:
        """提取方法调用"""
        calls = []
        
        # 匹配方法调用: xxx.method() 或 method()
        for match in re.finditer(r'(\w+)\.(\w+)\s*\(', body):
            calls.append(f"{match.group(1)}.{match.group(2)}")
        
        for match in re.finditer(r'(\w+)\s*\([^)]*\)', body):
            name = match.group(1)
            if name not in ['if', 'for', 'while', 'switch', 'return', 'throw', 'new', 'try', 'catch']:
                calls.append(name)
        
        return calls
    
    def _extract_error_handling(self, body: str) -> List[str]:
        """提取异常处理"""
        handlers = []
        
        # try-catch
        for match in re.finditer(r'catch\s*\((\w+)', body):
            handlers.append(f"catch:{match.group(1)}")
        
        # throw
        for match in re.finditer(r'throw\s+new\s+(\w+)', body):
            handlers.append(f"throw:{match.group(1)}")
        
        return handlers
    
    def _extract_db_operations(self, body: str) -> List[str]:
        """提取数据库操作"""
        ops = []
        
        # JDBC
        patterns = [
            r'executeQuery', r'executeUpdate', r'prepareStatement',
            r'jdbcTemplate', r'query\(', r'update\(', r'insert\(', r'delete\('
        ]
        
        for pattern in patterns:
            if re.search(pattern, body):
                ops.append(pattern)
        
        # MyBatis
        for match in re.finditer(r'(\w+Mapper)\.(\w+)', body):
            if 'Mapper' in match.group(1):
                ops.append(f"mybatis:{match.group(1)}.{match.group(2)}")
        
        return ops
    
    def _extract_external_calls(self, body: str) -> List[str]:
        """提取外部服务调用"""
        calls = []
        
        # RestTemplate / HttpClient
        patterns = [
            r'restTemplate\.(\w+)',
            r'httpClient\.(\w+)',
            r'HttpClient\.(\w+)',
            r'WebClient\.(\w+)',
            r'FeignClient',
            r'@FeignClient',
        ]
        
        for pattern in patterns:
            for match in re.finditer(pattern, body):
                calls.append(f"http:{match.group(0)}")
        
        # RPC
        for match in re.finditer(r'(\w+Service)\.(\w+)', body):
            service_name = match.group(1)
            method_name = match.group(2)
            # 排除本类的方法
            if 'Service' in service_name:
                calls.append(f"rpc:{service_name}.{method_name}")
        
        return calls
    
    def parse_class(self, file_path: str) -> List[ParsedClass]:
        """解析类定义"""
        
        content = Path(file_path).read_text()
        classes = []
        
        class_pattern = r'''
            (?:@\w+(?:\([^)]*\))?\s*)*   # 注解
            (?:public|private|protected)?\s+
            (?:class|interface|enum)\s+
            (\w+)\s*                     # 类名
            (?:extends\s+\w+)?\s*
            (?:implements\s+[\w,]+)?\s*
            \{                           # 类体开始
        '''
        
        for match in re.finditer(class_pattern, content, re.VERBOSE):
            name = match.group(1)
            start_line = content[:match.start()].count('\n') + 1
            
            # 找到类体结束
            brace_count = 1
            pos = match.end()
            while brace_count > 0 and pos < len(content):
                if content[pos] == '{':
                    brace_count += 1
                elif content[pos] == '}':
                    brace_count -= 1
                pos += 1
            
            end_line = content[:pos].count('\n') + 1
            
            # 提取注解
            annotations = []
            for ann_match in re.finditer(r'@\w+', content[match.start():match.end()]):
                annotations.append(ann_match.group(0))
            
            # 提取方法名
            methods = []
            for method_match in re.finditer(r'(?:public|private|protected)\s+\w+\s+(\w+)\s*\(', content[match.start():pos]):
                methods.append(method_match.group(1))
            
            classes.append(ParsedClass(
                name=name,
                start_line=start_line,
                end_line=end_line,
                methods=methods,
                annotations=annotations
            ))
        
        return classes
    
    def is_business_code(self, func_name: str, class_name: str = None) -> bool:
        """判断是否业务代码"""
        
        # 框架包名
        framework_packages = [
            'java.lang', 'java.util', 'java.io', 'java.net',
            'org.springframework', 'org.apache', 'javax.',
            'com.fasterxml', 'org.slf4j', 'lombok'
        ]
        
        # 框架类名
        framework_patterns = [
            'Controller', 'Service', 'Repository', 'Mapper', 'Entity', 'DTO', 'VO'
        ]
        
        # 业务代码通常包含这些关键词
        business_patterns = [
            'Order', 'Payment', 'User', 'Product', 'Inventory', 'Cart',
            'Trade', 'Account', 'Transaction', 'Invoice', 'Refund'
        ]
        
        # 判断
        if class_name:
            for pattern in business_patterns:
                if pattern in class_name:
                    return True
            for pattern in framework_patterns:
                # Service/Mapper 本身可能是业务层
                if pattern in class_name and pattern != 'Entity':
                    return True
        
        return False
    
    def _extract_imports(self, file_path: Path) -> List[str]:
        """提取 import 语句"""
        content = file_path.read_text()
        imports = []
        
        for match in re.finditer(r'import\s+([\w.]+);', content):
            imports.append(match.group(1))
        
        return imports
    
    def _get_full_func_name(self, func_name: str, file_path: Path) -> str:
        """获取全限定名"""
        # Java: com.example.OrderService.processOrder
        package = file_path.parent
        class_name = file_path.stem
        return f"{package}.{class_name}.{func_name}"
    
    def _find_entry_points(self, files: List[FileModel]) -> List[str]:
        """找出 API 入口点（Controller 方法）"""
        entry_points = []
        
        for file in files:
            for func in file.functions:
                # Controller 方法
                for cls in file.classes:
                    if 'Controller' in cls.name:
                        entry_points.append(f"{cls.name}.{func.name}")
                # 或者有 @RequestMapping/@GetMapping 等注解的方法
                if 'Mapping' in str(func.code_snippet):
                    entry_points.append(func.name)
        
        return entry_points