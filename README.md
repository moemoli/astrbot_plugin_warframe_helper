# Warframe Helper 插件

面向 AstrBot 的 Warframe（国际服）信息查询与交易助手。

## 指令一览（部分支持平台参数）

### 世界状态

- `/警报`：当前警报
- `/裂缝`、`/普通裂缝`、`/钢铁裂缝`、`/九重天裂缝`
- `/奸商`
- `/仲裁`
- `/突击`
- `/电波`
- `/入侵`
- `/集团`
- `/平原`、`/夜灵平原`、`/魔胎之境`、`/地球昼夜`
- `/双衍王境`（别名：`双衍王镜`）
- `/轮回奖励`
- `/执行官猎杀`
- `/钢铁奖励`

> 世界状态类指令支持平台参数（如 `pc / ps4 / xb1 / swi / cn`），例如：`/裂缝 cn`、`/突击 国服`。

### 国服数据回退（参考 hole-wf-api 思路）

当 WeGame 直连接口出现 `-13/-16` 时，插件会按顺序尝试：

1. 签名 URL 直连
2. Playwright 浏览器抓取页面中的 `/ajax_get_worldState` 响应
3. 你配置的第三方备用 URL / Base URL（可对接自建 hole-wf-api）
4. 最后回退到国际服 PC worldstate，避免指令硬失败

以上回退链路已内置默认行为。

### 仲裁数据源（按 hole-wf-api 补齐）

`/仲裁` 现在采用多源策略：

1. 优先 `warframestat` 的 `/arbitration` JSON
2. 回退 `browse.wf/arbys.txt`（时间戳 + 节点）并自动映射节点名

### 订阅提醒

- `/订阅 <条件> [次数|永久]`
	- 裂缝示例：`/订阅 钢月`（钢铁 月球 全能 生存）
	- 平原示例：`/订阅 夜灵平原 黑夜 3`
- `/退订 <条件>`、`/退订 全部`
- `/订阅列表`

### 静态数据（PublicExport）

- `/武器 <名称>`
- `/战甲 <名称>`
- `/MOD <名称>`

### 掉落/遗物（WFCD/warframe-drop-data）

- `/掉落 <物品> [数量<=30]`：查询物品掉落地点（支持中文名，插件会尝试解析到英文；数据源条目多为英文名）
- `/遗物 <纪元> <遗物名>` 或 `/遗物 <遗物名>`：查询遗物奖池

### 交易（warframe.market）

- `/wfmap <query>`：简写/别名映射到 warframe.market 词条
- `/wm ...`：物品订单
- `/wmr ...`：紫卡（Riven）拍卖
- `/wfp ...`：价格相关查询

### QQ官方机器人 Markdown 以及按钮配置

> 你需要预先申请 ```消息模板功能```

- 按钮配置
```json
{
  "rows": [
    {
      "buttons": [
        {
          "id": "prev",
          "render_data": {
            "label": "⬅️上一页",
            "visited_label": "⬅️上一页",
            "style": 1
          },
          "action": {
            "type": 1,
            "permission": {
              "type": 2
            },
            "data": "wfp:prev",
            "reply": false,
            "enter": true,
            "unsupport_tips": "你的客户端版本不支持消息按钮"
          }
        },
        {
          "id": "next",
          "render_data": {
            "label": "➡️下一页",
            "visited_label": "➡️下一页",
            "style": 1
          },
          "action": {
            "type": 1,
            "permission": {
              "type": 2
            },
            "data": "wfp:next",
            "reply": false,
            "enter": true,
            "unsupport_tips": "你的客户端版本不支持消息按钮"
          }
        }
      ]
    }
  ]
}
```

- Markdown配置

```markdown
# {{.title}}
​
![result #{{.image_w}}px #{{.image_h}}px]({{.image}})
**指令**：{{.kind}}  
{{.page}}
{{.hint}}

```

## Playwright 浏览器安装

插件使用 Playwright 进行截图渲染。首次部署请先安装浏览器依赖与浏览器内核：

```bash
playwright install-deps
playwright install
```

若只需要 Chromium，也可以指定：

```bash
playwright install-deps chromium
playwright install chromium
```

> 说明：在 Windows/macOS 上，`playwright install-deps` 可能提示不支持，可忽略；`playwright install` 仍需执行。