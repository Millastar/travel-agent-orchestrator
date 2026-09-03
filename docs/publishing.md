# GitHub 发布说明

## 应上传

提交 `.gitignore` 允许的全部内容，重点包括：

- `src/travel_agent_orchestrator/` 全部源码；
- `tests/`、`docs/`、`scripts/` 与 `.github/workflows/`；
- `README.md`、`LICENSE`、`pyproject.toml`；
- `requirements.txt`、`requirements-web.txt`、`requirements-dev.txt`；
- `compose.yaml`、`.gitignore`、`.env.example`。

## 严禁上传

- `.env` 和任何真实 API Key；
- `.venv/`、`venv/`、`__pycache__/`、`*.pyc`；
- `var/`、日志、真实用户消息、执行轨迹和本地评测输出；
- `.pytest_cache/`、`.ruff_cache/`、覆盖率文件；
- `.idea/`、`.vscode/`；
- Docker 卷、本地 PostgreSQL/Redis 数据或数据库导出；
- 真实银行卡、支付凭据、Cookie、token 或用户身份数据。

## 发布前检查

```powershell
.\scripts\check.ps1 -PythonPath "D:\Pycharm\movefromC\envs\project3\python.exe"
git status --short
git check-ignore .env var/logs/example.log src/travel_agent_orchestrator/__pycache__/x.pyc
```

再人工检查：README 中没有个人密钥或内网地址，预览图没有真实用户数据，所有“预订/支付/退款”均明确标为沙箱行为。
