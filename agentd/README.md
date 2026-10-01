# agentd

The AgentLink PC daemon. Single source of truth on the Windows laptop: sessions,
approvals, policy, git diff engine, encryption keys and the relay connection.

Exposes a local HTTP API on `http://127.0.0.1:47800` that the agent hook adapters
(`agentd-cline-hook`, `agentd-codex-hook`) call.

See `../docs/SPEC.md` for the full specification.

## Dev install

```powershell
pip install -e ".[dev]"
agentd doctor
agentd run
```

## Console scripts

| Script | Purpose |
|---|---|
| `agentd` | Daemon CLI (`run`, `status`, `doctor`, `pair`, `install`, `away`, `logs`) |
| `agentd-cline-hook` | Cline `PreToolUse` hook entrypoint |
| `agentd-codex-hook` | Codex `PreToolUse` hook entrypoint |
