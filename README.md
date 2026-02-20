# astrbot_plugin_warframe_helper

AstrBot Warframe Helper 插件

> [!NOTE]
> Repo: https://github.com/moemoli/astrbot_plugin_warframe_helper
> 
> [AstrBot](https://github.com/AstrBotDevs/AstrBot) is an agentic assistant for both personal and group conversations. It can be deployed across dozens of mainstream instant messaging platforms, including QQ, Telegram, Feishu, DingTalk, Slack, LINE, Discord, Matrix, etc. In addition, it provides a reliable and extensible conversational AI infrastructure for individuals, developers, and teams. Whether you need a personal AI companion, an intelligent customer support agent, an automation assistant, or an enterprise knowledge base, AstrBot enables you to quickly build AI applications directly within your existing messaging workflows.

# Supports

- [AstrBot Repo](https://github.com/AstrBotDevs/AstrBot)
- [AstrBot Plugin Development Docs (Chinese)](https://docs.astrbot.app/dev/star/plugin-new.html)
- [AstrBot Plugin Development Docs (English)](https://docs.astrbot.app/en/dev/star/plugin-new.html)

# 词条映射（别名/简写 -> 官方词条）

本插件包含一个小型“词条映射层”，用于将常见简写/外号解析为 warframe.market 的官方词条（slug）。

## 指令

- `/wfmap <query>`
	- 示例：`/wfmap 猴p` -> `Wukong Prime Set (wukong_prime_set)`

- `/wm <物品> [平台] [收/卖] [语言] [数量]`
	- 查询成功时返回一张渲染图片（包含物品名、物品图、订单列表与玩家头像）。
	- 示例：`/wm 猴p pc`（查询 PC 平台出售单，低到高前 10）
	- 示例：`/wm 猴p pc 收`（查询 PC 平台收购单，低到高前 10）
	- 示例：`/wm 猴p pc 收 zh 10`（指定语言与返回数量；语言缺失时默认 `zh`，数量缺失时默认 `10`）

- `/wmr <武器> [条件...]`
	- 查询 warframe.market 紫卡（Riven）一口价拍卖，查询成功时返回渲染图片。
	- 示例：`/wmr 绝路 双暴 负任意 12段 r槽`
		- 双暴：正面包含 `critical_chance` + `critical_damage`
		- 负任意：需要带负面，但负面词条不限
		- 12段：MR≥12
		- r槽：极性为 R（zenurik）
	- 当条件里出现未识别的词条简写时，会尝试调用当前会话的 LLM 猜测对应的 riven url_name，并通过 warframe.market 的属性列表二次校验后才会生效。

当内置规则与本地别名未命中时，插件会调用 AstrBot 当前会话的聊天模型（LLM）生成候选 slug，并通过 warframe.market v2 API 校验存在性后再返回结果。

## 扩展别名

创建或编辑：

- `data/plugin_data/astrbot_plugin_warframe_helper/aliases.json`

示例：

```json
{
	"aliases": {
		"猴p": "wukong_prime_set",
		"悟空p": "wukong_prime_set"
	}
}
```
