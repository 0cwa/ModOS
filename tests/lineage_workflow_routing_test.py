#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Evaluate the checked-in job guards without executing Actions or OTA steps."""

from pathlib import Path
import itertools
import json
import re
import sys


FLOW = Path(__file__).resolve().parents[1] / ".github/workflows/release-lineage.yml"


def job(name):
    match = re.search(r"^  " + re.escape(name) + r":\n(.*?)(?=^  \w|\Z)",
                      FLOW.read_text(), re.M | re.S)
    assert match, name
    return match[1]


def selected(name, event="schedule", message="", should_build="", artifact="",
             release_type="default", signing_mode="disposable"):
    expression = re.search(r"^    if: (.+)$", job(name), re.M)[1]
    if expression.startswith('"'):
        expression = json.loads(expression)
    expression = expression.removeprefix("${{ ").removesuffix(" }}")
    context = {
        "github.event_name": event,
        "github.event.head_commit.message": message,
        "needs.schedule_config.outputs.should_build": should_build,
        "inputs.source-artifact-id": artifact,
        "inputs.release-type": release_type,
        "inputs.signing-mode": signing_mode,
    }
    # This subset has the same precedence as Actions: comparisons, &&, then ||.
    # Replace whole tokens, preserving quoted literals (including commit text).
    expression = re.sub(r"'[^']*'|[A-Za-z_][A-Za-z0-9_.-]*",
                        lambda m: repr(context[m[0]]) if m[0] in context else m[0],
                        expression)
    expression = expression.replace("&&", " and ").replace("||", " or ")
    return bool(eval(expression, {"__builtins__": {}},
                     {"startsWith": lambda text, prefix: text.startswith(prefix)}))


SCHEDULED_RELEASE = "release(pdx235): 24.0 regression fixture"


def check_preflight(output, expected):
    values = [line.removeprefix("should_build=") for line in Path(output).read_text().splitlines()
              if line.startswith("should_build=")]
    assert values == [expected], values
    for event, message in (("schedule", ""), ("push", SCHEDULED_RELEASE)):
        assert selected("scheduled_build", event, message, values[0]) == (expected == "true")


def main():
    if len(sys.argv) > 1:
        check_preflight(*sys.argv[1:])
        return

    workflow = FLOW.read_text()
    shizuku_input = re.search(
        r"^      shizuku:\n(.*?)(?=^      \w|\Z)", workflow, re.M | re.S
    )
    assert shizuku_input, "LineageOS workflow lacks the Shizuku input"
    assert re.search(r"^        type: boolean$", shizuku_input[1], re.M)
    assert re.search(r"^        default: false$", shizuku_input[1], re.M)
    assert "      shizuku: ${{ inputs.shizuku }}" in job("manual_build")
    assert "      shizuku: false" in job("scheduled_build")

    scheduled = job("scheduled_build")
    assert "    needs: schedule_config\n" in scheduled
    assert "    uses: ./.github/workflows/build-rom.yml\n" in scheduled
    # The guard is on the reusable job, before patching, signing or publication.
    for event, message, eligible in (("schedule", "", True), ("push", SCHEDULED_RELEASE, True),
                                      ("push", "ordinary change", False),
                                      ("pull_request", SCHEDULED_RELEASE, False),
                                      ("workflow_dispatch", "", False)):
        assert selected("schedule_config", event, message) == eligible
        for result, artifact in itertools.product(("true", "false", "", "unknown"), ("", "123")):
            actual = selected("scheduled_build", event, message, result, artifact)
            assert actual == (eligible and result == "true" and artifact == ""), (
                event, message, result, artifact, actual)

    routes = ("manual_build", "manual_build_only", "manual_device_candidate", "transport_plan")
    for release_type, signing_mode, artifact, result in itertools.product(
            ("default", "force-publish", "build-only"), ("disposable", "repository"),
            ("", "123"), ("true", "false", "")):
        args = dict(event="workflow_dispatch", should_build=result, artifact=artifact,
                    release_type=release_type, signing_mode=signing_mode)
        expected = ("transport_plan" if artifact else "manual_build"
                    if release_type != "build-only" else "manual_device_candidate"
                    if signing_mode == "repository" else "manual_build_only")
        assert [route for route in routes if selected(route, **args)] == [expected], args
        assert not selected("scheduled_build", **args), args
    for route in routes:
        for event in ("schedule", "push", "pull_request"):
            assert not selected(route, event=event, message=SCHEDULED_RELEASE, should_build="true")
    print("LineageOS workflow routing tests passed")


if __name__ == "__main__":
    main()
