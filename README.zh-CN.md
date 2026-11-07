# melee

[English](README.md) · **简体中文**

AI 对局的自托管转播台：跑一局，看直播、回放、导播、战报——全部来自同一条事件流。

![首页](docs/screenshots/zh/01-home.png)

## 安装

**Windows** — 到[最新发布页](https://github.com/badhope/melee/releases/latest)下载：

| 文件 | 适合 |
|---|---|
| `melee-setup-<版本>-win64.exe` | 每用户安装包：开始菜单快捷方式，干净的卸载器 |
| `melee-portable-win64.zip` | 便携包：解压即用，双击 `start.bat` |

两种方式都会把服务起在 [http://127.0.0.1:8099](http://127.0.0.1:8099)。不需要管理员权限；除卸载器登记外不写注册表；数据不出本机——转播台只连接你自己配置的模型端点。

**源码运行**

```bash
python -m pip install -r requirements.txt
python scripts/serve.py
```

## 界面

| 直播 | 战报 |
|---|---|
| ![直播](docs/screenshots/zh/02-live.png) | ![战报](docs/screenshots/zh/08-recap.png) |
| 棋盘、选手面板、时间轴拖动 | 结果、关键手、高光剪辑 |

| 导播 | 后台 |
|---|---|
| ![导播](docs/screenshots/zh/10-director.png) | ![后台](docs/screenshots/zh/13-admin.png) |
| 自动/手动切镜，切镜也是事件 | 健康、队列、配额、赛季调度 |

[完整画廊](docs/screenshots/README.md) · [演示视频](docs/video/walkthrough.mp4) · 默认亮色主题，深色一键切换。

## 命令

```bash
melee serve                                    # 启动转播台
melee match create --game othello --player A --player B --open
melee match export <id> --out m.json           # 单文件导出，可在另一台机器重放
melee mcp                                      # MCP server（Claude / Cursor）
```

API 与 Agent 接入：[docs/API.md](docs/API.md) · [docs/AGENT-HARNESS.md](docs/AGENT-HARNESS.md)

## 几点说明

- 12 种赛制，每一种都有解说、切镜、战报与高光。
- 默认种子由服务端生成且不公开：比赛只能看，不能靠种子反推对手的隐藏信息。自己指定 `seed`，比赛就变成可复现的。
- 全站颜色集中在 `apps/web/src/theme.css` 一个令牌文件：默认亮场，深色一键切换；CI 门禁同时校验两套主题的 WCAG-AA 对比度。

## 开发

```bash
bash scripts/verify_all.sh    # 13 道门禁：测试、覆盖率、lint、安全、浏览器审计、对比度、外部验收
```

文档：[架构](docs/ARCHITECTURE.md) · [部署](docs/DEPLOY.md) · [公平性](docs/FAIRNESS.md) · [调研](docs/RESEARCH-2026-11-07.md) · [路线图](docs/ROADMAP.md) · [变更](CHANGELOG.md)

MIT License.
