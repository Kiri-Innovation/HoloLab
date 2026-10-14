---
title: 端口类型系统
group: 系统设计
order: 30
---
# 端口类型 IR

HoloLab 用结构化数据表示端口类型。显示标签只是这些数据最终呈现的文本；不要通过解析标签来判断兼容性、数组深度或调度方式。manifest 继续使用 YAML 字段，不引入新的类型字符串语法。

## 带版本的封装结构

`PortTypeIR` 的版本为 `schemaVersion: 1`，包含三份独立记录：

| 记录 | 含义 |
| --- | --- |
| `declaration` | 原始 manifest 字段及推导指令。 |
| `views` | 规范化后的调用类型和聚合类型，以及参与模式。 |
| `runtime` | 实际 Handle 的观测结果，包括证据及其适用范围。 |

算法声明与其实际生效的聚合类型不能混用。运行时计数既不会改变声明，也不能作为兼容性的依据。

## 递归类型表示

```ts
type TypeExpr =
  | { kind: 'unknown'; reason: string }
  | { kind: 'object'; tags: string[]; contentAxes: { label: string | null }[] }
  | { kind: 'array'; axis: { label: string | null }; element: TypeExpr };
```

每个 `array` 增加一层集合。各轴按从外到内的顺序排列。对象的 `contentAxes` 不会增加用于枚举分片的层级。未命名的轴使用 `null`；无论是标签的拼写还是数值计数，都不能补足缺失的语义。即使元素类型未知，数组本身仍是已知的数组。

`any` 是显式的泛型标签，与表示信息无法解析的 `unknown` 不同。已移除的图像标签即使与 `any` 组合使用，也仍然无效。

## 调用、聚合与广播

参与模式为 `none`、`map-input`、`broadcast-input` 或 `collect-output`。调用视图描述算法的一次调用，聚合视图描述连线上传递的值。

Node 启用 Fan-out 后，映射输入会在声明的调用值外增加一层集合。因此，一次消费 `[cam]` 图像集合的调用，会从聚合后的 `[frame, cam]` 集合中读取一个元素。每个收集输出也会增加一层外层集合。当前 Executor 的枚举深度为一层。

广播输入将声明的完整值传给每个分片。它不会移除层级、生成 N 份物理副本，也不会让集合满足单张图像的声明。通用数组辅助工具可以表达多层结构，但这不意味着 Executor 会在一次 Fan-out 中遍历所有层级进行派发。

`image` 表示一个可完整解码的单帧文件。图像集合需要显式声明 `arrayed`、目录存储形式以及按从外到内顺序排列的轴标签。图像没有隐式的图像数量内容轴。

## 兼容性

验证基于结构化的有效类型。普通对象叶项比较标签；普通集合值逐层递归比较。显式的 `any` 遵循泛型兼容规则。尚未解析的类型不能证明连接有效。

输入的 `require_axis_label_match` 默认为 `true`。在参与比较的每一层集合上，如果双方标签都非空，就必须一致，除非输入显式禁用了轴标签匹配。禁用此项只影响标签比较：不会重命名数据、改变集合形状、修改观测结果或改变调度方式。

`storage` 控制文件传输，不是独立的连线匹配规则。严格图像声明及实际交付的数据仍须满足各自的存储和形状契约。连线通过类型检查，并不证明数据在业务语义的每个方面都能被算法接受。

## 运行时观测

每个观测字段都携带证据：

```ts
type Evidence<T> =
  | { status: 'unknown'; reason: string }
  | { status: 'sampled'; value: T; source: string; sample: string }
  | { status: 'complete'; value: T; source: string };
```

`complete` 只适用于指定 Handle 的指定测量字段，不涵盖 Workflow 中的所有对象。外层集合的数量可以是完整测量结果，而内部位姿数量仍来自采样。零是一个测量值，未知不等于零。未知形状也不同于已测得的标量形状。

`RuntimeShape` 标识其对应的 Handle，并包含标签观测、按从外到内顺序排列的轴列表，以及内部计数。轴的角色为 `collection`、`content` 或 `unknown`，每个轴都有大小证据。整个轴列表的覆盖程度与各轴大小的覆盖程度可以不同。

图像契约验证会检查每个图像叶项。对不透明数据包的采样摘要只是观测结果，不是对包内全部内容的完整验证。

## 使用 API

画布 Node 的 `port_types` 包含带版本的 `inputs` 和 `outputs`。它由 Pack 目录推导而来，客户端应能处理该字段不可用的情况。图数据往返传输时可以接受此字段，但不会将其作为用户编写的契约持久化。草稿契约不会因为存在历史 Run 就自动带上历史测量数据。要获取观测结果，应读取相关输出 Handle 或 Handle 摘要。

`resolved_outputs` 等现有投影仍然可用。尚未保存的前端草稿可以使用结构化目录适配器，无需等待保存后的服务器响应。对于已经规范化的聚合数据，不要再次包装。

读取运行时数据时，应验证版本、结构和 Handle 标识。缺失、格式错误或标识不匹配的观测结果都应视为未测量。不要根据旧数值字段或 `legacyValue` 重建当前画布的测量证据。即使实际数据字段仍然未知，也可以显示已知的声明。

## 实现入口

- 后端模型与兼容性：`hololab/gateway/port_type_ir.py`。
- Workflow 规范化：`hololab/gateway/workflows.py`。
- 观测结果构建：`hololab/gateway/type_observation.py`。
- 前端结构化类型与操作：`hololab/frontend/src/types/portTypeIR.ts`。

manifest 字段参见 [Pack 规范](pack-spec.md)，图记录参见 [Workflow Schema](workflow-schema.md)。

## 纯操作

| 函数 | 输入 / 结果 | 规则 |
|---|---|---|
| `objectType(tags, contentAxes?)` | 结构化标识符 → `TypeExpr` | 复制输入列表；空标签转为未知；`any` 仍是已知的通配类型。 |
| `unknownType(reason)` | 原因 → `TypeExpr` | 不断言类型是标量还是数组。 |
| `wrapArray(element, axis?)` | `TypeExpr` → `TypeExpr` | 恰好增加一层集合。 |
| `wrapArrays(element, axes)` | 从外到内排列的轴 → `TypeExpr` | 按原顺序增加任意有限层集合。 |
| `unwrapArrays(type, depth=1)` | `TypeExpr` → `TypeResult` | 只移除集合层；遇到未知类型、非数组或无效深度时报告问题，不会默默按成功处理。失败时返回原始输入，不保留部分转换结果。 |
| `invocationFromAggregate(type)` | `TypeExpr` → `TypeResult` | 恰好移除一层已知集合，不适用于广播。 |
| `broadcastInput(type)` | `TypeExpr` → `TypeViews` | 调用与聚合视图都是同一个完整值，即使该值本身已经是数组。 |
| `viewsFromInvocation(type, participation, axis?)` | 规范化的调用类型 → `TypeViews` | `none` 不变；`map-input`/`collect-output` 包装一层；`broadcast-input` 不变。 |
| `unknownRuntime(handleId, reason)` | Handle 标识 → `RuntimeShape` | 表示缺少观测结果，不表示计数为零或已知的标量。 |

这些函数不修改输入，运行时不依赖其他库，也不访问 DOM 或网络。结构共享是有意的设计，使用方应将 IR 值视为只读。这些操作负责转换结构化类型；兼容性策略单独处理。

## API 数据结构与观测范围

```ts
type NodePortTypes = {
  schemaVersion: 1;
  inputs: Record<string, PortTypeIR>;
  outputs: Record<string, PortTypeIR>;
};
// graphNode.port_types and /api/resolve current_draft.port_types
// Handle summaries and latest_run.output_handles[port]:
// runtime_shape?: RuntimeShape & { schemaVersion: 1 }
```

`resolved_outputs` 为每个输出保留 `{tags, arrayed, dim_labels}`。`workflows.resolved_port_types` 为验证和 API 序列化提供规范化类型。运行整个 Workflow 的路由使用同一套目录转换逻辑。因此，推导出的输出维度也参与兼容性判断；推导出的集合不会被悄悄当成标量。

`type_observation.observe_summary` 在探测边界、写入缓存之前构建观测结果。`InternalCountItem {label: string, value: number}` 保留旧的 `internal_count_items` 数据结构；结构化的 `internalCounts` 则携带证据。

- 已注册标签的完整性只覆盖元数据范围，不证明文件内容符合标签。
- 一致的目录维度可以具有完整的目录树覆盖范围。
- 从代表性子项读取的计数属于采样结果；在完整 Handle 根目录范围内获取的计数可以是完整结果。采样路径相对于 Handle。
- 形状缺失或不规则时仍视为未知。不会将旧数值升级为测量证据。

摘要缓存键包含 `(handle_id, dim_labels)`；写入删除标记时，会使该 Handle 各范围的缓存失效。`runtimeObservation.ts` 验证运行时封装结构；`runtimeFromSummary` 拒绝属于其他 Handle 的证据。元素投影保留证据范围，不会凭空补出未经测量的维度。

## 前端适配器与渲染

`legacyPortType.ts` 是结构化旧字段的适配边界：

- `typeFromLegacy` 接受实际生效的连线字段，包括已解析的输出。
- `declarationType` 读取原始端口声明，不重新实现图推导逻辑。
- `legacyViews` 要求明确指出数据来自调用视图还是聚合视图。聚合字段已经包含有效的 Fan-out 层，不得再次包装。
- `adaptPortType` 使用实际生效的聚合字段。Node Fan-out 增加的层由解析器决定，而非这个适配器。
- `runtimeFromSummary` 只使用已验证的证据；`elementRuntime` 投影观测结构，但不改变其覆盖范围。

`renderPortType` 支持声明、长文本、紧凑和运行时模式。`formatTypeLabel` 与 `formatTypeLabelLong` 是兼容接口。命名数组渲染为 `arrayed[frame]<T>`；绝不能将标签反向解析成类型。已知契约可以显示为数量未知，而实际数据行仍保持未知状态。

| 显示位置 | 类型与观测上下文 |
| --- | --- |
| 端口与连线提示 | 声明、规范化视图、参与模式和证据。 |
| 连线紧凑标签 | 实际传输的类型及可用测量结果。 |
| 连线检查器 | 完整的有效标签和轴，以及可用观测结果。 |
| Snapshot 标签 | 已冻结的生产方解析输出，以及 Snapshot 的 Job Handle。 |
| 连接反馈 | 结构化的源类型、目标类型和兼容性原因。 |

Snapshot 的 Pack 声明仍然来自目录中所记录的算法版本；不要假定每个历史输入声明都单独冻结保存。已保存的 `arrayed_toggle` 会传给卡片与提示上下文，而 Pack 的 `arrayable` 能力来自其目录条目。`parallelism` 和 `batch_size` 是执行设置，不是显示数组深度的规则。预览数据来自 Snapshot 的 Job 和 Handle，而非当前草稿的过期状态。

## 旧数据处理策略

| 数据 | 策略 |
| --- | --- |
| 现有 API 标签、维度、计数和已解析输出 | 保留兼容的传输投影。 |
| 当前草稿的结构化字段 | 未保存的编辑继续使用显式适配器。 |
| 没有运行时证据的旧数值摘要 | 不据此重建当前画布的测量结果。 |
| `legacyValue` 或缺少新轴证据 | 即使存在旧数值，也视为未测量。 |
| 封装结构缺失、格式错误、版本不支持或 Handle 不匹配 | 回退到未知观测状态。 |
| Pack 或预览元数据不可用 | 保留安全的默认显示，不虚构信息。 |
