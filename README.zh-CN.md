# Claude Review

Claude Review 是一个非官方本地 Codex 插件，用于把高上下文、低推理、只读的证据收集任务委派给 Claude Code。Codex 始终负责判断、复核和所有写操作。

适用场景包括长日志分类、大文件信息提取、批量截图证据审查和按清单比较。架构、生产操作、安全与权限、资金、业务规则和代码修改不得委派。

## 安全与可靠性

- Claude Code 只获得 `Read`、`Glob`、`Grep` 工具。
- Safe mode 会禁用 Claude 自定义项、Hooks、插件和 MCP 服务。
- 必须提供附件路径；服务器会验证路径位于工作目录内，并在调用前限制数量和总大小。
- 强制执行单次预算、滚动 24 小时预算、并发、队列、结果、日志、保留周期和硬超时限制。
- 任务状态持久化，可查询和取消。
- 自动委派必须先通过至少 50 次、成功率不低于 95% 的真实基准测试。

附件列表是经过验证的规模与指令边界，但不是工作目录内部的操作系统级沙箱。不要把包含范围外密钥的目录作为工作目录。

## 运行要求

- Python 3.11 或更高版本，并能通过 `python` 调用。
- Claude Code CLI 2.1 或更高版本，并能通过 `claude` 调用且已经登录。
- 支持本地市场插件和 stdio MCP 的 Codex 或 ChatGPT 桌面环境。

本仓库发布的是本地插件，不等同于提交到通用公共插件目录；以当前架构进入通用目录需要经过审核的公共 HTTPS MCP 服务。

## 安装

将仓库克隆到本地市场的插件目录。例如默认个人市场可以采用：

```text
~/.agents/plugins/
├── marketplace.json
└── plugins/
    └── claude-review/
```

在 `marketplace.json` 的 `plugins` 数组中增加：

```json
{
  "name": "claude-review",
  "source": {
    "source": "local",
    "path": "./plugins/claude-review"
  },
  "policy": {
    "installation": "AVAILABLE",
    "authentication": "ON_INSTALL"
  },
  "category": "Productivity"
}
```

随后执行：

```bash
codex plugin add claude-review@personal
```

安装后新建聊天，使工具和技能重新加载。

## 配置

默认通过 PATH 查找 `python` 和 `claude`。模型默认继承 Claude Code 配置；如需可复现运行，可通过 `CLAUDE_REVIEW_MODEL` 明确指定。预算、并发、超时、日志和状态目录等全部配置项见 [README.md](README.md) 与 [`.mcp.json`](.mcp.json)。

## 验证

以下检查完全离线，不会调用 Claude 或产生模型费用：

```bash
python scripts/release_audit.py
python -m py_compile scripts/server.py scripts/benchmark.py tests/test_server.py
python -m unittest discover -s tests -p "test_*.py" -v
```

真实基准测试会产生模型费用，必须显式提供成本上限；示例见英文 README。

## 隐私与费用

Claude Code 会把读取的内容发送给本机 Claude Code 所配置的服务商。插件不捆绑凭据，也无法改变服务商的数据保留和计费政策。委派敏感资料前请阅读 [PRIVACY.md](PRIVACY.md) 及对应服务商条款。

## 独立声明

本项目使用 MIT 许可证，是独立的非官方项目，与 OpenAI、Anthropic 不存在隶属、认可或赞助关系。Claude、Claude Code、ChatGPT 和 Codex 是其各自权利人的商标。
