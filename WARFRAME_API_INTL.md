# Warframe（国际服）常用信息 API 速查

> 面向：astrbot_plugin_warframe_helper
>
> 目标：收集“国际服 Warframe”常用信息查询 API（世界状态、警报、奸商、裂缝等）与交易 API（warframe.market）。
>
> 说明：本文记录了**已验证可访问**与**可能受网络/反爬策略影响**的接口。若遇到 403/404/523，请先用浏览器验证可达性，再调整请求头/代理/缓存策略。

---

## 1) 世界状态 / 事件（推荐：WarframeStat.us）

### 1.1 WarframeStat.us（非官方聚合，推荐用于 Bot 查询）

- Base URL：`https://api.warframestat.us`
- 文档：`https://docs.warframestat.us/`
- 平台路径：`/pc`、`/ps4`、`/xb1`、`/swi`（按文档为准）
- 返回：JSON（多数时间字段为 ISO 8601 字符串）

#### 语言参数（重要）

- `language` 查询参数在文档中被标注为 required（必传）。
- 建议：**总是显式传** `language=zh`（或你需要的语言），避免缓存导致返回语言不一致。

#### 常用端点（以 PC 为例）

- 世界状态总览
  - `GET /pc?language=zh`
- 警报
  - `GET /pc/alerts?language=zh`
- 裂缝
  - `GET /pc/fissures?language=zh`
- 奸商（虚空商人 Baro Ki'Teer）
  - `GET /pc/voidTrader?language=zh`
- 仲裁
  - `GET /pc/arbitration?language=zh`
- 突击
  - `GET /pc/sortie?language=zh`
- 夜波
  - `GET /pc/nightwave?language=zh`
- 入侵
  - `GET /pc/invasions?language=zh`
- 平原/开放世界循环（常用于“现在白天/黑夜、轮换剩余时间”）
  - `GET /pc/earthCycle?language=zh`
  - `GET /pc/cetusCycle?language=zh`
  - `GET /pc/cambionCycle?language=zh`

> 备注：部分端点可能偶发不可用（例如曾观察到 `GET /pc/fissures` 返回 523）。建议对“世界状态类”接口做 30–60 秒缓存，并在失败时回退到“总览 /pc”。

#### 示例（PowerShell）

```powershell
# 警报（中文）
Invoke-RestMethod 'https://api.warframestat.us/pc/alerts?language=zh'

# 裂缝（中文）
Invoke-RestMethod 'https://api.warframestat.us/pc/fissures?language=zh'

# 奸商（中文）
Invoke-RestMethod 'https://api.warframestat.us/pc/voidTrader?language=zh'
```

#### 示例（最小字段提取）

```powershell
# 只看裂缝的 node / tier / expiry
Invoke-RestMethod 'https://api.warframestat.us/pc/fissures?language=zh' |
  Select-Object node tier expiry
```

---

## 2) 世界状态（官方源：api.warframe.com）

### 2.1 官方 WorldState（PC）

- 首选 URL（通常更可用）：`https://content.warframe.com/dynamic/worldState.php`
- 备用 URL（部分环境可能返回 403）：`https://api.warframe.com/cdn/worldState.php`

#### 注意事项

- 该接口返回体是**超大 JSON**（包含大量内部字段、活动、商店、赛季等）。
- HEAD 探测时可能看到 `Content-Type: text/html; charset=UTF-8`，但实际返回内容为 JSON（以解析结果为准）。
- 建议：仅在你需要“最权威/最完整”字段时使用；日常 Bot 查询优先用 WarframeStat.us。

#### 示例（PowerShell）

```powershell
# 拉取并解析 JSON（注意：返回很大）
Invoke-RestMethod 'https://content.warframe.com/dynamic/worldState.php'
```

---

## 3) 交易/市场（warframe.market）

### 3.1 概览

- 网站：`https://warframe.market/`
- API Base（常见写法）：`https://api.warframe.market/v1`
- API Base（新接口，当前更可用）：`https://api.warframe.market/v2`

> 可达性提示：在本机环境探测到：
> - `HEAD https://api.warframe.market/v1/items` 返回 `404`（且 `Content-Type: application/json`）
> - `GET https://api.warframe.market/v1/items/lex_prime_set/orders` 返回 `403`
> - `GET https://api.warframe.market/v2/items` 返回 `200`
> - `GET https://api.warframe.market/v2/items/wukong_prime_set` 返回 `200`
>
> 这通常意味着：站点可能有区域/反爬/UA/频控策略，或需要从可访问网络调用。

### 3.2 常用“查询类”端点（未在本环境完全验证）

以下端点是 warframe.market 生态里最常用的一组（建议你在能直连的网络下用浏览器/脚本验证）：

- 物品列表
  - `GET /items`
- 单物品详情（或元数据）
  - `GET /items/{item_url_name}`
- 单物品订单（买/卖单）
  - `GET /items/{item_url_name}/orders`
- 单物品统计
  - `GET /items/{item_url_name}/statistics`

#### 典型用途

- “按物品查询最低卖价/最高买价”：用 `.../orders`，按 `order_type` 与在线状态过滤。
- “价格趋势/历史”：用 `.../statistics`。

#### 调用建议

- 显式设置 `User-Agent` 与 `Accept: application/json`。
- 对订单/统计做短缓存（例如 30–120 秒），避免频繁刷新触发限制。
- 若出现 403/404：
  - 先用浏览器打开同 URL 确认可达性
  - 再尝试更换网络/代理
  - 最后再考虑是否需要额外请求头（platform / language 等以官方说明为准）

---

## 4) 建议的插件侧接入策略（供后续实现参考）

- **世界状态类**：默认使用 WarframeStat.us（支持 `language=zh`，字段对 Bot 更友好）
- **权威兜底**：需要时再访问官方 `worldState.php`（注意体积与缓存）
- **交易类**：优先 warframe.market；若目标环境经常被拦截，考虑增加缓存与失败提示（“当前网络无法访问 warframe.market API”）
