# A800 fileserver Content-Length 调查与修复（2026-11-05）

本次只修改 fileserver 实现、专属回归测试和本报告。不重启、不操作监听端口、不发送进程信号、不 push。测试使用内存 ASGI 和 Uvicorn 协议对象，没有启动服务。

## 已证实的 fileserver 缺陷

修改前 `hololab/node/fileserver.py:444` 的普通产物 GET 返回 Starlette `FileResponse(target)`。本机 Starlette 1.10.0 的 `responses.py:350` 附近先 `os.stat(path)`，`set_stat_headers` 用 `st_size` 声明长度；`_handle_simple`（该文件 423–438 行）发送头后重新打开路径，以 64 KiB 块一直读到 EOF，没有总量上限。

确定性复现：写入 3 字节产物，在 `http.response.start` 送出后追加字节，再继续处理响应。旧代码直接在 Uvicorn `httptools_impl.py:548` 抛出 `RuntimeError: Response content longer than Content-Length`。同样可以用较大文件原子替换原路径，或者在大文件第一块发送后继续追加来触发。问题是 stat/open/读到 EOF 之间的竞争，不是 UTF-8 字符计数或 Range 长度计算。

实际日志 `/tmp/hololab-a800.log:249743`、`:251361`、`:334447` 等有 17 个完整异常栈明确落在 `FileResponse._handle_simple`。它们之前最近的本地 GET 全是 `state/save_cam/iter*_ssim.png`（训练预览图）200 响应。这与产物写入期间预览的复现条件相符；日志没有 request ID、请求路径或写入操作的栈内关联，不能进一步断言某一次是 append 还是 replace。

用户指出的旧 `fileserver.py:860` 属于 tar 返回路径，**不是文件大小求和**：`tarfile.open(..., mode="w")` 完整关闭后才执行 `spooled.tell()`，真实长度已包含归档头、尾部和填充。没有手动设置 Transfer-Encoding。新增测试在内存和落盘 spool 两种模式下用 Uvicorn 验证长度，均通过；无需修改这一段。

## 修复与影响

`hololab/node/fileserver.py:184` 的 `_OpenedFileResponse`：

- 先打开文件，再对同一 fd `fstat`，由此生成 Content-Length；响应期间持有该 fd，路径替换不改变当前读取对象。
- 普通 GET 每次最多读取 `min(64 KiB, remaining)`，达到 fstat 长度即结束，后续追加不进入该响应。
- 单段、多段 Range 沿用 Starlette 的解析和报文生成，通过 `_open_file` 复用同一 fd。
- 禁用会按路径重新打开文件的 ASGI pathsend 扩展；正常结束、异常和取消均关闭 fd，不吞异常。

普通大文件仍按 64 KiB 流式传输，不全量 buffer、不复制临时快照。Range 206、后缀 Range、开放结尾 Range 和 multipart 长度已测试。`/_glob` 目录列表和 tar 逻辑不变。缩略图、转码缓存及 pack viewer 静态文件统一使用该响应类；缓存键、ETag、Last-Modified、304 判断和生成流程不变。

边界：同一 inode 被原地覆盖时，本方案不提供内容快照；截短产生提前 EOF 时仍明确报错，不补零、不伪造成功。预览正在写入的文件只返回取长度时的前缀，因此“HTTP 长度正确”不保证尚未完成的图片或视频可解码。Range 复用当前部署 Starlette 的内部 `_open_file` 扩展点，依赖升级或降级时应运行本回归。

## 测试

`tests/test_fileserver_content_length.py` 使用真实 `uvicorn.protocols.http.httptools_impl.RequestResponseCycle.send` 检查协议。没有把 TestClient 的宽松长度处理当作协议验证。

修复前两个追加测试稳定失败，异常原文与线上一致；修复后验证：空文件、小文件、64 KiB 边界、多块文件、头后追加/替换、流中追加、Range 替换、提前 EOF、发送失败关闭 fd，以及两种 tar spool 模式。

执行：

```sh
.venv-runtime/bin/python -m pytest -q tests/test_fileserver_content_length.py tests/test_fileserver.py tests/test_proxy_streaming.py
.venv-runtime/bin/python -m ruff check hololab/node/fileserver.py tests/test_fileserver_content_length.py
```

56 项通过（其中 19 项新增），仅现有依赖弃用提示。未运行会发送 SIGTERM 的 fileserver 生命周期测试。

## 统计：不能把全部同名异常归给 fileserver

只读分析当前 `/tmp/hololab-a800.log`，以完整独立的 `RuntimeError: Response content longer than Content-Length` 行计数，截至本次取样为 **6,252 个异常**。字符串匹配为 **12,504 行**，因为 traceback 中的 `raise RuntimeError(...)` 也重复包含同样文字。当前可读日志无法核实“2 万多次”。

- **6,235 个**栈经过 `starlette/middleware/base.py:204,248`。仓库中该 HTTP middleware 安装于 gateway 的 `hololab/gateway/submission.py:22`，fileserver 未安装；这组是 gateway 发送响应时的错误，不是 tar 的异常栈。
- **17 个**栈落在 `FileResponse._handle_simple`，以上述训练预览图为邻近请求。
- 没有一个捕获到的栈直接定位 tar 流。

共享日志没有 request ID，且 fileserver 关闭了 access log。因此只能给出“异常之前最近的本地 GET”的分布，**不能当作错误请求归属**：`/_preview` 206 为 4,456，`/_thumb` 304 为 830，`/_thumb` 200 为 338，原始 PNG 404 为 134，`/_thumb` 206 为 101。最近请求还可能来自完全不同的并发操作。最常见的邻近文件组是 job `899f6034-ba65-4e70-9285-6022d0c383f5` 的 `videos_dir/p_*/input.mp4` 低分辨率预览。

该统计反驳了“全部错误由 fileserver.py:860 导致”的归因。本次修复覆盖已经复现的 fileserver 竞争，不能声称消除了 6,235 个 gateway 异常；这组尚需单独关联请求和 tunnel/cache 数据。

## 502 转换链与证据边界

当前代码有两处明确的代理转换：

1. 直连：`hololab/gateway/proxy.py:364` 的 `client.send(..., stream=True)` 在收到上游响应头前失败，`:368` 转换为 502，detail 为 `upstream fetch failed: ...`。
2. 反向 tunnel：`hololab/node/file_tunnel.py:212` 附近将本地 HTTP 异常发送为 error frame；`hololab/gateway/tunnel.py:60` 在 response future 尚未完成时设置异常；`proxy.py:319` 转换为 502，detail 为 `preview tunnel failed: ...`（该等待也可能因超时失败）。

**Content-Length 超发发生时，fileserver 已经发出了响应头。** Uvicorn 随后抛错并断开连接；如果 gateway 已经下发头，无法把原响应状态改成 502，此时应表现为中断/不完整响应。后续请求若在获取上游头之前失败，才会经过上述 502 转换。因此不能仅凭这个 RuntimeError 宣称用户看到的 502 必然由它直接转换而来。

本次没有获得用户那次连续失败的具体 URL/job ID、502 detail 或外层代理访问日志，故其确切转换层仍未证实。已请求这些关联信息；未修改 gateway 或前端，也未臆造 502 因果关系。

## 生效条件

提交只改变磁盘代码，现有 8828/8829 进程不加载本次修改。需用户安排后续部署/重启 fileserver 才生效；本会话不执行。现存 gateway 问题和历史日志不会因该提交消失。
