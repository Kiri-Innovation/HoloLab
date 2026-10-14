# ruff: noqa: RUF001 -- Chinese report prose and mathematical symbols are intentional.
"""Build a reviewable HTML report from measured JSON artifacts."""

import html
import json

from run_experiment import OUT

metrics = json.loads((OUT / "comparison.json").read_text())
audit = json.loads((OUT / "reconnaissance.json").read_text())
old = metrics["old_static"]
frozen = metrics["frozen_t050"]
control = metrics["static30k"]
reduction = 100 * (1 - frozen["head_outer_alpha"] / old["head_outer_alpha"])
cols = {
    "own_head_soft_width_px": "自身头部轮廓软带等效宽度 ↓ (px)",
    "own_head_outer_opacity_px": "自身头部外围透明残留积分/周长 ↓ (px)",
    "foreground_psnr": "人物 PSNR ↑ (dB)",
    "head_psnr": "头部内部 PSNR ↑ (dB)",
    "background_alpha": "背景平均 α ↓",
    "head_outer_alpha": "头部外围 α ↓",
    "head_band_soft_fraction": "头部边界软带比例 ↓",
    "head_laplacian_variance": "头部 Laplacian 方差（辅助）",
    "silhouette_iou": "轮廓 IoU ↑",
}
rows = "".join(
    "<tr><th>"
    + html.escape(label)
    + "</th>"
    + "".join(f"<td>{metrics[n][key]:.6f}</td>" for n in metrics)
    + "</tr>"
    for key, label in cols.items()
)
text = f"""<!doctype html><html lang="zh"><meta charset="utf-8"><title>Steven 人物 4D 冻结实验</title><style>body{{font:16px/1.65 system-ui;max-width:1250px;margin:32px auto;padding:0 18px;color:#203040}}img{{width:100%}}table{{border-collapse:collapse;width:100%;font-size:14px}}td,th{{border:1px solid #bbc;padding:9px}}code{{background:#eef;padding:2px}}video{{width:100%}}</style>
<h1>4D 微动建模 → 冻结静态高斯：实测简报</h1>
<p><strong>{html.escape((OUT / "conclusion.txt").read_text())}</strong></p>
<p>所有训练、导出和验收仅使用原始帧路径；没有运行 Difix 或任何图像修复。固定 t=0.5 在看结果之前确定。以下三栏图片均为实际高斯渲染，不含输入图。</p>
<h2>实现与能力侦察</h2>
<p>FastGS-Pano 当前 hololab-integrate 分支，提交 <code>{audit["fastgs_commit"]}</code>，已具备完整的微动训练、动态渲染与冻结导出路径。另有远端跟踪分支 origin/feat/dynamic-deformable。不是仅有接口，但也不是预训练 4D 人体先验：8 层、宽 256 的 MLP 对 XYZ/time 做 Fourier 编码，预测位置、四元数、log-scale 的变化，用 delta 平方均值正则；颜色和不透明度保留 canonical 参数。没有显式时间平滑、运动跟踪监督或人体结构先验。</p>
<p><code>person_bbox.ply</code> 是可选 AABB 形变区域，本实验无 bbox，整个人物场景参与形变；<code>_front/_back/... </code> 是同帧全景裁剪后缀，不是额外物理时刻或人体脸部标记。<code>data_sources=fixed/both</code> 和 <code>images_fixed</code> 是修复数据路径，动态模式只支持 original，本次未调用。动态断点恢复尚不支持。现有 silhouette 适配器原先显式拒绝 dynamic，本次移除该限制，复用同一可微 α 输出；实测梯度见下方校验文件。</p>
<h2>输入一致性证据及限制</h2>
<p>原视频 93 张 1280×1280 帧，经现有 undistort 后训练输入为 1301×1301；这是一个相机随视频时间环绕，不存在同一时刻的同步多视角；相机与时间耦合，动态模型能吸收视角相关误差，不能把学到的运动直接解释为真实物理运动。合并 COLMAP 的 images.bin 没有有效 image-point 关联，因此没有伪造重投影统计（n=0）。首轮诊断失败日志 diagnostics.log 已保留，随后改为独立 CPU SIFT 双向匹配（ratio 0.7）和 Sampson 极线距离，单位为 1301×1301 去畸变输入像素。</p>
<p>0/4 帧的 109 个匹配：已知相机残差中位 0.733 px、P90 3.590 px；重新拟合基础矩阵后，中位 0.592 px，23.85% 仍超过 2 px。0/92 首尾的 88 个匹配则更一致：已知相机中位 0.527 px、P90 1.420 px；重新拟合后仅 1.14% 超过 2 px。这支持局部兼容性问题，但匹配错误、遮挡、相机标定误差仍可能参与，不能证明“全部由生成不一致、而非训练不足造成”。其他少匹配对统计可信度较低，不用其极端值作主结论。</p>
<p>下两张明确是输入特征证据图，不是渲染：红色表示已知相机极线残差超过 2 px，绿色反之。</p><img src="input_evidence_000_004.png"><img src="input_evidence_000_092.png">
<h2>训练与公平对照</h2>
<p>共同设置：93 帧，resolution=2，实际 650×650 训练/评估分辨率（去畸变输入为 1301×1301）、原有 person-gray-mask、masked RGB + 0.1×silhouette BCE，densify 500–12000、每 100 步、grad_abs_thresh=0.0002、max_gaussians=500000、黑色训练背景。4D 15000 步，3000 步后启用形变，原生 LR 8e-4→1.6e-6、正则权重 0.01。每 3000 步保存 canonical PLY 与 deform.pth。独立静态控制训练到 30000 步，并在 15000/20000/25000/30000 保存。原生 FastGS 在增密结束后每 3000 步继续较强剪枝，因此这个控制包含优化与剪枝，不能作为纯训练时长的因果隔离实验。</p>
<p>旧静态为上一轮交付 person_clean.ply（曾移除 136/225773 个不可见背景点），新 4D 冻结版没有额外点筛除、后期修图或 mask 裁切。所有评估相机、背景和分辨率相同。白底显示由黑底预乘渲染 RGB 加 1−α 得到；评价残差使用黑底原始渲染和原图的人物区。93 视角平均；全部为训练视角指标，不是 held-out 泛化评分。第四栏动态 matched 每视角用自己的时间，只用来诊断拟合能力，不能当作冻结结果。</p>
<table><tr><th>指标</th><th>旧静态 15k</th><th>静态 30k</th><th>4D 冻结 t=.5</th><th>动态 matched（非静态）</th></tr>{rows}</table>
<p>补测的自身轮廓指标以渲染 alpha&gt;0.5 定义模型轮廓，用其垂直范围上部 55% 为头部区域（可能包含抬手）。软带等效宽度 = 该区域 0.05&lt;alpha&lt;0.95 的像素面积 / 该区域轮廓弧长；外围透明残留积分 = alpha&lt;0.5 的 alpha 总和 / 同一弧长。这是透明残影代理指标，不是真实边缘 PSF，也不能检测完全不透明的双层几何。该指标是在初轮发现冻结姿态与 GT-mask 错位后补充，初轮指标和失败均保留。</p><p>其他指标头部定义为 mask 垂直范围上部 55%；外围 α 在头部 mask 膨胀半径 5–15 px 的环带测量；软带为头部轮廓附近 ±约 11 px 中 0.05&lt;α&lt;0.95 的像素比例。Laplacian 方差并非越大越好，噪声和重影也会使其增加。冻结模型改变姿态时，原帧 mask/像素不再是严格的同瞬间真值，残差需结合实际并排渲染解读。</p>
<h2>同相机并排渲染</h2><p>t=.5 对应 frame_000046，即下方背面视角，背面结果具有观测锚点优势，不能只挑背面宣布成功。正面和侧面仍有手部/手臂残影、变形，顶部有裁切。</p>
<img src="compare_front.png"><img src="compare_side.png"><img src="compare_back.png">
<h2>头部放大</h2><img src="head_front.png"><img src="head_side.png"><img src="head_back.png">
<h2>相邻相机中点自由视角（无真值）</h2><img src="compare_free_front.png"><img src="compare_free_side.png"><img src="compare_free_back.png">
<h2>转身视频</h2><video controls src="comparison_orbit.mp4"></video><p><a href="frozen_orbit.mp4">单独冻结模型转身视频</a> · <a href="person_frozen_t050.ply">冻结 PLY</a></p>
<h2>校验与审计</h2><p>冻结验证首次用峰值 1e-4 阈值失败；峰值约 0.000307、平均约 3.1e-8。保留 evaluate.log 和 freeze_validation_initial.json，后续同时要求峰值 &lt;1/255、平均 &lt;1e-6，以容纳 FP32 scale log/exp、rotation 归一化带来的微小差异。没有重新训练或篡改模型。</p><p><a href="freeze_validation.json">冻结/动态 t=.5 渲染一致性</a> · <a href="dynamic_silhouette_gradient.json">silhouette 到形变 MLP 的梯度</a> · <a href="comparison.json">完整均值指标</a> · <a href="input_diagnostics.json">逐对输入诊断</a> · <a href="input-sha256.json">186 个输入帧/掩码 SHA256</a> · <a href="events.jsonl">运行事件与失败</a></p>
<h2>结论</h2><div id="conclusion">{html.escape((OUT / "conclusion.txt").read_text())}</div>
</html>"""
(OUT / "index.html").write_text(text)
