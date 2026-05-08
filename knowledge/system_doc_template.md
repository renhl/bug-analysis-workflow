# 系统架构文档模板

此模板用于记录系统架构，保存到 WeKnora 知识库后，可以帮助问题路由和调用链分析。

---

## 系统名称

<!-- 系统名称 -->

订单系统

## 系统概述

<!-- 系统的整体描述 -->

订单系统负责处理用户下单、支付、发货等全流程。

## 服务架构

```
┌─────────────────────────────────────────────────────┐
│                    web-frontend                      │
│                  (用户界面层)                         │
└─────────────────────┬───────────────────────────────┘
                      │ HTTP API
                      ▼
┌─────────────────────────────────────────────────────┐
│                  order-service                       │
│          (订单核心逻辑, Java)                         │
│                                                      │
│  ├─ OrderController   /api/orders                   │
│  ├─ OrderService      订单创建、状态管理             │
│  └─ OrderRepository   数据访问                       │
└───────┬─────────────┬─────────────┬────────────────┘
        │             │             │
        ▼             ▼             ▼
┌───────────┐  ┌───────────┐  ┌───────────┐
│ payment   │  │ inventory │  │   user    │
│ -service  │  │ -service  │  │ -service  │
│ (支付)    │  │ (库存)    │  │ (用户)    │
└─────┬─────┘  └───────────┘  └───────────┘
      │
      ▼
┌───────────────────────┐
│  payment-callback     │
│  (支付回调处理)        │
└───────┬───────────────┘
        │ 调用 order-service.updateStatus
        ▼
   [更新订单状态]
```

## 服务详情

### order-service

| 属性 | 值 |
|------|-----|
| 名称 | order-service |
| 语言 | Java (Spring Boot) |
| 仓库 | git@github.com:your-org/order-service.git |
| 端口 | 8080 |
| 数据库 | orders, order_items |

**主要功能**:
- 订单创建
- 订单查询
- 状态管理

**依赖服务**:
- payment-service (支付)
- inventory-service (库存)
- user-service (用户信息)

### payment-service

| 属性 | 值 |
|------|-----|
| 名称 | payment-service |
| 语言 | Java |
| 仓库 | git@github.com:your-org/payment-service.git |
| 端口 | 8081 |
| 数据库 | payments |

**主要功能**:
- 支付发起
- 支付查询

### payment-callback

| 属性 | 值 |
|------|-----|
| 名称 | payment-callback |
| 语言 | Java |
| 仓库 | git@github.com:your-org/payment-callback.git |
| 端口 | 8082 |

**主要功能**:
- 接收支付回调
- 更新订单状态（调用 order-service）

**关键流程**:
```
支付成功 → callback 接收 → 调用 order-service.updateStatus → 订单状态更新
```

---

## 关键调用链

### 下单流程

```
用户下单 → order-service.createOrder
         → inventory-service.checkStock
         → order 创建成功
```

### 支付流程

```
用户支付 → payment-service.createPayment
         → 第三方支付
         → payment-callback.receiveCallback
         → order-service.updateOrderStatus
```

### 发货流程

```
商家发货 → order-service.shipOrder
         → inventory-service.deductStock
         → order 状态更新为已发货
```

---

## 数据库关系

```
orders (订单表)
├─ id
├─ user_id
├─ status
├─ amount
└─ created_at

payments (支付表)
├─ id
├─ order_id → orders.id
├─ status
├─ amount
└─ created_at

inventory (库存表)
├─ id
├─ product_id
├─ count
└─ updated_at
```

---

## 文档元数据

```json
{
  "type": "system_doc",
  "services": ["order-service", "payment-service", "payment-callback", "inventory-service"],
  "keywords": ["订单系统", "架构", "调用链"]
}
```