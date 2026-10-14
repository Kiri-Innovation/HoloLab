# HoloLab terminology

English is the editorial source. Update the English document first, then its complete Chinese counterpart. Never generate Chinese pages by overlaying translated fragments onto English Markdown. Existing Chinese copy is retained during the initial migration.

| English | Chinese | Usage |
| --- | --- | --- |
| Workflow | 工作流 | A graph edited on the canvas |
| Node / graph node | 节点 / 画布节点 | An algorithm step, not a compute device |
| Compute node | 计算设备 | The machine running tasks |
| port | 端口 | Input or output interface |
| Link | 连线 | A data dependency |
| Pack | 算法包（Pack） | Keep Pack in UI names and code |
| Gateway | Gateway / 网关 | Coordination service |
| Executor | Executor / 执行器 | Runs algorithm processes on a device |
| Run | 运行记录（Run） | Snapshot and associated Jobs; not necessarily one click |
| Job | 任务记录（Job） | An execution record; reusable across Runs |
| Artifact | 产物（Artifact） | Registered file or directory |
| Handle | 产物引用（Handle） | Reference and location metadata |
| Snapshot | 快照（Snapshot） | Implementation name behind a Run |
| Fan-out | Fan-out / 数组分片执行 | Keep the named mechanism in English |
| arrayed / arrayable | 保留原文 | Code identifiers, never translate |
| manifest | manifest / 配置声明 | Keep literal fields and commands unchanged |

Keep product UI labels such as Runs, Recent jobs, Artifacts, and Compute nodes as they actually appear in the product. Distinguish planned features from available behavior. Do not remove meaningful spaces in mixed Chinese/English text or globally normalize punctuation.
