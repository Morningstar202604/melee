# 前端（零构建）

五个页面，直接消费比赛服务的 REST + SSE。没有打包器、没有框架、没有 node_modules。

| 页面 | 作用 |
|---|---|
| `index.html` | 首页：直播条、赛事库、六赛制入口、14 天活动、胜者榜 |
| `watch.html` | 观战：舞台、选手卡、时间轴、回放、剧场模式、高光导出 |
| `season.html` | 赛季：积分榜、赛程、宿敌、新建/推进 |
| `recap.html` | 战报：一句话、数字、三个瞬间、选手数据、高光 |
| `director.html` | 导播台：选台、镜头、人工切镜、实时事件 |

## 跑起来

单端口即可（前端已挂在服务根路径）：

```bash
cd melee
python scripts/serve.py --port 8099
```

打开 <http://127.0.0.1:8099/>。

也可以把 `apps/web` 当独立静态站部署（`python -m http.server 8088`），
用 `?api=http://127.0.0.1:8099` 指向 API；`?token=` 会随导航带到其它页面。

- `match=` 直接打开某一场：`watch.html?match=m_xxxx&api=http://127.0.0.1:8099`。
- `?match=` 打开首页会自动跳转到 `watch.html`（兼容旧书签）。
- 不传 `match` 时观战页优先接最近一场直播，没有直播就显示"去赛事库"。

## 交互

| 操作 | 效果 |
|---|---|
| 拖时间轴 | 进入回放，事件前缀重算画面 |
| 暂停 / 继续 | 回放模式下暂停推进 |
| 从头回放 | 从第 0 条事件重新播放 |
| 回到直播 | 跳回最新事件并继续接收 SSE |
| 倍速 | 0.5x / 1x / 2x / 4x |
| 打开 | 输入 matchId 直接连 |

## 结构

```
index.html / watch.html / season.html / recap.html / director.html
src/view.js     事件 → 视图（纯 reducer，直播与回放共用）
src/sse.js      EventSource 封装（token → 短时票据）
src/api.js      统一 REST 客户端、格式化、赛制元数据
src/nav.js      顶栏与页脚（五页共用）
src/paint.js    首次绘制前清静态占位，之后走 lit-html 增量
src/home.js / main.js / season.js / recap.js / director.js
```

## 为什么没有用 React

播放器的状态是"一串事件 + 一个游标"，纯 reducer 足够；
DOM 更新只在事件到达或游标移动时发生，频率跟 SSE 走，不在动画帧里重渲染。
引入框架会增加构建链，但不会让画面更好看——好看来自排版、节奏和镜头，不是来自组件树。

如果后续要做导播台、赛季页、复杂的表单，再按计划文档的 `apps/web` 方案迁到 React + Vite。
届时 `src/view.js` 的 reducer 可以原样搬过去。

## 看不到画面时的排查

1. 浏览器控制台有没有 CORS 报错 → 检查 `api=` 参数与服务是否启动。
2. 顶栏显示「已结束 / 待命」→ 已结束的比赛不再保持 SSE 长连接（正常，事件仍可回放）。
3. 显示「这场没加载出来」→ 该 matchId 不存在；点「去赛事库」或「重试」。
4. 画面全空但事件数在涨 → 刷新页面，`view.js` 的 reducer 与事件类型可能不同步。
