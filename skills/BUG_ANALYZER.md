---
name: bug-analyzer
description: Adaptive bug root-cause analysis skill. Use when a user pastes logs, stack traces, traceId, data anomalies, or describes wrong runtime behavior. Produces evidence-backed root cause, fix recommendation, verification plan, and AnalysisResult-compatible output.
---

# Bug Analyzer v4 - Evidence-First Adaptive Workflow

## 如何接手 CLI 结果

优先让 CLI 做低成本分类和路由：

```bash
python3 cli_analyze.py "<问题描述>" --domain <domain> --repo <repo_path> --json
```

当 CLI 输出满足任一条件时，本 skill 接手深度分析：

- `confidence < 0.70`
- `code_locations` 为空但用户需要明确代码位置
- `root_cause` 是 fallback/提示类文本，而不是证据链结论
- 问题涉及支付、订单、会员、资金、权限、跨服务状态同步

接手时从用户或 CLI 输出中提取：

| 字段 | 来源 |
|---|---|
| `error_desc` | CLI 入参或用户原文 |
| `problem_type` | CLI JSON 的 `problem_type`，可重新校正 |
| `repo_path` | CLI `--repo` 或用户提供路径 |
| `domain` | CLI `--domain` |
| `expected_behavior` | CLI `--expected` 或用户描述 |
| `actual_behavior` | CLI `--actual` 或现场现象 |
| `trace_id` / `time_range` | 用户补充，有则用于日志验证 |

不要把 CLI 低置信度 fallback 当成根因；它只是路由和下一步信号。

## 设计目标

这套能力要解决的不是“把报错解释一遍”，而是把一次线上/测试问题分析收敛成可执行结论：

1. 快速判断问题属于哪一类，避免所有问题都走重流程。
2. 用证据链保护结论质量，禁止无代码证据时拍脑袋给根因。
3. 能独立作为 AI coding skill 使用，也能和本仓的 Python workflow、域配置、SLS、MySQL、WeKnora 联动。
4. 输出结构同时面向人和机器：人读报告，程序读 `AnalysisResult` 兼容 JSON。

核心原则：**先分流，再取证；先证据，再结论；先最小修复，再扩展治理。**

## 输入契约

用户可能只给一句话，也可能给完整日志。先把输入整理成标准字段，不要急着读代码。

| 字段 | 来源 | 用途 |
|---|---|---|
| `error_desc` | 用户原文、日志摘要 | 必填，作为问题主描述 |
| `stack_trace` | panic、Exception、Traceback、goroutine、at 行 | 定位 Type A1 |
| `trace_id` | traceId、requestId、链路 ID | 触发 SLS 查询和跨服务定位 |
| `time_range` | 用户提供的时间窗口 | 触发日志补充查询 |
| `domain` | 用户指定或关键词路由 | 限定业务域，如 `go_member` |
| `repo_path` | 用户指定或路由结果 | 限定代码仓库 |
| `expected_behavior` | “应该/预期/正常情况下” | 逻辑偏差必需证据 |
| `actual_behavior` | “实际/现在/用户反馈” | 和预期对比 |
| `changed_files` | git diff、MR/PR 文件列表 | 优先审查近期改动 |

## 保留证据源

WeKnora 知识库和阿里云 SLS 日志查询必须保留在分析链路中：

| 证据源 | 何时使用 | 用途 |
|---|---|---|
| 阿里云 SLS | 用户提供 `trace_id` | 还原调用链、定位错误服务、读取错误日志 |
| 阿里云 SLS | 用户提供 `time_range` | 按关键词查 ERROR/WARN，补运行时证据 |
| WeKnora | 配置了历史案例 KB | 搜索相似 Bug，避免重复分析 |
| WeKnora | 配置了业务规则/系统文档 KB | 校验业务预期、服务职责和历史规则 |
| WeKnora | 高置信度结论完成后 | 沉淀新 Bug 案例 |

如果连接器不可用，可以降级，但输出必须说明“未验证日志/知识库证据”，并降低置信度。

缺少关键信息时的处理：

- 有堆栈但找不到仓库：可以先给低置信度诊断，并明确要求补 `repo_path`。
- 逻辑偏差没有预期行为：先追问“正确业务规则是什么”，或者从规则库/历史案例补证据。
- 只有 traceId：先查日志定位服务，再决定是否读代码。
- 只有一句“某功能不对”：按 Type C1 走轻量深度，先路由域和入口点。

## 总流程

```
用户输入
  │
  ▼
Phase 0: 输入归一化
  │  提取 error_desc / stack_trace / trace_id / domain / repo_path / expected_behavior
  ▼
Phase 1: 分类与路由
  │  判定 Type A/B/C/Infra；选择 quick / medium / deep / infra
  ▼
Phase 2: 证据采集
  │  按深度读取代码、日志、DB、知识库、git diff
  ▼
Phase 3: 根因假设
  │  只输出最强假设；列证据链、触发条件和置信度
  ▼
Phase 4: 修复方案
  │  给最小修复、风险、验证步骤；必要时给备选方案
  ▼
Phase 5: 输出与沉淀
     人类报告 + AnalysisResult JSON + STATUS
```

## Phase 1: 分类与路由

### 1.1 语言识别

| 语言 | 识别特征 | 空指针特征 |
|---|---|---|
| Go | `panic:`、`.go:\d+`、`goroutine` | `nil pointer dereference` |
| Java | `Exception`、`.java:\d+`、`at package.Class.method` | `NullPointerException` |
| Python | `Traceback`、`.py", line \d+` | `NoneType`、`AttributeError` |
| TypeScript | `.ts:\d+`、`.tsx:\d+`、`TypeError` | `undefined`、`Cannot read properties` |
| Unknown | 无明显代码语言特征 | 需要从 repo 或用户补充判断 |

### 1.2 问题类型

本 skill 的问题类型与仓库 `core.models.ProblemType` 对齐：

| 类型 | 定义 | 典型输入 | 默认深度 |
|---|---|---|---|
| `A1 stack_trace` | 有堆栈、文件、行号 | panic、NPE、Traceback | quick |
| `A2 error_log` | 有错误日志但没有明确堆栈 | ERROR、异常码、失败消息 | medium |
| `B1 data_anomaly` | 数据状态不一致 | 支付成功但订单未更新 | deep |
| `B2 business_anomaly` | 业务状态异常但未必是代码逻辑 | 配置缺失、上下游状态不一致 | medium/deep |
| `C1 logic_error` | 行为不符合业务预期 | 应该调支付却跳过 | deep |
| `INFRA` | 网络、DNS、证书、下游不可用 | 502、503、connection refused | infra |

分类顺序：

1. 有明确堆栈和文件行号 → `A1`
2. 明显基础设施错误 → `INFRA`
3. 有 traceId 或 ERROR 日志但无堆栈 → `A2`
4. 描述数据状态不一致 → `B1`
5. 描述业务状态异常、配置、上下游状态 → `B2`
6. 描述“预期 vs 实际”逻辑偏差 → `C1`
7. 无法判断 → `A2`，但标记 `needs_user_input`

### 1.3 深度选择

| 深度 | 适用类型 | 文件上限 | 调用链层级 | 外部依赖 | 目标耗时 |
|---|---|---:|---:|---|---|
| quick | A1 且 file:line 明确 | 2 | 1-2 | 无 | 2 分钟 |
| medium | A2、B2、简单 Type C | 5 | 2 | 可选日志/KB | 5 分钟 |
| deep | B1、C1、跨服务/数据流 | 10-15 | 3+ | 日志/DB/KB/git | 10-20 分钟 |
| infra | INFRA | 0-2 | 0 | 日志/配置 | 2-5 分钟 |

升级规则：

- quick 路径发现入参来自复杂分支或跨服务调用 → 升级 medium。
- medium 路径发现数据状态、业务规则、多个服务都参与 → 升级 deep。
- deep 路径如果没有仓库、日志、规则、DB 中任一证据来源，置信度不得超过 0.55。
- infra 路径不要建议代码修复，除非证据显示是配置写错或错误重试策略。

## Phase 2: 证据采集

### 2.1 通用取证顺序

按最便宜到最有力的顺序取证：

1. 读取用户原始输入，保留关键原文。
2. 如果有 traceId/time_range，查日志定位服务、接口、错误时刻。
3. 如果有 repo_path，读最可能相关的代码位置。
4. 如果有 domain，加载域服务、业务规则、关联表、KB。
5. 如果有 changed_files/base_branch，优先看近期变更。
6. 只有当代码证据不足时，再扩大搜索范围。

搜索优先使用 `rg`：

```bash
rg -n "exact error message|关键业务词|函数名" /path/to/repo
rg -n "func .*Name|class .*Name|def .*name" /path/to/repo
rg -n "traceId|requestId|订单号|会员类型" /path/to/repo
```

### 2.2 Quick 路径：堆栈直达

目标：用最少文件确认崩溃点和直接触发条件。

步骤：

1. 读取 crash file 的 crash line 前后 20 行。
2. 读取上一级 caller，确认入参来源、错误处理、nil/null 来源。
3. 如果 caller 仍无法解释状态，再读取一层上游；最多 2 个文件。
4. 形成一个根因假设，不展开全仓搜索。

必查：

- crash line 访问了哪个对象、字段、下标或方法。
- 该对象是否可能为 nil/null/None/undefined。
- 错误是否被忽略，例如 Go 的 `_`、Java catch 后吞异常。
- 是否存在并发写、缓存 miss、配置缺失导致对象未初始化。

### 2.3 Medium 路径：错误来源定位

目标：找到错误产生点，并解释为什么会走到那里。

步骤：

1. 精确搜索错误文本、错误码、业务关键词。
2. 读取 top 3 命中位置，找到“错误被创建/返回/打印”的源头。
3. 向上追 1-2 层调用链，确认入参和分支条件。
4. 如果有日志，按时间线确认错误前后的状态变化。
5. 输出根因假设和最小修复。

适用：

- 有错误文本但无堆栈。
- 有 traceId，能从日志推断服务入口。
- 业务状态异常但范围较小。

### 2.4 Deep 路径：业务/数据/跨服务

目标：对比“业务规则、数据状态、代码实现、调用链”四类证据。

步骤：

1. 明确预期行为：来自用户、规则库、历史案例或接口文档。
2. 找入口：API、consumer、job、handler、controller、usecase。
3. 追数据流：输入参数 → 校验 → 分支 → DB/缓存/外部服务 → 输出。
4. 查业务规则：domain rules、配置、常量枚举、开关、白名单。
5. 查近期变更：

```bash
git log --oneline -10 -- <suspect-file>
git diff <base-branch>...HEAD -- <suspect-file>
git log --all -p -S "关键错误文本或业务枚举" -- <suspect-file>
```

6. 查测试：是否缺失覆盖、是否有被改坏的断言。
7. 必要时查 DB 或日志验证真实状态。

Deep 路径必须把“哪里不符合预期”说清楚，而不是只指出某行代码可疑。

### 2.5 Infra 路径：非代码故障

目标：快速判断是否是环境/依赖/网络/配置问题，并给运维验证动作。

不要默认读业务代码。优先检查：

- 下游 host/port 是否可达。
- DNS 是否解析。
- TLS 证书是否过期或链路不匹配。
- 服务是否发布中、重启中、限流中。
- 连接池、超时、重试、熔断配置是否异常。
- 如果是配置导致的错误，定位配置项和加载路径。

输出时明确写：`不是代码逻辑 bug` 或 `更像配置/基础设施问题`。

## Phase 3: 根因假设

### 3.1 证据等级

| 等级 | 证据 | 可支持的最高置信度 |
|---|---|---:|
| E0 | 只有用户描述 | 0.30 |
| E1 | 读到相关代码 | 0.55 |
| E2 | 代码 + 调用链/日志 | 0.70 |
| E3 | 代码 + 调用链 + git/测试/DB 任一验证 | 0.85 |
| E4 | 复现或测试验证通过 | 0.95 |

硬性限制：

- 未读取任何代码，不能声称代码根因；置信度不得超过 0.30。
- 未确认业务预期，不能把 Type C 结论写成确定性根因；置信度不得超过 0.55。
- 没有日志/DB/规则证据，数据异常根因不得超过 0.65。
- 基础设施问题的置信度表示诊断确定性，不代表代码修复确定性。

### 3.2 置信度计算

基础分：

- `0.20`：用户描述有可分析信号。
- `+0.20`：找到直接相关代码位置。
- `+0.15`：追到调用链或数据流。
- `+0.15`：日志、DB、业务规则或历史案例支持。
- `+0.10`：git 近期变更能解释问题引入。
- `+0.10`：相关测试或复现步骤验证。
- 上限 `0.95`。

扣分：

- `-0.30`：堆栈文件找不到。
- `-0.20`：代码证据和假设存在冲突。
- `-0.15`：只搜索到关键词，没有读到上下文。
- `-0.10`：输入缺少关键条件，如时间、环境、账号、业务类型。

### 3.3 根因模板

```markdown
### Root Cause Hypothesis

**Root Cause:** <一句话说明具体哪里错了，以及为什么导致现象>

**Evidence Chain:**
1. <证据 1：用户输入/日志/堆栈>
2. <证据 2：代码位置和分支>
3. <证据 3：调用链/规则/数据/git/test>

**Trigger Conditions:**
- <触发条件 1>
- <触发条件 2>

**Confidence:** <0.00-0.95>
```

只输出一个最强假设。其他可能性可以放在“待排除项”，但不要并列输出多个根因让用户自己选。

## Phase 4: 修复建议

### 4.1 修复优先级

1. **最小修复**：直接修复根因，影响面最小。
2. **防御性修复**：补错误处理、空值校验、幂等、日志。
3. **测试补强**：覆盖触发条件和边界枚举。
4. **治理建议**：只有当问题呈系统性时才写，如规则沉淀、监控、配置校验。

### 4.2 不同类型的修复规则

| 类型 | 修复原则 |
|---|---|
| A1 空指针/崩溃 | 在 nil/null 进入路径的最早合理位置处理，不只在 crash line 包一层 |
| A2 错误日志 | 修错误产生条件或错误传播路径，补上下文日志 |
| B1 数据异常 | 修状态流转、事务边界、幂等、补偿，不只改展示结果 |
| B2 业务异常 | 修配置、枚举、规则映射、上下游契约 |
| C1 逻辑偏差 | 修分支条件/业务规则实现，并补覆盖新业务类型的测试 |
| INFRA | 给配置/网络/依赖操作建议，不给业务代码 patch |

### 4.3 修复输出模板

```markdown
### Fix Recommendation

**Minimal Fix:** <改哪里、怎么改>

**Why It Works:** <逐条对应证据链解释>

**Verification:**
- <单测/集成测试/日志/DB 校验>
- <回归场景>

**Risk:**
- <可能影响的分支、服务、配置或数据>
```

## Phase 5: 输出格式

### 5.1 人类报告

```markdown
## Bug Analysis Report

### Summary
<2-3 句话：问题现象、最可能根因、建议修复>

### Classification
- Problem Type: <A1|A2|B1|B2|C1|INFRA>
- Language: <go|java|python|typescript|unknown>
- Depth: <quick|medium|deep|infra>
- Confidence: <0.00-0.95>

### Evidence Read
- <file/log/db/rule> - <为什么读它，读到了什么>

### Root Cause Hypothesis
<使用 Phase 3 模板>

### Fix Recommendation
<使用 Phase 4 模板>

### Verification Plan
<用户或 CI 应该怎么确认修复有效>
```

### 5.2 `AnalysisResult` 兼容 JSON

字段命名与 `core.models.AnalysisResult` 对齐，同时允许扩展字段：

```json
{
  "problem_type": "A1|A2|B1|B2|C1|INFRA",
  "root_cause": "one-sentence root cause",
  "code_locations": [
    {
      "file": "/absolute/path/to/file.go",
      "line": 42,
      "function": "FunctionName",
      "code_snippet": "optional short snippet",
      "verified": true
    }
  ],
  "fix_suggestion": "minimal actionable fix",
  "confidence": 0.85,
  "thinking": "brief public reasoning summary, not hidden chain-of-thought",
  "tool_calls": [
    {
      "tool": "rg|read|git|sls|mysql|weknora",
      "target": "what was searched/read",
      "result": "short evidence summary"
    }
  ],
  "references": [
    {
      "type": "domain_rule|similar_case|log|db|git|test",
      "source": "source id or file path",
      "summary": "why it matters"
    }
  ],
  "timeline": [
    {
      "time": "2026-05-18T10:00:00+08:00",
      "event": "optional event for data/log anomalies"
    }
  ],
  "raw_answer": "optional human report",
  "matched_cases": []
}
```

### 5.3 STATUS 块

最后必须输出：

```text
---STATUS---
result: DONE|BLOCKED|NEEDS_INPUT
confidence: <score>
problem_type: <A1|A2|B1|B2|C1|INFRA>
depth_used: <quick|medium|deep|infra>
files_read: <count>
evidence_level: <E0|E1|E2|E3|E4>
next: <下一步建议；如缺信息，写清要用户补什么>
---END STATUS---
```

## 工具与外部依赖策略

本 skill 必须能在没有外部系统时运行。外部系统只增强证据，不是硬依赖。

| 能力 | 可用时做什么 | 不可用时怎么办 |
|---|---|---|
| repo_path | 读代码、调用链、测试 | 要求用户补仓库，或给 E0/E1 低置信度结论 |
| SLS | traceId/time_range 查日志 | 跳过，说明未验证运行时证据 |
| MySQL | 验证数据状态 | 用代码和日志推断，置信度降级 |
| WeKnora | 查历史案例、业务规则 | 从代码常量/配置/用户描述提取规则 |
| domain config | 限定服务和业务域 | 全仓关键词搜索 |
| git | 查近期引入原因 | 不影响根因，但不能声称“某提交引入” |

## 质量门禁

### 必须做

- [ ] 先分类，再读代码。
- [ ] 输出根因前至少读取一个相关代码位置；否则声明为推测。
- [ ] 所有结论都有证据链。
- [ ] 修复建议必须对应触发条件。
- [ ] 给出验证步骤。
- [ ] 输出人类报告、JSON 和 STATUS。

### 禁止做

- [ ] 没有代码证据时输出确定性代码根因。
- [ ] 基础设施错误直接建议改业务代码。
- [ ] 空指针只在崩溃点加保护但不解释 nil 来源。
- [ ] 逻辑偏差没有确认业务预期就给高置信度。
- [ ] 把多个假设并列成“可能是 A/B/C”而不排序。
- [ ] 读超过深度上限仍没有新证据时继续无边界搜索。

## 常见场景策略

### Go panic / nil pointer

1. 读 panic 行。
2. 找 nil 对象来源。
3. 找上游是否忽略 `err` 或未初始化依赖。
4. 修复应优先处理错误返回或初始化路径。

### Java NullPointerException

1. 读异常行和方法入参。
2. 查对象是否由 Spring 注入、数据库查询、RPC 返回。
3. 区分“数据不存在”和“依赖未初始化”。
4. 修复应包含 null 语义处理和测试。

### 支付/订单/会员状态异常

1. 明确订单号/会员类型/支付类型/时间窗口。
2. 查日志时间线。
3. 查状态机代码和 DB 状态。
4. 对比业务规则和实际分支。
5. 修复状态流转、幂等、补偿或配置映射。

### 新枚举/新业务类型漏处理

1. 搜索枚举常量和 switch/if 分支。
2. 查所有白名单、黑名单、配置映射。
3. 查测试是否覆盖新枚举。
4. 修复所有必要分支，不只修用户触发的一个入口。

### 间歇性问题

默认按 B1/C1 deep 路径：

1. 查并发、缓存、异步任务、消息重复消费。
2. 查状态写入和读取是否跨事务/跨缓存。
3. 查重试和幂等。
4. 输出时明确“已证实”和“仍待排除”的边界。

## 示例：会员类型漏处理

用户输入：

```text
用户反馈：开通会员后没有调起支付，直接跳过了。
预期：type=14 应该和普通会员一样进入支付前检查。
```

分类：

- `problem_type`: `C1`
- `depth`: `deep`
- 原因：无堆栈，但存在明确“预期 vs 实际”的业务逻辑偏差。

取证：

1. 搜索 `type=14`、`MemberTypeWeComFreeUpgrade`、`preCheckIpSensitiveCity`。
2. 读取会员开通入口、拦截逻辑、支付跳转逻辑。
3. 对比 `rules.yaml` 中会员类型规则。
4. 查近期改动是否新增 type=14 但未补分支。

结论示例：

```markdown
Root Cause: `MemberTypeWeComFreeUpgrade(14)` 没有被加入支付前敏感城市检查和 redirect 分支，导致该会员类型走到默认跳过路径。

Evidence Chain:
1. 用户现象是 type=14 开通会员未调起支付，预期应进入支付前检查。
2. `preCheckIpSensitiveCity` 只处理旧会员类型，未包含 type=14。
3. `getRedirectUrl` 的 switch 未覆盖 type=14，默认分支返回空 redirect。

Confidence: 0.85
```

修复示例：

```markdown
Minimal Fix:
- 在 `preCheckIpSensitiveCity` 白名单中加入 `MemberTypeWeComFreeUpgrade`。
- 在 `getRedirectUrl` 中补 type=14 的跳转分支。
- 增加 type=14 的单元测试，覆盖“应调起支付”和“敏感城市拦截”两个场景。
```

## 维护建议

当本仓 Python workflow 演进时，这份 skill 需要同步更新三处：

1. `core.models.AnalysisResult` 字段变化。
2. `ProblemType` 枚举变化。
3. CLI/API 新增输入字段或外部连接器能力。

保持 skill 和代码模型同构，可以避免 AI 输出的 JSON 与程序消费模型脱节。
