# 环境契约与本地能力检查

`runtime.env` 引用节点 config.yaml 的 envs 逻辑名。可选 requires 声明全部是硬要求：

```yaml
requires:
  python: ">=3.10,<3.13" # 精确版本也可写 "3.10.14"
  packages: ["mmcv>=2.0", diff_gaussian_rasterization_ch3]
  cuda: "12.1" # 驱动至少支持 CUDA 12.1，不代表已安装 toolkit
  gpu: {vram_gb: 24, arch: [sm86, sm89]}
```

packages 使用 Python distribution 元数据名称，非 import 模块名，不接受 URL、extras 或条件 marker。
gpu 声明要求至少一张可见 GPU 同时满足总显存（GiB）和架构，不保证当前空闲显存。
没有 requires 的包显示未声明，保持原有运行行为；不会因本功能新增拦截。
扫描只读取声明，不启动环境、不 import 包。检查不自动安装任何内容。

本地能力检查只覆盖已声明且在可探测范围内的项；通过不代表一定能运行。
不验证第三方 import、editable 源码存在/匹配、原生扩展 ABI、.so 与源码一致性、
运行期环境变化或完整依赖闭包。没有自动 import 或额外 import 白名单。
实测环境不进入代码身份；requires 契约本身仍进入 manifest 语义身份。
环境检查结果只是时点证据，环境未冻结，也未被代码身份覆盖。
