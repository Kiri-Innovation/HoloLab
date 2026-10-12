# 输入端口的轴名匹配策略

## 字段与范围

```yaml
inputs:
  colmap_frames:
    tags: [colmap-folder]
    arrayed: true
    dim_labels: [frame]
    require_axis_label_match: false
```

- `InputSpec.require_axis_label_match` 是严格布尔值，默认 `true`。不声明字段时行为不变：双方轴名非空且不同才拒绝，任一侧无名仍是名称通配。
- `false` 只关闭该输入所有集合层的轴名比较。数组/标量、集合层数、元素 tags 保持现有规则（包括显式 `any` 泛型）；不改路径、数据、维度名、遍历顺序或调度。
- catalog 输入声明与 `port_types.inputs.*.declaration` 均携带字段。后端快照/运行校验与前端拖拽及最终 onConnect 使用同一输入策略。
- 类型字符串仍只用于展示。提示区分 tags、数组/标量、层数、轴名四种原因。

## 影响评估（实施前只读扫描）

扫描本机配置的 pack 根目录：59 个 pack、79 个输入。新增字段默认严格，**0 个既有 pack 因默认值改变而放宽**；本次只为 STG 0.3.0 的 `colmap_frames` 显式放宽。

读取本机 workflow 草稿数据库，52 条可解析的已保存算法连线中，0 条仅因轴名不匹配被拒绝。另有 15 条因旧 pack/端口不可用跳过，不能对这些边下结论；视图边不纳入类型统计。被拒绝的拖拽尝试不会保存，无法追溯统计其数量。

已确认新增可连边：工作流 `272b31c3-e2ca-4fd5-a8c8-03a38a97711d` 的 `merge_g0.folder → nmv4xtft71.colmap_frames`：

```text
实际：arrayed[group]<colmap-folder>
预期：arrayed[frame]<colmap-folder>
```

STG 的执行脚本按 element ID 排序读取一批 folder，未使用轴标签决定计算，因此无需强制名称相等。放宽也会允许其它同 tags、同层数但名称不同的集合流入这个输入；它不证明时序/业务语义正确，数据顺序仍由使用者和 pack 开发者负责。

STG pack 实际位于独立仓库 `SpacetimeGaussians/stg-train@0.3.0/manifest.yaml`，单独提交；其它版本及 HoloLab 的 packs 均未修改。

## 验收方法

- 后端读取**在线数据库的草稿副本 + 磁盘真实 manifest**，只在内存中添加候选边：放宽后该边校验错误为 `[]`；强制回到严格模式，则报告 group/frame 不匹配。未修改线上图、未触发训练。
- 当前 `image-undistort@0.5.2.images` 仍默认严格，给它错误轴名的同类型同层数图片集合会被拒绝。
- 浏览器回放已记录的真实相关图结构与 pack 声明；新增的 `strict` 输入只用于测试对照，不存在于真实 STG manifest。STG 真实输入绿勾并成功落线；严格对照红叉且失败松手不添加边。
- 50% / 100% / 150% 缩放、取消拖线、反向拖线、Escape、普通 tooltip、连接点几何纳入浏览器测试。新增字段不会改变端口 DOM 或锚点。

## 实现入口

- `hololab/manifest/schema.py`：输入声明及布尔校验。
- `hololab/gateway/port_type_ir.py`：递归结构兼容性及版本化声明字段。
- `hololab/gateway/workflows.py`：catalog 适配、图校验及 API IR 投影。
- `hololab/gateway/registry.py`：pack catalog 转发。
- `hololab/frontend/src/wire.ts`、`types/portTypeIR.ts`：前端契约类型。
- `hololab/frontend/src/tags.ts`、`canvas/connectionValidation.ts`：前端判定及明确原因；App 与拖拽反馈已有共享入口，无需另写判定。
- `tests/test_axis_match_policy.py`、`connectionValidation.test.ts`、`e2e/connection-feedback.spec.ts`：结构矩阵、真实案例、manifest → catalog → HTTP graph 的字段链路及交互回归。
- `docs/pack-spec.md`、`docs/type-ir.md`：声明策略与兼容边界。

## 测试结果

- 后端全套：1165 passed，3 skipped。跳过项分别需要真实画布 workflow ID、NumPy、已不存在的旧 fx 产物目录；NumPy 那项另用算法环境补跑，1 passed。
- 本次后端专项：68 passed，覆盖严格/放宽、无名/具名、多层/标量、tags 不匹配、默认值、真实图及 manifest/catalog/HTTP 转发。
- 前端单测：17 个文件，492 passed（共享连接判定 132 项）。
- 浏览器全套：65 passed；浅/深主题拖拽专项两项也独立通过。覆盖 tooltip、快照、普通节点、边选择、拖线、几何和缩放回归。
- `npm run build`：vendor 与 dist 重建，保留既有 bundle 大小提示。dist 是仓库忽略的生成目录。
- Ruff 与 `git diff --check` 通过。

## 截图

已自行查看浅/深两主题：目标端口处绿勾清楚；严格对照显示红叉、轴名原因、预期和实际类型；尖角仍对准目标。

- [浅色：真实 STG 输入绿勾](/tmp/axis-match-screenshots/light-drag-valid.png)
- [深色：真实 STG 输入绿勾](/tmp/axis-match-screenshots/dark-drag-valid.png)
- [浅色：严格端口拒绝](/tmp/axis-match-screenshots/light-drag-invalid.png)
- [深色：严格端口拒绝](/tmp/axis-match-screenshots/dark-drag-invalid.png)

本机截图与 `/tmp/axis-match-acceptance.json`、`/tmp/axis-match-impact.json` 是临时验收证据，不纳入 git。

## 部署

未 push、未重启服务。部署需同时包含 HoloLab 的字段支持与 STG manifest 的显式放宽；之后由用户重启/刷新 gateway 与执行节点的 pack catalog，并刷新浏览器。旧服务没有新字段支持时不会启用放宽。不修改任何旧 API 字段的语义。

## 字段定名回归

字段统一为用户指定的 `require_axis_label_match`，前端函数参数同步为
`requireAxisLabelMatch`。默认值和所有校验行为不变，不保留旧字段别名。
本轮相关后端测试 250 项、前端单测 492 项、浅/深主题拖拽浏览器测试
2 项通过；实际草稿副本与磁盘 manifest 的候选边验证仍通过。
浏览器重验绿勾及成功落线、严格端口拒绝、取消、缩放和普通 tooltip。
`npm run build` 重打 dist；源码、文档、测试、STG manifest 和新 dist
均检查字段定名，无旧名残留（不包括 git 历史与缓存）。
新截图：[/tmp/axis-label-rename-screenshots/](/tmp/axis-label-rename-screenshots/)。
