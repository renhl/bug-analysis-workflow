# Bug 分析：member_config 期数上限导致越期报错

**分析日期**: 2026-05-15
**问题标题**: member_config 表 end_period 有限值导致超出期数的会员访问报错
**严重程度**: P1（潜在，当前仅影响少量用户，随时间推移会扩大）

---

## 1. 问题现象

用户访问 `GET /composite/equity/member/detail?member_type=13` 时返回：
```json
{"code": 10001, "msg": "系统开小差了，请稍后再试～"}
```

经排查根因是 `member_config` 表的 `begin_period` / `end_period` 设计：每条配置记录只覆盖一个期数范围，当会员当前期数超出所有配置的 `end_period` 时，查询返回空 → 报错。

---

## 2. 根因机制

### 查询逻辑

Go 代码在 `getRenewalInfo`（[info.go:362](internal/services/member/info.go#L362)）中查询续费配置：

```go
period = member.Period + 1  // 当前在第 3 期，续费查第 4 期
memberConfig = GetByMemberTypeAndPeriodAndFlag(type, period, flag)
// SQL: WHERE type = ? AND begin_period <= 4 AND end_period >= 4 AND status = 1
```

同样在 `buildPayConfigs`（[info.go:417](internal/services/member/info.go#L417)）中：
```go
QueryConfigByMemberTypeAndPeriod(memberType, period)
// SQL: WHERE type = ? AND begin_period <= ? AND end_period >= ? AND status = 1
```

如果查不到任何记录 → return error → API 返回 500 → 网关包装为 10001。

### 正确模式

其他会员类型的配置模式是「首月/钩子期有上限，正价期 end_period=9999」：

| type | flag | begin-end | stage | 说明 |
|---|---|---|---|---|
| 10 | enjoy_first_free | 1-1 | 钩子 | 首月免费钩子 |
| 10 | enjoy_first_free | 2-9999 | 正价 | 后续无限期正价 |
| 10 | enjoy_first_one | 1-3 | 钩子 | 前三月 1 元钩子 |
| 10 | enjoy_first_one | 4-9999 | 正价 | 第 4 月起无限期正价 |

### 异常模式

**type=10 flag=enjoy_regular（宝付正价）只有期 1-3 的配置，没有 4-9999 的正价兜底：**

| type | flag | channel | begin-end | stage |
|---|---|---|---|---|
| 10 | enjoy_regular | **bf** | 1-3 | 0（正价） |
| 10 | enjoy_regular | **jd** | 1-3 | 0（正价） |
| 10 | enjoy_regular | **wechat** | 1-3 | 0（正价） |

这意味着 type=10 中通过 enjoy_regular 渠道购买的用户，在第 4 期及以后访问会员详情页时，会因查不到配置而报错。

---

## 3. 当前影响面

| 组合 | 当前最高期 | 配置上限 | 状态 |
|---|---|---|---|
| type=10 flag=enjoy_regular | 第 1 期（3 人） | 期 3 | ✅ 暂时安全 |
| type=10 flag=enjoy_first_one | 第 5 期（1 人） | 期 9999 | ✅ 安全 |
| type=10 flag=enjoy_first_free | 有配置覆盖 | 期 9999 | ✅ 安全 |
| type=6/8/9/11/12/13/14/20 | 各有配置 | 期 9999 | ✅ 安全 |

**当前影响很小（仅 3 人在第 1 期），但这是一个定时炸弹** — 随着会员续费周期推进，任何 type=10 enjoy_regular 的用户到达第 4 期时都会触发此问题。

---

## 4. 涉及代码

| 文件 | 行号 | 方法 | 影响 |
|---|---|---|---|
| [info.go:362](internal/services/member/info.go#L362) | getRenewalInfo | 续费查期数+1 配置 | 超出范围报 "会员配置不存在" |
| [info.go:417](internal/services/member/info.go#L417) | buildPayConfigs | 查询当前期支付配置 | 超出范围返回空配置 |
| [info.go:246](internal/services/member/info.go#L246) | checkContractStatus | 查询签约配置 | 不受影响（按 memberType 查，不按 period） |
| [member_config.go:100](internal/model/member_config.go#L100) | GetByMemberTypeAndPeriodAndFlag | SQL 查询 | `begin_period <= ? AND end_period >= ?` |
| [member_config.go:238](internal/model/member_config.go#L238) | QueryConfigByMemberTypeAndPeriod | SQL 查询 | 同上 |

---

## 5. 修复方案

### 方案 A：补充数据库配置（推荐，立即生效）

在 `member_config` 表中为 type=10 flag=enjoy_regular 追加期 4-9999 的正价配置：

```sql
-- bf 渠道
INSERT INTO member_config (type, pay_channel, flag, begin_period, end_period, stage,
  contract_method, status, product_sku, hook_product_sku, name, created_at, updated_at)
SELECT 10, 'bf', 'enjoy_regular', 4, 9999, 0, 1, 1, product_sku, hook_product_sku,
  '轻享健康会员-宝付正价-4期后', NOW(), NOW()
FROM member_config
WHERE type = 10 AND pay_channel = 'bf' AND flag = 'enjoy_regular' AND stage = 0
LIMIT 1;

-- jd 渠道
INSERT INTO member_config (type, pay_channel, flag, begin_period, end_period, stage,
  contract_method, status, product_sku, hook_product_sku, name, created_at, updated_at)
SELECT 10, 'jd', 'enjoy_regular', 4, 9999, 0, 1, 1, product_sku, hook_product_sku,
  '轻享健康会员-京东正价-4期后', NOW(), NOW()
FROM member_config
WHERE type = 10 AND pay_channel = 'jd' AND flag = 'enjoy_regular' AND stage = 0
LIMIT 1;

-- wechat 渠道
INSERT INTO member_config (type, pay_channel, flag, begin_period, end_period, stage,
  contract_method, status, product_sku, hook_product_sku, name, created_at, updated_at)
SELECT 10, 'wechat', 'enjoy_regular', 4, 9999, 0, 1, 1, product_sku, hook_product_sku,
  '轻享健康会员-微信正价-4期后', NOW(), NOW()
FROM member_config
WHERE type = 10 AND pay_channel = 'wechat' AND flag = 'enjoy_regular' AND stage = 0
LIMIT 1;
```

### 方案 B：代码降级（长期预防）

在 `getRenewalInfo` 和 `buildPayConfigs` 中增加兜底逻辑：

```go
// 如果 period 超出配置范围，回退到最近一期配置
if memberConfig == nil {
    memberConfig = GetLastPeriodConfig(memberType, flag) // 取 max(end_period) 的配置
}
```

---

## 6. 预防建议

### 新增会员类型时的配置 checklist

| 检查项 | 说明 |
|---|---|
| enum 定义 | `MemberTypeXXX = N` 已添加到 enum/member.go |
| member_config 所有渠道 | 每个 pay_channel 都需要 begin_period=1 的配置 |
| member_config 正价兜底 | 每个 channel+flag 组合必须有一条 end_period=9999 的正价记录 |
| 代扣处理器 | withhold_task.go 的 processorsV2 注册 |
| 订单初始化 | factory.go 的 initializerFactory 和 deductInitializerFactory 注册 |
| GetMemberDetail switch | info.go 中有对应的 case 分支 |
| 敏感城市拦截 | intercept.go 的 preCheckIpSensitiveCity、CheckIntercept 入口门控、getRedirectUrl |

### 监控建议

```sql
-- 定期检查是否有会员期数超出配置上限
SELECT m.type, m.flag, m.period, mc.max_end
FROM member m
LEFT JOIN (
    SELECT type, flag, MAX(end_period) as max_end
    FROM member_config WHERE status = 1
    GROUP BY type, flag
) mc ON m.type = mc.type AND (m.flag = mc.flag OR (m.flag = '' AND mc.flag IS NULL))
WHERE mc.max_end IS NOT NULL AND m.period > mc.max_end;
```

---

## 7. 验证结果

通过数据库全量扫描确认：

- ✅ 当前所有会员的期数都在配置范围内（无即时影响）
- ⚠️ type=10 flag=enjoy_regular 仅配置了期 1-3（3 人在第 1 期，未来会触发）
- ✅ 其余所有会员类型均有 end_period=9999 的正价兜底配置