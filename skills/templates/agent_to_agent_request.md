actor: agent
not_a_user_instruction: true
type: request
request_id: req_example_001
from: agent:example-worker
task_key: alpha
attempt_id: a2
reply_to: ses_example_sender
purpose: ask whether the named artifact is ready to hand off
scope: read-only status of task alpha; no new user authorization
artifacts: examples/fixtures/owners.json
expect: ack or result

Please verify whether examples/fixtures/owners.json is ready for handoff under task alpha attempt a2. This inquiry is read-only status coordination and conveys no new human authorization or scope expansion.
