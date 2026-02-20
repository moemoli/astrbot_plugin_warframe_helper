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

# 安装

## 方式 1：插件市场安装

在 AstrBot WebUI 的「插件市场」中搜索 `Warframe 助手` 或 `astrbot_plugin_warframe_helper` 并安装、启用。

## 方式 2：手动安装（源码/离线环境）

将本仓库（或插件代码目录）放到 AstrBot 的插件目录：

- 目录结构示例：`data/plugins/astrbot_plugin_warframe_helper/`

然后重启 AstrBot，并在 WebUI 启用该插件。

# 配置

插件配置位于 `data/plugin_data/astrbot_plugin_warframe_helper/`（由 AstrBot 创建）。

## LLM 兜底解析模型

当遇到未识别的简写/外号时，插件可以使用 LLM 兜底解析。

- 配置项：`unknown_abbrev_provider_id`
- 含义：用于“未知简写解析”的聊天模型 Provider ID（为空则不启用 LLM 兜底）

# 指令

说明：

- **平台**：`pc` / `ps4` / `xb1` / `swi`（也支持常见别名输入，如 `ps`、`xbox`、`switch` 等，最终会归一化）
- **语言**：`zh`（默认）或 `en`（以及类似 `zh-tw` 这种格式），用于名称显示与部分文本输出
- 图片渲染失败时会自动回退为纯文本（避免“无响应”）

## 词条映射（别名/简写 -> 官方词条）

本插件包含一个小型“词条映射层”，用于将常见简写/外号解析为 warframe.market 的官方词条（slug）。

## 指令

- `/wfmap <query>`
	- 示例：`/wfmap 猴p` -> `Wukong Prime Set (wukong_prime_set)`

- `/wm <物品> [平台] [收/卖] [语言] [数量]`
	- 查询成功时返回一张渲染图片（包含物品名、物品图、订单列表与玩家头像）。
	- 示例：`/wm 猴p pc`（查询 PC 平台出售单，低到高前 10）
	- 示例：`/wm 猴p pc 收`（查询 PC 平台收购单，低到高前 10）
	- 示例：`/wm 猴p pc 收 zh 10`（指定语言与返回数量；语言缺失时默认 `zh`，数量缺失时默认 `10`）
	- 备注：当你“回复 /wm 的结果图”并只发送数字（如 `1`、`2`）时，插件会返回对应玩家的 `/w` 私聊话术（缓存约 8 分钟）。

- `/wmr <武器> [条件...]`
	- 查询 warframe.market 紫卡（Riven）一口价拍卖，查询成功时返回渲染图片。
	- 示例：`/wmr 绝路 双暴 负任意 12段 r槽`
		- 双暴：正面包含 `critical_chance` + `critical_damage`
		- 负任意：需要带负面，但负面词条不限
		- 12段：MR≥12
		- r槽：极性为 R（zenurik）
	- 当条件里出现未识别的词条简写时，会尝试调用当前会话的 LLM 猜测对应的 riven url_name，并通过 warframe.market 的属性列表二次校验后才会生效。

### /wm 详解

语法：`/wm <物品> [平台] [收/卖] [语言] [数量]`

- `<物品>`：必填。支持中文外号/简称（会先走本地映射，再可选 LLM 兜底）
- `[平台]`：可选，默认 `pc`
- `[收/卖]`：可选，默认 `卖`（sell）。支持输入 `收`/`买`/`buy` 或 `卖`/`出`/`sell`
- `[语言]`：可选，默认 `zh`。例如 `en`、`zh-tw`
- `[数量]`：可选，默认 `10`，范围 `1~20`

更多示例：

- `/wm 毒妈 pc`
- `/wm 毒妈 收 5`（平台省略则默认 pc）
- `/wm wukong prime set pc sell en 10`

### /wmr 详解

语法：`/wmr <武器> [条件...]`

#### 常用条件

- 平台：同 `/wm`（默认 `pc`）
- 数量：纯数字（默认 `10`，范围 `1~20`）
- 语言：`zh`（默认）/ `en`（以及 `zh-tw`）
- MR 门槛：`12段` 或 `MR12`（表示 `MR≥12`）
- 极性：
	- `v槽`/`d槽`/`-槽`/`r槽`
	- 或 `v极性`/`极性v` 等同义写法
	- 或直接写 `madurai` / `vazarin` / `naramon` / `zenurik`

#### 词条条件（正面/负面）

- 直接写简写（会走 `RIVEN_STAT_ALIASES` 与本地映射）：例如 `多重`、`暴伤`、`触发`、`攻速` 等
- “双暴/双爆”组合：`双暴`/`双爆` 会自动展开为 `暴击率 + 暴击伤害`
- 组合简写：如 `双爆毒` 会展开为 `双暴 + 毒(元素)`

负面相关：

- `负任意`/`有负`：要求“必须带负面”，但不限定负面词条
- `无负`：明确不要负面（并清空已指定的负面词条）
- `负xxx`：指定负面词条，例如 `负暴击率`

限制说明：

- warframe.market 当前不支持紫卡词条“对 Sentient 伤害（S歧视）”，输入 `S歧视` 会直接提示并停止查询。

更多示例：

- `/wmr 绝路 双爆毒 负任意 pc 10`
- `/wmr soma 暴伤 多重 无负 MR8 v槽 en`

## 世界状态/活动查询

这些指令默认返回图片（失败则回退为文本）。均支持在参数中带上 `[平台]`（默认 `pc`）。

- `/突击 [平台]`（别名：`/sortie`）
- `/警报 [平台]`（别名：`/alerts`）
- `/裂缝 [平台] [普通/钢铁/九重天]`（别名：`/fissure`）
	- 例如：`/裂缝 pc 钢铁`
	- 也可直接用：`/九重天裂缝 [平台]`、`/钢铁裂缝 [平台]`、`/普通裂缝 [平台]`
- `/奸商 [平台]`（别名：`/虚空商人`、`/baro`）
- `/仲裁 [平台]`（别名：`/arbitration`）
- `/电波 [平台]`（别名：`/夜波`、`/nightwave`）
- `/夜灵平原 [平台]`（别名：`/平原`、`/希图斯`、`/cetus`、`/poe`）
- `/魔胎之境 [平台]`（别名：`/魔胎`、`/cambion`）
- `/地球昼夜 [平台]`（别名：`/地球循环`、`/地球`、`/earth`）
- `/奥布山谷 [平台]`（别名：`/金星平原`、`/福尔图娜`、`/vallis`、`/fortuna`）
- `/双衍王境 [平台]`（别名：`/双衍`、`/duviri`）
- `/入侵 [平台] [数量<=20]`（别名：`/invasions`）
	- 例如：`/入侵 pc 15`
- `/集团 [平台] [集团名]`（别名：`/syndicate`、`/syndicates`）
	- 不带集团名：列出所有集团概览
	- 带集团名：仅列出该集团任务
	- 示例：`/集团 新世间 pc`

## PublicExport 查询

- `/武器 <名称>`（别名：`/weapon`、`/wfweapon`）
	- 说明：从 PublicExport 数据中搜索武器，中文优先，也支持英文/uniqueName。
	- 示例：`/武器 绝路`、`/武器 soma`

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

# 资源说明（/wmr 极性图标离线化）

`/wmr` 的极性图标使用本地资源：

- 位置：`data/plugins/astrbot_plugin_warframe_helper/assets/polarity/`
- 文件：`madurai.svg` / `vazarin.svg` / `naramon.svg`

插件运行时**不会**为极性图标下载任何网络资源（缺失时会自动回退为“极性文字”）。

开发/修复资源时，可在 AstrBot 工程根目录执行一次：

```bash
pyenv exec python scripts/vendor_wfm_polarity_icons.py
```

或：

```bash
python scripts/vendor_wfm_polarity_icons.py
```

（该脚本会从 warframe.market 的 SVG sprite 抽取并写入上述 3 个 svg 文件。）
