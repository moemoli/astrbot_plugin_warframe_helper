# Warframe（国际服）常用信息 API 速查

> 面向：astrbot_plugin_warframe_helper
>
> 目标：收集“国际服 Warframe”常用信息查询 API（世界状态、警报、奸商、裂缝等）与交易 API（warframe.market）。
>
> 说明：本文记录了**已验证可访问**与**可能受网络/反爬策略影响**的接口。若遇到 403/404/523，请先用浏览器验证可达性，再调整请求头/代理/缓存策略。

---

## 1) 世界状态（官方源：api.warframe.com）

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

---

## 5) 静态数据/词典（官方源：PublicExport）

> 适合查询“物品/战甲/MOD/武器”等静态数据。优点是权威、字段丰富、可缓存；缺点是单次导出文件可能较大。

- Base URL：`https://content.warframe.com/PublicExport`
- 获取索引：`/index_{lang}.txt.lzma`
- 获取导出：`/Manifest/{filename}!{token}`

### 常用导出（文件名随语言变化）

- `ExportWeapons_{lang}.json`：武器
- `ExportWarframes_{lang}.json`：战甲
- `ExportUpgrades_{lang}.json`：MOD/升级（包含多种 upgrade 条目，需按字段做容错筛选）

### 本插件已接入的指令（基于 PublicExport）

- `/武器 <名称>`
- `/战甲 <名称>`
- `/MOD <名称>`

---

## 6) 掉落表/遗物奖池（WFCD：warframe-drop-data）

> 说明：该数据源以“可直接下载的 JSON 文件”为主，适合做掉落检索与遗物奖池展示。
>
> 可达性提示：在部分网络环境中，`drops.warframestat.us` 可能出现 403；因此插件侧建议优先使用 GitHub Raw / CDN 镜像并做缓存。

### 6.1 all.slim.json（用于掉落检索）

- 文件：`data/all.slim.json`
- 典型字段：`item` / `place` / `rarity` / `chance`
- 适用指令：`/掉落 <物品>`（按 item 关键词匹配，返回 place + 概率）
- 插件增强：支持输入中文物品名（基于 PublicExport 做中英名称对齐，best-effort）

### 6.2 relics.json + 单遗物明细（用于遗物奖池）

- 索引：`data/relics.json`
  - 常见结构：`{"relics": [ {"tier":"Axi","relicName":"A1",...} ] }`
  - 用途：当用户只输入 `A1` 时，用索引反查可能的纪元（tier）

- 单遗物：`data/relics/$TIER/$RELIC_NAME.json`
  - 常见结构：`{"tier":"Axi","relicName":"A1","rewards": {"Intact":[...],"Exceptional":[...],"Flawless":[...],"Radiant":[...]}}`
  - 适用指令：`/遗物 <纪元> <遗物名>`

### 6.3 推荐的插件侧接入策略

- 多 URL 兜底：GitHub Raw + jsDelivr（以及必要时的备用分支路径）
- 本地磁盘缓存：减少大 JSON（尤其 `all.slim.json`）重复下载
