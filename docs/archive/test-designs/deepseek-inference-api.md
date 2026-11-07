# DeepSeek 推理 API（/chat/completions）测试设计

> 依据：DeepSeek 官方文档 `api-docs.deepseek.com`（Create Chat Completion / Error Codes，抓取于 2026-10-20）。
> Endpoint：`POST https://api.deepseek.com/chat/completions`（beta 能力需 base_url 换 `https://api.deepseek.com/beta`）。
> 官方允许模型：`deepseek-flash`、`deepseek-v4-pro`（文档已不含 deepseek-chat/reasoner 旧名）。
> 错误码语义：400=请求体格式非法；401=鉴权失败；402=余额不足；422=参数值非法；429=限流；500/503=服务端。

---

## 一、测试脑图

> 缩进树可直接粘贴进 XMind / 幕布（markdown 导入）。

```text
DeepSeek 推理 API 测试
├── 1 请求基础
│   ├── 模型
│   │   ├── 有效：deepseek-flash / deepseek-v4-pro
│   │   ├── 无效：缺失 / 空串 / 已下线旧名(deepseek-chat) / 数字类型 / 不存在名
│   │   └── 语义：任务预选模型 vs params.model 覆盖（模型名有效性校验）
│   ├── 对话承载
│   │   ├── messages：正常 system+user / 缺失 / 空数组 / 非法 role /
│   │   │            tool 消息缺 tool_call_id / assistant content=null / 多模态 parts(text·image_url·file)
│   │   ├── input：DeepSeek/网关扩展承载（官方 /chat/completions 参数表无此字段，
│   │   │          按实际网关行为标注预期；平台「对话承载=input」已支持）
│   │   └── name：消息参与方可选 name 字段
│   └── 认证与账户（401 错 key / 402 余额，属环境维度，单列不放脚本用例）
├── 2 采样控制
│   ├── temperature：0 / 1.0(默认) / 2.0(上边界) / 2.1(越界) / -1 / 字符串
│   │   └── 语义：thinking 模式下无效果（校验请求成功但行为不敏感）
│   ├── top_p：1.0(默认) / 0.5 / 0.95 以下(钳制) / 0(违反 >0) / 1.5(越界)
│   │   └── 语义：非 thinking 模式固定 1.0 忽略
│   └── 已废弃：frequency_penalty / presence_penalty（传值预期 200 且无效果）
├── 3 输出控制
│   ├── max_tokens：1(下界) / 缺省(8K/64K) / 2048 / 393216(上界) / 0 / -5 / 超上限 / 字符串
│   ├── stop：缺省 / 单个 / 多个(2) / 16 个(上限) / 17 个(超) / 空串 / 数字类型
│   └── response_format：text / json_object（prompt 须含 json 指令）/ 非法 type /
│                        json_object 缺指令(400，官方明说)
├── 4 流式
│   ├── stream=false / true
│   ├── stream_options：include_usage=true 随 stream:true / 缺 stream 单独用(400，官方明说)
│   └── 流形态断言：[DONE] 收尾(sse_done) / chunk 数(chunks_min) / usage 在末块
├── 5 思考模式（thinking）
│   ├── type：enabled(默认) / disabled
│   ├── reasoning_effort：none / low / high(默认) / max（max → max_tokens 上限抬到 128K）
│   ├── 非法枚举：{"type":"ultra"} → 422/400
│   └── 冲突：thinking 开 + tool_choice=required/具名 → 400（官方明说）
├── 6 工具（FC）
│   ├── tools：合法单工具 / 多工具 / 重名(同名 function) / 名称非法字符 / 名称>128 / 空数组
│   ├── tool_choice：缺省(有 tools=auto) / none / auto / required / 具名 / 未定义具名 / 未配 tools 却给 choice
│   └── strict：true(Beta) / false
├── 7 统计与观测
│   ├── logprobs：true / false
│   ├── top_logprobs：0 / 20(上界) / 21(越界) / 未配 logprobs 却单独用
│   ├── user_id：合法 / 非法字符(空格·中文) / 超 512
│   └── usage：total_tokens>0 / 流式末块 usage
└── 8 鉴权与账户（环境维度）
    ├── 401 错误 API key
    ├── 402 余额不足
    └── 429 限流（压测域，不在本组）
```

---

## 二、因子-水平表（正交法输入，有效组合）

> 策略：有效场景用 **L18 正交表**（1 个 2 水平 + 7 个 3 水平，18 行）做 pairwise 覆盖；
> 无效场景用**单缺陷因子法**（每次只让一个参数非法，其余取有效缺省，避免缺陷遮蔽）。
> 固定 messages 模板（所有用例共用，含 json 指令以满足 response_format=json_object 前置）：
> `system: "You are a helpful assistant."` + `user: "用一句话介绍你自己，并以 JSON 输出：{\"intro\": \"...\"}"`

| 因子 | 参数 | 水平 1 | 水平 2 | 水平 3 |
|---|---|---|---|---|
| A | thinking | disabled | enabled（默认 effort=high） | enabled + effort=max |
| B | temperature | 1.0（默认） | 0.0 | 2.0（上边界） |
| C | top_p | 1.0（默认） | 0.5 | 0.9（钳制区） |
| D | max_tokens | 缺省 | 1（下界） | 2048 |
| E | stop | 缺省 | 单个 "你" | 多个 ["你","。"] |
| F | response_format | 缺省(text) | text（显式） | json_object |
| G | stream | false | true | true + stream_options.include_usage |
| H | tools/tool_choice | 无 | tools + tool_choice=auto | tools + tool_choice=具名 |

> 约束（官方明说，正交表生成后人工规避）：A=enabled 时 H=3（required/具名）非法 → 命中行调整为「无工具」。

---

## 三、正交用例（有效组合，L18，18 条）

> 预期：HTTP 200；messages=[固定模板]；未列参数=缺省。断言列见「五、落地映射」。
> 注：行 17 触发约束（A=2 且 H=3），已按约束调整为 H=1 并标注。

| 编号 | A thinking | B temp | C top_p | D max_tokens | E stop | F resp_format | G stream | H tools | 附加断言要点 |
|---|---|---|---|---|---|---|---|---|---|
| OC-01 | disabled | 1.0 | 1.0 | 缺省 | 缺省 | 缺省 | false | 无 | 「非空」+「finish_reason=stop」 |
| OC-02 | disabled | 1.0 | 0.5 | 1 | 单个 | text | true | auto | 「finish_reason=length」+「sse_done」 |
| OC-03 | disabled | 1.0 | 0.9 | 2048 | 多个 | json_object | true+usage | 具名 | 「指定工具被调用=get_weather」+「JSON 字段 path=intro 非空」 |
| OC-04 | disabled | 0.0 | 1.0 | 缺省 | 单个 | text | false | auto | 「非空」 |
| OC-05 | disabled | 0.0 | 0.5 | 1 | 多个 | json_object | true+usage | 无 | 「finish_reason=length」（截断下 json 可能不完整，不断言内容） |
| OC-06 | disabled | 0.0 | 0.9 | 2048 | 缺省 | 缺省 | true | auto | 「sse_done」+「最少块数 ≥2」 |
| OC-07 | disabled | 2.0 | 1.0 | 1 | 缺省 | json_object | false | 无 | 「JSON 字段 path=intro 非空」 |
| OC-08 | disabled | 2.0 | 0.5 | 2048 | 单个 | 缺省 | true+usage | auto | 「非空」 |
| OC-09 | disabled | 2.0 | 0.9 | 缺省 | 多个 | text | true | 无 | 「sse_done」 |
| OC-10 | enabled | 1.0 | 1.0 | 2048 | 多个 | text | true+usage | auto | 「非空」 |
| OC-11 | enabled | 1.0 | 0.5 | 缺省 | 缺省 | json_object | false | auto（约束调整：原具名） | 「JSON 字段 path=intro 非空」 |
| OC-12 | enabled | 1.0 | 0.9 | 1 | 单个 | 缺省 | true | 无 | 「finish_reason=length」+「sse_done」 |
| OC-13 | enabled | 0.0 | 1.0 | 1 | 缺省 | text | true | 无 | 「非空」（temperature 在 thinking 下无效果，行为不敏感冒烟） |
| OC-14 | enabled | 0.0 | 0.5 | 2048 | 单个 | json_object | false | auto | 「JSON 字段 path=intro 非空」 |
| OC-15 | enabled | 0.0 | 0.9 | 缺省 | 多个 | 缺省 | true+usage | auto | 「sse_done」（usage 末块为平台盲区，见速查表注） |
| OC-16 | enabled | 2.0 | 1.0 | 2048 | 单个 | text | true+usage | 无（约束调整，原 H=3） | 「非空」 |
| OC-17 | enabled | 2.0 | 0.5 | 1 | 多个 | json_object | false | auto（约束调整：原具名） | 「JSON 字段 path=intro 非空」 |
| OC-18 | enabled | 2.0 | 0.9 | 缺省 | 缺省 | 缺省 | true | auto | 「sse_done」 |

补充用例（正交外，语义专项）：
- OC-S1：A=enabled + effort=max → max_tokens 上限语义变化（128K），缺省可正常响应
- OC-S2：A=enabled + reasoning_effort=none ≡ disabled（等价类合并验证）
- OC-S3：top_p=0.1（<0.95 钳制区下限附近）→ 200 正常
- OC-S4：frequency_penalty=1.5、presence_penalty=-1（已废弃参数）→ 预期 200 且无效果（兼容性）

---

## 四、无效等价类用例（单缺陷法，其余参数全有效缺省）

> 预期码依据官方语义：**格式/结构错=400，参数值非法=422**；个别条目官方未明确到具体码，
> 首轮断言用 `http_status ∈ {400,422}`，实测后收敛为精确值（平台 http_status 关系支持收敛）。

| 编号 | 缺陷参数 | 取值（单缺陷） | 预期 HTTP | 预期要点（断言建议） |
|---|---|---|---|---|
| IC-01 | model | 缺失 | 400 | error.message 含 model |
| IC-02 | model | "deepseek-chat"（已下线旧名） | 400 | error 含 model not exist 类语义 |
| IC-03 | model | ""（空串） | 400 | — |
| IC-04 | model | 123（数字类型） | 400/422 | 类型校验 |
| IC-05 | model | "no-such-model"（不存在名） | 400 | 同 IC-02 |
| IC-06 | messages | 缺失 | 400 | — |
| IC-07 | messages | []（空数组） | 400 | — |
| IC-08 | messages | role="invalid"（非法角色） | 400 | — |
| IC-09 | messages | tool 消息缺 tool_call_id | 400 | — |
| IC-10 | messages | content=123（数字类型） | 400 | — |
| IC-11 | temperature | 2.1（越上界） | 422/400 | — |
| IC-12 | temperature | -1（越下界） | 422/400 | — |
| IC-13 | temperature | "hot"（字符串） | 422/400 | — |
| IC-14 | top_p | 1.5（越上界） | 422/400 | — |
| IC-15 | top_p | 0（违反 >0） | 422/400 | — |
| IC-16 | max_tokens | 0 | 422/400 | — |
| IC-17 | max_tokens | -5（负数） | 422/400 | — |
| IC-18 | max_tokens | 393217（超 384K 上限） | 400/422 | — |
| IC-19 | stop | 123（数字类型） | 422/400 | — |
| IC-20 | stop | 17 个序列（超 16 上限） | 422/400 | — |
| IC-21 | response_format | {"type":"yaml"}（非法 type） | 422/400 | — |
| IC-22 | response_format | json_object 但 prompt 无 json 指令 | 400 | 官方明说；固定模板用例需去掉 json 指令 |
| IC-23 | stream_options | {"include_usage":true} 但未开 stream | 400 | 官方明说 |
| IC-24 | thinking | {"type":"ultra"}（非法枚举） | 422/400 | — |
| IC-25 | thinking+tool_choice | thinking 开 + tool_choice="required" | 400 | 官方明说（冲突类） |
| IC-26 | tools | 两个同名 function（重名） | 422/400 | — |
| IC-27 | tools | function.name 含空格/中文 或 >128 字符 | 422/400 | — |
| IC-28 | tool_choice | 具名指向未定义函数 | 422/400 | — |
| IC-29 | tool_choice | "auto" 但未配 tools | 422/400 | — |
| IC-30 | top_logprobs | 21（越上界） | 422/400 | — |
| IC-31 | top_logprobs | 2 但未配 logprobs=true | 422/400 | — |
| IC-32 | user_id | 含空格/中文（非法字符集） | 422/400 | — |
| IC-33 | user_id | 513 字符（超长） | 422/400 | — |
| IC-34 | 请求体 | 非法 JSON 文本（结构破坏） | 400 | 平台用 `_raw_body` 控制键发送原始文本 |

边界补充（有效但贴边，属等价类边界验证，可并入 OC 或单列）：
- BE-01 max_tokens=393216（上界本身）→ 200；BE-02 stop 恰好 16 个 → 200；
- BE-03 top_logprobs=20 → 200；BE-04 user_id 恰好 512 字符合法字符 → 200

---

## 断言条件速查（低代码芯片 × 引擎键 × 语义）

| 芯片 | 参数 | 引擎键 | 语义 | 生效范围 |
|---|---|---|---|---|
| http_status | 关系 ==/!=/≥/≤/∈ + 状态码 | http_status | 预期状态码；**配置即进入预期错误模式**（4xx/5xx 不重试、错误体作为被测对象） | 全部 |
| JSON 字段 | path + ==/!=/∈/包含/长度/类型/非空 + 值 | json | content 剥代码围栏后 JSON 的字段校验；空规格 {} = 仅校验可解析 | 成功响应 |
| 包含关键词 | 关键词 + ☐忽略大小写 | contains / ignore_case | content 子串包含 | 成功响应 |
| 不含关键词 | 关键词 | not_contains | content 不得含该子串 | 成功响应 |
| 非空 | 无 | nonempty | content 非空白字符串 | 成功响应 |
| no_leak 无泄漏 | 无 | no_leak | 内部标签/提示词泄漏检测 | 成功响应 |
| 无乱码 | 无 | no_garbled | 乱码/坏编码检测 | 成功响应 |
| 无复读 | 无 | no_repeat | 复读检测 | 成功响应 |
| finish_reason | stop/length/tool_calls/content_filter | finish_reason | 响应结束原因 | 成功响应 |
| sse_done | 无 | sse_done | 流式以 [DONE] 正常收尾 | stream:true |
| 最少块数 | ≥N | chunks_min | SSE chunk 数下限（证明真分块） | stream:true |
| 指定工具被调用 | 函数名 | tool | 校验模型发起了该函数的 tool_call | 成功响应（FC） |
| 工具参数包含 | 文本 | tool_args_contains | tool_call arguments 含该子串 | 成功响应（FC） |
| 不发起工具调用 | 无 | no_tool | 模型不得发起任何工具调用 | 成功响应 |
| 错误体字段 | path + ==/包含/长度/类型/非空 | resp | 错误响应 body 的字段校验（受「忽略大小写」影响）；**仅预期错误模式（配 http_status）生效** | 预期错误模式 |

> 引擎断言键全集 = 芯片全集（15 芯片覆盖 16 键；ignore_case 为包含关键词的附属开关）。
> 平台盲区（引擎暂无对应断言）：成功响应的**外层字段**（usage.total_tokens、model 回显、
> reasoning_content 非空）——resp 现仅错误模式；此类校验先以 http_status+content 断言落地。

## 五、平台落地映射（断言能力对照）

| 校验点 | 平台断言（现能力） | 说明 |
|---|---|---|
| HTTP 状态码 | `http_status` 关系 == / != / ∈ / ≥ / ≤ | 无效用例首轮 `∈ {400,422}`，实测后收敛精确值 |
| 错误体结构 | `错误体字段` path=error.type/message 关系 包含/== | 大小写敏感；关键词以首轮实测报文为准 |
| 成功回复非空 | `非空` | 全部 OC 用例默认携带 |
| 正常收尾 | `finish_reason == stop`（length 用例除外） | OC-02/05/12 断 finish_reason=length |
| 流式形态 | `sse_done` + `最少块数 ≥2` | G=2/3 用例携带 |
| JSON 输出 | `JSON 字段`（content 剥围栏后可解析 + 字段校验） | F=json_object 用例 |
| 模型名覆盖 | 行卡「对话承载」+ params.model | 任务选预制模型，params.model 覆盖（已验证生效） |
| input 承载 | 行卡「对话承载=input」 | 官方 /chat/completions 参数表**无** input 字段（网关/兼容层扩展），预期按实际网关行为标注 |
| 原始坏报文 | params `{"_raw_body": "非法文本"}` | IC-34 专用（引擎控制键） |
| 外层字段（如 reasoning_content、usage 非 error 模式） | **暂无对应断言**（平台盲区） | resp 断言现仅作用于错误模式 _body；该类校验先以 http_status 冒烟落地，字段级待引擎 resp 全模式扩展 |

优先级建议：P0 = OC-01/06/18 + IC-01/02/06/07（冒烟，一轮跑通管线）；
P1 = 全部 OC（正交 18 条）；P2 = 全部 IC（无效等价类）；P3 = BE 边界 + OC-S 兼容专项。

---

## 六、落地建议

1. 一个脚本挂多变体：`复制此行` + 承载/params 微调（同因子对的多变体同脚本）；
   无效类按参数分组建脚本（model 组 / 采样组 / 输出控制组 / 流式组 / thinking 组 / 工具组），
   归到 `inference_api` 场景的「API 契约」类型下。
2. 全部用例挂 version 标注，跑完自动归档，大屏看参数维度的劣化趋势。
3. 首轮只跑 P0 校准预期码与错误体结构 → 回填本表「预期要点」→ 再放量 P1/P2。
