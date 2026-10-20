---
name: codex-session-watchdog
description: Ten-minute scheduled backup routine that detects missed Codex approvals, completion, stalled activity and errors without duplicate reports.
---

# Scheduled routine: Codex watchdog

Create a scheduled routine **every 10 minutes** on the bot that owns Codex work,
using the Codex MCP connector. This is a backup: webhook delivery can fail, expire
or be dropped. Importing the skill does not install the schedule. Use the same
private durable notification ledger as `codex-approval-alert` and `codex-delegate`.
Do not send secrets or full transcripts.

1. Call `codex_list`. On connection failure, report a single reachability incident
   and suppress repeats until recovery. Never probe connectivity by starting work.
2. For saved sessions, inspect `codex_status` and page `codex_logs` from the saved
   cursor. Persist cursor, latest event time, reported job/turn IDs and active
   incident fingerprints. At first installation, establish a watermark for old
   terminal jobs; report outstanding pending approvals/errors, but do not announce
   every historical completed turn. Follow jobs across pages with log events;
   status includes only recent jobs.
3. Check for these conditions, using bridge timestamps and current status:
   - Pending approval older than 5 minutes: alert once per request ID, including
     the local attach command. A newer approval waits for the next check.
   - Finished/failed/interrupted turn not yet reported since the watermark: send
     one short wrap-up per job/turn, including results/checks when available.
   - Starting/running/dispatching/approval session with no events for over
     20 minutes: report a possible stall, not a proven failure. For sessions
     without events, time from first observation; do not invent creation time.
   - Current failed/uncertain jobs, authentication errors, disconnected processes
     or recovery blocks: report once per incident and clear the marker on recovery.
4. Stay silent when nothing is running and no new terminal result or unresolved
   alert needs reporting. A newly finished task still deserves its one wrap-up,
   even if all sessions are now idle. Avoid “everything OK” messages.

Atomically claim each notification in the shared ledger before sending so a
concurrent webhook, delegate or watchdog does not repeat it. Use approval request
IDs and terminal job/turn IDs, plus condition identifiers for stalls/errors.
Do not repeat an unresolved incident every 10 minutes. Update its state silently;
notify again only for a distinct incident or material change. Without shared
storage, flag the configuration gap once; do not promise deduplication.

This routine observes and reports only. Never accept approvals, change ownership,
start/resume/interrupt sessions, change YOLO mode or replay instructions. A stale
webhook can be ignored while current unresolved state still needs this backup.
