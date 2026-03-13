# HEARTBEAT.md

## Default rule for this workspace

- On heartbeat wake-up, first check whether there is a pending **user-visible update**.
- If a task finished but the user may not have received the reply, send a short proactive update instead of `HEARTBEAT_OK`.
- If there is meaningful progress, blockage, or a result the user should know, send that update.
- Only reply `HEARTBEAT_OK` when there is truly nothing to report.
