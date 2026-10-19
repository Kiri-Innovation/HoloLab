# 原生数组参数与 Inspector MVP 验收

本次只修改参数系统、Inspector、三个 source pack 和相关文档/测试。未修改端口/连线 Type IR、website、其它 pack 或 compose-video 实现。未 push、未重启生产。所有真实执行均在独立的 `/tmp/hololab-array-e2e` 实例，gateway `19828`、fileserver `19829`、Vite `15184`。

## 调查（file:line）

调查基线：`3a2292b`。以下基线行号可用 `git show 3a2292b:<path>` 查看。

- `hololab/manifest/schema.py:20` 定义 ParamType，原有 int/float/bool/string/path/enum 及专用 `list[str]`；并非完全没有数组，只是没有通用数组。
- `hololab/manifest/schema.py:227` 的 ParamSpec 默认值为 Any/None，min/max/values 均属参数字段；原有 enum、path、list[str] 各有校验。参数与 InputSpec/OutputSpec 独立，不必改变端口类型。
- `hololab/frontend/src/canvas/NodeInspector.tsx:249` 原有 `spec.type === "path"` 分发，随后为 DimListEditor、enum 和普通输入框。
- `packs/float-array-source@0.1.0/run.py:51`、`packs/vec3-array-source@0.1.0/run.py:51` 原先对 items 调用 json.loads；数组元素其实是带可选 id 的对象，不是裸 float/vec3。
- `packs/video-array-assemble@0.1.0/run.py:54` 原先解析 paths_json 字符串。
- `hololab/gateway/workflows.py:84` 的 params 为 `dict[str, Any]`，保存可直接承载 JSON 数组，不需要改 workflow 结构或端口校验。

当前实现：

- `hololab/manifest/schema.py:229` ParamSpec，`:249` 按元素校验，`:284` 声明及默认值校验。
- `hololab/gateway/registry.py:604` 导出 items/min/max 到 catalog；`hololab/frontend/src/wire.ts:263` 前端参数字段对应。
- `hololab/node/runtime.py:1435` 先做三个 pack 的兼容转换，再验证数组，最后解析计算节点路径。
- `hololab/manifest/path_params.py:11` 逐个复用原 path 参数解析，错误包含索引。
- `hololab/frontend/src/canvas/ParameterInput.tsx:15` 共享标量分发；`:48` 通用数组行；文件末尾的 Vec3ArrayParameterInput 是参数展示适配器。
- `hololab/frontend/src/canvas/NodeInspector.tsx:79` 兼容旧值读取，参数区选择数组/标量输入及 vec3 三列展示。

## ⚠️ IR 语义决策

```yaml
params:
  offsets:
    type: array
    items: float
    default: [0, 0.25]
    min: 0
  paths:
    type: array
    items: path
    default: []
    path_kind: any
```

- `array<T>` 的声明为 `type: array` + `items: T`，T 为现有标量；值是 JSON 数组。
- 不支持嵌套数组、对象数组、array<list[str]>，未新增端口类型或 vec3 参数标量。
- min/max 是**每个数字元素的包含边界**，不是数组长度；数字必须有限，bool/数字字符串不能冒充数字。
- enum 的 values、path 的 must_exist/path_kind 逐元素传导；path 在执行节点验证、展开和规范化。
- 默认数组在加载 manifest 时校验，实际参数在新任务执行前校验。workflow GET/保存不新增强制校验，避免让历史草稿无法读取/保存。
- 空数组在 IR 合法，三个 pack 自身要求非空有效数据。optional 允许整个数组缺省/null，不允许 null 元素。
- 原有 string、enum、path 和专用 list[str] 的后端语义保留。普通字符串不会被全局 json.loads。

## 三个 pack 的迁移

| Pack | 新参数 | 兼容策略 |
|---|---|---|
| video-array-assemble | paths: array<path> | 旧 paths_json 在执行时转换；同时兼容调度器补入的 paths 默认空数组 |
| float-array-source | items: array<float>, ids: array<string> | 旧对象 JSON items 转成数值数组和 ids |
| vec3-array-source | x/y/scale: array<float>, ids: array<string> | 旧 transform 三元值转为三个等长标量数组，保留 ID |

**⚠️ vec3 表达决定：**现有参数标量没有 vec3，而本次禁止嵌套/对象数组。采用三个 array<float> 存储，Inspector 合成一行 x/y/scale；每个加删操作同步三列。这是 UI 展示适配，不是 array<vec3> 或对象数组。若外部草稿的三列长度不同，退回独立数组控件，避免丢弃数据；pack 会拒绝长度不匹配。

ids 留空时按连接的视频目录排序对齐，显式 ids 保留原有集合匹配规则。旧参数仅在运行时副本中转换，GET 不写回；Inspector 编辑时才通过正常 autosave 保存原生值。run.py 独立调用也保留旧 JSON 输入入口。

## 行编辑器

ParameterInput 是单一标量控件分发，供普通参数、数组行、vec3 三列共同复用；path 继续使用 PathInput、enum 继续使用 EnumSelect。数字框和 bool 勾选框保存原生数值/布尔值，string 保留字符串。每行右侧 −、底部 + Add item；有索引和可访问名称。沿用产品主题变量和 Inspector 控件样式，390px 窄屏无横向溢出。拖动排序本次未实现。

## 验证结果

- Python：新增数组 13 项；已有 manifest/enum/path/default 合并 50 项通过。覆盖六种标量元素、默认值、min/max、非有限数、嵌套拒绝、路径类型、兼容转换、原生输出以及执行前拒绝错误值。
- 三个 pack 原有 unittest：3 + 3 + 1 项通过。
- Playwright 原有 enum/path：6 项通过。
- `tsc -b`、`vite build` 通过；仅有现有大 bundle 提示。
- 真实独立节点注册四个 pack（三个目标 + 未修改的 compose-video）；修改**临时拷贝**的 manifest 注释，三个目标的 catalog hash 均热更新。
- 浏览器真实操作：paths 两行，offsets `[0,0.25]`，transform 两行 `[0,0,1]`、`[0.4,0.2,0.5]`；逐项执行 + 和 −；实际保存到独立 gateway。
- 原生链及旧 JSON 链各四个 job 全部 done（共八个真实任务），没有模拟 pack 执行。
- offsets/transforms 的所有文件和最终 output.mp4 **逐字节 SHA-256 一致**。见 [output-comparison.json](output-comparison.json)。
- GET 前后旧字符串参数完全一致，见 [legacy-before.json](legacy-before.json)、[legacy-after.json](legacy-after.json)。这是独立实例里的旧格式工作流回归，未写入生产工作流。
- [live-results.json](live-results.json) 记录真实 workflow/job ID；[native-graph.json](native-graph.json) 记录浏览器提交的原生数组。

脚本：`hololab/frontend/e2e/array-params-live.mjs`，不使用 API fixture，浏览器请求转发到真实独立 gateway。复跑需先把四个 pack 拷贝到 `/tmp/hololab-array-e2e/packs`，用独立 HOLOLAB_HOME/config 启动上述端口，workspace 指向 `/tmp/hololab-array-e2e/workspace`，将 env kiri 映射到可执行 python/ffmpeg 的 conda 环境；再从 frontend 目录执行 `node e2e/array-params-live.mjs`。脚本会创建测试媒体、工作流并运行，故勿把 base 改为生产地址。

## 截图

| 控件 | 浅色 | 深色 |
|---|---|---|
| float 行 | [浅色](float-light.png) | [深色](float-dark.png) |
| path 行 | [浅色](path-light.png) | [深色](path-dark.png) |
| vec3 三列行 | [浅色](vec3-components-light.png) | [深色](vec3-components-dark.png) |

[390px 窄屏](narrow.png)。所有截图来自实际 Inspector，显示加号及逐行减号。

## 部署边界

**新增 ParamType 需要更新 Python 进程。**旧进程不会通过重扫 YAML 学会 array，用户部署时应统一更新并重启 gateway/node，再使用三个新 manifest。这里验收的是运行新代码实例中的 manifest 热注册。未重启生产，也未触碰 compose 的 layer_overrides_json 等后续范围。
