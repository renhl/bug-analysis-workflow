# 项目总览

本文档提供 bug-analysis-workflow 的整体概览。

## 核心设计

### 问题类型分类

```
Type A: 显性错误（有明确信号）
├─ A1: 有堆栈 → 直接定位
└─ A2: 有ERROR日志 → 需分析

Type B: 隐性异常（数据/业务）
├─ B1: 数据异常 → 数据链分析
└─ B2: 业务异常 → 对照规则

Type C: 逻辑偏差（结果不对）
└─ C1: 需要推理 → Agent分析
```

### 核心流程

```
1. 路由确定仓库
   ├─ 有traceId → 日志路由(置信度0.95)
   ├─ 关键词匹配 → 关键词路由(置信度0.7)
   └─ 知识库查询 → KB路由(置信度0.85)
   └─ 合并结果 → 如果<0.5询问用户

2. 预处理
   ├─ 代码解析 → AST + 调用图
   ├─ 日志获取 → 阿里云SLS
   └─ 问题分类 → A/B/C

3. 知识增强
   └─ WeKnora搜索历史案例 → 如果匹配直接返回

4. 分析引擎
   ├─ Type A → 堆栈解析器
   ├─ Type B → 数据链解析器
   └─ Type C → 逻辑推理解析器

5. 结果验证
   └─ 验证代码位置存在 + 行号在函数范围

6. 知识闭环
   └─ 保存为新案例 → 知识库越来越强
```

## 文件结构

```
bug-analysis-workflow/
├── README.md              # 项目说明
├── requirements.txt       # 依赖
├── .gitignore
│
├── core/                  # 核心模块
│   ├── models.py          # 数据模型定义
│   ├── registry.py        # 服务注册表
│   ├── routers.py         # 问题路由器
│   └── workflow.py        # 主工作流
│
├── connectors/            # 外部连接器
│   ├── aliyun_sls.py      # 阿里云日志
│   └── weknora.py         # WeKnora知识库
│
├── config/                # 配置模板
│   ├── config.yaml.example
│   └── services.yaml.example
│
├── knowledge/             # 知识库模板
│   ├── bug_case_template.md
│   ├── business_rule_template.md
│   └── system_doc_template.md
│
└── scripts/               # 工具脚本
    └── init_registry.py   # 初始化脚本
```

## 核心组件说明

### ServiceRegistry (服务注册表)

存储: 业务关键词 → 服务名 → 仓库地址

索引:
- keyword_index: 关键词 → 服务列表
- db_table_index: 数据库表 → 服务列表

### CompositeRouter (组合路由器)

策略:
1. LogRouter: traceId → 日志调用链 → 服务列表
2. KeywordRouter: 问题描述 → 关键词 → 服务匹配
3. KnowledgeBaseRouter: 问题描述 → WeKnora → 服务提取

合并: 按置信度加权，低于阈值询问用户

### WeKnoraConnector (知识库连接器)

功能:
- search_knowledge: 知识检索
- agent_chat: Agent问答（ReAct推理）
- upload_text: 上传文档（保存案例）

### BugAnalysisWorkflow (主工作流)

入口: analyze(request)

流程: 路由 → 预处理 → 知识增强 → 分析 → 验证 → 保存

## 使用示例

```python
from core.workflow import BugAnalysisWorkflow
from core.models import AnalysisRequest, BugAnalysisConfig

# 配置
config = BugAnalysisConfig(
    registry_path="config/services.yaml",
    weknora_base_url="http://localhost:8080/api/v1",
    weknora_api_key="sk-xxx",
    weknora_kb_ids={
        "bug_cases": "kb-001",
        "business_rules": "kb-002",
        "system_docs": "kb-003"
    }
)

# 初始化
workflow = BugAnalysisWorkflow(config)

# 分析
result = workflow.analyze(
    AnalysisRequest(
        error_desc="订单支付成功后状态仍显示待支付",
        trace_id="abc123"  # 可选
    )
)

# 结果
print(result.root_cause)
print(result.code_locations)  # [CodeLocation(file="OrderService.java", line=245)]
print(result.fix_suggestion)
```

## 下一步实现

### Phase 1: MVP

- [ ] 完善代码解析器（Java）
- [ ] 实现阿里云SLS连接器
- [ ] 完善堆栈分析器
- [ ] 单元测试

### Phase 2: 多语言

- [ ] Go适配器
- [ ] TypeScript适配器
- [ ] 数据链分析器

### Phase 3: 知识增强

- [ ] WeKnora Agent集成
- [ ] 逻辑推理分析器
- [ ] 自动保存案例

### Phase 4: 生产化

- [ ] 错误处理完善
- [ ] 性能优化
- [ ] CI/CD集成