# builtin/interval-pick 0.1.0

## 实现

新增随本体分发节点 `builtin/interval-pick@0.1.0`，分类 basic/array/interval-pick。仓库路径为 `hololab/hololab/builtin_packs/interval-pick@0.1.0/`（HoloLab 仓库内部为 hololab/builtin_packs/）。

- `interval_pick.py:9`：纯下标选择函数，标准库实现；interval 默认 2，start 默认 0。
- `interval_pick.py:21`：排除隐藏条目，按 basename bytes 排序，取 `entries[start::interval]`，不解析文件名里的数字、时间或帧率。
- `interval_pick.py:25`：物化真实输出目录，对选中元素建立到其 resolved 路径的符号链接，保留原名和内部结构。
- `manifest.yaml:14`：interval 为 int/min=1；start 为 int/min=0。prepare 做前置类型/范围校验，脚本也防御直接调用。
- `manifest.yaml:24`：输出继承元素类型与完整轴名。

选择最外层下标 start、start+interval、……，输出长度 `max(0, ceil((N-start)/interval))`。空输入、start=N、start>N 都输出空目录。输入/输出重叠、非空输出拒绝，避免污染源或残留旧结果。不访问时间信息，不依赖 to_array/from_array 或转换器，收到的是实际数组目录。

## 通用声明

输入 `in: {tags: [any], arrayed: true}`；输出 `out` 同样 tags:any/arrayed:true，另有：

```yaml
tags_from: in
dim_labels_from_input: in
dim_labels_drop_outer: 0
```

不写死 image、colmap4d 或 frame。只有外轴长度改变，轴标签及内层数组原样继承。两种类型测试覆盖 image[frame] 和 arbitrary-bundle[sample,view]，均验证类型和标签传播。不能因为输入是多层数组就把目录内部展平。

## 与 slice 一致性

直接参考现有 slice@0.1.0 的 manifest 及脚本：相同的 any、tags_from、dim_labels_from_input/drop_outer=0、host 执行模式；相同的输出目录+resolved 符号链接、保留元素名、只处理外轴。测试用同一输入比较 interval-pick(start=1,interval=3) 与 slice(start=1,step=3) 的所有链接名及目标，完全相同。

明确区别：slice 的空输入/空结果报错；本节点按长度公式允许空数组。slice 遇到已有目标目录会跳过，本节点要求干净输出，不静默保留陈旧元素。排序采用新数组定义的 basename bytes，ASCII/正常 UTF-8 文件名与 slice 的字符串排序一致，并正确处理任意文件名字节。

## 内置分发与环境

设计文档 docs/builtin-packs-design.md:77 指出首批应为标准库及本体明确支持的能力，host 使用节点 Python，离线可运行。本节点满足更严格的标准库+host+无网络：requires={}，runtime.mode=host，无 GPU，无第三方 import、子进程算法、网络或权重下载；测试以 `python -I -S` 成功运行。

不是把任意目录放进 builtin_packs 就会入选。已向 index.json **仅追加本节点记录**；bundled.py 的 verified_record 重新核验名称/版本/root/identity-format 和完整代码哈希，测试及在线发现都成功。锁定哈希：`d5370f3c01629a4768da3bcf0185a94145c5272db06a35e2d9678e236fc7145e`。

pyproject.toml:64 的 wheel packages=[hololab]、:73 的 sdist include 已覆盖此资源路径，无需改打包配置。本轮没有重建 wheel/sdist；已验证源码安装下发行清单、哈希及 host 在线执行。不能把历史设计提案说成对所有现有 builtin 的强制标准库静态检查：当前目录还包含声明 ffmpeg 外部能力的 frame-extraction；本次新数组节点没有这些依赖。

## 验证证据

- schema.json：load_manifest 与 bundled.verified_record 通过，origin=bundled。
- tests.log：**105 passed**，包括新节点 91 项、现有 slice 14 项。80 种 N/interval/start 组合（N=0/1/2/5/8，interval=1/2/3/20，start=0/1/4/9），覆盖间隔大于 N、尾部不足、起点越界；另有非法整数/负数/bool、嵌套目录、隐藏条目、按下标而非名字数值选择、非空输出/重叠拒绝、stdlib CLI、类型/轴名继承。
- prepare-0.json、prepare--1.json：真实 gateway prepare HTTP **422**，分别为 `parameter 'interval': must be >= 1, got 0/-1`，没有执行非法 job；prepare-3.json 为 HTTP 200、run_blockers=[]。
- 没有修改通用 prepare 校验或其它平台代码，使用既有 ParamSpec.min 机制。

第一次 ad-hoc jobs/run 使用已有 frame_sequence handle，81 → 27，哈希及链接正确（verification.json）。该低层入口没有 graph 推导上下文，返回 any/无轴名；因此**不以它作为类型继承验收**。随后执行完整真实 workflow 验证类型，见下节。此边界与现有泛型节点一致，没有为此增加平台代码。

## 真实 workflow 实测

验证 workflow：`83b7948a-c12b-4e03-85fb-277c6c8f9627`（Interval Pick 0.1.0 verification）。原生产 workflow 未改；验证图为 single-video-source → frame-extraction → interval-pick，不含 array view 或转换节点。

快照 `6ab1c42c-4bfd-4fd5-a871-e7d6ded294c3` 三个节点均 done。输入真实 kiridog2_81f.mp4；frame-extraction 实际产出 81 张图片。interval=3/start=1，选取下标 1、4、……、79，共 **27 项**。

- interval-pick job：`52f88589-1407-4b05-bcf1-3b9650bd9839`，started→done **0.091872 s**。
- output handle：`2bd30f7d-63b1-45bc-8f96-cf76a3693781`。
- 输出目录：`/cloud/cloud-ssd1/Kiri4DGS/output/hololab/w/83b7948a-c12b-4e03-85fb-277c6c8f9627/j/52f88589-1407-4b05-bcf1-3b9650bd9839/out`。
- 真实 handle 元数据：tags=[image]，dim_labels=[frame]，dim_sizes=[27]，与输入 [image]/[frame]/[81] 对齐。
- graph-verification.json：全部 27 项符号链接逐一指向预期源文件，SHA-256 全等。只检查可见数组元素，排除 gateway 自己的隐藏元数据。

graph-input/output-handle.json、graph-job/log.json、snapshot.json 保存原始证据。验证 workflow 与产物保留，便于复核和保持符号链接源生命周期；未停止或修改任何其它任务。

## 截图与遗留

[node-light.png](node-light.png)、[node-dark.png](node-dark.png) 为真实画布浅/深色截图，显示 interval-pick 0.1.0、in/out、interval/start 和成功运行状态。

截图同时如实保留了现场预览隧道的 HTTP 502：`preview tunnel failed: Cannot call "send" once a close message has been sent.` 上游原视频/抽帧预览也出现同样错误，证据在 preview-check.json。任务与本地物化产物成功，不能把预览报错当作数据验证成功的替代证据；本轮没有越界修改前端/隧道或重启服务。

符号链接依赖源元素持续存在，与 slice 相同。空数组可能被某些下游算法拒绝，这不改变本节点的空数组语义。未 push、未重启；未改其它 pack 或 array-view 任务文件。开始时主仓库的 HoloLab 子模块指针已有他人提交差异，因此本次仅提交 HoloLab 内自己的文件，不将那些先存差异混入主仓库指针提交。
