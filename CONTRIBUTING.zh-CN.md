# 参与贡献

[English](CONTRIBUTING.md) · **简体中文**

谢谢你愿意花时间。melee 的体量不大，一个清楚的 bug 报告或一个聚焦的 PR 通常很快就能落地。

## 动手之前

- 比错别字大的改动，先开 Issue。先说清方向，比写完再推翻便宜。
- 安全问题请发到 [SECURITY.md](SECURITY.md) 里的地址，不要开公开 Issue。
- 提交即表示你同意以项目的 MIT 许可证发布你的贡献。

## 本地准备

```bash
python -m pip install -r requirements.txt -r requirements-dev.txt
python -m playwright install --with-deps chromium
bash scripts/verify_all.sh
```

最后一条就是全部门禁。开 PR 之前它应该打印 `ALL GREEN`。缺浏览器内核时审计会显示 `SKIP`；请在 PR 里说明，而不是当成通过。

## 什么样的改动算好

- **有一个去掉改动就会失败的测试。** 这个项目很少接受“相信我”的补丁，changelog 里刻意链接到测试。
- **diff 要小。** 重构和行为变化分开提交，评审才能一次只跟一个想法。
- **用人话写。** 报错、战报和后台标签是给人读的，不是给机器解析的。
- **不要藏状态。** 事件日志是唯一真相；观众能看到的东西必须能从日志推出来。

## 提交与 PR

提交标题用祈使句：`Fix turn timeout on reconnect`，不要写 `fixed stuff`。PR 说清改了什么、为什么、怎么验证。界面变了就附截图。

## 文档

每篇核心文档都有英文版 `docs/*.md` 与中文版 `docs/zh-CN/*.md`，顶部可切换。行为变化影响到读者能看到的东西时，两边要在同一个 PR 里一起改。截图来自 `scripts/capture_gallery.py`，不要手工修图。

## 发布

发布从 `main` 按 [版本与发布流程](docs/zh-CN/VERSIONING.md) 切出。贡献者不需要改版本号，维护者在发布时统一处理。
