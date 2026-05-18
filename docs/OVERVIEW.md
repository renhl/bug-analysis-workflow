# 项目总览

`bug-analysis-workflow` 是一套面向工程现场的 Bug 根因分析框架。它把用户输入的报错、日志、traceId、数据异常或业务偏差，转成稳定的 `AnalysisResult`。

当前推荐使用方式是 **CLI 先跑，skill 深挖**：

1. `cli_analyze.py` 负责快速分类、路由、外部证据拉取和兼容 JSON 输出。
2. `skills/BUG_ANALYZER.md` 负责低置信度或复杂问题的人工级证据链分析。

- `problem_type`
- `root_cause`
- `code_locations`
- `fix_suggestion`
- `confidence`

流程可以持续优化，但输出核心字段保持兼容，便于 CLI、AI skill、上层自动化工具复用。

## 设计原则

1. **先分类，再取证**：堆栈、日志、数据异常、逻辑偏差和基础设施问题走不同深度。
2. **证据优先**：没有代码证据时不输出确定性代码根因。
3. **证据源保留**：SLS、MySQL、WeKnora 是正式证据源；有配置时必须参与分析，不可用时才降级。
4. **输出稳定**：新增输入参数和内部上下文，不破坏现有 JSON 字段。
5. **域隔离**：会员、订单、支付等业务域各自维护服务、关键词、表和规则。

## 问题类型

当前与 `core.models.ProblemType` 对齐：

```
Type A: 显性错误
├─ A1 stack_trace      有堆栈、文件、行号
└─ A2 error_log        有错误日志、错误码、失败消息

Type B: 状态/业务异常
├─ B1 data_anomaly     数据状态不一致
└─ B2 business_anomaly 业务状态、配置或上下游异常

Type C: 逻辑偏差
└─ C1 logic_error      预期行为和实际行为不一致
```

基础设施错误不会新增输出枚举，当前保持兼容地映射为 `error_log`，修复建议会明确走网络、DNS、证书、网关、下游健康检查。

## 总流程

```
AnalysisRequest
  │
  ▼
Phase 0 输入归一化
  ├─ error_desc
  ├─ domain / repo_path
  ├─ trace_id / time_range
  ├─ expected_behavior / actual_behavior
  └─ changed_files / base_branch
  │
  ▼
Phase 1 轻量分类
  ├─ 文本堆栈识别
  ├─ 基础设施短路
  ├─ 数据异常关键词
  └─ 逻辑偏差关键词
  │
  ▼
Phase 2 路由定位
  ├─ 显式 repo_path 优先
  ├─ domain 限定服务集合
  ├─ traceId 日志路由
  ├─ 关键词路由
  └─ WeKnora 知识路由
  │
  ▼
Phase 3 证据采集
  ├─ 代码解析
  ├─ crash line 上下文
  ├─ 调用链 / 数据流
  ├─ 日志 / DB / 历史案例
  └─ git diff / changed files
  │
  ▼
Phase 4 分析引擎
  ├─ AI API
  ├─ skill context fallback
  └─ low-confidence fallback
  │
  ▼
Phase 5 验证与沉淀
  ├─ code location verification
  ├─ confidence gate
  └─ optional case upload
```

## 目录结构

```
bug-analysis-workflow/
├── README.md
├── cli_analyze.py              # CLI 入口
├── setup.py
├── requirements.txt
│
├── core/
│   ├── models.py               # AnalysisRequest / AnalysisResult / ProblemType
│   ├── workflow.py             # 主流程
│   ├── routers.py              # trace / keyword / KB 路由
│   ├── registry.py             # 服务注册表
│   ├── domain_config.py        # config/domains.yaml 加载
│   └── constants.py            # 阈值、权重、限制
│
├── adapters/
│   ├── base.py
│   ├── go_adapter.py
│   └── java_adapter.py
│
├── connectors/
│   ├── aliyun_sls.py
│   ├── mysql.py
│   └── weknora.py
│
├── config/
│   ├── config.yaml.example     # 全局平台配置
│   ├── domains.yaml            # 多业务域配置
│   └── services.yaml.example   # 传统服务注册表示例
│
├── domains/
│   ├── go_member/
│   └── go_order/
│
├── skills/
│   └── BUG_ANALYZER.md         # AI skill 规程
│
└── docs/
    ├── OVERVIEW.md
    └── bug-reports/
```

## 核心组件

### `AnalysisRequest`

输入模型，支持：

- 问题描述：`error_desc`
- 定位范围：`domain`、`repo_path`、`related_repos`
- 运行时证据：`trace_id`、`time_range`
- 逻辑偏差证据：`expected_behavior`、`actual_behavior`
- 变更上下文：`changed_files`、`base_branch`

### `CompositeRouter`

组合路由器按证据合并结果：

- `LogRouter`：traceId → 调用链 → 主服务。
- `KeywordRouter`：问题描述 → 业务关键词 → 服务注册表。
- `KnowledgeBaseRouter`：问题描述 → WeKnora 文档 → 服务名。

显式传入 `repo_path` 时，路由结果会被覆盖，直接分析该仓库。

### `WeKnoraConnector`

保留的知识库证据源：

- `search_knowledge`：检索历史案例、业务规则、系统文档。
- `agent_chat` / `knowledge_chat`：需要时做知识库问答。
- `upload_text`：高置信度结果沉淀为 Bug 案例。

调用入口：

- `BugAnalysisWorkflow._search_similar_cases`
- `BugAnalysisWorkflow._save_case`
- `KnowledgeBaseRouter.route`

### `AliyunSLSConnector`

保留的日志证据源：

- `extract_trace_chain(trace_id)`：按 traceId 还原服务调用链。
- `find_error_events(time_range, keywords)`：按时间窗口和关键词查询错误事件。
- `query_logs(...)`：底层日志查询接口。

调用入口：

- `LogRouter.route`
- `BugAnalysisWorkflow._get_logs`
- `BugAnalysisWorkflow._get_call_chain_from_trace`
- `BugAnalysisWorkflow._analyze_cross_service`

### `BugAnalysisWorkflow`

主入口 `analyze(request)`：

1. 校验输入。
2. 文本分类和基础设施短路。
3. 路由仓库。
4. 获取变更文件、解析代码、查询日志。
5. 搜索历史案例。
6. AI 分析或 fallback。
7. 验证位置并可选保存案例。

### `LanguageAdapter`

统一代码模型：

- `GoAdapter`：函数、调用、error handling、DB 操作、外部调用。
- `JavaAdapter`：方法、异常处理、DB 操作、HTTP/RPC 调用。

当前 TypeScript/Python 可以被文本识别，但尚未有完整 adapter。

## 输出兼容策略

默认 CLI JSON 保持：

```json
{
  "problem_type": "logic_error",
  "confidence": 0.7,
  "root_cause": "...",
  "code_locations": [],
  "fix_suggestion": "..."
}
```

内部可以扩展 `thinking`、`references`、`timeline`、`matched_cases`，但默认不强迫上层消费。

## 使用层级

### L0: 无外部依赖

只有 `--repo` 和问题描述：

- 可解析代码。
- 可识别堆栈行。
- 可生成低到中置信度结论。

### L1: 业务域配置

增加 `--domain` 或 `config/domains.yaml`：

- 限定服务集合。
- 提升关键词路由准确性。
- 可以查业务表、API、规则。

### L2: 运行时证据（SLS）

增加 SLS / traceId / time_range：

- 还原调用链。
- 定位错误服务。
- 构建事件时间线。

### L3: 知识库和 AI（WeKnora）

增加 WeKnora / AI API：

- 查历史案例。
- 对业务规则、代码、日志做综合推理。
- 高置信度结果可沉淀回案例库。

## 后续演进

- 补 TypeScript/Python adapter。
- 增强 code location verification，让 `verified` 更可信。
- 增加测试覆盖：CLI 参数、文本分类、AI response parse、路由合并。
- 将 evidence level 写入扩展字段，但不破坏现有输出。
