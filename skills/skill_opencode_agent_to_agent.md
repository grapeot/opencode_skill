# OpenCode Agent-to-Agent Skill

Teaches discovery, identity, a plain-text envelope, and receipt classification so an AI agent can contact another session over the existing append path.

## When To Use

Use this skill when an agent needs to:
- Discover or verify the session ID of a peer, worker, or owning session.
- Send a request, status update, result, or handoff to another session through the existing append path.
- Acknowledge or answer an incoming peer request without triggering message loops.
- Record a shared-repo handoff with ref, writing state, path conflicts, deploy status, and git owner. This skill does not merge.

This skill is enabling. It does not introduce a central broker, registry daemon, or new server process.

## Boundaries

- **No new architecture**: Reuses the existing OpenCode HTTP server and append endpoint. It does not alter database maintenance or the append request schema.
- **No delivery queue or reliability claims**: A bidirectional append between independent main sessions has been observed. However, busy-boundary races, idle wakeup, and restart recovery of runtime notifications were not tested. Do not claim a FIFO queue, message broker, or delivery guarantee.
- **No automatic return prompt**: An ordinary peer append does not automatically prompt the sender when the receiver replies internally. The receiver must explicitly append a message to the stated `reply_to` address. Runtime task-tool background completion notifications use a separate engine-level mechanism that injects synthetic text; do not confuse peer appends with task notifications or poll children to emulate them.
- **Distinct transports**: Task-runtime context acceptance, where an already-running `functions.task` receives appended context, and the legacy append transport this skill uses are separate transports. Do not merge them or treat one as proving the other. A status receipt such as `context sent` or `Background task updated` is transport-level only and is not proof the target saw the new scope.
- **No cryptographic identity**: `from` and `reply_to` are labels in the text. They do not authenticate who sent that message. Shared HTTP authentication authorizes access to the server; it does not prove the `from` session. Do not add a signature system.
- **No privilege escalation**: OpenCode stores appends as `user`-role messages. That storage role is a transport detail, not human instruction or authorization. Envelopes must set `actor: agent` and `not_a_user_instruction: true`. Out-of-scope actions must be referred back to the owning session.
- **Task board boundary**: An external task board is an optional integration, not a dependency of this package. This package does not call one. If you have no snapshot, use the owner map the parent supplied. If you do have one, check its installed schema and current state before use. Do not assume it is deployed, and do not invent endpoints. If the snapshot and the owner map disagree on task, attempt, session, directory, or project, stop and do not send.

## Available Resources

### Pure Library Helpers (`opencode_skill.peer`)
Import pure resolution and envelope utilities from `opencode_skill.peer`:
- Addressing: `resolve_for_send`, `resolve_self_session`, `list_sibling_ids`, `prefer_owner`, `choose_delivery_session`, `classify_runtime_id`, `session_id_from_task_return`.
- Envelope: `parse_envelope`, `render_envelope`, `message_scope`, `declared_message_type`, `classify_ack_claim`, `classify_acceptance_claim`, `attempt_relation`, `classify_merge_claim`, `may_send_ack`, `handoff_missing`, `interpret_status`. Ack, result, and handoff status all go through `message_scope`. These classify text you already have. They do not accept work or authorize a merge.

### Client Read Helpers
Use existing HTTP client methods rather than building custom queries or CLIs:
- `get_openapi`: Fetches `GET /doc` to verify server schema.
- `list_sessions`: Queries `GET /session` with supported filters.
- `list_children`: Queries `GET /session/{parentID}/children`. Pass the parent id, not your own session id.
- `list_experimental_sessions`: Queries `GET /experimental/session` for bounded cross-project listing.
- `get_session_info`: Queries `GET /session/{id}` to read the session routing directory and parentID. A comparable project field was not part of the observed session object; do not treat a missing project value as a match.
- `get_session_statuses`: Queries `GET /session/status` for one directory's non-idle map.

### Transport and Templates
- Send messages using the existing `append` command or `append_job`. Do not invent a new send subcommand.
- Use the envelope templates in `templates/` as shape examples. The ids and paths in them are synthetic. Replace them. Do not send the example text unchanged.
  - `templates/agent_to_agent_request.md`
  - `templates/agent_to_agent_ack.md`
  - `templates/agent_to_agent_result.md`
  - `templates/agent_to_agent_handoff.md`
- Synthetic validation, from the repository root: `examples/peer_resolution.py` with fixtures in `examples/fixtures/` checks same-title rejection, ambiguous owner maps, ack versus acceptance, and unknown self ID handling. It is not a live transport proof.

## Checks Before Sending

Execute these verification checks before sending. Order matters because verifying target existence and ownership prevents misdirected writes.

1. **Resolve from owner map**: Map the stable `task_key` and current `attempt_id` to one current session id. The session `directory` is the routing directory for that session. It is not the task worktree or output scope.
2. **Title is not identity**: Never use session title as a unique address. An aborted or replaced session can share the current session's title and return HTTP 200, even in another directory. HTTP existence is not ownership. Never fall back to an old session after replacement.
3. **GET the mapped session before sending**: Compare its `directory` with the owner record. That is the observed consistency check. If the owner record has a project and the GET view has no equal project value, stop with `project_mismatch`. Do not report project as matched when the GET view has no comparable field. If the owner record has no project, directory agreement does not mean project was checked. Send only to that current session.
4. **Halt on ambiguity**: If the owner map lists more than one current session for a task key, stop immediately with `ambiguous_owner`. Do not guess based on recency or title.
5. **Halt on conflicting sources**: If two current-owner records disagree on task, attempt, session, directory, or project, stop with `owner_conflict`. A shared session id is not enough. A missing attempt or project on one side is a disagreement, not a license to pick the other side. A stale primary is not replaced by the other record.
6. **Bounds and filters**: An empty list result or a list that hit `limit` does not prove session absence. A creation-time running field on an owner-map record is not live status.
7. **Resolving self and siblings**:
   - A child session ID is not guaranteed to be injected into the prompt or shell environment. The initial task dispatch should pass `reply_to` and the location of the parent-supplied owner map.
   - If self ID is unknown, state the task key or role source. Put `reply_to` on the parent coordinator address you were given, or on a session id you already verified. Do not guess the newest session to fill the field. The recent-session heuristic in `skill_opencode_submission.md` applies only to a human continuing the conversation they are already in.
   - When self ID is known and `parentID` is present, query siblings with `GET /session/{parentID}/children`. Do not pass your own session id to that path. Keep a child only when its `parentID` equals that known parent. A child with another parent, or no parent, is not a sibling. Do not infer roles from the list. Independent main sessions usually have no parent; address them through the owner map.

## Discovery Endpoints and Identity Namespaces

When verifying parameters, consult `GET /doc` on the local server. Do not hardcode ports or hostnames.

### Supported Discovery Endpoints
- `GET /session`: Query parameters are strictly `directory`, `workspace`, `scope`, `path`, `roots`, `start`, `search`, and `limit`.
  - No `parent`, `title`, `status`, or `role` query parameters exist.
  - `scope`, if set, can only be `project`. `scope=project` drops the directory restriction. Do not use it to find a peer.
  - `search` is a title fragment SQL LIKE pattern, not an exact key filter.
  - `start` filters `time_updated`, not creation time.
  - `roots` filters for sessions where `parentID` is empty.
  - Default `limit` is 100.
- `GET /session/{parentID}/children`: The path id is the parent, not the caller. The confirmed query parameter is `directory`. The endpoint description may mention "forked", but children can also be task-created sessions. Do not treat that description as a role model.
- `GET /session/{id}` and `GET /session/status`: The confirmed query parameter is `directory`. Status types are `idle`, `retry`, and `busy`.
  - Sessions in `idle` are removed from the map; absence from the map indicates the session is not in that directory's non-idle set.
  - Only correlate status when the status directory matches the session directory. Absence from another directory's status map is not global idle or abort.
  - `completed` is not a status enum. HTTP 200 and `idle` indicate only execution state, not task completion or business acceptance.
- `GET /experimental/session`: Cross-project listing.
  - Use only when a project-local list cannot locate the candidate, and always with bounded queries: `directory` or `search`, plus an explicit `limit`.
  - Query parameters: `directory`, `workspace`, `roots`, `start`, `cursor`, `search`, `limit`, `archived`.
  - `archived=true` returns both archived and non-archived sessions (it is not archived-only).
  - `cursor` filters for updated times strictly less than the cursor. The client helper returns the response body, not the `x-next-cursor` header; do not treat a single page as a census.

### Identity Namespaces
- `ses_*` from the current OpenCode task tool is the child session id. A GET of that id returns the same id. Do not treat a `ses_*` value from another runtime as a session id until you verify it.
- `job_*`: Logical task identifier used by task boards. This is not an OpenCode session address.
- Process launcher IDs: Process-level execution identifiers. Distinct from session IDs; verify namespace before use.
- If lookup of a task ID fails and the runtime creates a new session, update addressing to use the new returned ID. Do not continue sending to the failed ID.

## Message Envelope and Transport

### Transport Rules
- Messages are dispatched via the existing `append` command or `append_job`.
- The confirmed legacy append fields are `messageID`, `model`, `agent`, `noReply`, `system`, and `parts`. Do not add `reply_to`, `request_id`, `delivery`, `resume`, or `directory` to that JSON. Append does not choose the target directory. The process working directory and `.env` select the server.
- V2 session routes that appear in `GET /doc` are a different path. Do not copy their fields onto legacy append.
- Do not set `noReply`. That flag stores a message without asking the receiver to take a turn. Peer contact needs the normal append path.
- Calling `prompt_async` returns HTTP 204 (CLI status `submitted`). That confirms only that the async call was accepted. It does not confirm a timeline record, a valid model or agent, a reader, or deliverable acceptance.

Send the envelope file with the existing append command. Do not put the envelope on the command line.

```bash
.venv/bin/python -m opencode_skill append --session-id ses_example_current --prompt-file prompts/peer_request.md --model example/default-model
```

Do not pass `--wait` unless you intend to block until the status waiter sees idle. That `completed` status is not acceptance. Do not pass a second session id.
- An append to a busy session joins the current runner. The implementation re-reads history at a loop boundary. That does not guarantee the message is consumed, that the current tool is interrupted, or that delivery is a reliable FIFO. Busy-boundary races were not tested.

### Envelope Structure
All peer messages must begin with a parseable plain-text header, followed by a single blank line and a concise body. Copy the header lines into the prompt file. Do not wrap the sent message in a code fence.

#### Initial Request Header Keys
```yaml
actor: agent
not_a_user_instruction: true
type: request
request_id: <unique_req_id>
from: agent:<sender_role>
task_key: <task_identifier>
attempt_id: <attempt_identifier>
reply_to: <verified sender session, or the parent coordinator address if self is unknown>
purpose: <concise_purpose>
scope: <scope_boundary_and_authorization_statement>
artifacts: <path_or_none>
expect: ack or result
```
Add `task_key` and `attempt_id` when working within an assigned task. Add `artifacts` when referring to file outputs. Subsequent conversation turns need not repeat the full header.

#### Reply Header Keys
Replies (`ack`, `result`, `handoff`) must include:
```yaml
actor: agent
not_a_user_instruction: true
type: ack | result | handoff
request_id: <unique_reply_req_id>
in_reply_to: <original_request_id>
from: agent:<responder_role>
reply_to: <responder session, or that responder's coordinator>
```

## Receipt Layers and Acceptance

Distinguish between these five operational evidence layers:

1. `submitted`: HTTP 204 returned by `prompt_async` or CLI status `submitted`. Transport acceptance only.
2. `timeline`: The message has been persisted in the session's message history.
3. `semantic ack`: `classify_ack_claim` uses `message_scope`. It returns `current_ack_claim` only when the request id, task, and attempt all match the caller-supplied current context. A matching request id with an older attempt is `historical`, not a current ack. Missing request, task, or attempt context stays `unverified`. A header match is not proof of who sent it. Do not ack an ack. A natural-language ack that a person has already checked is outside this parser; do not reject it for lacking these headers.
4. `result`: A deliverable report with `type: result`. The header type is the boundary. Do not require the body to repeat it.
5. `accepted`: A decision by the confirmed current owner, outside this package. `classify_acceptance_claim` only says whether the text matches a request, task, attempt, and owner label the caller supplies. Missing context, a wrong request id, or an older attempt returns `does_not_match`. A match is not acceptance and does not close the current attempt. A late result from an older attempt can be kept as history.

CLI `--wait` reporting `completed` indicates only that the status poller observed an `idle` state; it is not business acceptance.

## Shared Repository Handoff

When handing off deliverables that modify a shared repository, the envelope must include explicit coordination fields:
- `ref`: Commit SHA or branch reference.
- `still_writing`: `true` or `false`. Do not omit it. `false` means the sender claims writing has stopped; it is not required for every handoff, and it is not a merge.
- `unfinished_paths`: Incomplete paths, or `none`.
- `conflict_paths`: Conflict paths, or `none`.
- `deploy_status`: Deploy state the sender can actually report, such as `not_deployed`. Do not invent a deploy result.
- `git_owner`: Who currently holds the git window. This is a coordination label, not a new authorization system.
- `merge_coordination`: `required` or `clear`. `classify_merge_claim` uses the same `message_scope` gate. It reports `still_writing` or `declared_clear` only for the caller-supplied current request, task, and attempt. An older attempt does not report current `still_writing`. `declared_clear` also needs the sender to match the caller-supplied git owner and `still_writing: false`. That value does not read Git and does not authorize a merge.
- `worktree`: Optional. A worktree path is not merge coordination.

## Observed Traps and Error Reference

### Traps to Avoid
- **Title as identity**: Trusting a title match from `GET /session` without the current owner record and a directory check.
- **Zombies and aborts**: Addressing a replaced or aborted session because it still returns HTTP 200.
- **Heuristic leakage**: Using the submission skill's recent-session heuristic for peer addressing or discovering an unknown self ID.
- **Enum confusion**: Treating the OpenAPI text description "completed" as a status enum, or treating `idle` as task completion.
- **Directory mismatch in status**: Treating absence from a status map as an abort without verifying that the query directory matches the session directory.
- **Transport pollution**: Placing `reply_to` or `request_id` in the API JSON body rather than the text envelope.
- **Unilateral prompt assumption**: Expecting a peer append to prompt the sender without an explicit return append to `reply_to`.
- **Authorization confusion**: Treating user-role storage in OpenCode as human authorization to expand task scope.
- **Worktree merge assumption**: Assuming worktree isolation bypasses the need for merge coordination with `git_owner`.

### Helper Error Codes
These codes come from `opencode_skill.peer`. They are not OpenCode server errors.
- `ambiguous_owner`: Owner map contains multiple current sessions for the same task key.
- `no_current_owner`: Task key has no assigned current owner in the map.
- `get_target_mismatch`: Session returned by `GET /session/{id}` does not match the target ID.
- `directory_mismatch`: Target session directory does not match the task directory.
- `project_mismatch`: Target session project does not match the expected project.
- `missing_get_check`: An append was attempted without verifying the session via GET.
- `stale_target`: Target session has been superseded or replaced.
- `double_post`: A second address was supplied. Do not send to both the current and replaced sessions.
- `owner_conflict`: Two supplied owner records disagree, including same session and directory with a different attempt or project.
- `parent_required`: Sibling lookup was not given the parent id.
- `unknown_self`: Sender session ID is unknown and cannot be verified.
- `unverified_address`: The id is not a session id for the stated runtime. `job_*` is a logical task id. A process-launcher job id is a third namespace.
- `unknown_status`: Session status returned a value outside `idle`, `retry`, or `busy`.
- `status_directory_mismatch`: Status map queried for a directory differing from session directory.
- `not_agent`: Inbound envelope does not declare `actor: agent`.
- `missing_not_a_user_instruction`: Inbound envelope lacks `not_a_user_instruction: true`.

## Acceptance Criteria

A peer send that uses this skill is done only when all of these are true:

1. The target came from the current owner for the stable task key and attempt, not from a title match or the newest session.
2. That session was GET-checked, and its routing directory matched the owner record. Project was reported as matched only when both sides had the same comparable value. No second address was used.
3. The prompt file is a text envelope with `actor: agent` and `not_a_user_instruction: true`. `reply_to` and `request_id` are in that text, not in the JSON body. A reply's `reply_to` is the responder or that responder's coordinator, not a copy of the requester address.
4. The recorded evidence layer is the one you actually have. A correlated ack claim was not treated as acceptance. An older attempt was not used to close the current attempt.
5. A shared-repo handoff includes ref, `still_writing`, unfinished and conflict paths, deploy status, and git owner. A worktree path was not treated as merge coordination.
6. Public files from this work contain no real session ids, user paths, credentials, or private endpoints.
