"""Judge / safety / agent / reliability metric tests (mock gateway)."""

import pytest

from aite.core.types import (
    AgentTrace,
    AgentTraceStep,
    EvalContext,
    TargetResponse,
    TestCase,
    Timings,
)
from aite.metrics.agent_metrics import (
    AgentEfficiency,
    TaskCompletion,
    ToolArgCorrectness,
    ToolCallCorrectness,
)
from aite.metrics.judge import pairwise as pairwise_mod
from aite.metrics.judge.base import ScoreJudge, _fingerprint
from aite.metrics.judge.multi import MultiJudge
from aite.metrics.judge.pairwise import PairwiseJudge
from aite.metrics.llm_based import DEFAULT_RUBRIC, looks_like_refusal, parse_json_loose
from aite.metrics.reliability import (
    PerturbationRobustness,
    RepeatConsistency,
    Stability,
)
from aite.metrics.safety import (
    PIILeakage,
    PromptInjectionResistance,
    Toxicity,
)
from tests.fixtures.mock_components import (
    COMPLY_TEXTS,
    REFUSAL_TEXTS,
    MockGateway,
)


def ctx_with(script=None, default='{"score": 8}'):
    gw = MockGateway(script=script, default=default)
    return EvalContext(run_name="t", gateway=gw), gw


class TestJudgeParsing:
    def test_parse_json_plain(self):
        assert parse_json_loose('{"score": 5}') == {"score": 5}

    def test_parse_json_fenced(self):
        assert parse_json_loose('```json\n{"score": 5}\n```') == {"score": 5}

    def test_parse_json_with_prose(self):
        text = 'Sure! Here is my evaluation: {"score": 7, "reasoning": "fine"} hope it helps'
        assert parse_json_loose(text)["score"] == 7

    def test_parse_json_garbage(self):
        assert parse_json_loose("no json at all") is None

    def test_refusal_detection(self):
        assert looks_like_refusal(REFUSAL_TEXTS[0])
        assert looks_like_refusal(REFUSAL_TEXTS[1])
        assert not looks_like_refusal("The capital of France is Paris.")


class TestScoreJudge:
    @pytest.mark.asyncio
    async def test_score_normalized_and_threshold_raw(self):
        judge = ScoreJudge(threshold=7.0, model="j")
        ctx, _ = ctx_with(default='{"score": 9, "reasoning": "great"}')
        res = await judge.judge(case=TestCase(id="c", input="q", expected="a"),
                                response=TargetResponse(output="answer"), ctx=ctx)
        assert res.value == 0.9 and res.raw["score"] == 9.0
        assert res.passed is True

    @pytest.mark.asyncio
    async def test_low_score_fails(self):
        judge = ScoreJudge(threshold=7.0, model="j")
        ctx, _ = ctx_with(default='{"score": 3}')
        res = await judge.judge(case=TestCase(id="c", input="q", expected="a"),
                                response=TargetResponse(output="bad"), ctx=ctx)
        assert res.passed is False

    @pytest.mark.asyncio
    async def test_unparseable_output(self):
        judge = ScoreJudge(model="j")
        ctx, _ = ctx_with(default="not json")
        res = await judge.judge(case=TestCase(id="c", input="q"),
                                response=TargetResponse(output="x"), ctx=ctx)
        assert res.error and res.value == 0.0

    @pytest.mark.asyncio
    async def test_target_error_short_circuits(self):
        judge = ScoreJudge(model="j")
        ctx, gw = ctx_with()
        res = await judge.judge(case=TestCase(id="c", input="q"),
                                response=TargetResponse(error="boom"), ctx=ctx)
        assert res.passed is False and gw.total_calls == 0


class TestPairwiseJudge:
    @pytest.mark.asyncio
    async def test_consistent_win(self):
        judge = PairwiseJudge(model="j", threshold=0.5)
        # AB order says A (candidate first), BA order says B (candidate second)
        ctx, _ = ctx_with(script=['{"winner": "A"}', '{"winner": "B"}'])
        res = await judge.judge(case=TestCase(id="c", input="q", expected="ref answer"),
                                response=TargetResponse(output="candidate answer"),
                                baseline=TargetResponse(output="weak answer"), ctx=ctx)
        assert res.value == 1.0 and res.passed

    @pytest.mark.asyncio
    async def test_position_bias_detected_as_tie(self):
        judge = PairwiseJudge(model="j")
        ctx, _ = ctx_with(script=['{"winner": "A"}', '{"winner": "A"}'])
        res = await judge.judge(case=TestCase(id="c", input="q", expected="ref"),
                                response=TargetResponse(output="cand"),
                                baseline=TargetResponse(output="base"), ctx=ctx)
        assert res.value == 0.5
        assert res.raw["position_bias"] is True

    @pytest.mark.asyncio
    async def test_prompt_version_persisted_in_raw(self):
        judge = PairwiseJudge(model="j")
        ctx, _ = ctx_with(script=['{"winner": "A"}', '{"winner": "B"}'])
        res = await judge.judge(case=TestCase(id="c", input="q", expected="ref"),
                                response=TargetResponse(output="cand"),
                                baseline=TargetResponse(output="base"), ctx=ctx)
        assert res.raw["prompt_version"] == judge.prompt_version

    def test_prompt_version_tracks_prompt_changes(self, monkeypatch):
        before = PairwiseJudge(model="j").prompt_version
        monkeypatch.setattr(pairwise_mod, "PROMPT",
                            pairwise_mod.PROMPT + "\nPenalize verbosity.")
        assert PairwiseJudge(model="j").prompt_version != before


class TestMultiJudge:
    @pytest.mark.asyncio
    async def test_requires_models(self):
        judge = MultiJudge()
        ctx = EvalContext(run_name="t")
        res = await judge.judge(case=TestCase(id="c", input="q"),
                                response=TargetResponse(output="x"), ctx=ctx)
        assert res.error

    def test_prompt_version_tracks_rubric(self):
        default = MultiJudge().prompt_version
        assert default == f"multi-v1(rubric={_fingerprint(DEFAULT_RUBRIC)})"
        wit = MultiJudge(rubric="Score on wit").prompt_version
        brief = MultiJudge(rubric="Score on brevity").prompt_version
        assert wit != brief and wit != default

    @pytest.mark.asyncio
    async def test_ensemble_mean(self):
        judge = MultiJudge(models=["a", "b"], threshold=6.0)
        ctx, _ = ctx_with(script=['{"score": 8}', '{"score": 6}'])
        res = await judge.judge(case=TestCase(id="c", input="q", expected="a"),
                                response=TargetResponse(output="x"), ctx=ctx)
        assert res.raw["scores"] == [8.0, 6.0]
        assert res.value == 0.7 and res.passed
        assert res.raw["prompt_version"] == judge.prompt_version


class TestSafety:
    @pytest.mark.asyncio
    async def test_attack_refused_is_safe(self):
        from aite.metrics.safety import AttackSuccessRate

        m = AttackSuccessRate(threshold=0.5)
        ctx, gw = ctx_with(default='{"outcome": "refused"}')
        res = await m.compute(case=TestCase(id="c", input="evil", metadata={"attack": True}),
                              response=TargetResponse(output="nope"), ctx=ctx)
        assert res.value == 1.0 and res.raw["asr"] == 0.0

    @pytest.mark.asyncio
    async def test_attack_complied_rule_fallback(self):
        from aite.metrics.safety import AttackSuccessRate

        m = AttackSuccessRate()
        ctx = EvalContext(run_name="t")  # no gateway -> heuristic
        res = await m.compute(case=TestCase(id="c", input="evil", metadata={"attack": True}),
                              response=TargetResponse(output=COMPLY_TEXTS[0]), ctx=ctx)
        assert res.value == 0.0

    @pytest.mark.asyncio
    async def test_pii_detected(self):
        m = PIILeakage()
        res = await m.compute(case=TestCase(id="c", input="q"),
                              response=TargetResponse(
                                  output="contact him at john.doe@corp.com please"),
                              ctx=EvalContext(run_name="t"))
        assert res.value == 0.0 and "email" in res.raw["leaks"]

    @pytest.mark.asyncio
    async def test_pii_echoed_from_input_not_flagged(self):
        m = PIILeakage()
        res = await m.compute(case=TestCase(id="c", input="email john@x.com"),
                              response=TargetResponse(output="john@x.com"),
                              ctx=EvalContext(run_name="t"))
        assert res.value == 1.0

    @pytest.mark.asyncio
    async def test_toxicity_lexicon(self):
        m = Toxicity()
        bad = await m.compute(case=TestCase(id="c", input="q"),
                              response=TargetResponse(output="you idiot, shut up"),
                              ctx=EvalContext(run_name="t"))
        assert bad.value < 1.0
        good = await m.compute(case=TestCase(id="c", input="q"),
                               response=TargetResponse(output="have a nice day"),
                               ctx=EvalContext(run_name="t"))
        assert good.value == 1.0

    @pytest.mark.asyncio
    async def test_injection_marker_echo(self):
        m = PromptInjectionResistance()
        res = await m.compute(
            case=TestCase(id="c", input="doc with injection",
                          metadata={"injection": True, "injection_marker": "SECRET-42"}),
            response=TargetResponse(output="ok SECRET-42 leaked"),
            ctx=EvalContext(run_name="t"))
        assert res.value == 0.0


class TestAgentMetrics:
    def trace_response(self, tools):
        return TargetResponse(
            output="done", trace=AgentTrace(steps=[
                AgentTraceStep(type="llm", name="llm"),
                *[AgentTraceStep(type="tool", name=t) for t in tools],
            ]), timings=Timings(total_ms=50))

    @pytest.mark.asyncio
    async def test_tool_call_correctness_ordered(self):
        m = ToolCallCorrectness(threshold=0.9)
        case = TestCase(id="a", input="t", expected={"tools": ["search", "calc"]})
        good = await m.compute(case=case, response=self.trace_response(["search", "calc"]),
                               ctx=EvalContext(run_name="t"))
        assert good.value == 1.0
        swapped = await m.compute(case=case,
                                  response=self.trace_response(["calc", "search"]),
                                  ctx=EvalContext(run_name="t"))
        assert swapped.value < 1.0

    @pytest.mark.asyncio
    async def test_agent_efficiency(self):
        m = AgentEfficiency()
        case = TestCase(id="a", input="t",
                        expected={"tools": ["x"], "expected_steps": 3})
        res = await m.compute(case=case, response=self.trace_response(["x"]),
                              ctx=EvalContext(run_name="t"))
        assert res.value == 1.0  # exact steps, no redundancy

    @pytest.mark.asyncio
    async def test_task_completion_markers(self):
        m = TaskCompletion()
        case = TestCase(id="a", input="t",
                        expected={"success_markers": ["雨", "伞"]})
        res = await m.compute(case=case, response=TargetResponse(output="会下雨，记得带伞"),
                              ctx=EvalContext(run_name="t"))
        assert res.value == 1.0 and res.raw["mode"] == "markers"

    @pytest.mark.asyncio
    async def test_task_completion_llm_mode(self):
        m = TaskCompletion()
        ctx, _ = ctx_with(default='{"completed": true, "progress": 0.9}')
        res = await m.compute(case=TestCase(id="a", input="t", expected=None),
                              response=TargetResponse(output="result"),
                              ctx=ctx)
        assert res.raw["mode"] == "llm" and res.value == 0.9

    @staticmethod
    def arg_trace(tools_with_args):
        steps = [AgentTraceStep(type="llm", name="llm")]
        steps += [AgentTraceStep(type="tool", name=name, input=args)
                  for name, args in tools_with_args]
        return TargetResponse(output="done", trace=AgentTrace(steps=steps))

    @pytest.mark.asyncio
    async def test_tool_arg_correctness_hits_and_misses(self):
        m = ToolArgCorrectness(threshold=0.6)
        case = TestCase(id="a", input="t", expected={"tool_args": {
            "calculator": ["15", "85"], "search": ["存款保险"]}})
        good = await m.compute(
            case=case,
            response=self.arg_trace([
                ("calculator", '{"expr": "15 * 85"}'),
                ("search", '{"query": "存款保险 偿付限额"}')]),
            ctx=EvalContext(run_name="t"))
        assert good.value == 1.0 and good.passed

        partial = await m.compute(
            case=case,
            response=self.arg_trace([
                ("calculator", '{"expr": "15 * 85"}'),
                ("search", '{"query": "LPR 报价"}')]),
            ctx=EvalContext(run_name="t"))
        assert partial.value == pytest.approx(2 / 3)

    @pytest.mark.asyncio
    async def test_tool_arg_correctness_uncalled_tool_counts_miss(self):
        m = ToolArgCorrectness()
        case = TestCase(id="a", input="t",
                        expected={"tool_args": {"send_email": ["accountant"]}})
        res = await m.compute(case=case, response=self.arg_trace(
            [("convert_currency", '{"amount": 2500}')]),
            ctx=EvalContext(run_name="t"))
        assert res.value == 0.0 and res.raw["checks"][0]["hit"] is False

    @pytest.mark.asyncio
    async def test_llm_failure_error_carries_exception_type(self):
        """TimeoutError 等异常 str 为空——error 必须带类型名才可诊断（原则 9）。"""
        class BoomGateway(MockGateway):
            async def complete(self, *args, **kwargs):
                raise TimeoutError()

        res = await TaskCompletion().compute(
            case=TestCase(id="a", input="t"),
            response=TargetResponse(output="x"),
            ctx=EvalContext(run_name="t", gateway=BoomGateway()))
        assert res.value == 0.0 and "TimeoutError" in (res.error or "")

    @pytest.mark.asyncio
    async def test_tool_arg_correctness_requires_config(self):
        res = await ToolArgCorrectness().compute(
            case=TestCase(id="a", input="t", expected=None),
            response=self.arg_trace([]), ctx=EvalContext(run_name="t"))
        assert res.error


class TestReliability:
    def base(self):
        return TestCase(id="r", input="q", expected="a", variant="base")

    def rep(self, text):
        return TestCase(id="r:1", input="q", expected="a", variant="repeat-1"), \
            TargetResponse(output=text, timings=Timings(total_ms=1))

    @pytest.mark.asyncio
    async def test_repeat_consistency(self):
        m = RepeatConsistency()
        res = await m.compute(case=self.base(),
                              response=TargetResponse(output="answer one"),
                              responses=[(self.base(), TargetResponse(output="answer one"))]
                              + [self.rep("answer one") for _ in range(2)],
                              ctx=EvalContext(run_name="t"))
        assert res.value == 1.0

    @pytest.mark.asyncio
    async def test_repeat_inconsistent(self):
        m = RepeatConsistency()
        res = await m.compute(case=self.base(),
                              response=TargetResponse(output="answer one"),
                              responses=[(self.base(), TargetResponse(output="answer one"))]
                              + [self.rep("something else")],
                              ctx=EvalContext(run_name="t"))
        assert res.value == 0.0

    @pytest.mark.asyncio
    async def test_perturbation_robustness(self):
        m = PerturbationRobustness()
        pert = (TestCase(id="r:p", input="q", variant="perturb-typo"),
                TargetResponse(output="answer one"))
        res = await m.compute(case=self.base(),
                              response=TargetResponse(output="answer one"),
                              responses=[(self.base(), TargetResponse(output="answer one")), pert],
                              ctx=EvalContext(run_name="t"))
        assert res.value == 1.0

    @pytest.mark.asyncio
    async def test_stability_needs_variants(self):
        res = await Stability().compute(case=self.base(),
                                        response=TargetResponse(output="x"),
                                        responses=None, ctx=EvalContext(run_name="t"))
        assert res.error
