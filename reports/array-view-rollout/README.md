# 一维双向数组视图：首个后端闭环

2026-11-05。已实现严格声明、转换计划、两个真实 provider 和后端整图 prepare/run 接入。真实 41,990,400 点数据已验证往返与切片。**尚未上线，未验证线上 gateway 派发；没有改前端，没有重启服务。单节点 Continue/Fork 暂未接入。**

本报告区分本地真实执行与线上能力，不将本地结果当作线上 job。无线上 job ID。

## 分阶段提交与代码位置

|阶段|提交|代码/验证|
|---|---|---|
|1 契约|46ba700|`hololab/manifest/array_views.py:12`：strict 嵌套 schema、1D 形状、版本固定的两个 provider；`:76` 附近冲突解析。`hololab/manifest/schema.py:103,219,706`：端口误放钩子直接报错，manifest 类型级能力正式字段。新增 + manifest/type 回归 102 passed。|
|2 计划|8bb1511|`hololab/gateway/array_views.py:15`：纯编译器，边局部插入普通 provider 节点；原 scalar 不变，不放宽 Object/Array 兼容；终端数组或具体 scalar 消费者恢复；来源不明拒绝。`registry.py:664` 输出能力声明。该阶段回归 108 passed。|
|3 数据 provider|5b95edb|`community_packs/colmap4d-to-array@0.1.0/convert.py:79,150,182` 及同代码 from-array provider。固定 0.1.0，逻辑环境 colmap4d，CPU。3 个 unittest 方法覆盖往返、同时间不同 ID、重复选择、真实 builtin slice、来源/内容/clock 破损、移除临时目录后的独立可读性。两个 manifest 校验通过。|
|4 prepare/快照|6f9e065|`hololab/gateway/preparation.py:51,77,284` 计划进入准备修订/快照，环境和代码身份沿现有普通节点流程；`app.py:2594` 整图入口先编译后校验/派发。相关准备、配方、生命周期回归通过。|
|5 收尾|见本报告所在提交|补拒绝 bool 冒充 schema version；真实数据哈希/严格验证/编译计划 demo 证据和本报告。|

类型能力只包含 `array`、`partition`、`to_array`、`from_array` 与版本，不存在 per-operation hooks。get-index/slice 的实现未改动，fan-out 使用既有执行器。未改 flatten/regroup。

原 handle 仍为标量，provider 输出才是真数组。IR 沿用已有 ObjectType/ArrayType 和 invocation/aggregate，runtime shape 未探测保持 unknown。没有新增前端 IR 字段；**现有浏览器连线校验尚未允许 scalar→array 的隐式转换，不能声称画布交互已经接通**。

`pinned_params` 不存转换状态；正常参数冻结会给生成的普通 provider 记录空参数 pin，这是已有机制。快照的 `execution.array_views` 保存分区、定义、provider 版本、生成节点；普通 provider 节点的代码身份、输入 handle lineage 和输出 handle 注册沿现有流程。

## 数据语义

- 首版仅完整 colmap4d，canonical BIN，唯一递增 point ID、完整时间覆盖、每点一个源观测、双向 track 全量一致。非该域输入报错，不偷偷降级。
- 每个元素是真实完整模型：sparse/0 全套表、时间文件和图片。保留相机表（含共享相机）、原 ID/ns/观测，不靠原模型指针冒充元素。
- 按原 int64 曝光中心分组；同时间多图同组；数组位置不是 IMAGE_ID 或秒数。
- namespace 是源模型文件哈希集合的 SHA-256；每元素记录位置、时间、图像 ID、provider 版本与文件哈希。正常执行时原 handle 与转换实现代码身份由 job/snapshot lineage 补足。
- 恢复当前限定同一来源命名空间、未编辑的组：同 ID 同内容去重；同 ID 异内容、namespace、clock/metadata 冲突拒绝；不同 ID 同时间保留；不重编号。
- provider 物化/恢复均写独立文件。builtin slice/get-index 仍可能写软链接；在线生命周期应通过普通 job 的输入 handle 依赖，由现有 `snapshot_delete.py:1` 所述传递保护机制保留生产者。这部分已有生命周期回归，**本轮未用线上新 provider job 实证**。
- 空选择恢复、修改过元素的自动对账、多观测/非规范编码暂不支持；均显式报错。

## 真实 4,199 万点验证

原模型：`/cloud/cloud-ssd1/Kiri4DGS/output/hololab/w/9967b1f8-3646-41f5-a269-394b6d392169/j/a31dba7e-9ca5-4c04-8d4b-c915a5a30937/model`。

产物：`/cloud/cloud-ssd1/Kiri4DGS/output/array-view-validation-20261105/`，约 13 GB。未修改原产物；磁盘最终约 113 GB 可用、93% 已用；未自动删除历史数据。

|执行|结果|本地进程耗时|
|---|---|---:|
|to_array 全量|81 组，41,990,400 点|102.809 s|
|from_array 全量|81 images，41,990,400 点|87.390 s|
|builtin get-index(30)|IMAGE_ID=31，t=1,000,000,000 ns|未单独计时|
|builtin slice [30:32] → restore|IMAGE_ID=31/32，1,036,800 点|恢复 1.059 s|

- `verification.json`：5 份二进制表 + 81 张图片，共 **86 文件 SHA-256 完全相同**；time_meta.json 按 JSON 比较相等。源目录的 framework 隐藏元数据不属于格式内容，未宣称其字节一致。
- `verify.py` 可只读复核哈希、index30 及 slice 时间 ID 对应关系。
- `library-validation.json`：index30 与 subset30-32 调用 colmap4d.validate_full，再 exit_code(strict=True)，均 **problems=[]、exit=0**。另外转换器对全模型与所有恢复元素做全量双向观测和每点源时间校验，不是抽样。
- `timings.json`、`to-array.txt`、`from-array.txt`、`subset.txt` 保存原始结果。

## 自动编译的最小 demo

`compiled_demo.py` 给平台的原图只有“已有 scalar model → builtin/slice(start=30,end=32)”；编译器自动增加 to_array 和终端 from_array。没有在原图手工指定两个转换节点。

`compiled-plan.json` 是实际 prepare 结果；脚本按生成图拓扑与真实 manifest 渲染并运行子进程，输入仍为上述完整 41,990,400 点模型。**这是本地实际数据执行，不是 gateway 调度，也未在运行实例注册这些 demo 输出 handle。**

- 自动 to_array：98.569 s。
- builtin slice：0.098 s。
- 自动 from_array：1.319 s。
- 三个实际操作累计约 **99.986 s**；`compiled-demo.json.seconds=1.424` 是续跑部分墙钟，不能当作整链时间。
- 第一次 demo runner 在 to_array 完成后误用了 `RenderedManifest.args`；修正为 `python_args` 后通过 `--resume` 接续已完成输出。原失败日志与续跑日志均保留，没有把失败隐藏或重新伪造输出。
- `compiled-demo-verification.json`：编译图最终产物与已验证的真实两组子集逐二进制/图片哈希相同，元数据语义一致。

## 回归与未完成项

- 扩大回归：206 passed，1 failed；失败固定依赖本机不存在的 `SpacetimeGaussians/packs/stg-init@0.3.2/manifest.yaml`（FileNotFoundError），见 `regression.txt`。未修改 STG/测试夹具。最后新增 bool-version 错误测试后，契约 + 计划测试 33 passed。
- 核心针对性回归 143 passed 的运行在扩大范围前完成；随后补充 fan-out 计划测试，准备/配方/生命周期子集 42 passed。
- 未 push、未发布、未重启、未改前端、未修改其它 pack 或已有 workflow；未使用 GPU。
- **上线待办**：需要用户安排 gateway 加载新代码，并确认 node 已重扫两个 provider，随后在线验证 prepare、provider handle 注册和真实整图派发。本轮未重启，当前在线 gateway 未加载这些新能力；没有证据表明仅执行这两个普通 provider 必须重启 node。
- **尚缺代码**：画布本地连线 eligibility/UI 呈现（本轮禁止改前端）；涉及转换的单节点 Continue/Fork，目前明确返回 422 指引整图运行，不伪装支持。其它无转换祖先的分支不被该限制拦截。
- **后续范围**：三个组件类型的时间视图、多观测模型、空选择和编辑元素恢复，均未纳入首个完整模型 provider。

结论：真实数据与后端整图计划的首个闭环已实现；不能据此宣布“在线画布已可直接使用所有数组操作”。
