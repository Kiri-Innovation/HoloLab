# HEIC 旋转修复验证

`builtin/images-array-source@0.1.1` 在 `to_pillow()` 前读取原始 EXIF
orientation，把方向烘进 RGB/RGBA 像素，并移除输出 EXIF orientation。
本文记录此前的旋转验证；实现现已迁移至 builtin，community 源码已移除。
本目录的原始日志与图片保留为历史证据；最新迁移验证见 ../heic-builtin-20261103/。
既有节点需显式切换到 0.1.1 才能获得修复。

## 全量结果

- 252/252 张成功转出，均为真实 PNG。
- 尺寸统计：1440×1920 = 252 张，其他尺寸 = 0 张。
- 252/252 张与独立手动旋转参考逐像素相等，模式一致。
- 252/252 张无 EXIF orientation；文件序列顺序保持一致。
- 汇总：`summary.json`；完整逐图证据：`pixel-evidence.json`。

## 方法与产物

使用 CPU、kiri Python 3.12.12、Pillow 11.3.0、pillow-heif 1.3.0。
输入为 `/cloud/cloud-ssd1/Kiri4DGS/data/persona_bench/beach_sunglasses_hat_sitting/input/image`。
用户已确认这批 252 张均为 EXIF 6，容器无 irot/imir。

运行最终版节点，将全部图片写入 `/tmp/hololab-heic-rotation/frames/`。
独立验证脚本重新解码每张 HEIC，手动执行 `ROTATE_270`，与 PNG 的
`tobytes()` 精确比较，同时检查尺寸、模式、无方向标签、真实 PNG 文件及顺序。
复现转换命令：

```bash
/cloud/cloud-ssd1/envs/kiri/bin/python hololab/builtin_packs/images-array-source@0.1.1/run.py \
  --frames-path /cloud/cloud-ssd1/Kiri4DGS/data/persona_bench/beach_sunglasses_hat_sitting/input/image \
  --output /tmp/hololab-heic-rotation/frames > /tmp/hololab-heic-rotation/conversion.log
/cloud/cloud-ssd1/envs/kiri/bin/python reports/heic-rotation-20261103/verify.py
```

逐张像素 SHA-256 见 `pixel-evidence.json`；日志见 `conversion.log`、
`verification.log`、`tests.log`。输出 PNG 留在临时目录，不纳入仓库。

`comparison.png` 展示 `305186217781234.heic` 的原始解码像素和最终 PNG。
已打开目视确认：左图人像横向；右图人物正常坐姿，海平线水平。

## 方向映射与边界

| EXIF | Pillow 操作 |
| --- | --- |
| 1 | 不变 |
| 2 | FLIP_LEFT_RIGHT |
| 3 | ROTATE_180 |
| 4 | FLIP_TOP_BOTTOM |
| 5 | TRANSPOSE |
| 6 | ROTATE_270（顺时针 90°） |
| 7 | TRANSVERSE |
| 8 | ROTATE_90（逆时针 90°） |

所有通道一起转动，无插值、缩放或颜色转换。无 EXIF/方向 1 保持像素不变；
非法方向值保持不变并输出警告。其他 Pillow 支持的 EXIF 字段保留。
非 HEIC 分支未改动，继续保留名称和绝对软链，不重新编码。

## 回归检查

79 项测试通过，覆盖本节点、manifest、utility arrayed packs、resource preflight。
8 种方向用非对称、非正方形 RGB/RGBA 图案按独立坐标期望验证，
模拟解码器在 `to_pillow()` 时清掉方向标签，确保读取时机正确。
另外覆盖无 EXIF、非法方向、orientation=1、真实 HEIC/RGBA 解码、
非 HEIC 与 builtin 软链契约一致、混合输入、名称冲突和序列顺序。
Ruff、manifest 校验、git diff --check 通过。

## 范围和限制

未发布、未改已安装包或工作流、未重启 HoloLab、未使用 GPU、未改源图。
未运行线上工作流，未测试其他场景，以及同时含容器 irot/imir 和 EXIF
旋转的真实样本。HDR >8 位仍按已有行为拒绝，辅助图及序列仍不导出。
