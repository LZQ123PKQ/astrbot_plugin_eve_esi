# astrbot-plugin-eve-esi

EVE Online ESI API 调用插件，支持 EVE 国服（晨曦），提供市场价格查询、舰船加成查询和简称管理功能。

## 功能特点

- **市场价格查询**：查询物品在吉他星系的市场价格
- **舰船加成查询**：查看舰船的技能加成和特有加成
- **加成字典管理**：支持修改加成描述、技能类型名称和添加effect映射
- **中文名称搜索**：通过市场中心API支持中文物品名称搜索
- **涂装/蓝图过滤**：自动过滤搜索结果中的涂装和蓝图
- **简称管理**：支持添加、查看和删除物品简称
- **多结果展示**：搜索结果前5个显示价格，剩余显示名称
- **价格格式化**：千分位分隔符+简化表示（亿/万）

## 安装方法

1. 将插件目录复制到 AstrBot 的 `data/plugins` 目录下
2. 启动 AstrBot，插件会自动加载

## 使用方法

### 核心命令

#### 市场价格查询
- `/吉他 <物品名称或ID>` - 查询吉他市场价格
  - 示例：`/吉他 三钛合金` 或 `/吉他 34`
- `/jt <物品名称或ID>` - 短命令

#### 舰船加成查询
- `/加成 <物品名称或ID>` - 查看舰船技能加成和特有加成
  - 示例：`/加成 乌鸦` 或 `/加成 670`

输出格式说明：
- 技能加成按技能类型分组显示（如"艾玛战列舰操作每升一级"）
- 特有加成单独列出
- 每条加成后显示 `(effect_name|attr_name)` 供参考

#### 加成字典管理
- `/加成修改 原描述(effect|attr)=新描述` - 修改加成描述
  - 示例：`/加成修改 能量炮台最佳射程加成(shipETOptimalRange2AF|maxRange)=小型能量炮台最佳射程加成`

- `/加成修改 原技能名=新技能名` - 修改技能类型名称
  - 示例：`/加成修改 旗舰巡洋舰操作=航空母舰操作`

- `/加成修改 描述(effect|attr)+技能类型名` - 添加effect到技能类型映射
  - 示例：`/加成修改 武器扰断器效果加成(shipBonusEwWeaponDisruptionStrengthAF2|trackingSpeedBonus)+艾玛航空母舰操作`

#### 简称管理
- `/简称 <全称>=<简称>` - 添加物品简称
  - 示例：`/简称 鱼鹰级海军型=海鱼鹰`
- `/简称列表 [全称或简称]` - 查看简称列表
  - 示例：`/简称列表 鱼鹰级海军型` 或 `/简称列表 海鱼鹰`
- `/简称删除 <简称>` - 删除简称
  - 示例：`/简称删除 海鱼鹰`

#### 帮助
- `/帮助` - 显示完整的帮助信息

## 技术实现

- 使用 EVE 国服 ESI API 进行数据获取
- 使用 EVE 国服市场中心API进行物品搜索
- 使用 aiohttp 进行异步网络请求
- 简称数据持久化存储在 `data/aliases.json`
- 加成描述字典存储在 `zidian1.txt`
- 技能类型规则存储在 `effect_dict.py`

## 文件说明

| 文件 | 说明 |
|------|------|
| `main.py` | 插件主文件，包含所有命令处理 |
| `effect_dict.py` | 技能类型识别规则和加成描述处理 |
| `zidian1.txt` | 加成描述字典，可动态修改 |
| `effects.json` | EVE effect 数据（用于加成修改） |
| `attributes.json` | EVE attribute 数据（用于加成修改） |
| `data/aliases.json` | 用户简称数据 |

## 注意事项

- 支持中文名称、英文名称或物品ID
- 伊甸币（PLEX）查询：由于市场改版暂不支持
- 模糊搜索自动过滤涂装和蓝图，请使用详细搜索或自行添加简称
- 一个全称可以有多个简称，搜索任意简称都会自动转换为全称

## 支持

- [AstrBot Repo](https://github.com/AstrBotDevs/AstrBot)
- [AstrBot Plugin Development Docs (Chinese)](https://docs.astrbot.app/dev/star/plugin-new.html)
- [EVE 国服 ESI API Docs](https://ali-esi.evepc.163.com/ui/)
- [EVE 国服市场中心](https://www.ceve-market.org/)
