# 环境契约与本地能力检查

`runtime.env` 引用节点 config.yaml 的 envs 逻辑名。可选 requires 声明全部是硬要求：

```yaml
requires:
  python: ">=3.10,<3.13" # 精确版本也可写 "3.10.14"
  packages: ["mmcv>=2.0", diff_gaussian_rasterization_ch3]
  cuda: "12.1" # 驱动至少支持 CUDA 12.1，不代表已安装 toolkit
  gpu: {vram_gb: 24, arch: [sm86, sm89]}
```

packages 使用发行包元数据名称，非 import 模块名，不接受 URL、extras 或条件 marker。
优先读取 Python distribution 元数据，其次读取 conda-meta 的名称/版本，记录各项来源；
版本约束采用 PEP 440，不自动推断 conda 包名与 Python distribution 名的别名。
gpu 声明要求至少一张可见 GPU 同时满足总显存（GiB）和架构，不保证当前空闲显存。
没有 requires 的包显示未声明，保持原有运行行为；不会因本功能新增拦截。
扫描只读取声明，不启动环境、不 import 包。检查不自动安装任何内容。

本地能力检查只覆盖已声明且在可探测范围内的项；通过不代表一定能运行。
不验证第三方 import、editable 源码存在/匹配、原生扩展 ABI、.so 与源码一致性、
运行期环境变化或完整依赖闭包。没有自动 import 或额外 import 白名单。
实测环境不进入代码身份；requires 契约本身仍进入 manifest 语义身份。
环境检查结果只是时点证据，环境未冻结，也未被代码身份覆盖。

## 时机、缓存与证据

节点 inventory 携带 requires，扫描时不探测。打开工作流、切换包/执行节点、节点重连、
窗口重新获得焦点或点击“重新检查”会查询目标节点；图卡移动与参数编辑不触发探测。
API 为 `POST /api/capabilities/check`，body 为 `{graph, fresh?: boolean}`，无提交/执行副作用。
节点卡片和工作流面板都显示未声明/缺失/无法确认/已声明的可探测项满足。

Python distribution 元数据由目标环境的 Python 查询；已有激活环境缓存时直接使用，
否则经配置的 conda run。不会显式 import 第三方包；Python 启动本身仍可能执行 .pth/sitecustomize，
此检查不是沙箱。单次子进程限时 20 秒；探测异常按无法确认处理，硬要求不能放行。
GPU 使用资源透镜的 nvidia-smi 查询函数，实时读取型号、显存、架构、驱动与驱动支持的 CUDA。
遵循目标环境 CUDA_VISIBLE_DEVICES 的索引/完整 UUID；不支持的设备选择（如 MIG）保守无法满足，
不宣称掌握实际调度/容器内的全部设备可用性。

环境缓存最多 64 项，按 prefix、conda 路径、激活变量区分。每次查询 stat 环境 Python、
site-packages、distribution 元数据、.pth/.egg-link、conda-meta（含 history）；
目录/元数据的 inode、mtime、ctime、size 改变即重探。首次探测发现额外搜索路径时再读一次，
防止把安装过程中混合读取的数据缓存为新状态。GPU 不使用此缓存。
`fresh=true` 绕过元数据缓存；准备与进程启动前均使用 fresh，不安装任何软件。
正常 pip/conda 安装完成后下一次查询自动失效，也可点“重新检查”；不监控整个源码/二进制树。
原有节点激活变量缓存独立存在：修改 activate.d/PATH 等仍须操作者刷新原激活配置或重启节点，
“重新检查”不承诺重新激活正在运行的节点环境。

准备失败消息给出图节点/包、缺失项、要求、当前值、补救方式；不发 JobAssign。
节点在等待执行槽结束后、算法进程启动前再次检查。声明了 requires 的任务不能降级给旧节点。
无 requires 时不新增探测或拦截，原先 runtime.env 映射/执行失败行为保持原样。

提交证据写在 `code_identity.environment`：status、checked_at、env/prefix、requires、
Python 版本、声明包实测版本和元数据来源、hardware、issues、environment_fingerprint、
cache_hit，以及 environment_covered=false、runtime_verified=false、边界文案。
这些字段不参与 code_identity.sha256；检查期间 manifest 变化则拒绝提交。
已提交快照和 job 不随重新检查刷新；重试仍使用原身份，但启动前重新检查当前能力。
历史无此字段显示“环境未校验”，不回填。

## 真实样例与验收

仅补三份样例，未修改有其他任务编辑的 stg-train@0.3.0：

| pack | 声明的包 | GPU 总显存 |
| --- | --- | --- |
| stg-init@0.1.1、stg-train@0.4.6 | torch、simple_knn、diff_gaussian_rasterization_ch3、holoviewer | 12 GiB |
| fastgs-pano-train@0.1.0 | torch、simple_knn、diff_gaussian_rasterization_fastgs | 20 GiB |

显存下限沿用原 runtime.gpu.vram_gb_min；不猜最低 Python/包/CUDA 版本。
依赖证据：STG helper_train.py:39–42、65–66，Utils/STG_Viewer/training_viewer.py:39；
FastGS train.py:12、scene/gaussian_model.py:20、gaussian_renderer/__init__.py:16。
这不是完整依赖声明，其他未声明项不在检查保证内。

2026-10-23 实测：kiri Python 3.12.12，fastgs Python 3.10.21；两者 torch 2.8.0+cu128、
simple_knn 0.0.0，对应 rasterizer 0.0.0；kiri holoviewer 0.1.0。
RTX 4090 总显存 23.98828125 GiB、sm89、驱动 580.105.08、驱动支持 CUDA 13.0。
三份样例均为 satisfied（仅声明项满足）。首次 STG/FastGS conda 元数据探测约 5.73/6.03 秒，
同环境第二个 STG 包缓存命中约 0.23 秒；包括 GPU 查询，不是性能承诺。

测试在临时目录/数据库中进行：缺失包出现在工作流 API 清单，准备失败且无 JobAssign，
绕过准备直接交给节点也不启动算法进程；满足契约的临时包真实执行并发出 job_done。
临时 venv 增加 dist-info 后缓存自动失效，包文件内故意抛异常也不被 import；
conda-only 元数据同样被读取。旧包不探测环境，旧节点不能接带契约的任务。
前端测试核对缺失清单、可探测边界、旧证据和隐式 algorithm 节点不会被漏掉。

修改样例 manifest 会改变 manifest 字节与代码身份，已有带身份运行需重新提交/Fork。
本次不安装环境、不重启服务、不 push。由操作者重启 gateway/node 后打开工作流，
展开“本地能力检查”；运行详情/快照证据可查看提交时结果和“环境未冻结”的边界。

验证命令（HoloLab 仓）：

```bash
python -m pytest tests/test_requirements_schema.py tests/test_capabilities.py tests/test_environment_execution.py -q
cd hololab/frontend
npm test -- --run src/canvas/EnvironmentChecks.test.tsx src/canvas/CodeIdentityPanel.test.tsx
npm run build
```

本轮全量后端 1,547 passed / 5 skipped；补充提交证据回归后定向 16 passed；
前端展示 5 passed，前端 build 成功。未在生产环境装包，安装失效测试使用临时 venv 元数据。
另做真实 pip 安装验收：在临时 venv 的 site-packages 安装本地测试 wheel
（--no-index --no-deps --no-cache-dir --no-compile --target，无网络）；结果 missing →
第二次 cache_hit=true → 安装后 satisfied、cache_hit=false、检出版本 1.0。
测试模块内故意抛异常，检查没有 import；临时目录验收后自动清理。
样例跨仓提交：STG `58a3708`，FastGS `45f8916`；父仓子模块指针未提交。
