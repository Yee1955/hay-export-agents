# What went wrong (and how it was fixed)

Real problems hit while building this, in order.

1. **The MCP SDK API had changed.** `from mcp.server.fastmcp import FastMCP` failed: in MCP SDK 2.x
   FastMCP was renamed `MCPServer`. Fixed the import and pinned `mcp==2.3.0`.

2. **Codex blocked every MCP call.** With approvals set to "deny all", the first tool call failed:
   "MCP tool call requires approval, but approval policy is never". The agent then told the user it
   couldn't check stock. Fix: set `default_tools_approval_mode = "approve"` on the hay server only,
   because the real approval checks live in our gate, not in Codex.

3. **The agent could see my personal ChatGPT apps.** Asking the agent to list its tools showed
   Supabase, Figma, Canva and other apps connected to the ChatGPT account, plus image generation and
   sub-agents. A hay procurement agent must not reach any of that. Fix: switch off `apps`, `plugins`,
   `shell_tool`, `unified_exec`, browser, multi-agent and more (`LOCKED_FEATURES`), then tested by asking
   the agent to read `/etc/hostname` with any tool it had. It couldn't; only the hay tools worked.

4. **A refused approval vanished from the audit log.** The wrong-manager refusal was logged inside the
   same transaction that was then rolled back, so the report said "0 blocked actions". Fix: log the
   refusal after the transaction ends, and a test that the denied row is kept.

5. **The lost-reply test printed a full stack trace.** The simulated fault was working, but the MCP
   server's traceback went to the screen. Fix: server logs go to `hay-server.log`; the screen says
   "No reply from the server. Run the same command again to recover." The lost reply is now also an
   audit row (`reply_lost`).

6. **A long task title was cut off silently.** The CEO agent wrote a 130-character title and the server
   trimmed it to 120 without telling anyone. Silent changes are worse than refusals. Fix: titles over
   100 characters are rejected with the reason, and the agent rewrites them.

7. **The recorded replay broke after fix 6.** Replay re-sent the old long title and the new rule
   refused it. That's the gate doing its job, but it meant recordings must be refreshed whenever a rule
   changes. Re-recorded both scenarios (500 t and 450 t).
