# Bug Analysis Workflow

输入报错、traceId、订单状态异常或业务偏差，输出稳定的根因分析结果：

```json
{
  "problem_type": "data_anomaly",
  "confidence": 0.78,
  "root_cause": "one-sentence root cause",
  "code_locations": [],
  "fix_suggestion": "minimal actionable fix"
}
```

这套工具现在按“两段式”使用：

1. **CLI 先跑**：快速分类、路由、读取配置和本地代码，给出兼容 JSON。
2. **Skill 接手**：当 CLI 置信度低，或需要人工级代码取证时，用 `skills/BUG_ANALYZER.md` 的流程深挖。

输出字段尽量不变，流程内部可以继续优化。

## 当前入口

| 场景 | 入口 | 说明 |
|---|---|---|
| 命令行快速分析 | `python3 cli_analyze.py ...` | 推荐第一步使用 |
| Python 集成 | `BugAnalysisWorkflow.analyze()` | 给其他工具调用 |
| AI 深度分析 | `skills/BUG_ANALYZER.md` | 低置信度或复杂问题时使用 |
| 多业务域配置 | `config/domains.yaml` | 推荐维护服务、仓库、关键词 |
| 传统单注册表 | `config/services.yaml.example` | 兼容旧模式 |
| WeKnora 知识库 | `connectors/weknora.py` | 保留，用于历史案例、业务规则、系统文档 |
| 阿里云 SLS 日志 | `connectors/aliyun_sls.py` | 保留，用于 traceId 调用链和错误日志 |

## 推荐流程

```
问题输入
  │
  ▼
CLI 快速分析
  ├─ 识别 A1/A2/B1/B2/C1
  ├─ 使用 repo/domain/trace 定位范围
  ├─ 有配置时读取 SLS / WeKnora / MySQL / git diff
  └─ 输出 AnalysisResult JSON
  │
  ├─ confidence >= 0.70
  │    └─ 直接进入修复/验证
  │
  └─ confidence < 0.70
       └─ 使用 bug-analyzer skill 深度取证
```

## 安装与配置

```bash
pip install -e .
cp config/config.yaml.example config/config.yaml
```

最小可用只需要本地仓库路径，不需要任何密钥：

```bash
python3 cli_analyze.py "panic: runtime error ... service/user.go:42" \
  --repo /path/to/repo \
  --json
```

推荐配置业务域：

```yaml
# config/domains.yaml
domains:
  go_member:
    display: 会员权益服务
    database: go_member
    repos:
      - path: /Users/me/go_member
        language: go
        url: https://example.com/go_member.git
    keywords:
      - 会员
      - 支付
      - member
    tables:
      - payment_slip
      - member
    apis:
      - /api/member/buy
```

查看已配置业务域：

```bash
python3 cli_analyze.py --list-domains
```

## WeKnora 和 SLS

这两块逻辑需要保留，是正式证据源，不是废弃模块：

- **WeKnora**：用于检索历史 Bug 案例、业务规则、系统文档；高置信度案例可自动沉淀回知识库。
- **阿里云 SLS**：用于按 `traceId` 还原调用链，或按 `time_range + keywords` 查询 ERROR/WARN 事件。

设计上它们是“有配置就参与，无配置就降级”：

```yaml
# config/config.yaml
weknora_base_url: https://your-weknora-host/api/v1
weknora_api_key: sk-real-key
weknora_kb_ids:
  bug_cases: kb-xxx
  business_rules: kb-yyy
  system_docs: kb-zzz

sls_access_key: your-access-key
sls_secret: your-secret
sls_endpoint: cn-beijing.log.aliyuncs.com
sls_project: your-project
```

触发时机：

| 证据源 | 触发条件 | 参与阶段 |
|---|---|---|
| SLS trace 链路 | CLI 传 `--trace` | 路由定位、日志取证、跨服务分析 |
| SLS 错误事件 | CLI 传 `--time-range` | 预处理、问题分类、时间线验证 |
| WeKnora 案例 | 配置了 `bug_cases` KB | 知识增强、相似案例匹配 |
| WeKnora 规则 | 配置了 `business_rules` KB 或域规则 | Type B/C 根因验证 |
| WeKnora 沉淀 | `enable_auto_save_case=true` 且高置信度 | 分析完成后的案例保存 |

## CLI 用法

### 堆栈 / panic / NPE

```bash
python3 cli_analyze.py "panic: runtime error: invalid memory address /path/to/file.go:87" \
  --repo /path/to/repo \
  --json
```

### 业务逻辑偏差

```bash
python3 cli_analyze.py "开通会员后没有调起支付" \
  --domain go_member \
  --repo /Users/me/go_member \
  --expected "会员开通应进入支付前检查并生成支付跳转" \
  --actual "type=14 直接跳过支付" \
  --json
```

### 数据状态异常

```bash
python3 cli_analyze.py "会员开通失败，支付成功，但订单状态还是支付中" \
  --domain go_member \
  --repo /Users/me/go_member \
  --expected "支付成功后订单状态应更新为 10，会员权益应派发" \
  --actual "payment_slip.state 仍为 7" \
  --json
```

### traceId / 时间窗口

```bash
python3 cli_analyze.py "订单支付成功后状态未更新" \
  --domain go_order \
  --trace abc123 \
  --time-range 2026-05-18T10:00:00,2026-05-18T10:10:00 \
  --json
```

### 本次变更相关问题

```bash
python3 cli_analyze.py "新会员类型支付成功后未开通" \
  --repo /path/to/repo \
  --changed-file internal/common/enum/pay.go \
  --changed-file consumer/mns/pay.go \
  --json
```

## Skill 用法

当 CLI 输出低置信度，或者你希望 AI 直接读代码、追调用链、形成证据链时，使用 [skills/BUG_ANALYZER.md](skills/BUG_ANALYZER.md)。

在 Codex / Claude Code 中直接说：

```text
使用 skills/BUG_ANALYZER.md 分析：

问题：会员开通失败，支付成功，但订单状态还是支付中
业务域：go_member
仓库：/Users/me/go_member
预期：支付成功后 payment_slip.state 应为 10，会员权益应派发
实际：payment_slip.state 仍为 7
补充：没有 traceId，先按代码链路分析
```

skill 输出包含三段：

1. 人类可读报告：分类、证据、根因、修复建议。
2. `AnalysisResult` 兼容 JSON：保持上层消费稳定。
3. `STATUS`：置信度、深度、读了哪些文件、下一步。

## Python API

```python
from core.workflow import BugAnalysisWorkflow, load_config_from_yaml
from core.models import AnalysisRequest

config = load_config_from_yaml("config/config.yaml")
config.domains_dir = "domains"

workflow = BugAnalysisWorkflow(config)
result = workflow.analyze(AnalysisRequest(
    error_desc="会员开通失败，支付成功，但订单状态还是支付中",
    domain="go_member",
    repo_path="/Users/me/go_member",
    expected_behavior="支付成功后订单状态应更新为 10",
    actual_behavior="payment_slip.state 仍为 7",
))

print(result.problem_type.value)
print(result.root_cause)
print(result.fix_suggestion)
```

## 问题类型

| 类型 | 枚举值 | 典型输入 | 默认动作 |
|---|---|---|---|
| A1 | `stack_trace` | panic、NPE、Traceback | 读 crash line 和 caller |
| A2 | `error_log` | ERROR、错误码、失败消息 | 搜索错误来源 |
| B1 | `data_anomaly` | 支付成功但订单状态未更新 | 追数据流、回调、DB 状态 |
| B2 | `business_anomaly` | 配置、上下游状态异常 | 查规则和配置 |
| C1 | `logic_error` | 预期和实际不一致 | 追分支、枚举、业务规则 |

基础设施错误保持输出兼容，映射为 `error_log`，修复建议会指向网络、DNS、证书、网关、下游健康检查。

## 目录说明

```text
bug-analysis-workflow/
├── cli_analyze.py              # CLI 入口
├── core/                       # 主流程、模型、路由、配置加载
├── adapters/                   # Go / Java 代码解析
├── connectors/                 # SLS / MySQL / WeKnora 证据源
├── config/                     # 全局配置和多域配置
├── domains/                    # 兼容目录式业务域配置
├── skills/BUG_ANALYZER.md      # AI 深度分析规程
├── docs/OVERVIEW.md            # 架构说明
└── tests/                      # 单元测试
```

## 外部依赖降级

| 依赖 | 有时 | 没有时 |
|---|---|---|
| 本地 repo | 读代码、定位行号 | 返回低置信度并要求补仓库 |
| SLS | traceId/time_range 还原时间线 | 跳过日志证据并降低置信度 |
| MySQL | 验证数据状态 | 仅基于代码和输入推断 |
| WeKnora | 查历史案例和业务规则 | 跳过知识增强并降低置信度 |
| AI API | 自动深度推理 | 交给 skill 手动深挖 |

## 判断结果是否可直接用

- `confidence >= 0.70`：可以按修复建议进入编码和验证。
- `0.40 <= confidence < 0.70`：需要补 traceId、订单号、日志、DB 状态或让 skill 继续读代码。
- `confidence < 0.40`：只当作分类和下一步指引，不要当最终根因。
