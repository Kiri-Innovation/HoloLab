# Colmap4D 抖动抽样与真实数据视觉对比

2026-11-06。库修复提交 `314ca21`。本轮仅修改库内抽样与验证材料。未重启服务、未 build/部署前端、未 push、未发版；上一轮库化及上线工作保持原状态。

## 先确认旧实现

`a8a5139` 的 `sample_points()` 不是随机算法，而是固定秩：`floor(i * (total - 1) / (take - 1))`。它与更早前端的 `i % ceil(total / budget) == 0` 不同，但仍有规则间隔。本轮对同一真实模型重放两种旧策略：前端版本形成竖条，a8a5139 版本形成斜条，均可在图中直接看到。

## 新算法：分层抖动，固定默认种子

采用用户选项 (b)，设 `K = min(total, budget)`：

```python
rng = random.Random(seed)  # 每次调用独立创建；默认 seed=0
indices = [
    rng.randrange(i * total // K, (i + 1) * total // K)
    for i in range(K)
]
```

空数据和不超过预算的数据走保留全部分支，不进行上述除法/随机选择。各半开区间不相交且至少含一个记录，区间宽度只差 0 或 1；每段随机选一条，因此**返回数量精确为 K、下标不重复、严格保持源顺序**。没有独立概率抽样的数量波动，也不需要额外截断或蓄水池来兜上界。不再强制选首尾；N=1 也在整个源区间内随机选择。

选择分层抖动是为了同时打散空间相关顺序的固定步长、保留整个源序列覆盖和精确预算。它不是全局独立同概率 Bernoulli 抽样：当总数不能被 K 整除时，不同宽度区间内的单点概率略有差别。原始 XYZ/RGB、ID、ns、时间关联、分页及跳过 track 的读取方式不变。

新公开参数：`sample_points(path, budget=..., seed=0)` 和 `preview_files(path, part="points", budget=..., seed=0)`。默认种子 0 适合快照/回归/前后比较，显式传入其它整数可重新取样；不读系统熵、不改变 Python 全局 RNG 状态。相同输入顺序、预算、种子及 Python RNG 实现可复现。未承诺不同 Python 版本的 randrange 实现永远产生相同选择，这也不是 colmap4d 文件协议的一部分。

## 视觉验收（同一真实 4 GB 模型）

源数据始终为 `output/hololab/w/9967b1f8-3646-41f5-a269-394b6d392169/j/5eba9322-2842-46ff-8e61-b0c8f63cb760/colmap4d`，共 41,990,400 点。三种算法预算都是 200,000；旧前端因 ceil stride 实际保留 199,955 点，两版库实际都保留 200,000 点。

- **[直接前后对比：旧前端竖条 → 新抖动自然稀疏](before-after.png)**
- **[三种算法同屏：旧前端 / a8a5139 旧库 / 新抖动](projection-comparison.png)**
- [ColmapUtil 实际 WebGL 渲染前后对比](viewer-before-after.png)

投影图使用选中点的真实 XYZ，经同一个实际 COLMAP 第一相机的 R/t 和内参投影，显示 t=0 的点。相机、时间片、画布、点大小及颜色处理在每张图的各面板间相同，没有插值、补点、密度修复或图片纹理。特意显示单个时间片，使跨帧叠加不会遮住规律性缺口；这不是合成点云或仅画源下标。先把全部 81 帧抽样，再显示第一帧。

我已逐张检查图像：固定取模的竖条和固定秩的斜条在新抖动面板中消失，呈现自然稀疏分布。半分辨率列占用作为辅助诊断，旧前端为 32/960，新抖动为 888/960。a8a5139 虽占满 960 列，却仍有明显斜纹，说明**不能只凭占列数或总点数验收**。

ColmapUtil 图在同一测试页面内同时渲染两个真实子集，视角、t=0、点大小 3 相同；实际绘制分别为 2,469 / 2,470 点实例，0 脚本错误、0 资源错误。浏览器使用 SwiftShader 软件渲染，没有占用 A800。样本在本机通过库读取后直接作为 Blob 输入现有 viewer；**这是离线算法视觉验收，不是上一轮正式 HTTP 通路上线验收**。没有直连 8829，也没有改动/重启生产后端。

## 确定性与验证证据

对真实大模型完整运行两次新算法（不是仅重置下标生成器），输出点文件和时间文件逐字节一致：

| 文件 | 两次均相同的 SHA-256 |
|---|---|
| points3D.bin | `dc8d34330e1bd4f64031b0a20ebe6df4920a2589325b0482020bc2e859db1d8c` |
| points_t.bin | `6ebc32b050b8fca839493dd51c66f0ce68b4b94c2bda6e607392aebc03866c65` |

第一次约 22.6 秒，第二次约 22.5 秒，均精确保留 200,000 点。脚本以 `nice -n 10` 运行，读取/绘图在 CPU 上完成。[完整数字与源文件 stat](evidence.json)、[WebGL 记录](viewer-evidence.json)。

库完整检查通过：`ruff check .`、`ruff format --check .`、pytest **100 passed / 7 skipped**。新增回归覆盖规则栅格的固定步长退化、精确数量/无重复、相同与不同种子、全局 RNG 不变、seed 传递和非法种子；原有 ID/ns、分页、截断等测试继续通过。尾部截断测试请求全部点，避免把“随机样本必须恰好选中坏尾行”误当验证契约；预览依然不替代全模型验收。

按库 AGENTS.md 的代码版本规则，将 `pyproject.toml`、`__version__`、`CITATION.cff` 同步到 **1.0.6**；没有打 tag 或发布，留给用户统一上线与发版。HoloLab 生产代码和上一轮依赖声明未改动。

## 代码和复现

- `packages/colmap4d/src/colmap4d/preview.py:376`：分层抖动和默认种子；`preview_files()` 传递 seed。
- `packages/colmap4d/conformance/test_preview.py`：抽样与确定性回归。
- [对比生成脚本](compare_sampling.py)：使用库的读取/编码能力重放两种旧下标策略，所有大样本表写入 `/tmp`，不提交数据副本。
- `hololab/frontend/scripts/smoke-colmap4d-jitter.mjs`：现有 ColmapUtil 的离线双画面验证。

从 Kiri4DGS 根目录，使用已有 NumPy/Matplotlib 的 Python：

```bash
PYTHONPATH=packages/colmap4d/src nice -n 10 /cloud/cloud-ssd1/envs/kiri/bin/python \
  hololab/reports/colmap4d-jitter-20261106/compare_sampling.py \
  output/hololab/w/9967b1f8-3646-41f5-a269-394b6d392169/j/5eba9322-2842-46ff-8e61-b0c8f63cb760/colmap4d \
  hololab/reports/colmap4d-jitter-20261106 /tmp/colmap4d-jitter-20261106
```

然后在 `hololab/hololab/frontend` 运行 `node scripts/smoke-colmap4d-jitter.mjs`。它只读取生产 ColmapUtil 静态资源，测试页面在隔离浏览器内拦截，不写入网站或工作流。
