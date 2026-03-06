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

## 远程浏览器配置

当 AstrBot 运行在 Linux Docker 且容器内缺少 Chromium 运行库时，推荐使用远程浏览器服务。

### 1) 一键启动 browserless

```bash
sudo docker run -d --name browserless \
  --restart unless-stopped \
  -p 3000:3000 \
  -e TOKEN=astrbot-token \
  -e MAX_CONCURRENT_SESSIONS=5 \
  -e CONNECTION_TIMEOUT=120000 \
  browserless/chrome:latest
```

### 2) 在插件配置中填写远程地址

新增配置项：`render_browser_ws_endpoint`

```json
{
  "render_browser_ws_endpoint": "ws://宿主机内网ip:3000?token=astrbot-token"
}
```

如果 AstrBot 与 browserless 运行在同一 Docker 网络（不同容器），可改为：

```json
{
  "render_browser_ws_endpoint": "ws://browserless:3000?token=astrbot-token"
}
```

### 3) 服务可用性检查

```bash
curl -s http://127.0.0.1:3000/json/version
```

返回浏览器版本 JSON 即表示远程服务可用。

### 4) 未配置远程地址时的默认行为

当 `render_browser_ws_endpoint` 为空时，插件会直接使用默认文生图（纯文本图片）兜底，不再尝试启动本地浏览器。