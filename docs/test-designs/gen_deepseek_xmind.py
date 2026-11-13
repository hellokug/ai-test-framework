# -*- coding: utf-8 -*-
"""生成《DeepSeek 关键 API 参数测试设计》.xmind（XMind 2020+/ZEN 格式）。

规格基准: https://api-docs.deepseek.com (create-chat-completion / error_codes)
用法: python gen_deepseek_xmind.py
"""
import json
import uuid
import zipfile
from pathlib import Path

OUT = Path(__file__).parent / "DeepSeek关键API参数测试设计.xmind"


def n(title, method=None, children=None):
    """节点: title 标题; method 设计方法标签; children 子节点列表"""
    return {"t": title, "m": method, "c": children}


def topic(node):
    t = {
        "id": uuid.uuid4().hex,
        "class": "topic",
        "title": node["t"],
        "structure": "org.xmind.ui.map.unbalanced",
    }
    if node["m"]:
        t["labels"] = node["m"] if isinstance(node["m"], list) else [node["m"]]
    if node["c"]:
        t["children"] = {"attached": [topic(c) for c in node["c"]]}
    return t


TREE = n("DeepSeek 关键 API 参数测试设计", None, [
    n("01 认证与接入", None, [
        n("有效 key + 正确 base_url(/v1/chat/completions) → 200", "等价类·有效"),
        n("无 Authorization 头 → 401 Authentication Fails", "错误推测"),
        n("错误 key → 401 Authentication Fails", "等价类·无效"),
        n("key 带前后空格 / 空字符串 → 401", "错误推测"),
        n("路径写错 /v1/chat/completion → 404", "错误推测"),
        n("Content-Type 非 JSON / 请求体非法 JSON → 400 Invalid Format", "错误推测"),
        n("欠费账号调用 → 402 Insufficient Balance", "等价类·无效"),
    ]),
    n("02 model 模型选择", None, [
        n("deepseek-flash → 200", "等价类·有效"),
        n("deepseek-v4-pro → 200", "等价类·有效"),
        n("model 缺失 → 422 Invalid Parameters", "等价类·无效"),
        n("不存在的模型 deepseek-v99 → 400/422 Model Not Exist", "等价类·无效"),
        n("类型错误 model=123 / null → 400", "错误推测"),
        n("大小写变体 DeepSeek-Flash → 拒绝(严格匹配)", "错误推测"),
        n("历史模型名 deepseek-chat / deepseek-reasoner → 别名兼容或报错", "错误推测"),
    ]),
    n("03 messages 消息体", None, [
        n("最小合法: 单条 user 消息 → 200", "等价类·有效"),
        n("system 设定 + user → system 约束生效", "等价类·有效"),
        n("多轮 user/assistant 交替 → 上下文连贯、指代正确", "场景法"),
        n("tool 角色 + 对应 tool_call_id 回填 → 结果被采纳", "场景法"),
        n("messages=[] 空数组 → 400 (minItems=1)", "边界值"),
        n("messages 字段缺失 → 400/422", "等价类·无效"),
        n("非法 role: \"admin\" → 400", "等价类·无效"),
        n("content 类型错误(数字/null) → 400", "错误推测"),
        n("content=\"\" 空字符串 → 行为确认(空输出或 400)", "边界值"),
        n("重复连续同 role(user,user) → 兼容性确认", "错误推测"),
        n("输入超上下文上限(input+max_tokens>context) → 400 超限报错", "边界值"),
        n("emoji / 多语言 / 换行 / 生僻字符 → 正常返回不乱码", "错误推测"),
    ]),
    n("04 思考模式 thinking / reasoning_effort", None, [
        n("thinking={type:enabled} → 返回 reasoning_content", "等价类·有效"),
        n("thinking={type:disabled} → 无 reasoning_content", "等价类·有效"),
        n("缺省不传 → 各模型默认行为确认", "等价类·有效"),
        n("thinking 对象缺 type 字段 → 400 (文档: type 必填)", "等价类·无效"),
        n("非法 type: \"on\" / \"off\" → 400", "等价类·无效"),
        n("reasoning_effort=none → 等效关闭思考", "等价类·有效"),
        n("reasoning_effort=low / high → 思考长度与耗时可观测差异", "等价类·有效"),
        n("reasoning_effort=max → thinking 输出默认上限升至 128K", "边界值"),
        n("OpenAI 别名映射: minimal→low, medium/xhigh→high", "契约断言"),
        n("thinking.enabled 与 reasoning_effort=none 同传 → 冲突处理(优先级/400)", "判定表"),
        n("思考模式下 temperature 不生效: temp=0 vs 2 输出分布对比", "判定表"),
        n("思考模式 max_tokens 默认 64K(非思考 8K) → 契约确认", "契约断言"),
    ]),
    n("05 采样控制 temperature / top_p", None, [
        n("temperature 默认 1 → 正常", "等价类·有效"),
        n("temperature=0 → 非思考模式下确定性增强", "边界值"),
        n("temperature=2 上界 → 200", "边界值"),
        n("temperature=-0.1 → 400", "边界值·越界"),
        n("temperature=2.1 → 400", "边界值·越界"),
        n("temperature=\"0.7\" 字符串 → 400", "错误推测"),
        n("top_p=1 → 200", "边界值"),
        n("top_p 取值范围 >0–1: top_p=0 是否合法需确认", "边界值"),
        n("top_p=1e-7 极小值 → 合法, 输出趋最高概率词", "边界值"),
        n("top_p=1.1 / 负数 → 400", "边界值·越界"),
        n("思考模式钳制: flash 传 top_p=0.5 → 被钳到 0.95(文档行为)", "契约断言"),
        n("v4-pro 思考模式 top_p 固定(带工具 0.95 / 无工具 1.0) → 传值无效性", "判定表"),
        n("temperature × top_p 同调 vs 单调 → 效果对比(官方不建议同调)", "判定表"),
    ]),
    n("06 输出长度与停止 max_tokens / stop", None, [
        n("max_tokens: 不传 → 默认 8K(非思考)/64K(思考)", "契约断言"),
        n("max_tokens: =1 → 仅 1 token, finish_reason=length", "边界值"),
        n("max_tokens: =393216(384K) 上界 → 200(配额允许时)", "边界值"),
        n("max_tokens: =393217 → 400/422", "边界值·越界"),
        n("max_tokens: =0 → 400", "边界值"),
        n("max_tokens: =-1 → 400", "等价类·无效"),
        n("max_tokens: =\"100\" 字符串 → 400", "错误推测"),
        n("max_tokens: =1.5 小数 → 400", "错误推测"),
        n("max_tokens: input+max_tokens>context → 400", "边界值"),
        n("max_tokens: 长答案+小上限 → 截断且 finish_reason=length", "状态断言"),
        n("stop: 单字符串命中 → 提前停止且不含停止词, finish_reason=stop", "等价类·有效"),
        n("stop: 数组多序列 → 任一命中即停", "等价类·有效"),
        n("stop: 16 个序列上界 → 200", "边界值"),
        n("stop: 17 个序列 → 400", "边界值·越界"),
        n("stop: 不存在的停止词 → 完整生成不受影响", "等价类·有效"),
        n("stop: 空字符串 \"\" → 400 或忽略", "错误推测"),
        n("stop: 首个 token 即命中 → content 为空但 200", "边界值"),
    ]),
    n("07 流式输出 stream / stream_options", None, [
        n("stream=false → 单 JSON 响应体", "等价类·有效"),
        n("stream=true → SSE: chunk 序列 + data: [DONE] 终止", "等价类·有效"),
        n("增量拼接语义 == 非流式整体结果", "契约断言"),
        n("stream_options={include_usage:true} → 末个 chunk 含 usage", "等价类·有效"),
        n("include_usage=false → 末 chunk 不含 usage", "等价类·有效"),
        n("stream_options 未开 stream → 400 (文档明确)", "判定表"),
        n("stream=true × tool_calls → 按 index 增量拼接 arguments", "场景法"),
        n("客户端中途断连 → 服务端无异常、usage 合理", "场景法"),
        n("慢速消费(背压) → chunk 不乱序、不丢失", "场景法"),
    ]),
    n("08 输出格式 response_format", None, [
        n("{\"type\":\"text\"} → 正常文本", "等价类·有效"),
        n("{\"type\":\"json_object\"} + prompt 明示 JSON → 输出可被 json 解析", "等价类·有效"),
        n("json_object 但 prompt 未提示 JSON → 输出为空(空格替换, 官方注记)", "错误推测"),
        n("非法 type: \"xml\" → 400", "等价类·无效"),
        n("大小写 \"JSON_OBJECT\" → 400", "错误推测"),
        n("直接传字符串 \"json_object\" 非对象 → 400", "错误推测"),
        n("{\"type\":\"json_schema\",...} → 400(不支持)或确认支持度", "错误推测"),
        n("json_object × stream → 分片拼接后仍为完整合法 JSON", "判定表"),
        n("json_object × tools → 工具参数仍符合 schema", "判定表"),
    ]),
    n("09 工具调用 tools / tool_choice", None, [
        n("单 tool + 诱导提问 → 返回 tool_calls", "等价类·有效"),
        n("多 tools → 选中语义正确的工具", "等价类·有效"),
        n("带 tools 未传 tool_choice → 默认 auto", "契约断言"),
        n("tool_choice=\"none\" → 不产生 tool_calls", "等价类·有效"),
        n("tool_choice=\"none\" 且无 tools → 行为确认", "等价类·有效"),
        n("tool_choice=\"required\" → 必产生 tool_calls", "等价类·有效"),
        n("指定函数名 {\"function\":{\"name\":..}} → 强制调用该函数", "等价类·有效"),
        n("思考模式 × required/named → 400 (文档明确不支持)", "判定表"),
        n("函数名重复 → 400 (names unique)", "边界值"),
        n("tools=[] 空数组 → 400 或忽略", "边界值"),
        n("function 缺 parameters / 非法 JSON Schema → 400 或降级行为", "错误推测"),
        n("strict 模式(Beta) → 参数严格符合 schema", "场景法"),
        n("多轮闭环: 发起调用 → 回填结果 → 生成最终答案", "场景法"),
        n("模型生成 arguments 非法 JSON → 客户端容错与重试策略", "错误推测"),
    ]),
    n("10 日志概率 logprobs / top_logprobs", None, [
        n("logprobs=true → 响应含 logprobs 内容", "等价类·有效"),
        n("top_logprobs=0 下界 → 200", "边界值"),
        n("top_logprobs=20 上界 → 200", "边界值"),
        n("top_logprobs=21 → 400", "边界值·越界"),
        n("top_logprobs 给出但 logprobs=false → 400 (依赖开启)", "判定表"),
        n("top_logprobs=-1 / \"5\" → 400", "错误推测"),
    ]),
    n("11 已废弃与不支持参数", None, [
        n("frequency_penalty=2 → 200 但不生效(官方 deprecated) → 用输出重复度验证", "判定表"),
        n("presence_penalty=2 → 同上验证无效性", "判定表"),
        n("传超范围值 -5 → 是否仍静默接受确认", "错误推测"),
        n("seed / logit_bias / n / parallel_tool_calls → 400 或忽略(实测归档)", "错误推测"),
        n("风险提示: \"接受但不生效\"是隐性契约, 需防静默回归", "说明"),
    ]),
    n("12 参数组合与交互", None, [
        n("因子: model(2)×thinking(2)×stream(2)×response_format(2)×tools(2)×temperature(0/1/2) → 全组合爆炸, pairwise 压缩至 ~12 组", "正交组合"),
        n("必测: 思考模式 + tools + required/named → 400", "判定表"),
        n("必测: stream + json_object 分片拼接合法性", "判定表"),
        n("必测: stream + tool_calls 增量拼接", "判定表"),
        n("必测: 思考模式 + max_tokens 默认值变化(64K)", "判定表"),
        n("必测: v4-pro 思考 + tools 的 top_p 固定值", "判定表"),
        n("冒烟回归集: 8–12 组核心组合进 CI", "说明"),
    ]),
    n("13 异常、稳定性与安全", None, [
        n("429 Rate Limit → 错误体结构 + 退避重试策略", "场景法"),
        n("500 Server Error → 幂等重试安全(重试不重复计费观测)", "场景法"),
        n("503 Overloaded → 重试与熔断", "场景法"),
        n("402 Insufficient Balance → 明确报错可用性", "等价类·无效"),
        n("超大 payload(逼近上下文上限) → 4xx 而非 5xx", "边界值"),
        n("并发 10/50/100 路 → 成功率、P95 时延", "场景法"),
        n("请求超时与断网重试 → 客户端行为约定", "场景法"),
        n("敏感内容请求 → finish_reason=content_filter", "状态断言"),
        n("finish_reason=insufficient_system_resource / aborted → 可观测与告警", "状态断言"),
        n("key 泄露轮换: 旧 key 立即失效 → 401", "错误推测"),
    ]),
    n("14 通用契约断言(所有用例的后置条件)", None, [
        n("HTTP 状态码与官方错误表一致(400/401/402/422/429/500/503)", "契约断言"),
        n("错误响应体结构: error.type/message/code 可机读", "契约断言"),
        n("usage: prompt_tokens/completion_tokens>0 且随输入规模单调", "契约断言"),
        n("finish_reason ∈ {stop,length,content_filter,tool_calls,insufficient_system_resource,aborted}", "契约断言"),
        n("响应 id 唯一、model 字段回显请求值", "契约断言"),
        n("流式: SSE 格式合法、以 [DONE] 结束、无半截 JSON", "契约断言"),
    ]),
    n("15 测试设计方法总览", None, [
        n("等价类划分: 每个参数先分有效/无效类, 无效类再细分缺失/越界/类型错 → 覆盖 01–13", "等价类"),
        n("边界值分析: 数值参数取 min、min+1、默认、max-1、max、越界六点 → temperature/top_p/max_tokens/top_logprobs/stop 条数", "边界值"),
        n("判定表法: 条件组合→预期动作, 用于参数交互(thinking×tool_choice、stream_options×stream、废弃参数生效性)", "判定表"),
        n("正交/Pairwise: 多参数组合爆炸时压缩到 ~12 组核心组合 → 分支 12", "正交组合"),
        n("场景法: 多轮对话、工具调用闭环、流式断连背压等端到端流程 → 分支 03/07/09/13", "场景法"),
        n("错误推测: 类型错填、大小写、空值、历史别名、隐性废弃行为", "错误推测"),
        n("状态/契约断言: finish_reason、usage、SSE 流状态作为通用后置条件 → 分支 14", "契约断言"),
    ]),
])


def build():
    sheet = {
        "id": uuid.uuid4().hex,
        "class": "sheet",
        "title": "DeepSeek 关键 API 参数测试设计",
        "rootTopic": topic(TREE),
    }
    content = [sheet]
    metadata = {"creator": {"name": "aite", "version": "1.0"}, "dataStructureVersion": "2"}
    manifest = {"file-entry": [
        {"entry": "content.json", "media-type": "application/json"},
        {"entry": "metadata.json", "media-type": "application/json"},
        {"entry": "manifest.json", "media-type": "application/json"},
    ]}
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("content.json", json.dumps(content, ensure_ascii=False))
        z.writestr("metadata.json", json.dumps(metadata))
        z.writestr("manifest.json", json.dumps(manifest))
    print(f"written: {OUT} ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    build()
