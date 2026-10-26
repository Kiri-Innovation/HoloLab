---
title: 算法包注册表 API 参考
group: 算法包站点
order: 10
---
# 算法包注册表 API 参考

**注册表服务不开源，接口行为以本文档为准。** 本文面向第三方发布、查询、下载及安装客户端；无需访问站点源码。文档核对日期：2026-10-26。示例均取自真实 HTTP 响应或已有真实验收记录，时间、包数量和 yank 状态是采样时的快照，不代表当前目录内容或规模。

公开服务根地址：`https://packs.xenotech.studio/`。源列表应配置这个根地址，不能配置成 `/simple/`；配方只记录 `scope/name@version` 和期望代码哈希，不把源地址嵌入包身份。

## 1. 接口总览与公共约定

| 方法 | 路径 | 用途 | 鉴权 |
|---|---|---|---|
| GET / HEAD | `/simple/` | 全站包名清单 | 无 |
| GET / HEAD | `/simple/<scope>/<name>/` | 指定包的所有已提交版本 | 无 |
| GET / HEAD | `/packages/<scope>/<name>/<version>/<sha>.tgz` | 下载归档；sha 为 code_sha256 | 无 |
| GET / HEAD | `/index.json` | 动态生成的全量只读兼容入口 | 无 |
| GET | `/healthz` | 受限健康诊断；**公网不可用，公网 404** | 应用本身不校验 token，但未公开 |
| POST | `/publish` | 发布一个不可变版本 | Bearer token |
| POST | `/yank` | 标记版本不推荐、拒绝新安装 | Bearer token |
| POST | `/unyank` | 恢复允许新安装 | Bearer token |

所有公开读接口不需要 token；三个写接口必须携带 `Authorization: Bearer <token>`。无注册、登录、token 获取、删除版本、覆盖版本、更新 evidence、批量发布或搜索 API。token 由服务方另行发放给可信发布者。

- 请求直接使用 HTTPS。HTTP 入口返回 **308** 到相同路径的 HTTPS 地址；不要先通过明文 HTTP 发送 token。
- 按表中拼写请求路径：读包路径保留末尾 `/`，写端点没有末尾 `/`。不要依赖自动补斜杠或重定向行为。
- JSON 文本使用 UTF-8。不要依赖对象键顺序。写请求的字段类型应严格按表填写，不依赖类型自动转换；禁止 NaN/Infinity 等非标准 JSON 数值。
- `/simple/`、单包元数据、`/index.json` 没有分页、游标、过滤、版本查询参数或条件请求契约。未定义的查询参数不具有过滤作用。一个包的所有版本一次返回；版本字典不承诺排序。
- `Accept: application/vnd.pypi.simple.v1+json` 可用于三个元数据读接口，返回相同 Content-Type；省略 Accept 返回 `application/json`。当前实现按 Accept 文本是否包含该媒体类型选择，**不实现完整 q 值协商**。`Accept: text/html` 仍返回 JSON，不提供 HTML 索引，也不返回 406。
- 路径和查询粒度参考 PyPI simple index，但正文是本文定义的 **HoloLab Index/Release**，不是标准 PyPI 的 files/hashes schema，不能据此用 pip 安装。
- 元数据真源是 SQLite；对象存储上的旧全站 JSON 已退出读写链路。发布提交前不出现在查询中。列包和随后查询版本是独立请求，不是一个跨请求快照。
- 公开接口目前不提供浏览器跨域 CORS 契约；实测 OPTIONS `/simple/` 返回 403。浏览器跨源写入会受预检限制，建议从应用服务端或命令行对接。

## 2. 身份、版本与两类哈希

### 2.1 包名和版本

规范名为 `scope/name`，scope 与 name 都是 ASCII 小写标识符：

| 字段 | 正则/约束 |
|---|---|
| scope | `^[a-z0-9][a-z0-9_-]*$`，1–63 字符 |
| name | `^[a-z0-9][a-z0-9_-]*$`，1–127 字符；写 API 中只传 name 部分，不包含 scope/ |
| version | `^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$`，写 API 最长 64 字符 |
| 完整引用 | `scope/name@version`，按精确版本选择，不支持范围或自动选最新 |

例如 `kiri/foo@1.2.3` 合法。大写、点号包名、空格、中文、前导 `_`/`-`、额外 `/`、`..`、URL 编码路径绕过都不属于合法规范名。版本拒绝 `v1.2.3`、`1.2`、`01.2.3`、`1.2.3-rc1`、`1.2.3+build`、范围表达式。

**别名是客户端规则：`foo ≡ kiri/foo`。** 客户端在构造请求 URL 前先补默认 scope：`foo@1.2.3` 请求 `/simple/kiri/foo/`，不能请求 `/simple/foo/`（实测 404）。写 API 始终分开传 `scope="kiri"`、`name="foo"`。manifest 中 name 允许相同的无 scope 别名，但规范化后必须与上传元数据一致。没有 PyPI 风格的大小写、`.`/`_`/`-` 合并规范化。

HoloLab 开发态允许 `1.2.3+` 表示 dev 标记；**中央发布和 yank/unyank 均不接受尾随 `+`**。发布者必须先冻结为三段数字正式版本；归档 manifest.version 也必须与 metadata.version 完全一致。

### 2.2 两个 SHA-256 不能互换

| 字段 | 对什么计算 | 服务端责任 | 客户端责任 |
|---|---|---|---|
| archive_sha256 | 上传 `.tgz` 的原始压缩字节，含 gzip/tar 元信息 | 流式重算、比对声明；对象存储写后/已有对象回读比对 | 下载时流式重算，并核对 archive_bytes |
| code_sha256 | HoloLab 声明范围的代码内容身份 | 只检查 64 位小写十六进制格式，原样记录发布者声明；**不重算** | 与配方期望哈希比对，解包后重算代码身份再比对 |

两个字段都是 64 位小写 hex，不接受 `sha256:` 前缀。即使代码相同，重新打包也可能改变 archive_sha256。归档 URL 中的 `<sha>` 是 **code_sha256**；不要用 archive_sha256 拼 URL，优先直接解析 Release.archive。

`evidence` 和 `external` 是发布方声明，不是服务端签名或完整性证明；仅比较 evidence.sha256 和 code_sha256 不构成独立验证。FORMAT=2 代码身份计算约定见第 12 节。

同名同版本永久保留，yank 不释放版本号。即使原包被 yank、内容相同、token 轮换或客户端重新打包，也不能通过新发布覆盖；需要新版本号或以原幂等键重试原操作。

包体存储目前允许公众写入，第三方可绕过发布接口篡改归档。本站对不可变的保证限于发布 API，不是对象存储层的绝对防篡改；下载客户端必须校验两类哈希，并建议由配方固定可信的预期 code_sha256。

## 3. GET /simple/：列出包名

**用途：** 展示“有哪些包”。已知包名的安装流程直接请求单包 URL，不需要先列全站。

**请求：** 无请求体、无鉴权、无参数。

```sh
curl --fail-with-body -H 'Accept: application/vnd.pypi.simple.v1+json' \
  'https://packs.xenotech.studio/simple/'
```

**响应：** 200；顶层 projects 为数组，每个元素只有 name（string，规范 scope/name）。按 scope/name 排序、去重，只要包有已提交版本就出现，**包括所有版本均 yanked 的包**。无包时 projects 为空数组，无 total/next/cursor。

以下正文来自真实调用，115 字节（展示时仅格式化空白）：

```json
{
  "projects": [
    {
      "name": "registry-smoke/hello-20261025184133"
    },
    {
      "name": "registry-smoke/simple-check-20261026114725"
    }
  ]
}
```

响应头包含 `Cache-Control: no-store` 和 `Vary: Accept`。HEAD 正文为空，状态及相关表示头与 GET 一致。

**错误：** 非 GET/HEAD 方法公开入口通常 403 HTML；服务不可达可有 502/504，客户端不可把失败当作空清单。未实现分页，客户端应对响应体设置合理上限。

## 4. GET /simple/<scope>/<name>/：查询单包版本

**用途：** 取得该包所有已提交版本，包括 yanked 版本，以精确版本查找、显示或检查安装状态。

**请求：** 无 body、无鉴权。路径参数遵守第 2 节规范。

```sh
curl --fail-with-body -H 'Accept: application/vnd.pypi.simple.v1+json' \
  'https://packs.xenotech.studio/simple/registry-smoke/hello-20261025184133/'
```

**响应：** 200，Index 对象：

| 字段 | 类型 | 含义 |
|---|---|---|
| format | integer | 当前为 1；不是 evidence.format，也不是 PyPI 协议版本 |
| authority | string | 当前为 hololab-central；配置多个镜像时应属于同一 authority，不应静默混用不同注册表 |
| releases | object<string, Release> | 键必须等于 `Release.name + "@" + Release.version`；这里只含请求的包 |

### Release 完整字段表

当前响应包含以下全部字段。可空字段以 JSON null 表达，不应把 null 当成字符串。

| 字段 | 类型 | 含义/来源 |
|---|---|---|
| name | string | 规范 scope/name，不是仅 name 片段 |
| version | string | 已发布的三段数字版本，不含 dev + |
| code_sha256 | string | 发布方声明的代码身份，64 位小写 hex |
| archive_sha256 | string | 上传归档字节的 SHA-256，经服务端复核 |
| archive | string | 相对**源根 URL**的安全路径，形如 packages/scope/name/version/code_sha256.tgz；不是相对当前单包 URL |
| archive_bytes | integer > 0 | 压缩归档精确字节数 |
| unpacked_bytes | integer ≥ 0 | tar 成员声明大小总和（合法目录大小为零），由服务端校验并统计；不含 tar 头/填充和文件系统块开销 |
| manifest | string | 归档根内的相对 manifest 路径，默认 manifest.yaml |
| env | string | manifest.runtime.env，未声明时为空字符串；仅记录，不保证该环境存在或由服务安装 |
| requires | object 或 null | manifest.requires 原映射；无要求时 null。注册表不规定其内部 schema，也不安装/解析依赖 |
| external | object | metadata.external 原声明，默认 `{}`；没有强制内部字段或依赖闭包保证 |
| evidence | object | metadata.evidence 原声明，默认 `{}`；可含 format、hot/cold、manifest 等，但注册表不验证其真实性，也不保证这些子字段存在 |
| published_at | string | 版本创建时的 UTC ISO 8601 时间，含时区，示例使用 +00:00；yank 不改它 |
| publisher | string | 服务写入的发布者标签，当前固定 trusted-publisher；不是请求可设置的字段，也不是数字签名 |
| yanked | boolean | true 时客户端必须拒绝新安装；记录和包体仍可读 |
| yank_reason | string 或 null | 当前撤回理由；unyank 后为 null |
| yank_updated_at | string 或 null | yank 状态或有效理由最后改变时间，UTC ISO 8601；初始 null；unyank 后仍保留变更时间 |

真实响应（协议测试包；文档核对时已通过读请求确认内容）：

```json
{
  "format": 1,
  "authority": "hololab-central",
  "releases": {
    "registry-smoke/hello-20261025184133@0.0.1": {
      "name": "registry-smoke/hello-20261025184133",
      "version": "0.0.1",
      "code_sha256": "d9507411fd839fd3b4822711ac1f5dfc4526ffbb72ebf0f2764f6249ae525688",
      "archive_sha256": "2a0337cf8bc140d6c639ef686f6209c3e3245481a4cedbbc29c7ff05d60fa891",
      "archive": "packages/registry-smoke/hello-20261025184133/0.0.1/d9507411fd839fd3b4822711ac1f5dfc4526ffbb72ebf0f2764f6249ae525688.tgz",
      "archive_bytes": 343,
      "unpacked_bytes": 261,
      "manifest": "manifest.yaml",
      "env": "test",
      "requires": null,
      "external": {
        "dependency_discovery_complete": false,
        "uncovered": [],
        "undeclared": []
      },
      "evidence": {
        "cold": {
          "baseline_at": null,
          "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
          "stats": {
            "bytes": 0,
            "files": 0,
            "links": 0,
            "top_level_names": []
          },
          "verification": "not_checked"
        },
        "controls": {
          "manifest_sha256": "b3975205157d30ca8eb02325f435f7b6684b2419b726cecce74bc2dec9cc93f5",
          "packignore_sha256": null
        },
        "dependency_discovery_complete": false,
        "fixed_excluded": {
          "bytes": 0,
          "files": 0,
          "links": 0,
          "top_level_names": []
        },
        "format": 2,
        "git": {
          "commit": null,
          "dirty": null,
          "tree_sha": null
        },
        "hot": {
          "sha256": "579c7a744ab562386960c543b49b2d8889cef529ff539c6589fc21fc4f813f5c",
          "stats": {
            "bytes": 32,
            "files": 1,
            "links": 0,
            "top_level_names": [
              "hello.py"
            ]
          },
          "verification": "verified_at_submission"
        },
        "ignored": {
          "bytes": 0,
          "files": 0,
          "links": 0,
          "top_level_names": []
        },
        "manifest": {
          "excluded_fields": [
            "published_as",
            "published_at",
            "version",
            "yank",
            "yank_reason",
            "yanked",
            "yanked_at"
          ],
          "normalized": {
            "apiVersion": "hololab.dev/v1",
            "exec": {
              "shell": "python \"{{ package_root }}/hello.py\""
            },
            "inputs": {},
            "kind": "Algorithm",
            "name": "registry-smoke/hello-20261025184133",
            "outputs": {
              "out": {
                "tags": [
                  "text"
                ]
              }
            },
            "root": ".",
            "runtime": {
              "env": "test"
            }
          },
          "raw_sha256": "b3975205157d30ca8eb02325f435f7b6684b2419b726cecce74bc2dec9cc93f5",
          "sha256": "263c8fc3d3f9746b0999dbae2eefcf8b9a0875244a53aff9693fe6f3ee81feaf"
        },
        "quality_hints": [],
        "recorded_at": 1792924894.8461387,
        "root": ".",
        "sha256": "d9507411fd839fd3b4822711ac1f5dfc4526ffbb72ebf0f2764f6249ae525688",
        "source": "registry-smoke/hello-20261025184133@0.0.1",
        "uncovered": [],
        "undeclared": []
      },
      "published_at": "2026-10-25T10:41:35.028765+00:00",
      "publisher": "trusted-publisher",
      "yanked": true,
      "yank_reason": "deployment smoke test pack; kept in archive as pipeline evidence, not recommended for use",
      "yank_updated_at": "2026-10-26T03:19:49.615178+00:00"
    }
  }
}
```

该包已 yanked，示例用于解释协议，不能作为推荐安装包。evidence 的历史 verification 字符串是数据内容，不能推断当前服务重新验证了代码身份。

**错误：**

- 404、真实响应：`{"detail":"Package not found"}`：合法路径但没有已提交版本。
- 422、真实响应：`{"detail":"Invalid package name"}`：例如 scope 包含 `!`。
- 404、真实响应：`{"detail":"Not Found"}`：例如误请求 `/simple/foo/`，路由形状不对。
- 请求包存在但所需版本不存在时，服务仍返回该包完整 200；**由客户端在 releases 中判断精确版本缺失**，不是服务端版本查询 404。

## 5. GET /packages/<scope>/<name>/<version>/<sha>.tgz：下载归档

**用途：** 取得 Release.archive 指向的 tar+gzip 字节。无鉴权，无 body；也支持 HEAD。

```sh
curl --fail-with-body --output package.tgz \
  'https://packs.xenotech.studio/packages/registry-smoke/hello-20261025184133/0.0.1/d9507411fd839fd3b4822711ac1f5dfc4526ffbb72ebf0f2764f6249ae525688.tgz'
```

**响应：** 200 二进制 `.tgz`，不是 JSON。本站代理腾讯云 COS 获取包体，**正常下载不返回 302**；不要求客户端持有 COS 凭据或自行签名。以下是此次真实调用的表示头：

```http
HTTP/2 200
Content-Type: application/gzip
Content-Length: 343
Cache-Control: no-store
Content-Disposition: attachment
```

`Content-Disposition` 没有 filename 参数，客户端自行选本地文件名。实际下载归档 SHA-256：`2a0337cf8bc140d6c639ef686f6209c3e3245481a4cedbbc29c7ff05d60fa891`，与 Release 一致。可能包含 ETag、Last-Modified、Accept-Ranges 等对象存储头；**ETag 不等价于本文任一 SHA-256**。Range/条件下载未作为此次验证的完整契约；若采用断点续传，必须最终对完整字节重新校验。

**错误：** 路径未匹配公开下载规则可能 404 HTML；规则匹配但对象不存在可能为 COS 的 404 XML；COS 权限错误可能 403，代理故障可能 502/504。按状态码和 Content-Type 处理，不假定 JSON。不要安装错误页。

包体下载不查询 yanked 状态，已 yank 包体仍可下载。因此“拒绝新安装”必须由客户端检查最新元数据实现，不是依赖下载 403。直接访问猜测的 URL 也不代表版本已提交，必须先取得 Release。

## 6. GET /index.json：只读兼容入口

**用途：** 为尚未升级的客户端保留全站 Index。**动态从数据库生成；不是权威文件、不持久化成全站索引，也不更新 COS 旧 index.json。** 新客户端应使用按包接口。

```sh
curl --fail-with-body 'https://packs.xenotech.studio/index.json'
```

**响应：** 200，schema 与第 4 节 Index 一致，但 releases 包含全站已提交版本。无分页、无鉴权。目录内容与响应大小随已提交版本变化。兼容入口不是 302，暂无公布的移除日期。

```http
Content-Type: application/json
Cache-Control: no-store
Vary: Accept
Deprecation: true
Link: </simple/>; rel="successor-version"
```

**真实 JSON 示例：** 以下是 2026-10-26 历史 `/index.json` 调用的完整响应，其中条目用于说明响应结构，不代表当前目录规模。使用 vnd JSON Accept 时，只改变媒体类型，不改变 schema。

```json
{
  "format": 1,
  "authority": "hololab-central",
  "releases": {
    "registry-smoke/hello-20261025184133@0.0.1": {
      "name": "registry-smoke/hello-20261025184133",
      "version": "0.0.1",
      "code_sha256": "d9507411fd839fd3b4822711ac1f5dfc4526ffbb72ebf0f2764f6249ae525688",
      "archive_sha256": "2a0337cf8bc140d6c639ef686f6209c3e3245481a4cedbbc29c7ff05d60fa891",
      "archive": "packages/registry-smoke/hello-20261025184133/0.0.1/d9507411fd839fd3b4822711ac1f5dfc4526ffbb72ebf0f2764f6249ae525688.tgz",
      "archive_bytes": 343,
      "unpacked_bytes": 261,
      "manifest": "manifest.yaml",
      "env": "test",
      "requires": null,
      "external": {
        "dependency_discovery_complete": false,
        "uncovered": [],
        "undeclared": []
      },
      "evidence": {
        "cold": {
          "baseline_at": null,
          "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
          "stats": {
            "bytes": 0,
            "files": 0,
            "links": 0,
            "top_level_names": []
          },
          "verification": "not_checked"
        },
        "controls": {
          "manifest_sha256": "b3975205157d30ca8eb02325f435f7b6684b2419b726cecce74bc2dec9cc93f5",
          "packignore_sha256": null
        },
        "dependency_discovery_complete": false,
        "fixed_excluded": {
          "bytes": 0,
          "files": 0,
          "links": 0,
          "top_level_names": []
        },
        "format": 2,
        "git": {
          "commit": null,
          "dirty": null,
          "tree_sha": null
        },
        "hot": {
          "sha256": "579c7a744ab562386960c543b49b2d8889cef529ff539c6589fc21fc4f813f5c",
          "stats": {
            "bytes": 32,
            "files": 1,
            "links": 0,
            "top_level_names": [
              "hello.py"
            ]
          },
          "verification": "verified_at_submission"
        },
        "ignored": {
          "bytes": 0,
          "files": 0,
          "links": 0,
          "top_level_names": []
        },
        "manifest": {
          "excluded_fields": [
            "published_as",
            "published_at",
            "version",
            "yank",
            "yank_reason",
            "yanked",
            "yanked_at"
          ],
          "normalized": {
            "apiVersion": "hololab.dev/v1",
            "exec": {
              "shell": "python \"{{ package_root }}/hello.py\""
            },
            "inputs": {},
            "kind": "Algorithm",
            "name": "registry-smoke/hello-20261025184133",
            "outputs": {
              "out": {
                "tags": [
                  "text"
                ]
              }
            },
            "root": ".",
            "runtime": {
              "env": "test"
            }
          },
          "raw_sha256": "b3975205157d30ca8eb02325f435f7b6684b2419b726cecce74bc2dec9cc93f5",
          "sha256": "263c8fc3d3f9746b0999dbae2eefcf8b9a0875244a53aff9693fe6f3ee81feaf"
        },
        "quality_hints": [],
        "recorded_at": 1792924894.8461387,
        "root": ".",
        "sha256": "d9507411fd839fd3b4822711ac1f5dfc4526ffbb72ebf0f2764f6249ae525688",
        "source": "registry-smoke/hello-20261025184133@0.0.1",
        "uncovered": [],
        "undeclared": []
      },
      "published_at": "2026-10-25T10:41:35.028765+00:00",
      "publisher": "trusted-publisher",
      "yanked": true,
      "yank_reason": "deployment smoke test pack; kept in archive as pipeline evidence, not recommended for use",
      "yank_updated_at": "2026-10-26T03:19:49.615178+00:00"
    }
  }
}
```

**错误：** 不支持的方法 403 HTML；服务不可用可能 502/504。不要把直接 COS 旧 JSON 作为兼容备用源，它可能永久停留在旧状态。

## 7. GET /healthz：受限健康诊断

**用途：** 报告未完成操作数及已提交版本数。**不属于外部可调用的公开端点**；不存在需另外提交的“健康检查 token”。

**公开请求：** `GET https://packs.xenotech.studio/healthz` 实测 **404 text/html**，不是 JSON，不说明注册表不健康。第三方连通性检查应改用 `GET /simple/`。

应用实际诊断响应已通过受限访问只读核验，200 正文为：

```json
{"ready":true,"pending":0,"releases":3}
```

| 字段 | 类型 | 含义 |
|---|---|---|
| ready | boolean | pending 为 0 时 true |
| pending | integer ≥ 0 | 尚未提交的发布/操作数量 |
| releases | integer ≥ 0 | 已提交版本数量，包含 yanked |

**错误/非就绪：** 按实现，pending 非零时同一 schema、ready=false、HTTP 503；此次未故意制造该状态。ready=true 仅代表上述判断，不代表全部包体正在被逐一验证可下载。不提供外部诊断地址。

## 8. POST /publish：发布归档和声明

**用途：** 创建一个尚不存在的正式版本。先校验归档、上传包体并确认，最后才使版本在元数据接口可见。

### 8.1 请求

- `Authorization: Bearer <token>`，大小写/空格按示例保持；不要放 query string 或 JSON 中。
- 推荐 `Idempotency-Key: <operation-id>`，1–128 字符，正则 `^[A-Za-z0-9_.-]{1,128}$`。该 header 可省略，但无法可靠恢复未获得响应的首次请求。
- Body 为 **multipart/form-data**，只能有一个名为 **metadata** 的文本字段和一个名为 **archive** 的文件字段。不是 JSON body，也不是直接发送 gzip body。
- metadata 文本本身是 JSON object，最长 **16,384 个字符**；archive 是 tar+gzip 文件。边界由 HTTP 库生成，不要手工设置一个缺失 boundary 的 Content-Type。

| metadata 字段 | 类型 | 必填/默认 | 约束与含义 |
|---|---|---|---|
| scope | string | 必填 | 第 2 节规范 |
| name | string | 必填 | 不含 scope/ |
| version | string | 必填 | 正式三段版本，不接受 + |
| code_sha256 | string | 必填 | 发布方计算的代码身份，64 位小写 hex |
| archive_sha256 | string | 必填 | 本次上传文件的字节 SHA-256，64 位小写 hex |
| manifest | string | 默认 manifest.yaml | 1–1024 字符；安全的归档内相对路径，必须存在 |
| evidence | object | 默认 `{}` | 原样记录的发布方证据，不受信任 |
| external | object | 默认 `{}` | 原样记录的外部依赖说明，不受信任 |

**未知 metadata 字段被拒绝。** 不得提交 archive、archive_bytes、unpacked_bytes、env、requires、published_at、publisher、yanked 等响应字段作为上传元数据；它们由服务确定或从 manifest 读取。

可复制的请求形状（文件均为调用者本地文件，token 为占位符）：

```sh
curl --fail-with-body -X POST 'https://packs.xenotech.studio/publish' \
  -H 'Authorization: Bearer <token>' \
  -H 'Idempotency-Key: <operation-id>' \
  --form-string "metadata=$(cat metadata.json)" \
  -F 'archive=@package.tgz;type=application/gzip'
```

注意：示例中的 `<operation-id>` 必须替换成满足正则的真实值，不能保留尖括号。使用 `--form-string` 防止 metadata 内的 `;`、`@` 被 curl 解释为表单文件语法。文件名 package.tgz 仅是传输标签，不决定服务端包身份。

下面 metadata 来自已执行的真实发布请求。**这是刻意声明全零代码哈希的传输测试包，不是可安装的正常包；真实作者必须计算正确 code_sha256，不能照抄零值或复用这个已存在版本。**

```json
{
  "scope": "registry-smoke",
  "name": "simple-check-20261026114725",
  "version": "0.0.1",
  "manifest": "manifest.yaml",
  "code_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
  "archive_sha256": "178a676182714991b8afad3fc0e4f6878e251db146d402aebd8e6ee6d28fe253",
  "evidence": {
    "purpose": "API transport test; deliberately declared zero code hash, not an installable release"
  }
}
```

### 8.2 成功响应

首次发布 **201**，JSON 顶层 release 为第 4 节完整 Release，idempotency_key 为实际使用的幂等键（string）。同键重试成功 **200**，相同 envelope。以下为真实 201 响应：

```json
{
  "release": {
    "archive": "packages/registry-smoke/simple-check-20261026114725/0.0.1/0000000000000000000000000000000000000000000000000000000000000000.tgz",
    "archive_bytes": 232,
    "archive_sha256": "178a676182714991b8afad3fc0e4f6878e251db146d402aebd8e6ee6d28fe253",
    "code_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
    "env": "test",
    "evidence": {
      "purpose": "API transport test; deliberately declared zero code hash, not an installable release"
    },
    "external": {},
    "manifest": "manifest.yaml",
    "name": "registry-smoke/simple-check-20261026114725",
    "published_at": "2026-10-26T03:47:25.959122+00:00",
    "publisher": "trusted-publisher",
    "requires": null,
    "unpacked_bytes": 108,
    "version": "0.0.1",
    "yank_reason": null,
    "yank_updated_at": null,
    "yanked": false
  },
  "idempotency_key": "simple-check-20261026114725-0.0.1"
}
```

### 8.3 幂等、不可变和恢复

| 情形 | 结果 |
|---|---|
| 尚未发布/预留的版本，新操作 | 成功 201 |
| 相同幂等键、相同操作和规范化元数据，已完成 | 200，返回该操作原始 release 快照 |
| 相同幂等键，但 metadata 或操作种类不同 | 409，Idempotency key reused with different request |
| 同名同版本换新键再次发布，哪怕内容完全一致 | 409，Version already published or reserved; never overwrite |
| 同名同版本不同内容 | 校验先失败时可先返回 422；有效归档进入版本冲突判断后 409；永不覆盖 |
| 同键操作仍在上传 | 409，Publication in progress; retry same Idempotency-Key；延迟后原样重试 |
| 暂时存储失败/结果不明 | 503；保留原文件、元数据和幂等键，稍后原样重试 |
| 已有目标对象字节或长度不同 | 409；不会覆盖，原请求可能需服务方介入 |

幂等键**全站跨写端点共用命名空间**，不是每包或每 token 单独计数；发布、yank、unyank 不可共用同一键。未传键会生成新的随机键，并在成功响应返回；如果第一次响应丢失，客户端无法得知该键，所以生产客户端应自行持久化键再发请求。

请求校验发生在幂等查询前。即使原操作已成功，重试仍必须提供合法 multipart、同一个完整归档及匹配声明；不能用空文件探测状态。元数据对象顺序不影响指纹，省略默认字段与传等价默认值会被规范化，但建议保留原始请求材料，不重新打包。

超时或 503 不等于“肯定未发布”；用相同幂等键重试，并查询单包元数据确认。失败预留的版本可能未出现在查询里，但新键发布它仍 409。没有对外删除预留版本的接口。

已提交发布操作的幂等重放返回**当时快照**；如果版本后来 yank，重放 publish 返回的 yanked 可能还是 false。要得知当前状态，重新 GET 单包元数据。

真实的重复版本错误：

```json
{"detail":"Version already published or reserved; never overwrite"}
```

**其它错误：** 400 表单解析错误；401 未授权；403 token scope 不允许；413 超出请求/归档大小；422 元数据、哈希或归档安全失败；429 限速/并发/日配额；503 存储失败。详情见第 10 节。

## 9. POST /yank 与 POST /unyank

### 9.1 POST /yank

**用途：** 只改变已提交版本的 yank 标记和理由；不删除包、不改变身份或归档。客户端据此拒绝新安装，已有安装不会被远程删除。

**请求：** Bearer token；可选独立 Idempotency-Key；`Content-Type: application/json`。请求体只允许：

| 字段 | 类型 | 必填/默认 |
|---|---|---|
| scope | string | 必填，第 2 节规范 |
| name | string | 必填，不含 scope/ |
| version | string | 必填，正式三段版本 |
| reason | string 或 null | 可选，默认 null，最长 1024 字符 |

真实验收使用的请求体如下；此处只记录，不要求读者重放对公共测试包的修改：

```json
{"scope":"registry-smoke","name":"simple-check-20261026114725","version":"0.0.1","reason":"Transport-only API test; declared code identity is intentionally unverified"}
```

请求形状：

```sh
curl --fail-with-body -X POST 'https://packs.xenotech.studio/yank' \
  -H 'Authorization: Bearer <token>' -H 'Idempotency-Key: <new-operation-id>' \
  -H 'Content-Type: application/json' --data-binary @yank.json
```

**响应：** 200，`{"release": Release, "idempotency_key": string}`。真实响应：

```json
{
  "release": {
    "archive": "packages/registry-smoke/simple-check-20261026114725/0.0.1/0000000000000000000000000000000000000000000000000000000000000000.tgz",
    "archive_bytes": 232,
    "archive_sha256": "178a676182714991b8afad3fc0e4f6878e251db146d402aebd8e6ee6d28fe253",
    "code_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
    "env": "test",
    "evidence": {
      "purpose": "API transport test; deliberately declared zero code hash, not an installable release"
    },
    "external": {},
    "manifest": "manifest.yaml",
    "name": "registry-smoke/simple-check-20261026114725",
    "published_at": "2026-10-26T03:47:25.959122+00:00",
    "publisher": "trusted-publisher",
    "requires": null,
    "unpacked_bytes": 108,
    "version": "0.0.1",
    "yank_reason": "Transport-only API test; declared code identity is intentionally unverified",
    "yank_updated_at": "2026-10-26T03:47:27.641769+00:00",
    "yanked": true
  },
  "idempotency_key": "e8a1afa232f84f0d873469766464aac1"
}
```

**错误：** 401 鉴权失败、403 scope 禁止、404 Release not found（含尚未提交）、409 幂等键冲突、413 请求超过 16 KiB、422 请求字段/键非法、429 限制。其它未知字段或类型非法统一 Invalid yank metadata。

### 9.2 POST /unyank

**用途：** 允许恢复新安装，将 yanked 置 false、yank_reason 置 null；归档和发布身份不变。

**请求：** 与 yank 同 schema、鉴权和 header；POST 到 `/unyank`。reason 可省略；即使传入也不保留为结果理由，但它仍参与请求幂等指纹，重试不能任意改动它。

```sh
curl --fail-with-body -X POST 'https://packs.xenotech.studio/unyank' \
  -H 'Authorization: Bearer <token>' -H 'Idempotency-Key: <new-operation-id>' \
  -H 'Content-Type: application/json' --data-binary @unyank.json
```

真实验收的 unyank 请求体与上述 yank 请求体相同，使用另一次独立操作；真实 200 响应：

```json
{
  "release": {
    "archive": "packages/registry-smoke/simple-check-20261026114725/0.0.1/0000000000000000000000000000000000000000000000000000000000000000.tgz",
    "archive_bytes": 232,
    "archive_sha256": "178a676182714991b8afad3fc0e4f6878e251db146d402aebd8e6ee6d28fe253",
    "code_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
    "env": "test",
    "evidence": {
      "purpose": "API transport test; deliberately declared zero code hash, not an installable release"
    },
    "external": {},
    "manifest": "manifest.yaml",
    "name": "registry-smoke/simple-check-20261026114725",
    "published_at": "2026-10-26T03:47:25.959122+00:00",
    "publisher": "trusted-publisher",
    "requires": null,
    "unpacked_bytes": 108,
    "version": "0.0.1",
    "yank_reason": null,
    "yank_updated_at": "2026-10-26T03:47:27.820040+00:00",
    "yanked": false
  },
  "idempotency_key": "7fc7ecfab14348648215b8dafdb5e078"
}
```

**错误：** 同 yank；无版本时 404，不会创建版本。

### 9.3 标记操作的幂等性

- 同键同操作同请求：返回该操作原结果，不再次改变当前状态；同键不同操作/请求 409。
- 用新键把已经 yanked 的版本再次 yank：仍 200；理由相同时不改 yank_updated_at；理由不同则更新理由和时间。省略 reason 等价于 null，可能清除旧理由。
- 用新键把已经 unyanked 的版本再次 unyank：仍 200；状态未变则不更新 yank_updated_at。
- 从 yank 到 unyank 或理由改变时更新时间；published_at、两个哈希、archive 等不变。
- 重放旧 yank 幂等键时，即使后来已 unyank，仍返回旧操作的 yanked=true 快照，**不会重新 yank**。要重新改变状态，必须使用新键。

## 10. 鉴权、校验、限额与错误

### 10.1 鉴权失败

三个 POST 写端点在解析 body 前检查 Bearer token；缺失或不匹配都为 401。此次分别对三个端点发送无 token 的请求，真实正文相同：

```json
{"detail":"Invalid bearer token"}
```

当前未返回 WWW-Authenticate 挑战头；不要依赖挑战后才发 token。token scope 不允许的请求返回 403（JSON detail 为 Token not authorized for this scope）。token 不授予绕过版本唯一约束的能力。

### 10.2 服务端实际校验边界

1. 检查身份字段、哈希字符串格式、manifest 字段、metadata 无额外字段和 token scope。
2. 归档流式计算 SHA-256；空文件或声明不符返回 422、`Archive SHA-256 mismatch`。不计算 code_sha256。
3. 解析 tar+gzip，拒绝绝对路径、`..`、反斜杠、冒号、NUL、`.git` 路径段、空/根路径；路径长度上限 1024 字符。拒绝重复规范路径和 casefold 大小写冲突。
4. 只允许普通文件和目录；拒绝符号链接、硬链接、设备、FIFO、其它特殊类型及 sparse；目录大小须为零、成员大小不得负数，截断文件拒绝。
5. 检查解压炸弹限额（下表），包括真实解压 tar 字节，不仅信任成员声明。
6. manifest 必须存在、为文件、大小 ≤1 MiB，可安全解析成预期 YAML 结构；root 从 manifest 所在目录解析后不得逃出解包根。manifest 名称规范化后与 scope/name 一致，version 与上传声明一致。runtime.env 若存在须是 string，requires 若存在须是 object 或 null。
7. 上述归档、manifest 检查及验证超时统一 422、`Unsafe archive or invalid manifest`，不泄漏不可信路径详细错误。
8. 版本唯一约束；对象已存在时校验完整字节摘要与长度，相同只允许恢复已有操作，不同 409，不覆盖。

服务只验证注册表需要的 manifest 字段，**不是完整算法可运行性认证**。不执行代码、不安装依赖、不做病毒扫描，不证明 evidence、external 或所有运行时依赖完备。

| 限制 | 当前值 | 超限行为 |
|---|---|---|
| 整个 HTTP 请求 body | ≤520 MiB，含 multipart 开销 | 413；可能是代理 HTML，也可能 JSON |
| publish archive 文件 | >0 且 ≤512 MiB | 太大 413；空内容 422 |
| publish metadata 文本 | ≤16,384 字符 | 422 Invalid publication metadata |
| yank / unyank body | ≤16 KiB | 413 Request too large |
| tar 成员数 | ≤50,000 | 422 Unsafe archive or invalid manifest |
| tar 成员大小总和 | ≤2 GiB | 同上 |
| 解压 tar 总字节（含头/填充） | ≤ `min(2 GiB + 50,000×2048, max(1 MiB, archive_bytes×100))` | 同上；不是对所有小包都强制 100:1 |
| manifest 文件 | ≤1 MiB | 同上 |
| 验证资源预算 | 约 45 秒 CPU、90 秒墙钟，内存受限 | 无法完成验证通常 422；客户端不应通过重试绕过 |
| 活动写请求 | 最多 2 个（三个写端点共享） | 429 Too many active writers |
| 日发布预留次数 | 最多 100 次，UTC 日，全服务合计 | 429 Daily publication quota reached |
| 日发布归档字节累计 | 最多 2 GiB，UTC 日，全服务合计 | 同上 |

MiB=1024² 字节，GiB=1024³ 字节。日配额按预留的 publish 操作计算，失败但已预留的操作也占用；成功幂等重放不新计一次。yank 不增加发布次数。

**nginx 限速：** 三个写端点按客户端连接 IP 共用 **60 请求/分钟** 的速率，`burst=20`、`nodelay`；突发额度内立即处理，超限返回 **429**，通常 HTML。它是速率整形，不是“每个自然分钟统一重置 60 次”的计数器。读端点没有本配置专门的 limit_req；不代表提供无限容量。请求体读取间隔超时 60 秒，写请求上游响应读取间隔上限 300 秒；网络或代理也可能先超时。未承诺 Retry-After 头，客户端自行指数退避并加随机抖动。

### 10.3 错误码总表

应用错误通常是 JSON object，含 detail:string；存储类 503 另含 retry:string。**下表“代码确认”是从实现逐字核对的可能错误，不冒充此次实际 HTTP 响应。** 非 JSON 错误按 Content-Type 处理；不要按英文 detail 做唯一业务分类，具体消息未来可能扩充。

| 状态 | 情形 | 正文/标识 | 证据 |
|---|---|---|---|
| 200 | 读取、yank/unyank、完成发布的幂等重放 | 对应成功 schema | 真实调用/历史调用 |
| 201 | 新版本发布成功 | release + idempotency_key | 历史调用 |
| 308 | 明文 HTTP 到 HTTPS | Location 指向 HTTPS | 配置确认；本次不发送明文 token |
| 400 | 无效 Content-Length、multipart 解析/字段数量问题 | 应用 detail 可为 Invalid content length；表单解析器也可能返回自身 detail | 代码确认 |
| 401 | 缺失/错误 Bearer token | `{"detail":"Invalid bearer token"}` | 此次三个写端点均实测 |
| 403 | token scope 无权限 | detail 为 Token not authorized for this scope | 代码确认 |
| 403 | 方法不允许，例如 GET /publish、OPTIONS /simple/ | text/html，403 Forbidden | 此次实测 |
| 404 | 包不存在 | `{"detail":"Package not found"}` | 此次实测 |
| 404 | 路由形状不对 | `{"detail":"Not Found"}` 或代理 HTML | 此次实测 |
| 404 | yank/unyank 版本不存在/未提交 | detail 为 Release not found | 代码确认 |
| 404/403 | 归档不存在/存储拒绝访问 | 可能是对象存储 XML，不保证 detail | 代码/代理路径确认，未制造存储故障 |
| 405 | 方法不受应用支持 | 应用框架 Method Not Allowed；公开入口通常先拦为 403 | 代码确认，不应依赖公网返回 405 |
| 409 | 版本已存在或已预留 | `{"detail":"Version already published or reserved; never overwrite"}` | 历史真实调用 |
| 409 | 幂等键换内容或操作类型 | detail 为 Idempotency key reused with different request | 代码确认 |
| 409 | 同键正在上传 | detail 为 Publication in progress; retry same Idempotency-Key | 代码确认 |
| 409 | 已有对象不匹配 | detail 为 Existing COS object differs; refusing overwrite 或 Existing COS object differs in size | 代码确认 |
| 409 | 待恢复归档不符 | detail 为 Pending archive differs from journal | 代码确认，需服务方介入 |
| 413 | 请求/文件过大 | detail 为 Request too large 或 Archive exceeds 512 MiB；也可能代理 HTML | 代码确认，未上传超大请求 |
| 422 | 身份/metadata 不合法 | detail 为 Invalid publication metadata / Invalid yank metadata / Invalid package name | 包路径此次实测，其余代码确认 |
| 422 | 幂等键非法 | detail 为 Invalid Idempotency-Key | 代码确认 |
| 422 | 表单形状错误 | detail 为 Expected exactly one metadata field and one archive file 或 archive must be a file | 代码确认；解析阶段也可能先 400 |
| 422 | 归档哈希不符/空归档 | detail 为 Archive SHA-256 mismatch | 代码确认 |
| 422 | 不安全 tar、无效 manifest、验证超时 | detail 为 Unsafe archive or invalid manifest | 代码确认 |
| 422 | 违反对象路径前缀保护 | detail 为 COS key must remain within PACKS/ | 代码确认；合法普通客户端不应触发 |
| 429 | 代理限速 | 通常 HTML，429 Too Many Requests | 配置确认，未对线上压测 |
| 429 | 活动写请求或日配额 | detail 为 Too many active writers / Daily publication quota reached | 代码确认 |
| 503 | 对象存储暂时失败或恢复条件缺失 | detail 为 COS HEAD failed / COS PUT failed; outcome must be reconciled / COS object verification failed / Pending publication spool missing; operator recovery required 等；另有 retry | 代码确认，未注入线上故障 |
| 503 | 受限健康诊断非就绪 | ready/pending/releases schema，无 detail | 代码确认；非公开端点 |
| 500 | 未被分类的内部失败 | 不保证 JSON | 通用故障边界，未人为制造 |
| 408/502/504 | 超时、代理无法连接或上游响应失败 | 通常 HTML，不保证 JSON | 配置/网络故障边界，未人为制造 |

存储类 503 的 retry 字段固定文本为 `Retry the identical request with the same Idempotency-Key`；对包含 operator recovery required 的错误，原样重试可能仍失败，需联系服务方。先依据状态、当前元数据和幂等记录判断，不把全部 409 当作可自动重试。

## 11. 缓存、代理与凭据保护

下表元数据缓存头指成功响应；应用异常处理返回的 404/422 等错误不一定带相同缓存头，客户端及 CDN 也不应缓存错误并当作长期包状态。

| 路径 | 当前缓存行为 | header/代理要点 |
|---|---|---|
| `/simple/`、单包元数据、`/index.json` | Cache-Control: no-store，Vary: Accept；禁用代理缓存 | 不主动剥离请求 Authorization/Cookie，但读取不需要它们，客户端不应发送；无承诺的 ETag/304 协商 |
| `/publish`、`/yank`、`/unyank` | Cache-Control: no-store；不得缓存请求或结果 | Authorization 透传供鉴权；服务重建 Host/转发来源头，不能用自行填写转发 IP 头绕过按连接 IP 的限速 |
| `/packages/...` | 站点覆盖对象原始缓存头为 no-store，禁用代理缓存 | 发往 COS 前**清空 Authorization 和 Cookie**，重写 Host；不是剥离所有 header，Accept/Range 等仍可能转发 |

不要在 CDN 上强制缓存元数据，否则 yank 后仍可能看到旧状态；不要缓存写接口。当前公共写对象存储的风险下，包体 HTTP 响应也不应被 CDN 强制长期缓存。可以在客户端使用**经过双哈希验证的本地内容缓存**，但每次新安装前仍需获取当前元数据并检查 yank；哈希不符立即硬停，不改用另一份缓存或另一源“试到成功”。

不要直接对接旧 COS index，不把 COS 域名或存储布局当成客户端稳定 API。包体目前无下载 302，若客户端通用 HTTP 库未来跟随重定向，不得向不同 origin 转发 Bearer token/Cookie；下载原本无需任何 token。

## 12. 最小客户端与代码身份复核

### 12.1 安装伪代码

下面是新安装路径。列包用于 UI，可省略；精确安装不全量下载元数据。错误均应终止当前安装，不执行归档内代码。

```text
base = "https://packs.xenotech.studio/"

# 可选：展示包名，不用于定位单个已知包
projects = GET_JSON(base + "simple/").projects
show(projects[*].name)

(name, version) = parse_exact_spec(user_spec)
name = canonical_name(name)            # foo -> kiri/foo，然后验证 scope/name
reject_dev_plus_or_invalid_version(version)
index = GET_JSON(base + "simple/" + name + "/",
                 Accept="application/vnd.pypi.simple.v1+json")
require index.format == 1
require expected_authority == index.authority
require every release.name == name
r = index.releases[name + "@" + version]  # 缺键 -> 找不到版本；不要选“最近版本”
validate_release_schema_and_key_identity(r)
if expected_code_sha256 is set and r.code_sha256 != expected_code_sha256:
    HARD_STOP("代码身份与配方不一致；禁止换源")
if r.yanked: STOP("拒绝新安装", r.yank_reason)

validate_safe_relative_path(r.archive)  # 拒绝绝对路径、..、反斜杠、冒号、空路径
url = resolve_relative(base, r.archive) # 相对源根，不相对 /simple/scope/name/
require same_origin(url, base)
bytes, computed_archive_sha = DOWNLOAD_AND_STREAM_SHA256(url,
                                  maximum_bytes=r.archive_bytes, no_token=true)
require len(bytes) == r.archive_bytes
if computed_archive_sha != r.archive_sha256: HARD_STOP("归档完整性不符")

staging = new_temporary_install_directory()
SAFE_EXTRACT_TAR_GZIP(bytes, staging,
    reject_absolute_and_parent_paths=true,
    reject_links_special_sparse_and_duplicates=true,
    limit_unpacked_bytes=r.unpacked_bytes,
    independent_decompression_and_member_limits=true)
manifest = safe_path_inside(staging, r.manifest)
require canonical_name(manifest.name) == r.name
require manifest.version == r.version
require resolved_manifest_root_inside(staging)
computed_code_sha = recompute_hololab_format2_from_files(manifest, staging)
if computed_code_sha != r.code_sha256: HARD_STOP("代码身份不符；禁止换源")

# 不覆盖已安装目录；写来源、Release 和校验结果收据，再原子移入最终目录
persist_verified_receipt(staging, base, r)
atomic_install_without_overwrite(staging, destination_for(r.name, r.version))
```

安装不等于执行或安装运行环境。依赖/环境处理是另一个步骤，不能在完整性校验之前运行包脚本。网络不可达或合法的包缺失可以按调用者的镜像策略处理；哈希不匹配、身份不匹配和 yanked 都不能静默回退。镜像必须匹配预期 authority。

### 12.2 当前 HoloLab FORMAT=2 的代码身份算法

本节补足 `recompute_hololab_format2_from_files`，避免误把它实现成 tarball SHA 或随意拼接文件摘要。注册表本身不强制 evidence.format，不能仅凭收到一个 64 位字符串断言包采用某种算法；当前 HoloLab 安装端采用以下 FORMAT=2。其它身份格式必须显式协商或拒绝，不能“校验不了就跳过”。

定义：`SHA(bytes)` 为 SHA-256 的小写十六进制字符串。`C(value)` 为以下 JSON 规范化后 UTF-8 字节的 SHA：键按字符串顺序排序；分隔符为逗号和冒号、无额外空格；非 ASCII 转义；列表保序。字节级行为等价于 Python `json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")`。不要用不同 Unicode 转义/浮点渲染的“近似 canonical JSON”代替；跨语言实现应对齐测试向量。manifest 应安全解析为字符串键映射，值可被标准 JSON 无损表达，不能含时间对象或非有限数字等不可兼容值。

1. **manifest 摘要。** 对原始 YAML 安全解析得到的映射，仅删除顶层字段 `version`、`yank`、`yanked`、`yank_reason`、`yanked_at`、`published_at`、`published_as`。其它字段（包括未知字段、root、cold、name）全部保留，不填充缺省字段，不把无 scope name 替换成规范名。`manifest_sha = C(normalized_manifest)`。这不是原始 manifest 文件字节 SHA。
2. **范围。** root 默认为 `.`，相对所选 manifest 的目录解析，必须在安全解包边界内；以 root 为树根使用 POSIX 相对文件路径。cold 默认为空列表；每项规范化为 POSIX 相对路径、禁止绝对路径或 `..`。文件路径等于 cold 项、位于其子树或 cold 项为 `.` 时属于 cold。
3. **忽略与层级。** root 下 `.packignore` 按 UTF-8、gitignore 语义解释（兼容 GitIgnoreSpec）；目录被忽略时，其子文件不能仅靠内部反向规则重新纳入。固定忽略名称：`.git`、`__pycache__`、`.pytest_cache`、`.mypy_cache`、`.ruff_cache`、`.DS_Store`，以及 `.pyc`/`.swp` 后缀；固定/忽略目录状态继承到后代。root 的 `.packignore` 文件和所选 manifest（若在 root 内）是控制文件，优先归 hot；否则优先级为 fixed → ignored → cold → hot。**所选 manifest 随后从 hot 树摘要中排除**，它已经单独语义计入；`.packignore` 本身仍在 hot 树中计入其字节。目录本身不计树摘要，fixed/ignored 文件不计 hot/cold。
4. **文件树摘要。** 对 hot/cold 分别收集实际文件，不使用 evidence 中的现成摘要。普通文件有任意执行位时 mode 为 ASCII `100755`，否则 `100644`；路径按 UTF-8/POSIX 文件名字节排序。逐文件追加 `mode + " " + relative_path + NUL + SHA(file_bytes)`（末尾 SHA 为 64 个 ASCII hex 字符），各记录之间**不添加换行或额外分隔符**。对全部追加字节计算 SHA 得到 tree_sha。空树为 SHA(空字节)。中央归档已禁止符号链接，安装客户端也必须拒绝，不需要接受链接模式来迁就坏包。
5. **最终身份。** `code_sha256 = C([2, hot_tree_sha, manifest_sha, cold_tree_sha])`。安装时必须从归档重新扫描 cold，不能复用发布时缓存的 baseline；Git commit、时间、统计、evidence 描述均不作为最终公式额外输入。哈希只覆盖声明范围，不意味着所有外部依赖也被锁定。

**真实交叉验证向量：** 此次读取第 5 节 343 字节原冒烟归档，在内存中对其无 cold、无 ignore 的内容重算，得到：

```text
hot_tree_sha = 579c7a744ab562386960c543b49b2d8889cef529ff539c6589fc21fc4f813f5c
manifest_sha = 263c8fc3d3f9746b0999dbae2eefcf8b9a0875244a53aff9693fe6f3ee81feaf
cold_tree_sha = e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
code_sha256 = d9507411fd839fd3b4822711ac1f5dfc4526ffbb72ebf0f2764f6249ae525688
```

结果与真实 Release 完全一致。此向量不替代复杂 cold/gitignore/Unicode/数值跨语言测试；这些分支此次仅按客户端实现核对。正文中的全零 code 哈希发布测试刻意证明“服务器不重算”，**不能通过这一步安装校验**。

## 13. 文档证据与未验证范围

本文依据实现版本 `5b749e8` 核对请求模型、存储、发布与恢复流程、归档校验、代理规则、验收脚本，以及 Index/Release 和包名契约。代码身份小节另核对 HoloLab FORMAT=2 算法和安装流程；读者无需访问私有源码。

| 范围 | 核验方式 |
|---|---|
| GET /simple/、HEAD /simple/ | 本次真实公开 HTTPS 请求，200，包名清单 115 字节 |
| 单包 JSON 与 Accept | 本次 vnd JSON 请求 200；text/html Accept 仍为 JSON 200；原包元数据完整可读 |
| GET /index.json | 本次 200、4177 字节、3 条 Release；核验 Deprecation/Link/no-store |
| 包体下载 | 本次公开 GET 200、343 字节；计算归档 SHA，与 Release 一致；正常无 302 |
| 公开 /healthz 与受限诊断 | 公开 404 HTML；受限只读查询 200，ready=true/pending=0/releases=3 |
| 包不存在、非法包名、单段别名路径 | 本次分别实测 404/422/404，正文见各端点 |
| 缺 token 的三个写端点 | 本次分别 POST，全部 401；未触发写入 |
| 不允许的方法 | 本次 GET /publish 与 OPTIONS /simple/ 为 403 HTML |
| publish 201、同键重放 200、重复版本 409 | 引用 2026-10-26 已执行的真实 HTTPS 验收响应；本次不再发布 |
| yank/unyank 200 与标记变化 | 引用同次历史真实验收响应；本次未改变任何包状态 |
| FORMAT=2 | 本次对原冒烟归档做只读、内存重算并匹配 code_sha256；复杂范围分支仅核对代码 |
| 限速/配额/并发满、超大请求、安全恶意 tar、错误 archive hash、COS 故障恢复、健康非就绪、scope 禁止 | 本次只按实现列明，不对线上注入故障、占配额或进行压力测试 |
| CDN、Range/条件请求、其它语言完整安装 | 本次未做端到端验证；不额外承诺未实现的行为 |

所有 JSON 成功示例直接取自真实响应，仅重新排版；错误表中未实际触发的条目明确标注“代码确认”，不把推导值包装成实测结果。未包含密钥、真实 Bearer token、受限地址或内部部署步骤。
