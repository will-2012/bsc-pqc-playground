"""SSM transport: wait for completion; keep raw AWS output in private files."""

import json
import os
from pathlib import Path
import subprocess
import time
import uuid


class SSM:
    def __init__(self, logs, timeout=900):
        self.logs = Path(logs)
        self.logs.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.timeout = timeout

    def aws(self, args):
        p = subprocess.run(
            ["aws", *args, "--output", "json"],
            capture_output=True,
            text=True,
            timeout=60,
            env={**os.environ, "AWS_PAGER": ""},
        )
        if p.returncode:
            (self.logs / "aws-error.txt").write_text(p.stderr)
            raise RuntimeError("AWS request failed; inspect private logs")
        return json.loads(p.stdout)

    def run(self, h, commands):
        ticket = uuid.uuid4().hex
        params = self.logs / (ticket + "-request.json")
        params.write_text(json.dumps({"commands": commands, "executionTimeout": [str(self.timeout)]}))
        cid, terminal = None, False
        try:
            r = self.aws(
                [
                    "ssm",
                    "send-command",
                    "--region",
                    h["region"],
                    "--instance-ids",
                    h["instance_id"],
                    "--document-name",
                    "AWS-RunShellScript",
                    "--parameters",
                    "file://" + str(params),
                ]
            )
            cid = r["Command"]["CommandId"]
            (self.logs / (ticket + "-command.json")).write_text(
                json.dumps({"commandId": cid, "host": h["id"]})
            )
            end = time.monotonic() + self.timeout + 60
            while time.monotonic() < end:
                # Invocation metadata is eventually consistent. list returns empty until visible.
                reply = self.aws(
                    [
                        "ssm",
                        "list-command-invocations",
                        "--region",
                        h["region"],
                        "--command-id",
                        cid,
                        "--details",
                    ]
                )
                inv = reply.get("CommandInvocations", [])
                if not inv or inv[0]["Status"] in (
                    "Pending",
                    "InProgress",
                    "Delayed",
                    "Cancelling",
                ):
                    time.sleep(2)
                    continue
                detail = self.aws(
                    [
                        "ssm",
                        "get-command-invocation",
                        "--region",
                        h["region"],
                        "--command-id",
                        cid,
                        "--instance-id",
                        h["instance_id"],
                    ]
                )
                (self.logs / (ticket + "-result.json")).write_text(json.dumps(detail, indent=2))
                terminal = True
                if detail["Status"] != "Success" or detail.get("ResponseCode") != 0:
                    raise RuntimeError(f"{h['id']}: remote command failed; inspect private logs")
                return detail.get("StandardOutputContent", "")
            raise RuntimeError(f"{h['id']}: command timed out")
        except BaseException:
            if cid and not terminal:
                # Cancellation is best effort; preserve the command ID for inspection.
                try:
                    self.aws(
                        [
                            "ssm",
                            "cancel-command",
                            "--region",
                            h["region"],
                            "--command-id",
                            cid,
                            "--instance-ids",
                            h["instance_id"],
                        ]
                    )
                except Exception:
                    pass
            raise
        finally:
            params.unlink(missing_ok=True)
