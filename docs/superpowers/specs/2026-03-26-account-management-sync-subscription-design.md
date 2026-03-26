# 账号管理单账号同步订阅设计

## 背景

当前“绑卡任务”页面中的“同步订阅”动作已经能通过正确的订阅检测链路，复核账号当前的订阅状态，并在部分错误 workspace / org 作用域场景下避免把 Team 账号误判为 Free。用户希望将这条已验证正确的能力复用到“账号管理”页面，使没有绑卡任务的账号也能直接触发同步。

目标不是新增一套概览刷新逻辑，而是复用现有“绑卡任务 -> 同步订阅”的检测与回写路径，让账号管理页面也能直接执行相同动作。

## 用户确认的范围

- 复用的动作：**绑卡任务页的“同步订阅”**
- 入口形式：**账号管理单账号入口**
- 入口位置：**账号管理操作列的“更多”菜单**
- 调用方式：**直接执行，不依赖绑卡任务存在**

## 目标

在账号管理页为单个账号新增“同步订阅”入口。点击后：

1. 直接对该账号执行与绑卡任务一致的订阅同步逻辑
2. 复用已有的订阅检测、token 刷新、作用域复核和数据库回写规则
3. 返回同步结果给前端并刷新账号列表
4. 不要求账号存在绑定任务，不创建绑定任务

## 非目标

- 不新增批量同步入口
- 不修改账号总览 `/accounts/overview/refresh` 的行为
- 不调整“显示套餐”的前端 UI 逻辑
- 不新增独立的 workspace/org 手工选择 UI
- 不改动绑卡任务页已有按钮行为

## 现有可复用能力

### 前端现状

绑卡任务页已有“同步订阅”按钮：

- `static/js/payment.js` 中 `syncBindCardTask(taskId)`
- 调用接口：`POST /payment/bind-card/tasks/{task_id}/sync-subscription`

账号管理页当前操作位于：

- `static/js/accounts.js` 渲染操作菜单
- 当前“更多”菜单包含：刷新、上传、标记

### 后端现状

绑卡任务同步接口位于：

- `src/web/routes/payment.py` 中 `sync_bind_card_task_subscription()`

该接口内部复用了：

- `_check_subscription_detail_with_retry()`

该检测链路已经包含：

1. 订阅状态检测
2. 必要时 token refresh
3. 去除 `ChatGPT-Account-Id` 的无作用域复核，降低错误 workspace 作用域导致的 free 误判
4. 按既有规则回写 `account.subscription_type` / `subscription_at`

## 设计方案

### 方案选择

采用**新增账号级同步接口 + 前端新增单账号菜单入口**。

原因：

- 最大程度复用已验证正确的绑卡任务同步逻辑
- 不强耦合到绑卡任务模型
- 比复用批量接口更清晰，响应结构也更适合单账号交互
- 改动范围小，便于验证和回归

### 后端设计

新增账号级接口，例如：

- `POST /payment/accounts/{account_id}/sync-subscription`

该接口职责：

1. 根据 `account_id` 查询账号
2. 解析运行时代理（保持与绑卡任务同步逻辑一致）
3. 调用共享的“检测 + 回写” helper，内部复用 `_check_subscription_detail_with_retry()` 获取 `detail, refreshed`
4. 返回单账号同步结果

#### 请求体约定

本次前端契约固定为：

- 前端始终调用 `api.post('/payment/accounts/{id}/sync-subscription', {})`
- 后端必须接受空 JSON 对象 `{}` 作为合法请求体

本次规格**不要求**兼容“完全省略请求体”的调用方式；实现只需要保证前端发送 `{}` 时不会触发 422。

#### 共享逻辑要求

本次不要在账号级接口里复制第三份订阅同步逻辑。

应抽出或复用一个共享 helper，负责以下职责：

1. 执行 `_check_subscription_detail_with_retry()`
2. 根据检测结果将订阅状态回写到 `account`
3. 返回 `detail, refreshed, status, now` 或等价结果

要求以下入口复用同一套回写规则：

- 绑卡任务 `sync_bind_card_task_subscription()`
- 新增账号级 `sync-subscription` 接口
- 如当前批量检测路径也存在相同回写逻辑，应尽量收敛到同一 helper


#### 回写规则

与 `sync_bind_card_task_subscription()` 保持一致：

- 当检测结果为 `plus` 或 `team`：
  - 更新 `account.subscription_type`
  - 更新 `account.subscription_at`
- 当检测结果为 `free` 且置信度为 `high`：
  - 清空 `account.subscription_type`
  - 清空 `account.subscription_at`
- 当检测结果为 `free` 且置信度不是 `high`：
  - 不覆盖已有订阅

#### 返回结构

返回结构为强制契约，必须包含以下字段：

```json
{
  "success": true,
  "subscription_type": "team",
  "detail": {
    "status": "team",
    "source": "wham_usage.no_scope.plan",
    "confidence": "medium",
    "note": "..."
  },
  "account_id": 123,
  "account_email": "user@example.com"
}
```

其中：

- 顶层必填字段：`success`、`subscription_type`、`detail`、`account_id`、`account_email`
- `detail` 内必填字段：`status`、`source`、`confidence`、`note`
- `subscription_type`：前端用于 toast 和列表刷新后的结果判断
- `detail.status`：必须与本次检测得到的订阅状态一致，供前端展示与手工验证使用
- `detail.source` / `detail.confidence` / `detail.note`：前端用于提示诊断信息
- 返回字段命名需与绑卡任务同步接口尽量保持一致，避免前端拼接两套分支逻辑

### 前端设计

在账号管理页单账号“更多”菜单新增一项：

- 文案：`同步订阅`

位置顺序建议：

1. 刷新
2. 上传
3. 同步订阅
4. 标记

新增前端函数，例如：

- `syncAccountSubscription(id)`

行为：

1. 调用 `POST /payment/accounts/{id}/sync-subscription`，请求体固定传 `{}`
2. 根据返回的 `subscription_type / detail.source / detail.confidence / detail.note` 生成 toast
3. 调用 `loadAccounts()` 刷新账号列表

提示规则与绑卡任务页保持一致：

- `PLUS/TEAM`：成功 toast
- `FREE`：warning toast，并带上 `source/confidence/note`

## 数据流

1. 用户在账号管理页点击“更多 -> 同步订阅”
2. 前端调用账号级同步接口
3. 后端对目标账号执行 `_check_subscription_detail_with_retry()`
4. 后端依据检测结果回写订阅字段
5. 后端返回结果明细
6. 前端展示 toast
7. 前端刷新账号列表，更新订阅徽标

## 错误处理

- 账号不存在：返回 404
- 订阅检测失败：返回 500，并包含简洁错误信息
- 前端 toast 展示失败原因，不引入额外重试
- 不因没有绑卡任务而报错；该能力就是为“无绑卡任务账号”设计

## 测试与验证

### 手工验证

准备一个：

- 数据库中当前订阅不是最新状态
- 但通过现有绑卡任务“同步订阅”可以纠正的账号

验证步骤：

1. 打开账号管理页
2. 在目标账号上点击“更多 -> 同步订阅”
3. 观察 toast 中返回的 `status/source/confidence`
4. 刷新后确认账号列表订阅状态更新
5. 再到绑卡任务页对同账号执行“同步订阅”，确认结果一致

### 回归验证

- 账号管理现有“刷新 / 上传 / 标记 / 删除”操作不受影响
- 绑卡任务页“同步订阅”行为不变
- free 且低置信度结果不会误清空已有 plus/team

## 风险与控制

### 风险 1：复制逻辑导致后续分叉

控制：

- 优先抽取或直接复用现有检测与回写逻辑
- 避免在新接口里写一套与绑卡任务不同的判断分支

### 风险 2：前端提示语与绑卡任务页不一致

控制：

- 尽量沿用同样的字段和提示模式
- 保持 `source/confidence/note` 的可见性，方便诊断

### 风险 3：用户误以为该动作会打开 checkout 或创建任务

控制：

- 按钮命名明确使用“同步订阅”
- 不出现“绑卡”“支付”“任务”等字样

## 实现边界

本次仅实现：

- 账号管理单账号“同步订阅”菜单项
- 对应账号级后端接口
- 与绑卡任务同步逻辑一致的检测和回写行为

后续如果需要，再单独设计批量同步入口。