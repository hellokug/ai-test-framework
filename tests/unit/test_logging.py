"""build_web_log_config（serve 落盘日志配置）：dictConfig 可用、业务与服务器
日志按来源分文件（aite.log / uvicorn.log）互不混写。

dictConfig 会全局替换 logger 状态，测试后必须还原各 logger 的
handlers/level/propagate，否则影响同进程后续依赖 caplog 的测试。
"""

import logging
import logging.config


class TestBuildWebLogConfig:
    def test_dict_config_splits_app_and_server_logs(self, tmp_path):
        from aite.utils.logging import APP_LOG_NAME, SERVER_LOG_NAME, build_web_log_config

        cfg = build_web_log_config(tmp_path)
        assert cfg["loggers"]["aite"]["handlers"] == ["default", "file_app"]
        assert cfg["loggers"]["uvicorn.access"]["handlers"] == ["access", "file_server"]
        assert cfg["handlers"]["file_app"]["filename"] == str(tmp_path / APP_LOG_NAME)
        assert cfg["handlers"]["file_server"]["filename"] == str(tmp_path / SERVER_LOG_NAME)

        logging.config.dictConfig(cfg)
        try:
            logging.getLogger("aite.web.app").warning("业务日志验证 %s", "ok")
            logging.getLogger("uvicorn.error").info("服务器日志验证 %s", "ok")
        finally:
            for name in ("aite", "uvicorn", "uvicorn.access"):
                lg = logging.getLogger(name)
                lg.handlers.clear()
                lg.setLevel(logging.NOTSET)
                lg.propagate = True
        app_text = (tmp_path / APP_LOG_NAME).read_text(encoding="utf-8")
        server_text = (tmp_path / SERVER_LOG_NAME).read_text(encoding="utf-8")
        assert "业务日志验证 ok" in app_text and "aite.web.app" in app_text
        assert "服务器日志验证 ok" in server_text
        assert "uvicorn" not in app_text and "业务" not in server_text
