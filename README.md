# Codex Indicator

一个克制、轻量的系统托盘工具，用于同时查看和管理本机及 SSH 服务器上的 Codex CLI 会话。

```text
顶栏： C 🟢2 🟠1 🔵1

🟢 运行中 · robot_ws — 修复导航节点定位漂移
🟠 等待操作 · robot-server:perception — 选择相机标定方案
🔵 已完成 · arm_control — 增加机械臂限位保护
```

它显示会话状态、项目和短对话名称，并提供重命名、归档以及新建 Codex 终端入口。不创建工作树，不展示完整聊天记录，也不需要 tmux、Docker 或后台云服务。

## 特性

- 同时跟踪多个 Codex CLI 终端。
- 一个真实终端只显示一条，自动排除 IDE app-server 等无 TTY 后台进程。
- Ubuntu 可自动识别本机正在运行的 SSH 终端，并只读获取对应服务器上的 Codex 状态。
- 支持 `运行中`、`等待操作`、`空闲`、`已完成` 状态。
- 不同状态使用不同颜色，长对话名称在菜单中自动用 `...` 截断。
- 点击会话可跳转到对应终端；“管理对话”子菜单可修改名称、归档对话或在同一项目新建终端。
- Ubuntu 可一次性允许当前全部命令、文件修改和权限审批；操作前会再次确认，普通 `request_user_input` 问题不会被自动回答，也不会自动允许未来的新请求。
- 项目名称来自当前工作目录或最近的 Git 仓库根目录。
- 对话名称优先读取 Codex 本地线程元数据。
- Ubuntu 使用原生 Ayatana AppIndicator，可在顶栏直接显示状态计数。
- Windows 和 macOS 使用系统托盘/菜单栏。
- Hooks 安装是增量操作：保留已有 Hooks，并在修改前创建备份。
- Ubuntu 可从 Codex 进程已打开的 rollout 文件中被动恢复现有会话；只解析事件类型，不读取正文。
- 中文环境显示中文，其他环境显示英文。
- 全部数据保留在本机。

## 隐私边界

Codex Indicator 不连接任何自己的服务器，不发送项目文件、提示词、回复、API Key 或 Codex 登录凭据。

状态缓存只保存：

- Codex 会话 ID
- 状态
- 工作目录
- 最近事件和时间
- Codex 进程 ID、终端标识（可用时）
- SSH 主机别名及服务器返回的项目/标题摘要（远程会话）

菜单中的对话名称来自 Codex 已经维护的本地 `state_*.sqlite` 或 `session_index.jsonl`。Ubuntu 的现有会话恢复只检查 rollout 尾部的事件类型、工具名和完成标记；不会把完整提示词、回复或工具输入输出复制到自己的缓存中。

在 Ubuntu 上执行“允许当前全部待审批”时，工具会临时读取 GNOME Terminal 当前可见画面，确认它确实是 Codex 的命令、文件修改或权限审批界面后，才向该窗口发送一次 Enter。画面内容不会保存；普通选项询问、目录信任和 Hook 信任页面不会被自动确认。

SSH 识别只针对本机已有的 `ssh` 终端进程，使用 `BatchMode` 和现有 SSH 配置执行只读探测，不保存私钥或密码。远端列表按登录来源 IP 和 TTY 过滤；重命名/归档使用 Codex 官方 app-server 接口。

## Ubuntu 22.04+

推荐从 [Releases](https://github.com/Heimao-c/codex-indicator/releases) 下载 `.deb`：

```bash
sudo apt install ./codex-indicator_0.2.0_amd64.deb
codex-indicator
```

首次启动后，在托盘菜单中点击：

1. `安装/修复 Codex Hooks`
2. `开机自动启动`

从源码进行无 root 的用户级安装：

```bash
git clone https://github.com/Heimao-c/codex-indicator.git
cd codex-indicator
sh scripts/install-linux.sh
```

Ubuntu 需要这些系统组件：

```bash
sudo apt install python3-gi gir1.2-gtk-3.0 \
  gir1.2-ayatanaappindicator3-0.1 gir1.2-atspi-2.0 \
  libayatana-appindicator3-1 libx11-6 libxtst6 x11-utils
```

GNOME 必须启用 AppIndicator/KStatusNotifierItem 支持。Ubuntu 桌面通常默认提供。

## Windows

1. 从 Releases 下载 `codex-indicator-windows-x64.zip`。
2. 解压后保持 `CodexIndicator` 和 `CodexIndicatorHook` 两个目录在一起。
3. 运行 `CodexIndicator/CodexIndicator.exe`。
4. 从托盘菜单安装 Hooks 并启用开机启动。

发布包暂未进行代码签名，Windows SmartScreen 可能显示提醒。请只从本仓库 Releases 下载并核对发布来源。

## macOS

1. 从 Releases 下载与机器相符的 `codex-indicator-macos-arm64.zip`（Apple Silicon）或 `codex-indicator-macos-x64.zip`（Intel）。
2. 解压并保持 `.app` 与 `CodexIndicatorHook` 目录在一起。
3. 将整个解压目录移动到 `/Applications` 或个人 Applications 目录。
4. 首次启动可右键 `CodexIndicator.app` → `打开`。
5. 从菜单栏安装 Hooks 并启用登录启动。

当前发布包未使用 Apple Developer 证书签名，因此 Gatekeeper 会要求首次手动确认。首次点击某个会话跳转终端时，macOS 还可能要求允许 Codex Indicator 控制 Terminal。

## Codex Hooks 信任

Codex 会要求用户审核非托管命令 Hooks。安装后，在已有或新开的 Codex CLI 中输入：

```text
/hooks
```

确认命令指向本机 Codex Indicator，再选择信任。Hooks 未获信任时不会执行，托盘无法得到精确的实时状态。

Codex Indicator 注册以下官方生命周期事件：

[OpenAI Codex Hooks 官方文档](https://learn.chatgpt.com/docs/hooks)

- `SessionStart` / `SessionEnd`
- `UserPromptSubmit`
- `PreToolUse` / `PostToolUse`
- `PermissionRequest`
- `PreCompact` / `PostCompact`
- `SubagentStart` / `SubagentStop`
- `Stop`

状态映射：

| 状态 | 触发条件 |
| --- | --- |
| `● 运行中` | 提交提示、执行工具、压缩上下文或运行子代理 |
| `◐ 等待操作` | 权限批准，或调用 `request_user_input` |
| `○ 空闲` | 会话已启动但尚未提交任务 |
| `✓ 已完成` | 当前 Codex turn 触发 `Stop` |

## 命令行

```bash
codex-indicator --install-hooks
codex-indicator --uninstall-hooks
codex-indicator --install-autostart
codex-indicator --uninstall-autostart
codex-indicator --dump-status
codex-indicator --doctor
```

`--dump-status` 只输出状态、项目、标题和目录，不输出对话正文。

## 开发与测试

核心代码只依赖 Python 标准库。Ubuntu 原生界面使用系统 PyGObject；Windows/macOS 构建使用 `pystray` 和 Pillow。

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 -m codex_indicator --doctor
```

GitHub Actions 会在 Ubuntu、Windows 和 macOS 的 Python 3.10/3.12 上运行测试。推送 `v*` 标签时会生成三平台发布包。

## 已知限制

- 只有 Hooks 获得信任后，状态才是实时且精确的。
- `request_user_input` 可以识别为等待操作，但为避免误选仍必须回到 Codex 原生终端作答。
- 已经运行的 Codex 进程可能需要通过 `/hooks` 重新载入并信任新 Hooks。
- Ubuntu 的终端跳转和批量允许支持 GNOME Terminal 的 X11 会话；Windows 的终端跳转支持 Windows Terminal，macOS 支持系统 Terminal。其他终端模拟器暂未接入。
- SSH 会话自动发现与一键批量审批目前仅在 Ubuntu 上提供；Windows/macOS 依靠 Hooks 跟踪本机会话。
- SSH 自动读取要求主机支持无交互密钥认证；密码登录的现有连接无法被另一个探测连接复用。
- Windows/macOS 发布包目前未签名。

## 卸载

Ubuntu 源码安装：

```bash
sh scripts/uninstall-linux.sh
```

其他平台先从菜单卸载 Hooks 和开机启动，再删除应用目录。

## 许可证

[MIT](LICENSE)
