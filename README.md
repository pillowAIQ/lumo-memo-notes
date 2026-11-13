# memo_app — 个人备忘录

自研极简备忘录软件（Windows）。随手记录想法与待办事项，单机 SQLite 存储。

## 技术栈

- **Python 3.11 + PySide6**（Qt 桌面 GUI）
- **pynput**（全局热键监听）
- **SQLite**（stdlib `sqlite3`，WAL 模式）
- 数据默认存于 `~/.memo_app/memo.db`，可用环境变量 `MEMO_APP_DATA_DIR` 修改

## 运行

```bat
run.bat
```

或手动：

```bash
.venv/Scripts/python.exe -m memo_app
```

## 功能

### 快捷记录（Ctrl+O）

- 全局热键 **Ctrl+O** 呼出极简输入框（屏幕上方居中、置顶、半透明）
- 输入内容 → **Enter** 保存并自动收起；**Esc** 或点击别处关闭
- 注意：Ctrl+O 会覆盖各应用的"打开文件"快捷键（用户已确认接受）
- 修改快捷键：编辑 `src/memo_app/controller.py` 顶部的 `HOTKEY` 常量

### 桌面待办挂件

- 主界面显示时自动隐藏；主界面最小化或关闭到托盘后，显示右下角半透明置顶面板
- 显示未完成备忘录（最多 8 条 + "还有 N 条"提示）
- 圆圈点击标记完成；**拖动**可移动位置；**双击空白处**打开主界面
- 右上角 **−** 收起为标题小条，点击还原按钮恢复列表，分组展开状态保留
- **×** 手动隐藏并关闭自动显示；托盘菜单“自动显示桌面挂件”可重新启用

### 系统托盘

- 关闭主窗口 = 隐藏到托盘（不退出）
- 托盘菜单：打开主界面 / 快捷记录 / 桌面挂件开关 / 退出
- 托盘图标为程序化绘制的松绿色笔记本图标（`src/memo_app/icons.py`）

### 主界面

- 卡片流列表：置顶、优先级排序；普通记录完成后移入回收站，恢复后回到待办
- 相对时间、全文搜索、双击行内编辑（Enter 保存 / Esc 取消）
- 右键菜单：置顶、标记完成、复制、删除；悬停 × 或 Delete 键删除
- 回收站：软删除，可恢复 / 彻底删除 / 清空

### 界面设计

- 暖纸白与松绿配色，统一主界面、快捷记录、桌面挂件和对话框
- 常驻搜索：Ctrl+F 聚焦、Esc 清空；全部 / 已置顶 / 有提醒快捷筛选
- 标题与搜索同排，记录正文与分类、截止信息分层展示
- 默认 1080×800，支持 680×520 紧凑窗口；分类过多时独立滚动
- 设计调研与实现说明：[界面升级说明](docs/research/ui-redesign.md)

隔离数据的界面检查与截图：

```bash
.venv/Scripts/python.exe ui_smoke.py
```

检查不会读取或修改真实备忘录；截图输出至 `audit_shots/redesign/`。

## 目录结构

```
memo_app/
├── run.bat                    # Windows 启动脚本
├── pyproject.toml             # 打包/依赖配置
├── src/memo_app/
│   ├── __init__.py            # 包入口 + Windows DLL 预加载 workaround
│   ├── __main__.py            # python -m memo_app 入口
│   ├── db.py                  # SQLite 数据层（schema + CRUD）
│   ├── app.py                 # PySide6 主界面（美化卡片流）
│   ├── capture.py             # Ctrl+O 极简输入弹窗
│   ├── widget.py              # 半透明桌面待办挂件
│   ├── controller.py          # 编排：热键/托盘/弹窗/挂件
│   └── icons.py               # 程序化图标绘制
└── docs/
    └── research/
        └── memo-apps-survey.md   # 市面备忘录软件调研报告
```

## 数据模型

`memos` 表已预留扩展字段，后续加功能无需改表：

| 字段 | 说明 |
|---|---|
| `content` | 备忘录文本 |
| `is_pinned` | 置顶 |
| `is_done` | 完成勾选（待办基础） |
| `created_at` / `updated_at` | ISO 时间戳 |
| `deleted_at` | 软删除时间（回收站） |

## 已知环境问题（已修复）

本机 PATH 中 `anaconda/Library/bin/icuuc.dll`（实为旧版 ICU 58）和
`K:\main\_internal\python3.dll`（Python 3.8 shim）会抢占 PySide6 的 DLL
解析，导致 `ImportError: DLL load failed`。`src/memo_app/__init__.py`
中已做预加载规避；换机器/换环境如再遇到同类问题，检查 PATH 中的同名 DLL 即可。

## 后续路线（详见调研报告第四节）

- V2：截止日期 / 优先级 / 重复提醒（待办强化）
- V3：标签、多端同步（先文件夹级同步，再考虑自建服务）
- V4：剪藏、语音输入、AI 总结等
