"""
数据链分析器 (Type B) - 从数据异常推理操作链，定位代码
"""

import re
from typing import List, Dict, Optional
from datetime import datetime

from core.models import (
    AnalysisRequest, AnalysisResult, ProblemType,
    CodeLocation, LogEvent, CodeModel, FunctionModel
)


class DataChainAnalyzer:
    """
    数据链分析器
    
    处理 Type B 问题：
    - B1: 数据异常（数据状态不对，程序未报错）
    - B2: 业务异常（结果不符合预期）
    
    分析流程：
    1. 检测数据异常点（通过数据库查询或问题描述）
    2. 搜索相关日志（时间范围 + 关键词）
    3. 重建操作时间线
    4. 映射操作到代码函数
    5. 推理状态错误原因
    """
    
    def __init__(self, sls_connector=None, db_connector=None, weknora_connector=None):
        self.sls = sls_connector
        self.db = db_connector
        self.weknora = weknora_connector
    
    def analyze(self, 
                request: AnalysisRequest,
                code_model: CodeModel,
                logs: List[LogEvent],
                similar_cases: List[Dict]) -> AnalysisResult:
        """
        主分析入口
        """
        
        # Step 1: 提取异常描述中的关键信息
        anomaly_info = self._parse_anomaly_description(request.error_desc)
        
        # Step 2: 搜索相关日志
        relevant_logs = self._find_relevant_logs(request, anomaly_info, logs)
        
        # Step 3: 重建时间线
        timeline = self._build_timeline(relevant_logs, anomaly_info)
        
        # Step 4: 找到可疑操作点
        suspicious_points = self._find_suspicious_points(timeline, anomaly_info)
        
        # Step 5: 映射到代码
        code_locations = self._map_to_code(suspicious_points, code_model)
        
        # Step 6: 推理根因
        root_cause = self._infer_root_cause(
            anomaly_info, timeline, suspicious_points, code_model
        )
        
        # Step 7: 生成修复建议
        fix_suggestion = self._generate_fix_suggestion(
            root_cause, code_locations, request
        )
        
        # 计算置信度
        confidence = self._calculate_confidence(
            timeline, code_locations, suspicious_points
        )
        
        return AnalysisResult(
            problem_type=ProblemType.B1 if anomaly_info.get('type') == 'data' else ProblemType.B2,
            root_cause=root_cause,
            code_locations=code_locations,
            fix_suggestion=fix_suggestion,
            confidence=confidence,
            timeline=timeline,
            matched_cases=similar_cases
        )
    
    def _parse_anomaly_description(self, desc: str) -> Dict:
        """
        解析异常描述
        
        提取：
        - 异常类型（数据/业务）
        - 涉及的实体（订单、支付等）
        - 预期状态 vs 实际状态
        - 关键时间点
        """
        
        info = {
            'type': 'business',  # 默认业务异常
            'entity': None,
            'expected': None,
            'actual': None,
            'keywords': [],
            'time_hint': None
        }
        
        # 判断类型
        if any(kw in desc for kw in ['数据', '状态', '字段', '值']):
            info['type'] = 'data'
        
        # 提取实体
        entity_patterns = [
            ('订单', 'order'),
            ('支付', 'payment'),
            ('库存', 'inventory'),
            ('用户', 'user'),
            ('商品', 'product'),
        ]
        
        for cn, en in entity_patterns:
            if cn in desc:
                info['entity'] = en
                info['keywords'].append(cn)
                break
        
        # 提取预期状态 vs 实际状态
        # 格式："...应该..." "...实际..." "...不符合..."
        
        if '应该' in desc or '预期' in desc:
            # 分割预期和实际
            parts = re.split(r'应该|预期|但|实际', desc)
            if len(parts) >= 2:
                info['expected'] = parts[-2].strip()
                info['actual'] = parts[-1].strip()
        
        if '不符合' in desc:
            info['type'] = 'business'
        
        # 提取时间提示
        time_patterns = [
            r'(\d{4}-\d{2}-\d{2})',
            r'(\d{2}:\d{2}:\d{2})',
            r'(\d+分钟前)',
            r'(\d+小时前)',
        ]
        
        for pattern in time_patterns:
            match = re.search(pattern, desc)
            if match:
                info['time_hint'] = match.group(1)
                break
        
        return info
    
    def _find_relevant_logs(self, 
                           request: AnalysisRequest,
                           anomaly_info: Dict,
                           existing_logs: List[LogEvent]) -> List[LogEvent]:
        """
        搜索相关日志
        
        策略：
        1. 如果已有日志，直接使用
        2. 如果有 SLS 连接器，按关键词查询
        3. 否则返回空列表
        """
        
        if existing_logs:
            return existing_logs
        
        if not self.sls:
            return []
        
        # 构建查询
        keywords = anomaly_info.get('keywords', [])
        if request.trace_id:
            # 按 traceId 查询
            return self.sls.extract_trace_chain(request.trace_id)
        
        # 按关键词和时间范围查询
        if request.time_range:
            logs = self.sls.find_error_events(
                time_range=request.time_range,
                keywords=keywords,
                level='WARN'  # Type B 问题通常不是 ERROR
            )
            
            # 也搜索 INFO 级别（可能包含状态变更记录）
            info_logs = self.sls.query_logs(
                query=' AND '.join(keywords) if keywords else '*',
                time_range=request.time_range,
                limit=100
            )
            
            logs.extend(info_logs)
            return logs
        
        return []
    
    def _build_timeline(self, logs: List[LogEvent], anomaly_info: Dict) -> List[Dict]:
        """
        重建操作时间线
        
        输出格式：
        [
            {
                'timestamp': '2024-01-10 10:00:00',
                'service': 'order-service',
                'operation': 'createOrder',
                'status': 'INFO',
                'message': '创建订单成功',
                'location': 'OrderService.java:45'
            },
            ...
        ]
        """
        
        timeline = []
        
        # 按时间排序
        sorted_logs = sorted(logs, key=lambda l: l.timestamp)
        
        for log in sorted_logs:
            event = {
                'timestamp': str(log.timestamp),
                'service': log.service,
                'operation': self._extract_operation(log.message),
                'status': log.level,
                'message': log.message[:200],
                'location': log.location,
                'trace_id': log.trace_id
            }
            timeline.append(event)
        
        # 如果没有日志，根据异常描述推断可能的时间线
        if not timeline and anomaly_info.get('expected') and anomaly_info.get('actual'):
            timeline = self._infer_timeline_from_description(anomaly_info)
        
        return timeline
    
    def _extract_operation(self, message: str) -> str:
        """从日志消息提取操作名"""
        
        # 常见操作模式
        patterns = [
            r'创建(\w+)',
            r'更新(\w+)',
            r'删除(\w+)',
            r'(\w+)成功',
            r'(\w+)失败',
            r'处理(\w+)',
            r'调用(\w+)',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, message)
            if match:
                return match.group(0)
        
        return 'unknown'
    
    def _infer_timeline_from_description(self, anomaly_info: Dict) -> List[Dict]:
        """从描述推断时间线（无日志时的备用）"""
        
        # 根据预期和实际推断可能发生了什么
        timeline = []
        
        entity = anomaly_info.get('entity', '')
        expected = anomaly_info.get('expected', '')
        actual = anomaly_info.get('actual', '')
        
        if entity == 'order':
            if '支付成功' in expected and '待支付' in actual:
                # 推断：支付回调未正确处理
                timeline = [
                    {'operation': '用户下单', 'status': 'INFO'},
                    {'operation': '用户支付', 'status': 'INFO'},
                    {'operation': '支付回调', 'status': 'WARN', 'message': '状态更新可能失败'},
                ]
        
        elif entity == 'inventory':
            if '扣减成功' in expected and '库存不足' in actual:
                timeline = [
                    {'operation': '创建订单', 'status': 'INFO'},
                    {'operation': '扣减库存', 'status': 'WARN', 'message': '库存检查可能失败'},
                ]
        
        return timeline
    
    def _find_suspicious_points(self, timeline: List[Dict], anomaly_info: Dict) -> List[Dict]:
        """
        找到可疑操作点
        
        策略：
        1. 状态变更相关的操作
        2. WARN 级别的日志
        3. 预期状态更新后的最后一个操作
        """
        
        suspicious = []
        
        entity = anomaly_info.get('entity', '')
        
        # 状态相关关键词
        state_keywords = ['状态', '更新', '修改', '变更', '同步']
        
        for event in timeline:
            # WARN 级别
            if event.get('status') == 'WARN':
                suspicious.append({
                    'event': event,
                    'reason': 'WARN级别日志',
                    'confidence': 0.7
                })
            
            # 状态变更操作
            message = event.get('message', '')
            if any(kw in message for kw in state_keywords):
                suspicious.append({
                    'event': event,
                    'reason': '状态变更操作',
                    'confidence': 0.8
                })
            
            # 外部调用（可能是同步失败）
            if '回调' in message or '同步' in message or '通知' in message:
                suspicious.append({
                    'event': event,
                    'reason': '外部调用/回调',
                    'confidence': 0.75
                })
        
        # 如果没有找到，取最后一个操作
        if not suspicious and timeline:
            suspicious.append({
                'event': timeline[-1],
                'reason': '时间线最后一个操作',
                'confidence': 0.5
            })
        
        return suspicious
    
    def _map_to_code(self, suspicious_points: List[Dict], code_model: CodeModel) -> List[CodeLocation]:
        """
        映射可疑操作到代码位置
        
        策略：
        1. 如果日志有 location，直接解析
        2. 否则按操作名搜索函数
        3. 优先搜索 db_operations 相关的函数
        """
        
        locations = []
        
        if not code_model:
            # 从日志 location 直接解析
            for point in suspicious_points:
                location = point['event'].get('location')
                if location:
                    file, line = self._parse_location(location)
                    if file:
                        locations.append(CodeLocation(
                            file=file,
                            line=line,
                            verified=False
                        ))
            return locations
        
        for point in suspicious_points:
            event = point['event']
            operation = event.get('operation', '')
            
            # 优先：日志中的位置
            if event.get('location'):
                file, line = self._parse_location(event['location'])
                if file:
                    locations.append(CodeLocation(
                        file=file,
                        line=line,
                        verified=False
                    ))
                    continue
            
            # 搜索相关函数
            matched_funcs = []
            
            # 按操作关键词搜索
            for file in code_model.files:
                for func in file.functions:
                    # 状态更新相关
                    if any(kw in func.name for kw in ['update', 'Update', 'save', 'Save', 'sync', 'Sync']):
                        matched_funcs.append((func, 0.7))
                    
                    # 数据库操作相关
                    if func.db_operations and 'update' in str(func.db_operations).lower():
                        matched_funcs.append((func, 0.8))
                    
                    # 外部调用相关（可能处理回调）
                    if func.external_calls and 'callback' in str(func.external_calls).lower():
                        matched_funcs.append((func, 0.75))
            
            # 取置信度最高的
            if matched_funcs:
                best_func = max(matched_funcs, key=lambda x: x[1])[0]
                locations.append(CodeLocation(
                    file=best_func.file,
                    line=best_func.start_line,
                    function=best_func.name,
                    code_snippet=best_func.code_snippet[:200] if best_func.code_snippet else None,
                    verified=True
                ))
        
        return locations
    
    def _parse_location(self, location: str) -> tuple:
        """解析日志位置字符串"""
        
        # Java: OrderService.java:245
        match = re.search(r'([\w.]+):(\d+)', location)
        if match:
            return match.group(1), int(match.group(2))
        
        # Go: order/service.go:45
        match = re.search(r'([\w/]+\.go):(\d+)', location)
        if match:
            return match.group(1), int(match.group(2))
        
        return None, None
    
    def _infer_root_cause(self,
                         anomaly_info: Dict,
                         timeline: List[Dict],
                         suspicious_points: List[Dict],
                         code_model: CodeModel) -> str:
        """
        推理根因
        
        根据时间线和可疑点推断状态错误的可能原因
        """
        
        entity = anomaly_info.get('entity', '')
        expected = anomaly_info.get('expected', '')
        actual = anomaly_info.get('actual', '')
        
        # 常见模式匹配
        
        # 1. 状态同步失败
        if expected and actual and expected != actual:
            # 检查是否有回调/同步操作
            has_callback = any('回调' in p['event'].get('message', '') for p in suspicious_points)
            has_sync = any('同步' in p['event'].get('message', '') for p in suspicious_points)
            
            if has_callback:
                return f"回调处理可能失败：{expected} 未正确更新为 {actual}。检查回调处理逻辑和事务完整性。"
            
            if has_sync:
                return f"状态同步可能失败：预期 {expected}，实际 {actual}。检查同步任务的执行状态和错误处理。"
        
        # 2. 数据库操作失败（静默）
        if code_model:
            for point in suspicious_points:
                for file in code_model.files:
                    for func in file.functions:
                        if point['event'].get('operation') in func.name:
                            # 检查是否有错误处理
                            if not func.error_handling:
                                return f"函数 {func.name} 缺少错误处理，可能导致数据库操作失败但未记录。"
                            
                            # 检查是否有事务
                            if 'Transactional' not in str(func.code_snippet):
                                return f"函数 {func.name} 可能缺少事务控制，导致部分更新成功部分失败。"
        
        # 3. 并发问题
        if timeline and len(timeline) > 1:
            # 检查是否有短时间内多次相同操作
            operations = [e.get('operation') for e in timeline]
            if len(set(operations)) < len(operations):
                return "可能存在并发问题：同一操作多次执行，导致状态不一致。"
        
        # 默认：基于描述推理
        if expected and actual:
            return f"状态不一致：预期 {expected}，实际 {actual}。请检查相关状态更新代码的逻辑和错误处理。"
        
        return f"数据异常：{anomaly_info.get('entity', '未知')} 状态不符合预期。需要更多信息定位根因。"
    
    def _generate_fix_suggestion(self,
                                root_cause: str,
                                code_locations: List[CodeLocation],
                                request: AnalysisRequest) -> str:
        """生成修复建议"""
        
        suggestions = []
        
        if code_locations:
            suggestions.append(f"建议检查以下代码位置：")
            for loc in code_locations[:3]:
                suggestions.append(f"  - {loc.file}:{loc.line} ({loc.function or '未知函数'})")
        
        # 基于根因类型给出具体建议
        
        if '回调' in root_cause:
            suggestions.extend([
                "1. 确认回调处理函数是否有事务控制",
                "2. 检查回调失败是否有重试机制",
                "3. 添加回调处理失败的日志记录",
            ])
        
        if '同步' in root_cause:
            suggestions.extend([
                "1. 检查同步任务的执行状态",
                "2. 添加同步失败的通知机制",
                "3. 实现手动补偿/修复机制",
            ])
        
        if '错误处理' in root_cause:
            suggestions.extend([
                "1. 添加 try-catch / if err != nil 错误处理",
                "2. 记录错误日志",
                "3. 实现错误恢复机制",
            ])
        
        if '事务' in root_cause:
            suggestions.extend([
                "1. 添加事务注解或事务代码",
                "2. 确保数据库操作在同一事务中",
                "3. 实现回滚机制",
            ])
        
        return '\n'.join(suggestions) if suggestions else "请提供更多信息以生成具体修复建议。"
    
    def _calculate_confidence(self,
                             timeline: List[Dict],
                             code_locations: List[CodeLocation],
                             suspicious_points: List[Dict]) -> float:
        """计算置信度"""
        
        confidence = 0.0
        
        # 时间线贡献
        if timeline:
            confidence += 0.2
            if len(timeline) >= 3:
                confidence += 0.1
        
        # 可疑点贡献
        if suspicious_points:
            max_point_conf = max(p.get('confidence', 0.5) for p in suspicious_points)
            confidence += max_point_conf * 0.3
        
        # 代码位置贡献
        if code_locations:
            confidence += 0.3
            verified_count = sum(1 for loc in code_locations if loc.verified)
            confidence += verified_count * 0.05
        
        return min(confidence, 0.9)