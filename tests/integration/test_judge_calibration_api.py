"""判官校准端到端（HTTP 层）：rubric 资产 CRUD + 校准任务 run→succeeded→校准卡。"""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


class TestJudgeCalibrationApi:
    @pytest.fixture()
    def client(self, tmp_path):
        from fastapi.testclient import TestClient

        from aite.web.app import create_app
        app = create_app(storage_url=f"sqlite+aiosqlite:///{tmp_path / 'web.db'}")
        with TestClient(app) as c:
            yield c

    @pytest.fixture()
    def judge_url(self):
        """本地桩判官：第 4 行（0 基 i==3）判 0，其余判 1。"""

        class H(BaseHTTPRequestHandler):
            count = 0

            def do_POST(self):
                i = H.count
                H.count += 1
                score = 0 if i == 3 else 1
                body = json.dumps({"choices": [
                    {"message": {"content": f"理由\nSCORE: {score}"}}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        H.count = 0
        server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        yield f"http://127.0.0.1:{server.server_address[1]}/v1"
        server.shutdown()
        server.server_close()

    @pytest.fixture()
    def calset(self, tmp_path):
        p = tmp_path / "cal.jsonl"
        humans = [1, 0, 1, 0]
        p.write_text("\n".join(
            json.dumps({"question": f"Q{i}", "model_output": f"A{i}",
                        "reference_answer": "G", "human_score": h}, ensure_ascii=False)
            for i, h in enumerate(humans)), encoding="utf-8")
        return p

    def _wait(self, client, task_id, statuses, timeout=30):
        last = None
        for _ in range(timeout * 5):
            last = client.get(f"/api/v1/accuracy/tasks/{task_id}").json()
            if last["status"] in statuses:
                return last
            time.sleep(0.2)
        raise AssertionError(f"任务未到达 {statuses}: {last}")

    def test_rubric_crud(self, client):
        r = client.post("/api/v1/accuracy/rubrics",
                        json={"name": "严格二分", "content": "按标准打分 {question}"})
        assert r.status_code == 201
        rid = r.json()["id"]
        assert client.post("/api/v1/accuracy/rubrics",
                           json={"name": "严格二分", "content": "x"}).status_code == 409
        assert client.get("/api/v1/accuracy/rubrics").json()["count"] == 1
        patched = client.patch(f"/api/v1/accuracy/rubrics/{rid}", json={"content": "新标准"})
        assert patched.status_code == 200
        assert patched.json()["fingerprint"] != r.json()["fingerprint"]
        assert client.delete(f"/api/v1/accuracy/rubrics/{rid}").status_code == 200
        assert client.get(f"/api/v1/accuracy/rubrics/{rid}").status_code == 404

    def test_calibration_task_end_to_end(self, client, judge_url, calset):
        spec = {"task_type": "judge_calibration", "dataset_path": str(calset),
                "judge": {"base_url": judge_url, "model": "stub-judge"},
                "rubric_text": "按标准打分：{question} / {pred} / {gold}"}
        created = client.post("/api/v1/accuracy/tasks",
                              json={"name": "校准冒烟", "spec": spec})
        assert created.status_code in (200, 201)
        task_id = created.json()["id"]
        run = client.post(f"/api/v1/accuracy/tasks/{task_id}/run")
        assert run.status_code == 202
        final = self._wait(client, task_id, {"succeeded", "failed"})
        assert final["status"] == "succeeded", final.get("error")
        result = client.get(f"/api/v1/accuracy/tasks/{task_id}/result").json()["result"]
        assert result["total_cases"] == 4
        rep = result["calibration_report"]
        # human=[1,0,1,0]，桩判官打分 [1,1,1,0]：po=0.75、pe=0.5 → κ=0.5；漏放 1 行
        m = rep["metrics"]
        assert m["agreement"] == 0.75 and m["kappa"] == 0.5
        assert m["miss_rate_漏放"] == 0.25 and m["kill_rate_误杀"] == 0.0
        assert len(rep["rubric_sha256_12"]) == 12
        assert len(rep["disagree_rows"]) == 1

    def test_calibration_task_bad_dataset_fails(self, client, judge_url):
        spec = {"task_type": "judge_calibration", "dataset": "不存在的数据集",
                "judge": {"base_url": judge_url, "model": "stub-judge"},
                "rubric_text": "r"}
        created = client.post("/api/v1/accuracy/tasks", json={"name": "坏数据集", "spec": spec})
        task_id = created.json()["id"]
        client.post(f"/api/v1/accuracy/tasks/{task_id}/run")
        final = self._wait(client, task_id, {"failed"})
        assert "不存在" in final["error"]
