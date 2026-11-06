# Colmap4D 读取库化与正式代理调查

2026-11-06。未重启生产服务、未 push、未改其它 pack。两仓独立提交；根仓已有子模块改动未混入提交。

## 实现边界

格式知识已移入独立 `packages/colmap4d` 仓库的 `colmap4d.preview`，包版本升至 **1.0.5**（库提交 `a8a5139`）。输入均为路径和读取参数，返回标准 Python 数据或 bytes 文件集，没有 HTTP / HoloLab / NumPy / pycolmap 依赖。现有 API、格式规范和旧消费者不变。该包应发一个兼容的补丁版本以供节点安装；本轮没有打 tag、发布或 push，也没有改运行环境中的安装包。

| 纯库 API | 行为 |
|---|---|
| `inspect_components(path)` | 只 stat，分别统计位姿表、图片目录、点几何、点时间体积；目录不可可靠计量时返回未知 |
| `read_camera_page(path, offset=0, limit=...)` | 位姿及对应时间，BIN 观测通过 seek 跳过；从不读取点云/像素正文 |
| `read_point_page(path, offset=0, limit=...)` | 源记录顺序分页，只解码返回的几何，不解析 track |
| `sample_points(path, budget=N)` | 均匀选取至多 N 个源记录秩；包含首尾，N=1 选中间；保留 ID 与整数 ns |
| `preview_files(path, part=..., budget=..., offset=..., limit=...)` | 按组成部分生成有界、普通 COLMAP 文件集，供已有 viewer 消费；不是重建导出 |

点时间只为选中 ID 建映射，流式扫描时间表、按 ID 关联，乱序和重复时间记录采用 last-wins；缺失时间仍表示无时间边界。BIN 的变长 track 决定了深分页仍需 O(offset + limit) 个前置记录头、抽样需 O(源记录数) 个记录头；**没有宣称常数时间随机访问，也没有全量解码所有几何或 track**。TXT 使用流式计数与选择两遍读取。预览读取不替代全模型格式验收。

单向依赖为：`浏览器 → 正式 gateway /proxy → 节点 HTTP 适配器 → colmap4d.preview`。库不引用平台。HoloLab 新增的 `node/format_preview.py` 仅负责路径权限、参数/并发限制、调库、将 bytes 编码为 JSON/base64；原文件代理继续传输像素图片。前端只接收 Blob/元数据、显示按钮并调用 ColmapUtil。浏览器 BIN/TXT 解析、时间表扫描、抽样代码和 Worker 已删除。`handle_summary.py`、`tag_probes.py` 没有改动。

## 传输接口与上线状态

新节点入口：

```text
GET /proxy/{node_id}/_format/colmap4d/{handle-relative-path}?part=metadata
GET /proxy/{node_id}/_format/colmap4d/{handle-relative-path}?part=cameras&offset=0&limit=128
GET /proxy/{node_id}/_format/colmap4d/{handle-relative-path}?part=points&budget=200000
GET /proxy/{node_id}/_format/colmap4d/{handle-relative-path}?part=points&offset=1000&limit=1024
```

该入口本地对应节点 `/_format/colmap4d/...`，走现有反向隧道，不直连 8829。参数限制为最多 20 万点/页、10 万相机/页，并保留原工作区边界。`/_format/` 需要节点隧道白名单配套更新。库缺失返回明确 503；HoloLab 声明可选 extra `preview-colmap4d = ["colmap4d>=1.0.5,<2"]`，没有把格式解析加到平台核心依赖中。

部署分为两步，避免前端提前调用尚不存在的新后端：

- 已提交并部署 URL 修正版 `2396e77`；生产 HTML 当前引用 `index-CZawuZRe.js`，仍用原有接口。
- 库化后端、配套前端以及隧道小修复只提交，等待用户统一更新。候选前端产物在 `/tmp/hololab-colmap4d-library-dist`，当前候选入口为 `index-C7ttBWa4.js`。
- 用户安排更新前，需要在节点**文件服务所用的 Python 环境**安装本地 colmap4d 1.0.5（或发布后安装对应版本/extra），更新 gateway/node 后端，再部署配套前端。当前 `.venv-runtime` 尚未安装该新库；本轮测试通过 PYTHONPATH 指向独立库源码，不改变运行服务的环境。

## 502 与路径校验

`node/file_tunnel.py:37` 的 `_allowed_path()` 拒绝以下路径：不以 `/` 开头、带反斜杠、路径中存在空段/`.`/`..`，以及未列入白名单的 `/_...` 路由。`:135–139` 在方法不是 GET/HEAD、path 不是字符串或校验失败时发送 `error: forbidden request`。gateway `proxy.py:310–320` 将这个隧道错误包装为 502，所以 502 本身不能说明是文件读取失败。

真实 handle 的权威地址为：

```text
/proxy/3038e635-4840-46dd-aa0c-6bd9c477b852/w/9967b1f8-3646-41f5-a269-394b6d392169/j/5eba9322-2842-46ff-8e61-b0c8f63cb760/colmap4d
```

正确的旧预览请求例如在其后追加 `/sparse/0/cameras.bin`；不能追加 `//sparse/...`，也不能把 `/cloud/.../output/hololab` 拼进请求。实测正确请求返回 206，故意加入重复斜杠则返回 `502 ... forbidden request`，见 [路径检查记录](proxy-path-checks.json)。

**前端原本已经使用 `target.proxy_url`**（`NodePreviews.tsx:322`），没有查到真实 colmap4d 请求改用了绝对文件系统路径。修正是统一通过 `colmap4dUrl()` 拼接，消除空段和 `.` member，拒绝父目录；不能把这一防御性修正说成已经证明了所有历史 502 的唯一根因。

当前正式生产页面还复现了其它失败：[浏览器完整记录](deployed/evidence.json)、[正式页面截图](deployed/workflow.png)。这轮直接打开 `http://127.0.0.1:8828/w/9967b1f8-3646-41f5-a269-394b6d392169`，没有替换页面/API，没有直连文件服务。真实 colmap4d 的 images.bin 请求 URL 与 handle 前缀一致且没有重复斜杠，仍出现 502；同页面的图片请求记录到 `Cannot call "send" once a close message has been sent`。另一个只使用标准 URL 的连续 Range 脚本第 5 次短读：应读 100 B，收到 38 B。因此当前进程的正式链路**仍未验收通过**。

进一步用确定性回归测试确认并提交了两处小修复（`da98399`）：

1. `gateway/tunnel.py:59`：响应头前发生 `forbidden request` 等错误时没有 body 消费者，却把 EOF 留进共享 body queue，下一请求可能读到它而提前结束；改成只使响应 Future 失败，不向队列加入该 EOF。
2. `gateway/tunnel.py:172`：release 在锁外检查 closed，等待锁期间断开/移除的连接仍可能重新入池；改为在持锁后检查。

两项新增测试在旧代码上都失败、修复后通过。这证明了可复现的独立机制，**不宣称它们解释了全部线上错误或已在运行进程生效**。未借此重启/重连生产服务。正式验收还需要上述库、节点入口/白名单、gateway 小修复经用户统一更新后，在同一 `/proxy` 路径复测；本轮不使用任何 8829 直连截图作为验收。

## 真实大数据与验证

输入仍为 `5eba9322-2842-46ff-8e61-b0c8f63cb760/colmap4d`。库内读取的 [测量记录](library-real-data.json)：

- 图片 132,098,827 B；点几何 2,477,433,608 B；点时间 671,846,408 B。
- 相机读取约 1.3 ms，81 个位姿，生成 9,320 B 文件集；跳过约 1 GB 的 observations。
- 41,990,400 点在 200,000 点预算内均匀采样，实际输出 200,000 点；约 22.2 秒，独立 Python 进程峰值 RSS 179,112 KiB（约 175 MiB）。无 GPU 使用。
- 点文件集 13,400,016 B；当前 HTTP JSON/base64 线传体积约为其 4/3，不能把文件集大小当作精确网络字节数。

库：`ruff check .`、`ruff format --check .`、全量 pytest 通过（95 passed，7 skipped）。HoloLab：相关 Python 测试 64 passed，包含路径权限、真实库调用、缺依赖、预算/分页限制、原文件服务与隧道回归。前端 8 项相关测试、TypeScript 和候选生产 build 通过。没有修改 stg-init 或任何其它 pack。

## 代码位置

| 文件 | 内容 |
|---|---|
| `packages/colmap4d/src/colmap4d/preview.py:98` | 组成体积 stat |
| `.../preview.py:247` | 相机分页 |
| `.../preview.py:364` / `:375` | 点分页、均匀抽样 |
| `.../preview.py:433` | 组成部分文件集 |
| `packages/colmap4d/conformance/test_preview.py` | 新 API、按需隔离、精度、截断与抽样测试 |
| `hololab/hololab/node/format_preview.py:16` | 薄 HTTP 适配器 |
| `hololab/hololab/node/fileserver.py:371` | 挂载适配器 |
| `hololab/hololab/node/file_tunnel.py:37` | 只读路径校验及新入口白名单 |
| `hololab/hololab/gateway/tunnel.py:59` / `:172` | 队列 EOF 隔离、归池竞态修复 |
| `hololab/hololab/frontend/src/canvas/colmap4dPayload.ts:21` | 权威 proxy_url → 传输入口；没有格式解码 |
| `hololab/hololab/frontend/src/canvas/Colmap4DPreview.tsx` | 分级 UI、缓存 Blob、图片传输与 viewer 生命周期 |
| `hololab/hololab/frontend/src/canvas/colmap4dUrls.ts:2` | 统一 URL 拼接 |
