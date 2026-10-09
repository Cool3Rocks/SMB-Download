# SMB Download

面向 Steam Deck 的 Decky 插件：从 SMB 共享下载游戏文件夹，在游戏模式中选择启动程序，并将游戏添加到 Steam 的「非 Steam 游戏」库。

**当前版本：0.1.1**

配置 SMB → 浏览文件夹 → 下载到本地 → 选择 EXE → 添加到 Steam。

## 功能

- **SMB 浏览**：支持中文路径、多层目录、名称与修改时间排序，以及当前目录的文件夹名称搜索。
- **下载管理**：显示扫描和下载状态，支持暂停、手动继续、断点续传，以及取消并清理临时文件。关闭插件页面后仍可继续下载。
- **Steam 入库**：手动选择游戏目录中的 EXE，设置游戏名称及 Proton；默认使用 Proton Experimental。支持修正已有条目的启动设置。
- **下载时保持亮屏**：下载期间临时调整自动变暗和休眠计时，任务结束后恢复原值；可在设置中关闭。
- **游戏删除**：移除任务关联的 Steam 快捷方式、下载目录和任务记录，保留 SMB 源文件。
- **诊断工具**：在设置页查看日志和 SMB 依赖检查结果。

## 使用条件

- Steam Deck / SteamOS，已安装 Decky Loader。
- Steam Deck 能访问的 SMB 共享，以及相应的用户名和密码。
- 有足够可用空间的本地安装目录，可位于内置存储或 SD 卡。
- 发布包的 Python 依赖面向 **Linux x86_64 / CPython 3.11**；其他后端运行时需重新打包对应依赖。

插件通过 SMB 直接访问共享，不需要将共享挂载到 SteamOS。发布包已包含所需 Python 依赖。

## 安装

1. 从本仓库的 [Releases](https://github.com/Cool3Rocks/SMB-Download/releases) 获取安装包，版本 0.1.1 的文件名为 `SMB-Download-0.1.1.zip`。
2. 使用 Decky Loader 的开发者插件安装功能安装 ZIP，具体入口以已安装的 Decky 版本为准。
3. 也可手动解压，将包内的 `SMB-Download/` 文件夹放入 `/home/deck/homebrew/plugins/`。
4. 手动部署后重启 Decky 服务：

   ```bash
   sudo systemctl restart plugin_loader.service
   ```

5. 返回游戏模式，在 Decky 中打开 **SMB Download**。

升级前请备份原插件及本地状态目录。由旧命名版本升级时，配置、任务和 Steam AppID 恢复记录不会自动迁移：请先完成或取消旧任务，确认电源计时已恢复，再停用旧插件并安装新版。新版需要重新配置 SMB。

## 快速上手

### 1. 配置连接

在设置页填写连接信息，保存后测试连接：

| 设置 | 示例 | 说明 |
| --- | --- | --- |
| IP / 主机名 | `192.168.1.100` | 提供 SMB 共享的设备 |
| 端口 | `445` | SMB 服务端口 |
| 共享名称 | `Games` | 填共享名，不填完整网络路径 |
| 用户名 / 密码 | 共享账户的凭证 | 账户需有读取权限 |
| 游戏源目录 | `PC/Ready` | 共享内相对路径；留空表示共享根目录 |
| 本地安装目录 | `/home/deck/Games` | 也可选择 SD 卡上的目录 |

例如，共享名称为 `Games`、游戏源目录为 `PC/Ready` 时，浏览的是该共享下的 `PC/Ready` 文件夹。

### 2. 下载游戏

打开「浏览 SMB」，点击文件夹右侧的「下载」，或进入目录后点击「下载当前文件夹」。任务启动后会显示扫描和传输状态。

暂停、断网或插件重启后，需手动点击「继续下载」。续传会检查远端文件的清单、大小和修改时间；文件发生变化时会停止续传。

### 3. 添加到 Steam

下载完成后打开任务的入库设置，选择实际启动游戏的 EXE，确认游戏名称及 Proton 设置，然后点击「添加到 Steam」。

EXE 列表包含游戏目录及其子文件夹中的可执行文件；工作目录自动使用所选 EXE 所在目录。插件通过运行中的 Steam 客户端接口创建快捷方式，不直接修改 `shortcuts.vdf`。封面可自行配置。

### 4. 删除游戏

点击任务中的「删除游戏」，核对确认窗口中的名称、路径和范围。已入库游戏会先验证 Steam 快捷方式已移除，再删除受管的本地游戏目录。

删除不会清理其他位置的 Proton 前缀、存档或封面图片。

## 当前限制

- 同一时间运行一个下载任务。
- 搜索仅作用于当前目录，不递归搜索 SMB 共享。
- 当前启动程序选择界面仅列出 `.exe`，不提供安装器或压缩包安装流程。
- 不支持匿名 SMB、SMB 符号链接或目录联接。
- 插件负责下载和创建启动条目，游戏能否运行取决于游戏本身及 Proton 兼容性。
- 本地测试使用模拟文件源和 Steam 接口；真实 SMB、Steam UI、Proton、亮屏及手柄交互仍需 Steam Deck 实机验证。

## 本地数据与日志

运行数据保存在 `~/.local/share/SMB-Download/`：

| 文件 | 用途 |
| --- | --- |
| `smb-download-settings.json` | SMB 连接及下载设置 |
| `smb-download-tasks.json` | 下载任务及恢复记录 |
| `smb-download-power.json` | 电源计时恢复记录 |
| `plugin.log` | 插件运行日志 |

连接配置及任务快照可能包含 SMB 密码。文件权限为 `0600`，内容未加密；页面读取接口不会返回密码，任务完成入库后会清除连接快照中的密码。提交问题时请勿附带原始配置或任务文件。

查看日志：

```bash
tail -n 200 -f ~/.local/share/SMB-Download/plugin.log
sudo journalctl -u plugin_loader.service -n 200 -f
```

## 开发与构建

需要 Node.js、pnpm，以及 Python 和 pip。首次安装前端依赖和打包 Python 依赖需要联网。

在项目根目录执行：

```bash
pnpm install --frozen-lockfile
pnpm run typecheck
node --test tests/test_smb_download_steam.cjs tests/test_smb_download_power.cjs tests/test_smb_download_delete.cjs
python -B -m unittest discover -s tests -v
pnpm run build
python -B tools/build_smb_download.py
```

打包结果为 `artifacts/SMB-Download.zip`，包内只有一个 `SMB-Download/` 插件目录。发布时可按版本重命名为 `SMB-Download-0.1.1.zip`。

打包脚本默认下载适用于 Linux x86_64 / CPython 3.11 的依赖；需要其他 Python 版本时，在新构建目录中使用：

```bash
python -B tools/build_smb_download.py --python-version 3.11
```

再次打包会复用 `py_modules/smb_download_vendor/` 中的依赖。更改依赖版本或目标 Python 版本后，请在干净的构建目录中重新生成。

源码、测试和依赖锁文件应提交到仓库；`node_modules/`、`dist/`、打包依赖、安装包及本地运行数据已由 `.gitignore` 排除。安装包可单独作为 GitHub Release 附件发布。

## 项目结构

```text
main.py                         Decky 后端入口
plugin.json                     插件元数据
src/                            前端页面、Steam 入库与电源控制
py_modules/smb_download_*.py     SMB 下载服务、插件接口与恢复逻辑
py_modules/smb_download_stdlib/  冻结 Python 运行时所需的标准库补充
tests/                          前后端测试
tools/build_smb_download.py      Linux 依赖打包与安装包生成
docs/smb-download.md            开发说明及实机验收清单
```

## 问题反馈

请通过本仓库的 [Issues](https://github.com/Cool3Rocks/SMB-Download/issues) 提交反馈，并附上插件版本、Decky 版本、后端 Python 版本、复现步骤和已去除敏感信息的日志。

更详细的实现、恢复机制和实机验收步骤见 [开发文档](docs/smb-download.md)。

## 许可证

本项目采用 [Apache License 2.0](LICENSE)。随项目提供的 CPython 标准库补充及打包的第三方依赖遵循各自许可证，相关许可证文件保留在对应目录中。
