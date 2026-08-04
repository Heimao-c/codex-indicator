# Codex Indicator

一个克制、轻量的系统托盘工具，用于同时查看多个本地 Codex CLI 会话。

```text
顶栏： C ●2 ◐1 ✓1

● 运行中 · robot_ws — 修复导航节点定位漂移
◐ 等待操作 · perception — 选择相机标定方案
✓ 已完成 · arm_control — 增加机械臂限位保护
```

它只做三件事：显示会话状态、项目名称和 Codex 对话名称。不接管终端，不创建工作树，不展示完整聊天记录，也不需要 tmux、Docker 或后台云服务。

## 特性

- 同时跟踪多个 Codex CLI 终端。
- 支持 `运行中`、`等待操作`、`空闲`、`已完成` 状态。
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

菜单中的对话名称来自 Codex 已经维护的本地 `state_*.sqlite` 或 `session_index.jsonl`。Ubuntu 的现有会话恢复只检查 rollout 尾部的事件类型、工具名和完成标记；不会把完整提示词、回复或工具输入输出复制到自己的缓存中。

## Ubuntu 22.04+

推荐从 [Releases](https://github.com/Heimao-c/codex-indicator/releases) 下载 `.deb`：

```bash
sudo apt install ./codex-indicator_0.1.0_amd64.deb
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
  gir1.2-ayatanaappindicator3-0.1 libayatana-appindicator3-1
```

GNOME 必须启用 AppIndicator/KStatusNotifierItem 支持。Ubuntu 桌面通常默认提供。

## Windows

1. 从 Releases 下载 `codex-indicator-windows-x64.zip`。
2. 解压后保持 `CodexIndicator` 和 `CodexIndicatorHook` 两个目录在一起。
3. 运行 `CodexIndicator/CodexIndicator.exe`。
4. 从托盘菜单安装 Hooks 并启用开机启动。

发布包暂未进行代码签名，Windows SmartScreen 可能显示提醒。请只从本仓库 Releases 下载并核对发布来源。

## macOS

1. 从 Releases 下载 `codex-indicator-macos-universal.zip`。
2. 解压并保持 `.app` 与 `CodexIndicatorHook` 目录在一起。
3. 将整个解压目录移动到 `/Applications` 或个人 Applications 目录。
4. 首次启动可右键 `CodexIndicator.app` → `打开`。
5. 从菜单栏安装 Hooks 并启用登录启动。

当前发布包未使用 Apple Developer 证书签名，因此 Gatekeeper 会要求首次手动确认。

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
- `request_user_input` 可以识别为等待操作，但必须回到 Codex 原生终端作答。
- 已经运行的 Codex 进程可能需要通过 `/hooks` 重新载入并信任新 Hooks。
- 本项目只显示状态，不负责切换或聚焦具体终端标签页。
- Windows/macOS 发布包目前未签名。

## 卸载

Ubuntu 源码安装：

```bash
sh scripts/uninstall-linux.sh
```

其他平台先从菜单卸载 Hooks 和开机启动，再删除应用目录。

## 许可证

[MIT](LICENSE)
