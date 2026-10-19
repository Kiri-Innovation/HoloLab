---
title: 一切面向 Agent
group: 快速开始
order: 25
---
# 一切面向 Agent

你可以在画布上工作，遇到问题时把具体对象交给 agent，不必先在对话里重新描述现场。HoloLab 给人提供可视化工作区，也让 agent 通过结构化接口读取同一份工作流和执行记录。

## 一切皆可引用

不用描述“刚才失败的那个节点”，直接使用它的引用入口。工作流、画布节点、Run、Job、产物引用（Handle）、Pack 和计算设备都有 `hololab://` 引用；`hololab://workflows` 和 `hololab://artifacts` 则指向整个列表。引用标识的是实例内的对象，因此也要把实例地址告诉 agent。

<div class="flops-only">

在 Flops 内嵌浏览器中，引用会插入对话输入框；普通浏览器会复制引用文本，供你粘贴。插入的引用附带一份标记为 `hololab.reference-snapshot/v1` 的 Markdown 摘要，分为 identity、state、context 和 navigation 四部分。它记录的是引用时的情况；agent 可以重新解析引用，获取当前数据。

</div>

## 一切皆可被 agent 读取

agent 可以调用 `GET /api/resolve?ref=hololab://…` 读取对象，再沿返回的关联链接继续查。对于画布节点，响应包含已保存的参数、上下游连接，以及存在时的最近执行归属和输出 Handle。

下面是解析响应的结构示意，省略了具体数据：

```text
kind + ref   → 指的是哪个对象？
resource     → 对象数据与上下文
related      → 继续读取的 API 链接
```

解析接口返回 JSON，上面的四段式 Markdown 摘要由它生成。任务日志可从 `/api/jobs/<id>/log` 读取，运行快照可从 `/api/snapshots/<id>` 读取。agent 因而可以调查失败步骤，不必从界面像素里辨认状态；但要判断渲染效果好不好，仍然需要看产物。

## 一切包含在技能中

仓库中的 `skill/SKILL.md` 介绍如何检查实例、解析引用、编辑和运行工作流、等待结果、读取产物与日志，以及取消任务。它也指向 `docs/` 中的 Pack 编写指南和规范。

让 agent 从仓库副本读取这个文件，或通过它支持的技能机制装载。只提供实例 URL 不会自动安装技能：目前实例没有自动下载或安装技能的端点。这份技能是附带后续文档入口的操作指南，并非把所有手册都打包在一个文件里。

## 先试一个只读问题

把实例地址、技能文件和一条 Job 引用交给能访问该实例 HTTP 接口的 agent，问它：“这个任务为什么失败？先读参数和日志，再建议怎么改。”第一次交接就有了具体对象，也不需要另起一次运行。

实体概念见[介绍 HoloLab](/docs/introduction)和[产物与运行记录](/docs/use/run-records)。接下来：[第一个工作流 →](/docs/use/workflows)。
