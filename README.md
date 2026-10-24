# git-dynamic-index

一个轻量的 Python CLI 工具，用于采集本地 Git 仓库状态并更新 Markdown 中的动态索引区块。

A lightweight Python CLI tool that collects local Git repository status and updates a dynamic Markdown index block.

它解决的是“README/状态页里的分支、最近提交、工作区状态总是过期”的问题。工具只修改目标 Markdown 两个标记之间的内容，不联网、不自动 commit、不自动 push。

## 功能

- `gdi init`：在目标 Markdown 中初始化标记区块；文件不存在时创建，已存在时只追加，不覆盖其余内容。
- `gdi update`：采集 Git 状态并更新区块；内容没有变化时不写文件。
- `gdi status`：只读显示仓库、目标文件、区块新鲜度和 `post-commit` Hook 状态。
- `gdi install-hooks`：安装受管理的 `post-commit` Hook。
- `gdi uninstall-hooks`：只删除本工具安装的 Hook，不触碰外来 Hook。

生成内容包含：

- 当前分支；detached HEAD 显示为 `detached`。
- 最近一次改动“目标文件以外内容”的提交短哈希、subject 和日期。
- 工作区改动总数，以及 staged、unstaged、untracked、conflict 计数。
- 最多 5 个改动文件；超出时明确写 `(+N more; counts are complete)`。
- 上游 ref 名；不执行 fetch，因此不显示 ahead/behind。

## 环境要求

- Python 3.10+
- Git 2.22+（开发与验证使用 Git 2.55）
- Windows PowerShell 为主要验证环境；Linux/macOS 未实测

## 安装

在项目根目录创建隔离环境并安装：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\gdi.exe --version
```

如果使用 PowerShell 激活了虚拟环境，也可以直接运行 `gdi --version`。

开发与测试需要额外安装 pytest：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest -q
```

## 使用

在仓库根目录，默认操作 `README.md`：

```powershell
gdi init
gdi update
gdi status
```

也可以明确指定仓库和目标文件：

```powershell
gdi init --repo . --file docs/STATUS.md
gdi update --repo . --file docs/STATUS.md
gdi status --repo . --file docs/STATUS.md
```

`--repo` 可以指向仓库内任意目录；`--file` 相对仓库根目录解析，也可以传绝对路径，但必须位于该仓库内，且扩展名必须是 `.md` 或 `.markdown`。

### 动态区块格式

区块必须恰好包含一对标记，每个标记独占一行：

| 标记 | 内容 |
|---|---|
| 开始 | `<!-- AUTO-STATUS-START -->` |
| 结束 | `<!-- AUTO-STATUS-END -->` |

只有两个标记之间的内容会被替换；标记之外的内容保持原样。`gdi init` 会在已有文件末尾追加该区块，不会删除或重排已有内容。

生成示例：

```markdown
## Project status

- **Branch**: `main`
- **Last commit (excl. this file)**: `a1b2c3d` feat: add parser (2026-01-02)
- **Working tree (excl. this file)**: 2 changed (1 staged, 1 unstaged, 0 untracked, 0 conflicts)
- **Changed files**: `M` `src/app.py`, `??` `notes/new file.md`
- **Upstream**: `origin/main`
```

`staged` 和 `unstaged` 是 Git 状态标志，同一个文件可能同时计入两者；`changed` 是去重后的路径数。

## Hook 自动更新

第一版只安装 `post-commit` Hook，原因是它覆盖主要场景，且不会在 commit 之前改动 Git index。安装：

```powershell
gdi install-hooks
gdi status
```

Hook 写入目标仓库自己的 `.git/hooks/post-commit`，内容是绝对解释器路径加：

```text
python -m git_dynamic_index update --repo <repo> --file <file> --hook
```

它不依赖当前工作目录，失败时退出码仍为 0，只向 stderr 输出错误，不会阻止用户的 commit。

### 为什么提交后 README 会变成未提交修改

`post-commit` 在提交完成后更新 README，因此 README 会显示为 modified。这是正常行为，工具不会自动 stage 或 commit。

推荐流程：

```powershell
git add README.md
git commit -m "docs: refresh status index"
```

也可以把 README 的改动随下一次提交一起提交。

关键点：区块明确排除了目标文件自身：

- 工作区统计不把目标 Markdown 算进去；
- “最近提交”只统计改动目标文件以外内容的提交；
- 不输出生成时间，避免每次运行都产生差异。

所以单独提交 README 后，下一次 Hook 计算出的内容不变，不会再次改写，也不会形成“提交索引 → 索引又过期”的无限循环。

`gdi status` 会同时显示真实 HEAD 和排除目标文件后的 `Last:`，方便核对。

## 安全边界

- 只读调用 Git；不 fetch、不联网、不修改 Git 历史。
- 不自动 commit、不自动 push、不自动 stage。
- 目标文件必须是仓库内的 `.md`/`.markdown` 普通文件；符号链接一律拒绝。
- 标记缺失、重复、顺序错误、文件不是 UTF-8、目标不是普通文件时明确报错，文件不变。
- 写入使用同目录临时文件 + `os.replace` 原子替换；失败时保留原文件并清理临时文件。
- 保留 UTF-8 BOM 和 LF/CRLF 换行风格；标记外内容逐字节保留。
- `gdi update` / `gdi status` 设置 `GIT_OPTIONAL_LOCKS=0`，避免顺手刷新 `.git/index`。
- 不修改 `core.hooksPath`。该配置存在时拒绝自动安装 Hook，避免写入全局/共享 Hook 目录。
- 已有非 gdi 管理的 Hook 不会被覆盖；安装会失败并打印需要手动加入的那一行。

## 常见错误

| 错误 | 处理 |
|---|---|
| `is not inside a Git working tree` | 用 `--repo` 指向目标仓库，或先进入仓库目录 |
| `does not exist; run gdi init first` | 先运行 `gdi init` |
| `expected exactly one start/end marker` | 检查标记是否成对、独占一行、顺序正确 |
| `is not valid UTF-8` | 将文件转换为 UTF-8；工具不会猜测编码 |
| `could not replace ... close the file` | 关闭编辑器/同步软件中的文件后重试 |
| `core.hooksPath is set` | 不自动安装；把 Hook 命令手动加入现有 Hook 调度器 |
| `existing hook is not managed` | 保留已有 Hook，手动加入打印出的命令行 |
| `cannot import git_dynamic_index` | 在该 Python 环境中安装项目后再安装 Hook |

## 当前限制

- 只安装 `post-commit`。切换分支、merge、rebase 或 reset 后，可能需要手动运行 `gdi update` 或等下一次 commit 刷新。
- 不 fetch，因此上游只显示 ref 名，不显示 ahead/behind。
- 不输出生成时间；提交日期来自 Git 的 commit date。
- 适配大型仓库时 `--untracked-files=all` 可能较慢。
- 并发运行没有文件锁；原子替换保证不会写坏，但最后写入者获胜。
- Windows 已完成真实验证；Linux/macOS 未实测。POSIX 文件权限、真实符号链接和换行文件名测试在当前 Windows 环境跳过；换行文件名解析已由独立测试覆盖。
- 仓库内目标文件的符号链接不支持。

## 验证记录

2026-10-24，Windows，Python 3.12.10，Git 2.55.0：

- 在临时隔离 venv 中执行 `pip install -e ".[dev]"`，`gdi --version` 输出 `gdi 0.1.0`，`gdi --help` 正常。
- 完整测试：`106 passed, 4 skipped`。
- 跳过项：POSIX 文件模式（Windows 不适用）、两个真实符号链接测试（当前 Windows 权限无法创建）、换行文件名创建（Windows 文件系统拒绝；解析逻辑单独测试）。
- 端到端测试使用真实临时 Git 仓库，验证了 commit 触发 Hook、只提交 README 不循环、Hook 失败不阻塞 commit、中文/空格路径。

## 许可证

本项目使用仓库中的 MIT License；本次开发未更换许可证。
