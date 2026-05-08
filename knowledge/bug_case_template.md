# Bug 案例文档模板

此模板用于记录 Bug 分析结果，保存到 WeKnora 知识库后，可以帮助快速定位相似问题。

---

## 问题描述

<!-- 简要描述问题现象 -->

订单支付成功后，订单状态仍显示"待支付"。

## 问题表现

<!-- 具体表现：用户反馈、数据状态、日志错误等 -->

- 用户反馈：支付成功但订单状态未更新
- 数据状态：order.status=1 (待支付), payment.status=2 (支付成功)
- 日志错误：14:30:03.289 ERROR DB connection timeout

## 根因定位

<!-- 根本原因分析 -->

### 代码位置

| 文件 | 行号 | 函数 |
|------|------|------|
| OrderService.java | 245 | updateOrderStatus() |

### 根因分析

updateOrderStatus() 方法缺少事务保护（@Transactional），在 DB 连接超时时，setStatus 已执行但 save 失败，导致状态不一致。

### 代码片段

```java
// OrderService.java:245
public void updateOrderStatus(PaymentResult result) {
    Order order = orderRepo.findById(result.getOrderId());
    order.setStatus(result.getStatus());  // ← 直接设置
    orderRepo.save(order);                // ← 无事务保护，可能失败
}
```

## 修复方案

<!-- 修复建议 -->

1. **立即修复**: 在 updateOrderStatus() 方法上添加 @Transactional 注解
2. **增强保护**: 添加重试机制处理连接超时
3. **监控完善**: 添加状态更新成功的日志确认

```java
@Transactional
public void updateOrderStatus(PaymentResult result) {
    Order order = orderRepo.findById(result.getOrderId());
    order.setStatus(result.getStatus());
    orderRepo.save(order);
    log.info("Order status updated: orderId={}, status={}", order.getId(), order.getStatus());
}
```

## 分析过程

<!-- 可选：记录分析过程，帮助理解 -->

1. 数据检测：发现 order.status ≠ payment.status
2. 日志分析：发现 14:30:03 有 DB 连接超时
3. 代码搜索：找到 updateOrderStatus() 函数
4. 根因推理：缺少事务保护导致并发问题

## 关联信息

<!-- 可选：关联的组件、案例、文档 -->

- 相关服务：order-service, payment-service, payment-callback
- 相关案例：#BC-2024-089（类似的状态同步问题）
- 相关文档：订单系统架构说明.md

## 关键词

<!-- 用于检索的关键词 -->

- 支付状态
- 状态同步
- 事务
- OrderService
- 并发问题

---

## 文档元数据（用于 WeKnora）

```json
{
  "type": "bug_case",
  "code_location": "OrderService.java:245",
  "problem_type": "B1",
  "services": ["order-service", "payment-service"],
  "keywords": ["支付", "状态", "事务", "OrderService"]
}
```