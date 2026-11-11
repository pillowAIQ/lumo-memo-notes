# 备忘录 / 笔记软件调研报告

> 调研日期：2026-11-11
> 目的：为自研个人备忘录软件收集市面产品的定位、功能特点与实现方案，作为后续技术选型的依据。

---

## 一、产品全景分类

市面上的"记东西"类软件可以按**核心隐喻**分成六类：

| 类别 | 核心隐喻 | 代表产品 |
|---|---|---|
| 全能云笔记 | 数字文件柜 / 知识库 | 印象笔记 Evernote、OneNote、有道云笔记、语雀 |
| 本地优先知识库 | 本地文件 + 知识网络 | Obsidian、Joplin、Logseq |
| 轻量速记 | 卡片 / 聊天式碎片记录 | flomo、Apple Notes、Google Keep、微信收藏 |
| 大纲 / 块编辑器 | 结构化文档 | 幕布、Notion、wolai、FlowUs |
| 任务待办 | GTD 清单 | Todoist、TickTick、Apple Reminders、Microsoft To Do |
| 手写 / 自由画布 | 数字纸张 | OneNote、Samsung Notes、GoodNotes |

你的需求"随时记录要记的东西、待办事项"横跨 **轻量速记 + 任务待办** 两类，是市面上验证最充分的赛道。

---

## 二、各产品详解

### 1. 印象笔记 Evernote（国际版 / 国内版）

- **定位**：全能信息捕手，"数字文件柜"。强调信息的**收集与检索**。
- **核心功能**：网页剪藏（Web Clipper）、邮件/微信文章收藏、OCR 识别图片文字、PDF/Office 多格式附件、笔记本+标签两级组织、全文搜索（含附件内容）、一键生成思维导图。
- **实现方案**：
  - v10（2020）为 **Electron 重写**，UI 是 Web 技术，数据存本地。
  - 存储：**SQLite**（笔记/笔记本/标签元数据）+ **每条笔记一个文件**（附件 content-addressed 存储）；正文是 **ENML**（Evernote 自定义 XML 标记语言）。
  - 笔记正文放在 **Yjs 文档**里——即使用了 CRDT 数据结构做冲突合并。
  - 同步：自家服务器 + 本地 LocalStorage SQLite 暂存未同步变更。
- **特点/短板**：剪藏与检索最强；但产品臃肿、笔记多了加载慢、迭代慢。

### 2. Microsoft OneNote

- **定位**：数字螺旋笔记本，**自由画布**形态。
- **核心功能**：元素（文本/图片/手写）在画布上任意摆放、手写笔 + 手写识别、音频录制与笔记同步、无限层级分区/页面结构、深度集成 Microsoft 365 / Copilot。
- **实现方案**：
  - 笔记存为 `.one` / `.onetoc2` 文件，遵循微软公开的 **[MS-ONESTORE] Revision Store File Format** 规范——带 revision 历史 + transaction log 的对象存储。
  - 同步走 OneDrive。
- **特点/短板**：画布自由度最高、手写体验好；但结构松散、字体/排版调整繁琐、目录导航弱，用户反馈同步偶发问题。

### 3. Notion

- **定位**：All-in-one 工作区，"块"（block）是万物的基础单位。
- **核心功能**：块编辑器（页面=块树）、**数据库**（table/board/calendar/gallery/timeline 多视图）、模板生态、实时多人协作、Notion AI。
- **实现方案**（公开技术博客披露）：
  - 前端 **React + Next.js + TypeScript**；桌面端是 **Electron 壳加载 Web 客户端**（本质是网页版套壳）。
  - 后端 Node.js / Ruby on Rails，跑在 AWS。
  - 存储：**PostgreSQL 分片**——先按 workspace 分到 32 个物理库（每库 15 个逻辑 shard），2023 年再扩到 96 库 × 5 shard = **480 个逻辑 shard**；用 Debezium CDC → Kafka → Hudi → S3 建数据湖。
  - 2025 年才补上原生离线模式（此前离线体验弱）。
- **特点/短板**：灵活性/模板/协作业界天花板；但学习曲线陡、大空间性能下降、离线能力起步晚。

### 4. Obsidian

- **定位**：本地优先的**个人知识图谱**工具，"第二大脑"。
- **核心功能**：纯 Markdown 笔记、`[[双向链接]]` + backlink、**Graph View** 可视化知识网络、标签、1000+ 社区插件、主题系统、Canvas 白板。
- **实现方案**：
  - **Electron** 应用（Chromium 渲染 + Node.js 直连文件系统，开启了 nodeIntegration）。
  - **Vault = 本地文件夹**：笔记就是一个个 `.md` 文件，配置在 `.obsidian/` 目录，**没有自有数据库**。
  - 性能关键：维护一个 **IndexedDB 元数据缓存**（标题、链接、标签、frontmatter、标题层级等），由文件监视器（fs.watch）驱动增量更新；缓存可随时重建。
  - 插件 API：`Plugin` 基类 + `Vault`/`Workspace`/`MetadataCache` 等核心接口。
  - 同步：不自带，靠 Obsidian Sync（付费）或 iCloud/Dropbox/Git/Syncthing 等任意文件同步。
- **特点/短板**：数据完全自有、纯文本永不过时、生态最强；但单人向、无内置实时协作、富文本/排版能力弱。

### 5. Apple Notes（苹果备忘录）

- **定位**：系统级轻量备忘录，零成本随手记。
- **实现方案**（逆向资料）：
  - 存储在 `~/Library/Group Containers/group.com.apple.notes/NoteStore.sqlite`（CoreData 库）。
  - 笔记正文是 **zlib 压缩的 protobuf**；对象有 UUID；同步状态存在 `ZICCLOUDSYNCINGOBJECT` 表。
  - 文档同步用了类 CRDT 的 "topotext" 结构，但实际合并由 iCloud 服务端串行化完成。
- **特点**：极简、Siri/锁屏/小组件生态整合、免费；但封闭、格式私有、组织能力弱。

### 6. Google Keep

- **定位**：极简卡片式 Google 备忘录。
- **实现方案**：典型的**云端 REST API** 产品。API 资源模型极简：`Note { title, body(Section), attachments, labels, permissions }` + `trashTime/trashed` 回收站语义。卡片式时间流 + 颜色 + 归档/删除。
- **特点**：快、轻、与 Google 生态（Assistant、日历提醒）打通；缺点是功能天花板低、无层级组织。

### 7. 有道云笔记

- **定位**：功能均衡的国产泛用笔记，深度适配国内网络与操作习惯。
- **核心功能**：文档扫描、手写笔记、Markdown 编辑器、思维导图、AI 助手、PDF 转 Word、翻译、多端同步。
- **特点**：上手零成本（贴近国内办公软件习惯）、功能覆盖面广；短板是定位泛、深度不如垂直产品。

### 8. 语雀 Yuque

- **定位**：在线文档 + 云端知识库，"笔记界的 GitHub"，团队协作导向。
- **实现方案**：纯 Web 文档平台，树状目录组织，Markdown 支持强，开放 API 与第三方接入生态丰富。
- **特点**：文档结构化程度高、协作/社区/模板完善；偏"写作与知识库"而非"速记"。

### 9. flomo 浮墨笔记 ⭐（与你需求最接近）

- **定位**：卡片速记，"小而美"，只聚焦**记录与回顾**。官方明确说自己"不擅长"大纲、长文档、待办、思维导图——这些留给别家。
- **核心功能**：
  - **memo 是最小单位**：一条简短内容，像聊天一样无排版压力地记录；
  - 多级标签、每日回顾、随机漫步、认知地图、相关笔记、笔记关系图；
  - 语音输入、微信输入、PC/Mac/Web/App 全平台免费同步；
  - **API 优先**：极简输入 API（随机字符串认证即可），催生了 iOS 捷径、Chrome 扩展、Alfred、Telegram Bot 等第三方生态；
  - 近年加入 AI（flomo Agent / 记忆档案 / MCP 连接）。
- **实现方案**（官方《flomo 的开发理念》披露）：
  - **第一版选 Web 技术**：一次编写多端复用；Web App + PWA 特性获得接近原生 App 的体验，迭代按天甚至更高频；
  - 后期 iOS/Android 客户端改用原生以追求交互体验；
  - 因为 memo 与界面解耦、以输入为核心，API 需求最旺盛——只做输入 API 就能撬动整个生态。
- **设计哲学**："尽量少地设计"、安静的容器、不追求完美产品、先人肉验证再规模化。
- **启示**：验证了"最小记录单元 + 极速输入 + 多入口（API/扩展/语音）"这条路线的产品力。

### 10. 幕布 Mubu（大纲笔记）

- **定位**：大纲式笔记，一键转思维导图，兼任务管理。现由 flomo 团队维护。
- **实现方案**：层级折叠的**大纲树编辑器**——回车新建节点、Tab 缩进建层级、折叠/聚焦/拖拽跨区重组；支持 Markdown 与 LaTeX；大纲⇄导图双视图。
- **大纲编辑器的两种形态**（行业分类）：
  - **One-pane（内在结构）**：Workflowy、幕布——结构在文本里，跨区编辑方便；
  - **Two-pane（外在结构）**：结构树在侧栏、正文在主区，概览导航强但跨节点编辑繁琐。
- **特点**：上手极快（就是列表缩进）、结构化强；短板是表达复杂语义/长文阅读体验差、可读性割裂。

### 11. Notion 类国产替代：wolai、FlowUs、我来 wolai

- 块编辑器路线：把文档拆成 block，支持多视图数据库。wolai 主打 AI + 块，FlowUs 对标 Notion。共同点是**学习成本高于速记类**，更适合搭个人系统而非随手记。

### 12. 任务待办类：Todoist / TickTick / Apple Reminders / Microsoft To Do

| 产品 | 特点 | 实现要点 |
|---|---|---|
| **Todoist** | 自然语言输入（"下周五下午4点交税"自动解析日期）、Projects/Sections/Sub-tasks/Labels/优先级、Today/Upcoming 视图、Karma 打卡激励、MCP server | 云端 API + 多端；NLU 日期解析是核心竞争力 |
| **TickTick 滴答清单** | 一体化套件：日历 + 待办 + 习惯打卡 + 番茄钟 + 艾森豪威尔矩阵 + 地理位置提醒；Today 视图手动排序 | 多端 App + web；功能全家桶路线 |
| **Apple Reminders** | 系统级、Siri、地理围栏提醒、智能列表、iCloud 同步、免费 | 与 iOS 生态深度绑定，不可跨平台 |
| **Microsoft To Do** | 类似 Reminders，接入 Outlook/Teams | 微软生态绑定 |

- **共性实现要素**：任务有 due date / priority / recurrence / list(project) / tags 等结构化字段；需要"今天/即将到期"聚合视图；自然语言快速添加是体验关键。

---

## 三、实现方案横向对比

### 3.1 按存储层分

| 方案 | 代表 | 优点 | 缺点 |
|---|---|---|---|
| **纯 Markdown 文件** | Obsidian、Joplin | 人类可读、git 友好、工具无关、永不过时 | 元数据/关系要另建索引；海量小文件 IO 慢；加密麻烦；富文本表达受限 |
| **SQLite 单库** | Apple Notes、Evernote、NotallyX | 读写快、事务、天然全文搜索(FTS)、可 SQLCipher 加密 | 单文件损坏风险；对用户不可读；schema 迁移要自己管 |
| **云端 DB** | Notion (Postgres)、Google Keep | 多端实时一致、协作天然支持 | 必须联网；离线弱；数据不自有 |
| **Markdown + SQLite 混合** | WikiMind、Obsidian(正文.md+IndexedDB缓存) | 内容可读 + 查询高效 | 双写一致性问题；需 linter/缓存重建机制 |
| **CRDT 文档** | Evernote v10(Yjs)、Flint(Automerge)、stele(Yrs) | 离线编辑、多设备并发自动合并、E2EE 友好 | 实现复杂；调试困难；对简单单机应用是杀鸡用牛刀 |

### 3.2 按编辑器形态分

| 形态 | 代表 | 适合场景 |
|---|---|---|
| 富文本 WYSIWYG | Evernote(ENML)、有道 | 正式文档、排版要求高 |
| Markdown 编辑器 | Obsidian、Typora | 技术用户、纯文本信仰 |
| 块编辑器（block tree） | Notion、wolai、FlowUs | 页面=积木，视图可切换（表格/看板/日历） |
| 大纲树（outliner） | 幕布、Workflowy | 结构化清单、头脑风暴 |
| 卡片时间流 | flomo、Google Keep | 碎片速记、轻量回顾 |
| 自由画布 | OneNote | 手写、白板式发散 |

### 3.3 按客户端架构分

| 架构 | 代表 | 特点 |
|---|---|---|
| **Electron**（Web 技术套壳） | Obsidian、Evernote、Notion 桌面端 | 一套代码跨 Win/Mac/Linux；包体大、内存高 |
| **Web + PWA** | flomo 早期 | 迭代最快、零安装；原生能力弱 |
| **纯原生** | TickTick、Apple Notes | 体验最好；每端一套代码 |
| **跨端框架**（Flutter/RN/Tauri） | 各种新项目 | Tauri 用系统 WebView，包体远小于 Electron，值得考虑 |

### 3.4 按同步方案分

| 方案 | 说明 |
|---|---|
| 自建云端 + API | Notion、Todoist、flomo 模式；实时一致但离线要自己做缓存层 |
| 文件级同步 | Obsidian 模式：笔记是文件，交给 iCloud/Dropbox/SyncThing/Git；实现最简单，冲突处理粗糙 |
| CRDT 增量同步 | Yjs/Automerge 状态 + 服务端合并；离线多端并发编辑的正确解，复杂度最高 |
| 快照 + 增量混合 | memrynote 模式：周期全量快照 + 实时 CRDT 增量，兼顾恢复与实时 |

---

## 四、给自研备忘录的启示与待决问题

### 4.1 已被验证的"最小可行产品"配方

从 flomo / Google Keep / Apple Notes 的成功路径看，一个备忘录的核心闭环是：

```
极速输入（<1秒打开、无排版压力）→ 结构化存储（可检索、可分类）
→ 聚合视图（今天要做什么 / 最近记了什么）→ 轻量回顾
```

### 4.2 关键技术选型清单（待你决策）

1. **定位**：纯速记（flomo 式）/ 速记+待办（你的描述）/ 知识库（Obsidian 式）？
2. **平台**：Windows 桌面优先？还是要手机+多端？→ 决定 Electron / Tauri / Flutter / Web+PWA
3. **存储**：Markdown 文件（可读可备份）vs SQLite（查询快）vs 混合？单机够用还是要多端同步？
4. **同步**：暂时不要同步（最简单）/ 文件夹同步（放到坚果云等）/ 自建服务 / CRDT？
5. **记录模型**：一条条 memo（卡片流）vs 笔记本+笔记（Evernote 式）vs 大纲条目（幕布式）vs 任务项（Todoist 式）？
6. **组织方式**：时间流 / 文件夹树 / 标签 / 双链？
7. **高级功能（后置）**：全文搜索、OCR、剪藏、语音输入、AI 总结、导出。建议全部后置。

### 4.3 建议的渐进路线

- **MVP**：单机桌面应用，SQLite 或 Markdown 存储，卡片流记录 + 标签/置顶 + 全文搜索 + "今天"聚合视图。先跑通输入→存储→检索闭环。
- **V2**：加待办字段（due date / 完成状态 / 优先级）、定时提醒。
- **V3**：多端同步（先文件夹级同步，再考虑自建服务）。
- **V4**：剪藏、语音、AI 等生态能力。

---

## 五、参考资料

- [Notion sharding Postgres 架构](https://www.notion.com/blog/sharding-postgres-at-notion) / [The Great Re-shard](https://www.notion.com/blog/the-great-re-shard)
- [Notion 技术架构解析（notion-enhancer）](https://notion-enhancer.github.io/documentation/concepts/)
- [Evernote v10 本地存储逆向分析](https://github.com/oooooooo/export-evernote/blob/main/docs/evernote-cache.md)
- [Obsidian 官方 - 数据存储方式](https://obsidian.md/help/data-storage)
- [Obsidian 插件 API](https://github.com/obsidianmd/obsidian-api)
- [Obsidian 架构分析](https://ggprompts.com/architecture/obsidian/)
- [Obsidian 数据模型研究](https://github.com/brancusi/fictty/blob/main/docs/research/data-model/obsidian.md)
- [Apple Notes 存储格式逆向](https://github.com/dunhamsteve/notesutils/blob/master/notes.md)
- [Google Keep REST API](https://developers.google.com/workspace/keep/api/reference/rest/v1/notes)
- [flomo 设计哲学](https://help.flomoapp.com/about-us/about-us/design-concept.html)
- [flomo 开发理念（技术选型）](https://help.flomoapp.com/about-us/about-us/development-concept.html)
- [flomo 官网功能介绍](https://help.flomoapp.com/)
- [大纲编辑器评测与分类](https://developer.aliyun.com/article/979509)
- [幕布产品介绍](https://ai.fly63.com/mubu)
- [Todoist 功能](https://www.todoist.com/features) / [TickTick 功能](https://tidbits.com/2025/08/14/ticktick-provides-a-focused-daily-task-list-and-more/)
- [Todoist vs Apple Reminders 对比](https://www.todoist.com/inspiration/todoist-vs-apple-reminders)
- [Local-first 笔记架构现状](https://www.getoyma.com/blog/state-of-local-first-notes)
- [CRDT/本地优先同步方案（stele）](https://github.com/cluion/stele)
- [NotallyX 笔记改 Markdown 文件的可行性讨论](https://github.com/Crustack/NotallyX/issues/693)
- [国产笔记软件测评（woshipm）](https://www.woshipm.com/evaluating/6291747.html)
- [PM 视角笔记产品对比（极客文档）](https://geekdaxue.co/read/u298697@uf1fda/mv6im8)
