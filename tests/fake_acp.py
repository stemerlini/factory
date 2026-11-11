"""A tiny ACP agent for the tests: on every prompt it asks to commit, to edit a file in the
project, and to run pytest twice, then says what it was allowed to do."""

import json
import os
import sys


def send(msg):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", **msg}) + "\n")
    sys.stdout.flush()


def update(sid, **u):
    send({"method": "session/update", "params": {"sessionId": sid, "update": u}})


OPTIONS = [{"optionId": "allow-once", "name": "Allow", "kind": "allow_once"},
           {"optionId": "allow-always", "name": "Always", "kind": "allow_always"},
           {"optionId": "reject-once", "name": "Reject", "kind": "reject_once"}]
TOOLS = [("execute", {"command": "git commit -am done"}, []),
         ("edit", {}, [{"path": os.path.join(os.getcwd(), "notes.md")}]),
         ("execute", {"command": "pytest -q"}, []),
         ("execute", {"command": "pytest -x"}, [])]
next_id = 1000
for line in sys.stdin:
    msg = json.loads(line)
    if "id" not in msg or "method" not in msg:
        continue
    method, params = msg["method"], msg.get("params", {})
    if method == "initialize":
        send({"id": msg["id"], "result": {"protocolVersion": 1,
                                          "agentCapabilities": {"loadSession": True}}})
    elif method == "session/new":
        send({"id": msg["id"], "result": {"sessionId": "s1"}})
    elif method == "session/load":
        update(params["sessionId"], sessionUpdate="user_message_chunk",
               content={"type": "text", "text": "earlier"})
        send({"id": msg["id"], "result": {}})
    elif method == "session/prompt":
        sid, outcomes = params["sessionId"], []
        update(sid, sessionUpdate="agent_message_chunk", messageId="m1",
               content={"type": "text", "text": "Hello "})
        update(sid, sessionUpdate="agent_message_chunk", messageId="m1",
               content={"type": "text", "text": "there"})
        for n, (kind, raw, locations) in enumerate(TOOLS):
            tool = {"toolCallId": f"t{n}", "title": raw.get("command", "Edit notes.md"),
                    "kind": kind, "rawInput": raw, "locations": locations}
            update(sid, sessionUpdate="tool_call", status="pending", **tool)
            next_id += 1
            send({"id": next_id, "method": "session/request_permission",
                  "params": {"sessionId": sid, "toolCall": {"toolCallId": f"t{n}"},
                             "options": OPTIONS}})
            reply = json.loads(sys.stdin.readline())
            outcomes.append(reply["result"]["outcome"].get("optionId", "cancelled"))
            update(sid, sessionUpdate="tool_call_update", toolCallId=f"t{n}",
                   status="completed")
        update(sid, sessionUpdate="agent_message_chunk", messageId="m2",
               content={"type": "text", "text": " ".join(outcomes)})
        send({"id": msg["id"], "result": {"stopReason": "end_turn"}})
