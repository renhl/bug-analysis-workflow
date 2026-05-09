"""
逻辑推理分析器 (Type C) - 从业务规则和代码逻辑推理偏差点
"""

import re
from typing import List, Dict, Optional
from dataclasses import dataclass

from core.models import (
    AnalysisRequest, AnalysisResult, ProblemType,
    CodeLocation, CodeModel, FunctionModel
)


@dataclass
class BusinessRule:
    """业务规则"""
    name: str              # 规则名称
    condition: str         # 条件描述
    expected_result: str   # 预期结果
    priority: int = 1      # 优先级 1-5


class LogicInferenceAnalyzer:
    """
    逻辑推理分析器
    
    处理 Type C 问题：
    - C1: 逻辑错误（代码逻辑与业务规则不符）
    - C2: 边界情况（特殊场景处理不当）
    
    分析流程：
    1. 从知识库提取业务规则
    2. 定位相关的代码逻辑
    3. 模拟执行路径
    4. 找出逻辑偏差点
    5. 定位到代码行
    """
    
    def __init__(self, weknora_connector=None):
        self.weknora = weknora_connector
    
    def analyze(self,
                request: AnalysisRequest,
                code_model: CodeModel,
                logs: List,
                similar_cases: List[Dict]) -> AnalysisResult:
        """
        主分析入口
        """
        
        # Step 1: 提取业务规则
        business_rules = self._extract_business_rules(request, similar_cases)
        
        # Step 2: 提取问题中的预期行为
        expected_behavior = self._parse_expected_behavior(request)
        
        # Step 3: 定位相关代码逻辑
        related_functions = self._find_related_functions(request, code_model)
        
        # Step 4: 分析逻辑偏差
        deviations = self._analyze_logic_deviations(
            business_rules, expected_behavior, related_functions, code_model
        )
        
        # Step 5: 定位偏差代码
        code_locations = self._locate_deviation_code(deviations, code_model)
        
        # Step 6: 推理根因
        root_cause = self._infer_root_cause(deviations, request)
        
        # Step 7: 生成修复建议
        fix_suggestion = self._generate_fix_suggestion(deviations, code_locations)
        
        # 计算置信度
        confidence = self._calculate_confidence(deviations, code_locations)
        
        return AnalysisResult(
            problem_type=ProblemType.C1,
            root_cause=root_cause,
            code_locations=code_locations,
            fix_suggestion=fix_suggestion,
            confidence=confidence,
            thinking=self._generate_thinking_trace(deviations),
            matched_cases=similar_cases
        )
    
    def _extract_business_rules(self, 
                                request: AnalysisRequest,
                                similar_cases: List[Dict]) -> List[BusinessRule]:
        """
        提取业务规则
        
        来源：
        1. 从知识库搜索
        2. 从相似案例提取
        3. 从问题描述推断
        """
        
        rules = []
        
        # 从相似案例提取
        for case in similar_cases:
            if 'business_rule' in case:
                rules.append(BusinessRule(
                    name=case.get('business_rule', '未知规则'),
                    condition=case.get('condition', ''),
                    expected_result=case.get('expected_result', ''),
                    priority=2
                ))
        
        # 从知识库搜索
        if self.weknora and request.expected_behavior:
            kb_results = self.weknora.search_knowledge(
                query=f"业务规则 {request.error_desc}",
                kb_ids=[],  # TODO: 配置业务规则知识库
                top_k=3
            )
            
            for result in kb_results:
                content = result.get('content', '')
                extracted = self._parse_rule_from_doc(content)
                if extracted:
                    rules.append(extracted)
        
        # 从问题描述推断常见业务规则
        inferred_rules = self._infer_common_rules(request.error_desc)
        rules.extend(inferred_rules)
        
        return rules
    
    def _parse_rule_from_doc(self, content: str) -> Optional[BusinessRule]:
        """从文档解析业务规则"""
        
        # 常见规则格式：
        # "订单金额超过1000元需要审批"
        # "库存不足时不能下单"
        # "支付成功后订单状态变为已支付"
        
        patterns = [
            r'规则[：:]\s*(.+)',             # 规则：xxx
            r'当\s*(.+)\s*时，\s*(.+)',       # 当 xxx 时，xxx
            r'(.+)\s*需要\s*(.+)',           # xxx 需要 xxx
            r'(.+)\s*不能\s*(.+)',           # xxx 不能 xxx
            r'(.+)\s*后\s*(.+)',             # xxx 后 xxx
        ]
        
        for pattern in patterns:
            match = re.search(pattern, content)
            if match:
                groups = match.groups()
                return BusinessRule(
                    name=groups[0] if len(groups) > 0 else '未知规则',
                    condition=groups[0] if len(groups) > 0 else '',
                    expected_result=groups[-1] if len(groups) > 1 else '',
                    priority=3
                )
        
        return None
    
    def _infer_common_rules(self, desc: str) -> List[BusinessRule]:
        """推断常见业务规则"""
        
        rules = []
        
        # 订单相关规则
        if '订单' in desc:
            rules.extend([
                BusinessRule(
                    name='订单创建规则',
                    condition='用户下单',
                    expected_result='订单状态为待支付',
                    priority=1
                ),
                BusinessRule(
                    name='支付成功规则',
                    condition='支付成功',
                    expected_result='订单状态更新为已支付',
                    priority=2
                ),
                BusinessRule(
                    name='库存检查规则',
                    condition='创建订单',
                    expected_result='检查库存是否充足',
                    priority=2
                ),
            ])
        
        # 支付相关规则
        if '支付' in desc:
            rules.extend([
                BusinessRule(
                    name='支付状态同步规则',
                    condition='支付成功',
                    expected_result='订单和支付状态同步',
                    priority=2
                ),
                BusinessRule(
                    name='支付回调规则',
                    condition='收到支付回调',
                    expected_result='正确处理并更新状态',
                    priority=2
                ),
            ])
        
        # 库存相关规则
        if '库存' in desc:
            rules.extend([
                BusinessRule(
                    name='库存扣减规则',
                    condition='下单成功',
                    expected_result='库存正确扣减',
                    priority=2
                ),
                BusinessRule(
                    name='库存回滚规则',
                    condition='订单取消/支付失败',
                    expected_result='库存恢复',
                    priority=2
                ),
            ])
        
        return rules
    
    def _parse_expected_behavior(self, request: AnalysisRequest) -> Dict:
        """
        解析预期行为
        
        从 request.expected_behavior 或 error_desc 提取
        """
        
        expected = {
            'condition': None,
            'action': None,
            'result': None
        }
        
        if request.expected_behavior:
            # 格式化解析
            # 输入: "支付成功后订单状态应该变为已支付"
            # 输出: {condition: "支付成功", action: "订单状态更新", result: "已支付"}
            
            patterns = [
                r'(.+)\s*后\s*(.+)\s*应该\s*(.+)',   # xxx 后 xxx 应该 xxx
                r'当\s*(.+)\s*时\s*(.+)\s*应该\s*(.+)',  # 当 xxx 时 xxx 应该 xxx
                r'(.+)\s*应该\s*(.+)',               # xxx 应该 xxx
            ]
            
            for pattern in patterns:
                match = re.search(pattern, request.expected_behavior)
                if match:
                    groups = match.groups()
                    expected['condition'] = groups[0] if len(groups) > 0 else None
                    expected['action'] = groups[1] if len(groups) > 1 else None
                    expected['result'] = groups[-1] if len(groups) > 2 else groups[-1]
                    break
        
        # 从 error_desc 补充
        if not expected['condition']:
            desc = request.error_desc
            
            if '应该' in desc:
                parts = desc.split('应该')
                expected['result'] = parts[-1].strip()
                
                # 尝试提取条件
                if '后' in parts[0]:
                    expected['condition'] = parts[0].split('后')[0].strip()
        
        return expected
    
    def _find_related_functions(self,
                                request: AnalysisRequest,
                                code_model: CodeModel) -> List[FunctionModel]:
        """
        定位相关函数
        
        策略：
        1. 按问题描述关键词搜索
        2. 按预期行为搜索
        3. 搜索入口点
        """
        
        if not code_model:
            return []
        
        functions = []
        
        # 从 error_desc 提取关键词
        keywords = self._extract_keywords(request.error_desc)
        
        for file in code_model.files:
            for func in file.functions:
                # 关键词匹配
                keyword_match = any(
                    kw.lower() in func.name.lower() 
                    for kw in keywords
                )
                
                # 数据库操作相关
                db_match = any(
                    kw.lower() in str(func.db_operations).lower()
                    for kw in keywords
                )
                
                # 外部调用相关（处理回调等）
                external_match = any(
                    kw.lower() in str(func.external_calls).lower()
                    for kw in keywords
                )
                
                if keyword_match or db_match or external_match:
                    functions.append(func)
        
        # 添加入口点（如果没找到）
        if not functions:
            for ep in code_model.entry_points:
                matched = code_model.search_function(ep)
                functions.extend(matched)
        
        return functions[:10]  # 最多10个相关函数
    
    def _extract_keywords(self, desc: str) -> List[str]:
        """提取关键词"""
        
        keywords = []
        
        # 业务关键词
        business_words = [
            'order', 'Order', '订单',
            'payment', 'Payment', '支付',
            'inventory', 'Inventory', '库存',
            'user', 'User', '用户',
            'status', 'Status', '状态',
            'callback', 'Callback', '回调',
            'sync', 'Sync', '同步',
            'create', 'Create', '创建',
            'update', 'Update', '更新',
            'cancel', 'Cancel', '取消',
        ]
        
        for word in business_words:
            if word in desc:
                keywords.append(word)
        
        return keywords
    
    def _analyze_logic_deviations(self,
                                  business_rules: List[BusinessRule],
                                  expected_behavior: Dict,
                                  related_functions: List[FunctionModel],
                                  code_model: CodeModel) -> List[Dict]:
        """
        分析逻辑偏差
        
        对比业务规则和代码逻辑，找出偏差点
        """
        
        deviations = []
        
        for rule in business_rules:
            # 针对每个规则，检查代码是否实现
            
            for func in related_functions:
                deviation = self._check_rule_in_function(rule, func)
                if deviation:
                    deviations.append(deviation)
        
        # 检查预期行为是否在代码中实现
        if expected_behavior.get('condition'):
            deviation = self._check_expected_in_code(
                expected_behavior, related_functions, code_model
            )
            if deviation:
                deviations.append(deviation)
        
        return deviations
    
    def _check_rule_in_function(self, rule: BusinessRule, func: FunctionModel) -> Optional[Dict]:
        """
        检查函数是否实现规则
        
        返回偏差点描述
        """
        
        deviation = None
        
        # 条件检查
        condition_keywords = self._extract_keywords(rule.condition)
        
        # 检查函数是否处理了条件
        condition_handled = any(
            kw in func.name or kw in str(func.code_snippet)
            for kw in condition_keywords
        )
        
        # 检查是否有条件判断逻辑
        has_condition_logic = any(
            kw in str(func.code_snippet)
            for kw in ['if', 'if err', 'switch', 'case', 'when', 'condition']
        )
        
        # 结果检查
        result_keywords = self._extract_keywords(rule.expected_result)
        
        # 检查函数是否产生了预期结果
        result_produced = any(
            kw in str(func.db_operations) or kw in str(func.external_calls)
            for kw in result_keywords
        )
        
        # 判断偏差
        
        # 1. 条件未处理
        if condition_keywords and not condition_handled:
            deviation = {
                'type': 'condition_missing',
                'rule': rule.name,
                'function': func.name,
                'file': func.file,
                'line': func.start_line,
                'description': f"函数 {func.name} 未处理规则条件 '{rule.condition}'",
                'severity': 'high' if rule.priority >= 2 else 'medium'
            }
        
        # 2. 条件处理但无判断逻辑
        elif condition_handled and not has_condition_logic:
            deviation = {
                'type': 'logic_missing',
                'rule': rule.name,
                'function': func.name,
                'file': func.file,
                'line': func.start_line,
                'description': f"函数 {func.name} 缺少条件判断逻辑，规则 '{rule.name}' 可能未正确执行",
                'severity': 'medium'
            }
        
        # 3. 结果未产生
        elif result_keywords and not result_produced:
            deviation = {
                'type': 'result_missing',
                'rule': rule.name,
                'function': func.name,
                'file': func.file,
                'line': func.start_line,
                'description': f"函数 {func.name} 未产生规则预期结果 '{rule.expected_result}'",
                'severity': 'high' if rule.priority >= 2 else 'medium'
            }
        
        return deviation
    
    def _check_expected_in_code(self,
                                expected: Dict,
                                functions: List[FunctionModel],
                                code_model: CodeModel) -> Optional[Dict]:
        """
        检查预期行为是否在代码中实现
        """
        
        # 检查条件触发点
        condition = expected.get('condition', '')
        result = expected.get('result', '')
        
        # 搜索处理条件的函数
        condition_funcs = []
        for func in functions:
            if self._matches_condition(func, condition):
                condition_funcs.append(func)
        
        # 检查这些函数是否产生了预期结果
        for func in condition_funcs:
            if not self._produces_result(func, result):
                return {
                    'type': 'expected_result_missing',
                    'function': func.name,
                    'file': func.file,
                    'line': func.start_line,
                    'description': f"函数 {func.name} 处理 '{condition}' 但未产生预期结果 '{result}'",
                    'severity': 'high'
                }
        
        # 如果没有找到处理条件的函数
        if not condition_funcs:
            return {
                'type': 'condition_handler_missing',
                'condition': condition,
                'description': f"未找到处理条件 '{condition}' 的函数",
                'severity': 'medium'
            }
        
        return None
    
    def _matches_condition(self, func: FunctionModel, condition: str) -> bool:
        """判断函数是否匹配条件"""
        
        condition_keywords = self._extract_keywords(condition)
        
        # 函数名匹配
        name_match = any(kw.lower() in func.name.lower() for kw in condition_keywords)
        
        # 外部调用匹配（如支付回调）
        external_match = any(kw in str(func.external_calls) for kw in condition_keywords)
        
        return name_match or external_match
    
    def _produces_result(self, func: FunctionModel, result: str) -> bool:
        """判断函数是否产生结果"""
        
        result_keywords = self._extract_keywords(result)
        
        # 数据库操作匹配（如状态更新）
        db_match = any(kw in str(func.db_operations) for kw in result_keywords)
        
        # 代码片段匹配
        code_match = any(kw in str(func.code_snippet) for kw in result_keywords)
        
        return db_match or code_match
    
    def _locate_deviation_code(self,
                               deviations: List[Dict],
                               code_model: CodeModel) -> List[CodeLocation]:
        """
        定位偏差代码位置
        """
        
        locations = []
        
        for deviation in deviations:
            if deviation.get('file') and deviation.get('line'):
                locations.append(CodeLocation(
                    file=deviation['file'],
                    line=deviation['line'],
                    function=deviation.get('function'),
                    verified=True
                ))
            
            # 尝试从代码片段定位更精确位置
            if deviation.get('type') == 'logic_missing':
                # 搜索 if/switch 等判断语句
                for file in code_model.files if code_model else []:
                    if file.path == deviation.get('file'):
                        for func in file.functions:
                            if func.name == deviation.get('function'):
                                # 定位到具体判断语句
                                line = self._find_condition_line(func)
                                if line:
                                    locations.append(CodeLocation(
                                        file=file.path,
                                        line=line,
                                        function=func.name,
                                        verified=True
                                    ))
        
        return locations
    
    def _find_condition_line(self, func: FunctionModel) -> Optional[int]:
        """在函数中找到条件判断语句的行号"""
        
        if not func.code_snippet:
            return None
        
        # 搜索条件判断
        patterns = ['if', 'switch', 'case', 'when']
        
        lines = func.code_snippet.split('\n')
        for i, line in enumerate(lines):
            for pattern in patterns:
                if pattern in line:
                    return func.start_line + i
        
        return None
    
    def _infer_root_cause(self, deviations: List[Dict], request: AnalysisRequest) -> str:
        """
        推理根因
        """
        
        if not deviations:
            return f"未发现明显的逻辑偏差。请检查问题描述是否准确，或提供更多上下文信息。"
        
        # 整合所有偏差
        causes = []
        
        for dev in deviations:
            desc = dev.get('description', '')
            severity = dev.get('severity', 'medium')
            
            if severity == 'high':
                causes.append(f"[严重] {desc}")
            else:
                causes.append(f"[一般] {desc}")
        
        # 找最可能的根因
        high_severity = [d for d in deviations if d.get('severity') == 'high']
        
        if high_severity:
            main_dev = high_severity[0]
            root_cause = f"主要根因：{main_dev.get('description')}\n\n"
            root_cause += "其他偏差：\n" + '\n'.join(causes[:3])
        else:
            root_cause = "可能根因：\n" + '\n'.join(causes[:3])
        
        return root_cause
    
    def _generate_fix_suggestion(self,
                                deviations: List[Dict],
                                code_locations: List[CodeLocation]) -> str:
        """
        生成修复建议
        """
        
        suggestions = []
        
        if code_locations:
            suggestions.append("建议修改以下代码位置：")
            for loc in code_locations:
                suggestions.append(f"  - {loc.file}:{loc.line} ({loc.function or '未知函数'})")
        
        for dev in deviations:
            dev_type = dev.get('type', '')
            
            if dev_type == 'condition_missing':
                suggestions.extend([
                    "1. 在函数中添加条件判断逻辑",
                    "2. 确保条件满足时执行相应操作",
                    "3. 添加日志记录条件判断结果",
                ])
            
            elif dev_type == 'logic_missing':
                suggestions.extend([
                    "1. 添加 if/switch 条件判断",
                    "2. 处理边界条件和异常情况",
                    "3. 确保所有分支都有明确处理",
                ])
            
            elif dev_type == 'result_missing':
                suggestions.extend([
                    "1. 确保函数执行后产生预期结果",
                    "2. 添加数据库更新或状态变更操作",
                    "3. 检查结果是否正确保存",
                ])
            
            elif dev_type == 'expected_result_missing':
                suggestions.extend([
                    "1. 在条件处理后添加结果产生逻辑",
                    "2. 检查业务规则是否完整实现",
                    "3. 确保状态更新在事务中完成",
                ])
        
        return '\n'.join(suggestions) if suggestions else "请提供更多信息以生成修复建议。"
    
    def _calculate_confidence(self,
                             deviations: List[Dict],
                             code_locations: List[CodeLocation]) -> float:
        """计算置信度"""
        
        confidence = 0.0
        
        # 偏差数量贡献
        if deviations:
            confidence += 0.2
            high_count = sum(1 for d in deviations if d.get('severity') == 'high')
            confidence += high_count * 0.15
        
        # 代码位置贡献
        if code_locations:
            confidence += 0.3
            verified_count = sum(1 for loc in code_locations if loc.verified)
            confidence += verified_count * 0.1
        
        # 规则匹配贡献
        matched_rules = set(d.get('rule') for d in deviations if d.get('rule'))
        confidence += len(matched_rules) * 0.1
        
        return min(confidence, 0.85)
    
    def _generate_thinking_trace(self, deviations: List[Dict]) -> str:
        """生成思考过程"""
        
        trace = []
        
        trace.append("## 逻辑推理分析过程")
        trace.append("")
        trace.append("### Step 1: 提取业务规则")
        trace.append("从问题描述和知识库中提取相关业务规则。")
        trace.append("")
        trace.append("### Step 2: 定位相关代码")
        trace.append("按关键词搜索可能涉及的业务函数。")
        trace.append("")
        trace.append("### Step 3: 分析逻辑偏差")
        
        for dev in deviations:
            trace.append(f"- 发现偏差：{dev.get('description')}")
        
        trace.append("")
        trace.append("### Step 4: 推理根因")
        trace.append("根据偏差类型和严重程度，推理最可能的根因。")
        
        return '\n'.join(trace)