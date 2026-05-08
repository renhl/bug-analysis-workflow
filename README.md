# Bug Analysis Workflow

> 智能问题分析工作流 —— 从问题描述定位到代码行号

## 核心能力

- **问题描述 → 代码定位**: 只描述问题，自动定位到具体代码文件和行号
- **多语言支持**: Java、Go、TypeScript/JavaScript
- **知识增强**: 基于 WeKnora 知识库，历史案例加速定位
- **多路径分析**: 堆栈分析、数据链分析、逻辑推理三种策略

## 工作原理

```
输入: "订单支付成功后状态仍显示待支付"
  ↓
路由层: 关键词匹配 + 知识库查询 → 确定 order-service、payment-service
  ↓
预处理: 代码解析(AST) + 日志获取(阿里云SLS) + 问题分类
  ↓
知识增强: WeKnora 查历史案例 + 理解业务规则
  ↓
分析引擎: 选择路径(堆栈/数据链/逻辑推理)
  ↓
输出: 根因 + OrderService.java:245 + 修复建议
  ↓
闭环: 保存为知识库案例 → 越用越强
```

## 问题类型覆盖

| 类型 | 描述 | 定位难度 | 示例 |
|-----|------|---------|------|
| **Type A** | 有明确错误信号 | 低 | NullPointerException、堆栈信息 |
| **Type B** | 数据/业务异常 | 中 | 数据不一致、状态未同步 |
| **Type C** | 逻辑偏差 | 高 | 符合代码但不符合预期 |

## 核心组件

### 1. 问题路由器

用户只描述问题时，自动确定涉及的代码仓库：

- **日志路由**: 有 traceId → 从调用链提取服务名（置信度 0.95）
- **关键词路由**: 业务关键词匹配 → 服务映射（置信度 0.7）
- **知识库路由**: WeKnora 文档 → 业务上下文（置信度 0.85）

### 2. WeKnora 知识增强

- **KB-BugCases**: 历史 bug 案例，相似问题快速定位
- **KB-BusinessRules**: 业务规则文档，理解预期行为
- **KB-SystemDocs**: 系统架构文档，服务调用关系

### 3. 三条分析路径

- **堆栈分析**: 有异常堆栈 → 直接定位行号
- **数据链分析**: 数据异常 → 时间线重建 → 操作定位
- **逻辑推理**: 业务规则 vs 代码逻辑 → 找偏差点

### 4. 多语言代码解析

基于 tree-sitter 的 AST 解析，支持：

- Java: class、method、try-catch、throws、@Transactional
- Go: func、struct、error handling (if err != nil)
- TypeScript: function、class、fetch/axios、try-catch

## 快速开始

### 1. 配置服务注册表

```yaml
# config/services.yaml
services:
  - name: order-service
    repo_url: git@github.com/your-org/order-service.git
    language: java
    keywords: [订单, 下单, order]
    dependencies: [payment-service]
    
  - name: payment-service
    repo_url: git@github.com/your-org/payment-service.git
    language: java
    keywords: [支付, payment]
```

### 2. 配置 WeKnora

```yaml
# config/weknora.yaml
base_url: http://your-weknora:8080/api/v1
api_key: sk-xxxxx
kb_ids:
  bug_cases: kb-00000001
  business_rules: kb-00000002
  system_docs: kb-00000003
```

### 3. 运行分析

```python
from bug_analysis_workflow import BugAnalysisWorkflow

workflow = BugAnalysisWorkflow(config)

result = workflow.analyze(
    error_desc="订单支付成功后状态仍显示待支付",
    # 可选参数
    trace_id="abc123",  # 更精准定位
    time_range=("2024-05-08 14:00", "2024-05-08 14:30"),
    db_query="SELECT * FROM orders WHERE id = 12345"
)

print(f"根因: {result.root_cause}")
print(f"代码位置: {result.code_locations}")  # OrderService.java:245
print(f"修复建议: {result.fix_suggestion}")
```

## 项目结构

```
bug-analysis-workflow/
├── core/                   # 核心模块
│   ├── analyzer.py         # 主分析器
│   ├── code_model.py       # 统一代码模型
│   └── result.py           # 分析结果模型
│
├── routers/                # 问题路由
│   ├── composite_router.py # 组合路由器
│   ├── log_router.py       # 日志路由(traceId)
│   ├── keyword_router.py   # 关键词路由
│   └── kb_router.py        # 知识库路由
│   └── registry.py         # 服务注册表
│
├── adapters/               # 语言适配器
│   ├── base.py             # 抽象基类
│   ├── java_adapter.py
│   ├── go_adapter.py
│   └── typescript_adapter.py
│
├── connectors/             # 外部连接器
│   ├── aliyun_sls.py       # 阿里云日志
│   ├── database.py         # 数据库
│   ├── weknora.py          # WeKnora 知识库
│   └── git_repo.py         # Git 仓库
│
├── analyzers/              # 分析路径
│   ├── stack_trace.py      # 堆栈分析(Type A)
│   ├── data_chain.py       # 数据链分析(Type B)
│   └── logic_inference.py  # 逻辑推理(Type C)
│
├── workflows/              # 主工作流
│   └── bug_analysis.py     # 主入口
│
├── config/                 # 配置模板
│   ├── services.yaml       # 服务注册表模板
│   ├── weknora.yaml        # WeKnora 配置
│   ├── aliyun_sls.yaml     # 阿里云日志配置
│
├── knowledge/              # 知识库模板
│   ├── bug_case_template.md
│   ├── business_rule_template.md
│   └── system_doc_template.md
│
├── scripts/                # 工具脚本
│   ├── init_registry.py    # 初始化服务注册表
│   ├── init_knowledge.py   # 初始化知识库
│
└── docs/                   # 详细文档
    ├── architecture.md     # 架构设计
    ├── routing.md          # 路由机制详解
    ├── analysis_paths.md   # 分析路径详解
    ├── weknora_integration.md
    └── implementation_guide.md
```

## 实现计划

| Phase | 内容 | 时间 |
|-------|------|------|
| Phase 1 | MVP: Java + 堆栈分析 + 阿里云日志 | 3周 |
| Phase 2 | Go/TS 支持 + 数据链分析 | 2周 |
| Phase 3 | WeKnora 集成 + 逻辑推理 | 2周 |
| Phase 4 | 自动化 + CI/CD 集成 | 1周 |

## 依赖

- Python 3.10+
- tree-sitter (代码解析)
- httpx (API 调用)
- PyYAML (配置解析)
- WeKnora (知识库)

## License

MIT