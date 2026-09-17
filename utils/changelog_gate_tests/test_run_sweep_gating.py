"""Exercise the real workflow conditions against concrete authorization and failure cases.

The small evaluator below supports only the GitHub Actions expressions used by
these gates; this is not a substitute for executing the workflow in Actions.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from functools import lru_cache
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
_WF = yaml.load(
    (REPO_ROOT / ".github/workflows/run-sweep.yml").read_text(),
    Loader=yaml.BaseLoader,
)
CHECK_IF = _WF["jobs"]["check-changelog"]["if"]
GATE_IF = _WF["jobs"]["reuse-sweep-gate"]["if"]
CLASSIFIER_STEP = next(
    step for step in _WF["jobs"]["setup"]["steps"] if step.get("id") == "classify"
)
CLASSIFIER_IF = CLASSIFIER_STEP["if"]
SETUP_IF = _WF["jobs"]["setup"]["if"]

# --------------------------------------------------------------------------
# Minimal GitHub Actions expression engine (supports the subset used by the
# gating conditions: && || ! == != contains() always(), parens, paths).
# --------------------------------------------------------------------------
def _tokenize(s: str) -> list[tuple[str, str]]:
    toks: list[tuple[str, str]] = []
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c.isspace():
            i += 1
            continue
        if c == "'":
            j = i + 1
            while j < n and s[j] != "'":
                j += 1
            toks.append(("str", s[i + 1 : j]))
            i = j + 1
            continue
        if s[i : i + 2] in ("==", "!=", "&&", "||"):
            toks.append(("op", s[i : i + 2]))
            i += 2
            continue
        if c in "!(),":
            kind = {"!": "op", "(": "lp", ")": "rp", ",": "comma"}[c]
            toks.append((kind, c))
            i += 1
            continue
        m = re.match(r"[A-Za-z0-9_.*\-]+", s[i:])
        if not m:
            raise SyntaxError(f"bad char {c!r} in {s!r}")
        toks.append(("word", m.group(0)))
        i += len(m.group(0))
    return toks


def _truthy(v: object) -> bool:
    if isinstance(v, bool):
        return v
    if v is None:
        return False
    if isinstance(v, (str, list, dict)):
        return len(v) > 0
    return bool(v)


class _Parser:
    def __init__(self, toks: list[tuple[str, str]], ctx: dict) -> None:
        self.t, self.i, self.ctx = toks, 0, ctx

    def _peek(self) -> tuple[str | None, str | None]:
        return self.t[self.i] if self.i < len(self.t) else (None, None)

    def _next(self) -> tuple[str, str]:
        tok = self.t[self.i]
        self.i += 1
        return tok

    def parse(self) -> object:
        v = self._or()
        if self.i != len(self.t):
            raise SyntaxError(f"trailing tokens: {self.t[self.i:]}")
        return v

    def _or(self) -> object:
        v = self._and()
        while self._peek() == ("op", "||"):
            self._next()
            # Bind the operand before combining: it must always consume its
            # tokens, even when `or`/`and` would short-circuit on truthiness.
            rhs = self._and()
            v = _truthy(v) or _truthy(rhs)
        return v

    def _and(self) -> object:
        v = self._eq()
        while self._peek() == ("op", "&&"):
            self._next()
            rhs = self._eq()
            v = _truthy(v) and _truthy(rhs)
        return v

    def _eq(self) -> object:
        v = self._unary()
        if self._peek() in (("op", "=="), ("op", "!=")):
            op = self._next()[1]
            eq = v == self._unary()
            return eq if op == "==" else not eq
        return v

    def _unary(self) -> object:
        if self._peek() == ("op", "!"):
            self._next()
            return not _truthy(self._unary())
        return self._primary()

    def _primary(self) -> object:
        kind, val = self._peek()
        if kind == "lp":
            self._next()
            v = self._or()
            assert self._next()[0] == "rp"
            return v
        if kind == "str":
            self._next()
            return val
        if kind == "word":
            self._next()
            if self._peek()[0] == "lp":
                self._next()
                args: list[object] = []
                if self._peek()[0] != "rp":
                    args.append(self._or())
                    while self._peek()[0] == "comma":
                        self._next()
                        args.append(self._or())
                assert self._next()[0] == "rp"
                return _call(val, args)
            if val in ("true", "false"):
                return val == "true"
            return self.ctx.get(val)
        raise SyntaxError(f"unexpected token {self._peek()}")


def _call(name: str, args: list[object]) -> object:
    if name in ("always", "success"):
        return True
    if name == "contains":
        haystack, needle = args[0], args[1]
        return False if haystack is None else needle in haystack
    raise SyntaxError(f"unsupported function {name}()")


@lru_cache(maxsize=None)
def _tokens(expr: str) -> tuple[tuple[str, str], ...]:
    return tuple(_tokenize(expr))


def _eval(expr: str, ctx: dict) -> bool:
    return _truthy(_Parser(_tokens(expr), ctx).parse())


def test_expression_evaluator_handles_truthiness_and_precedence() -> None:
    # The workflow checks depend on this helper; verify it with independent examples.
    for expression, context, expected in [
        ("always()", {}, True),
        ("!false", {}, True),
        ("'a' == 'b'", {}, False),
        ("x != 'true'", {"x": "true"}, False),
        ("x != 'true'", {"x": ""}, True),
        ("contains(labels, 'sweep')", {"labels": ["sweep"]}, True),
        ("contains(labels, 'sweep')", {}, False),
        ("true || false && false", {}, True),
        ("(true || false) && false", {}, False),
    ]:
        assert _eval(expression, context) is expected, expression


# --------------------------------------------------------------------------
# DAG evaluation: check-changelog -> reuse-sweep-gate -> setup
# --------------------------------------------------------------------------
def _ctx(sc: dict) -> dict:
    return {
        "github.event_name": sc["event"],
        "github.repository": "SemiAnalysisAI/InferenceX",
        "github.event.action": sc.get("action"),
        "github.event.pull_request.draft": sc.get("draft", False),
        "github.event.pull_request.head.repo.full_name": sc.get(
            "head_repo", "SemiAnalysisAI/InferenceX"
        ),
        "github.event.pull_request.labels.*.name": sc.get("labels", []),
        "github.event.label.name": sc.get("label_name"),
        "vars.PRIORITY_SCHEDULER_ENABLED": sc.get("scheduler_enabled", "true"),
        "github.event.head_commit.message": sc.get("msg", ""),
    }


def run_dag(sc: dict) -> tuple[str, str, str]:
    """Return (check-changelog result, reuse-sweep-gate result, setup decision)."""
    if sc['event'] == 'pull_request' and sc.get('action') not in _WF['on']['pull_request']['types']:
        return 'skipped', 'skipped', 'SKIP'
    ctx = _ctx(sc)

    if not _eval(CHECK_IF, ctx):
        check_result = "skipped"
    else:
        check_result = sc.get("check", "success")
    ctx["needs.check-changelog.result"] = check_result
    ctx["needs.check-changelog.outputs.skip-pr-sweep"] = (
        sc.get("check_skip", "false")
    )

    if not _eval(GATE_IF, ctx):
        gate_result, skip = "skipped", ""
    else:
        gate_result = "success"
        skip = "true" if sc.get("reuse_auth") else ""
    ctx["needs.reuse-sweep-gate.result"] = gate_result
    ctx["needs.reuse-sweep-gate.outputs.skip-pr-sweep"] = skip


    setup = "RUN" if _eval(SETUP_IF, ctx) else "SKIP"
    return check_result, gate_result, setup


_PR = {"event": "pull_request", "draft": False}

# (id, scenario, expected (check, reuse, setup))
CASES = [
    ("PR-sync-unlabeled-reuse-authorized",
     {**_PR, "action": "synchronize", "labels": [], "reuse_auth": True},
     ("success", "success", "SKIP")),
    ("PR-sync-trim-reuse-authorized",
     {**_PR, "action": "synchronize", "labels": ["sweep-enabled"], "reuse_auth": True},
     ("success", "success", "SKIP")),
    ("PR-sync-full-noreuse",
     {**_PR, "action": "synchronize", "labels": ["full-sweep-enabled"],
      "reuse_auth": False}, ("success", "success", "RUN")),
    ("PR-sync-full-reuse-authorized",
     {**_PR, "action": "synchronize", "labels": ["full-sweep-enabled"],
      "reuse_auth": True}, ("success", "success", "SKIP")),
    ("PR-sync-full-changelog-failure",
     {**_PR, "action": "synchronize", "labels": ["full-sweep-enabled"],
      "check": "failure"}, ("failure", "skipped", "SKIP")),
    ("PR-sync-trim-sweep-enabled",
     {**_PR, "action": "synchronize", "labels": ["sweep-enabled"]},
     ("success", "success", "RUN")),
    ("PR-sync-all-evals-without-sweep-label",
     {**_PR, "action": "synchronize", "labels": ["all-evals"]},
     ("success", "success", "SKIP")),
    ("PR-sync-evals-only-without-sweep-label",
     {**_PR, "action": "synchronize", "labels": ["evals-only"]},
     ("success", "skipped", "SKIP")),
    ("PR-sync-agentx-fast-without-sweep-label",
     {**_PR, "action": "synchronize", "labels": ["agentx-fast"]},
     ("success", "skipped", "SKIP")),
    ("PR-sync-full-with-all-evals-uses-reuse",
     {**_PR, "action": "synchronize",
      "labels": ["full-sweep-enabled", "all-evals"],
      "reuse_auth": True}, ("success", "success", "SKIP")),
    ("PR-sync-full-with-evals-only-ignores-reuse",
     {**_PR, "action": "synchronize",
      "labels": ["full-sweep-enabled", "evals-only"],
      "reuse_auth": True}, ("success", "skipped", "RUN")),
    ("PR-sync-full-with-agentx-fast-ignores-reuse",
     {**_PR, "action": "synchronize",
      "labels": ["full-sweep-enabled", "agentx-fast"],
      "reuse_auth": True}, ("success", "skipped", "RUN")),
    ("PR-sync-full-with-both-modifiers-ignores-reuse",
     {**_PR, "action": "synchronize",
      "labels": ["full-sweep-enabled", "all-evals", "evals-only"],
      "reuse_auth": True}, ("success", "skipped", "RUN")),
    ("PR-sync-no-sweep-label",
     {**_PR, "action": "synchronize", "labels": []},
     ("success", "success", "SKIP")),
    ("PR-sync-external-fork-defers-to-trusted-dispatch",
     {**_PR, "action": "synchronize", "labels": ["full-sweep-enabled"],
      "head_repo": "external/InferenceX"},
     ("skipped", "skipped", "SKIP")),
    ("PR-labeled-with-sweep-label",
     {**_PR, "action": "labeled", "label_name": "full-sweep-enabled",
      "labels": ["full-sweep-enabled"]}, ("success", "skipped", "RUN")),
    ("PR-labeled-with-all-evals-without-sweep-label",
     {**_PR, "action": "labeled", "label_name": "all-evals",
      "labels": ["all-evals"]}, ("success", "skipped", "SKIP")),
    ("PR-labeled-with-evals-only-without-sweep-label",
     {**_PR, "action": "labeled", "label_name": "evals-only",
      "labels": ["evals-only"]}, ("success", "skipped", "SKIP")),
    ("PR-labeled-with-agentx-fast-without-sweep-label",
     {**_PR, "action": "labeled", "label_name": "agentx-fast",
      "labels": ["agentx-fast"]}, ("success", "skipped", "SKIP")),
    ("PR-labeled-all-evals-modifies-full-sweep",
     {**_PR, "action": "labeled", "label_name": "all-evals",
      "labels": ["full-sweep-enabled", "all-evals"]},
     ("success", "skipped", "RUN")),
    ("PR-labeled-evals-only-modifies-full-sweep",
     {**_PR, "action": "labeled", "label_name": "evals-only",
      "labels": ["full-sweep-enabled", "evals-only"]},
     ("success", "skipped", "RUN")),
    ("PR-labeled-agentx-fast-modifies-full-sweep",
     {**_PR, "action": "labeled", "label_name": "agentx-fast",
      "labels": ["full-sweep-enabled", "agentx-fast"]},
     ("success", "skipped", "RUN")),
    ("PR-labeled-skip-queue-restarts-full-sweep",
     {**_PR, "action": "labeled", "label_name": "skip_queue",
      "labels": ["full-sweep-enabled", "skip_queue"]},
     ("success", "skipped", "RUN")),
    ("PR-unlabeled-skip-queue-restarts-numeric-sweep",
     {**_PR, "action": "unlabeled", "label_name": "skip_queue",
      "labels": ["full-sweep-enabled"]},
     ("success", "skipped", "RUN")),
    ("PR-labeled-patchwork-restarts-full-sweep",
     {**_PR, "action": "labeled", "label_name": "ci-patchwork",
      "labels": ["full-sweep-enabled", "ci-patchwork"]},
     ("success", "skipped", "RUN")),
    ("PR-unlabeled-patchwork-restarts-full-sweep",
     {**_PR, "action": "unlabeled", "label_name": "ci-patchwork",
      "labels": ["full-sweep-enabled"]},
     ("success", "skipped", "RUN")),
    ("PR-labeled-with-unrelated-label",
     {**_PR, "action": "labeled", "label_name": "documentation",
      "labels": ["full-sweep-enabled"]}, ("skipped", "skipped", "SKIP")),
    ("PR-unlabeled-removed-sweep-label",
     {**_PR, "action": "unlabeled", "label_name": "full-sweep-enabled",
      "labels": []}, ("success", "skipped", "SKIP")),
    ("PR-draft",
     {**_PR, "action": "synchronize", "draft": True,
      "labels": ["full-sweep-enabled"]}, ("success", "success", "RUN")),
    ("PR-draft-label-opt-in",
     {**_PR, "action": "labeled", "draft": True, "label_name": "sweep-enabled",
      "labels": ["sweep-enabled"]}, ("success", "skipped", "RUN")),
    ("PR-draft-without-sweep-label",
     {**_PR, "action": "synchronize", "draft": True,
      "labels": []}, ("success", "success", "SKIP")),
    ("PR-draft-fork-still-requires-trusted-dispatch",
     {**_PR, "action": "labeled", "draft": True, "label_name": "full-sweep-enabled",
      "labels": ["full-sweep-enabled"], "head_repo": "external/InferenceX"},
     ("skipped", "skipped", "SKIP")),
    ("PR-draft-invalid-changelog",
     {**_PR, "action": "synchronize", "draft": True,
      "labels": ["full-sweep-enabled"], "check": "failure"},
     ("failure", "skipped", "SKIP")),
    ("PR-draft-reuse-authorized",
     {**_PR, "action": "synchronize", "draft": True,
      "labels": ["full-sweep-enabled"], "reuse_auth": True},
     ("success", "success", "SKIP")),
    ("PR-ready-for-review",
     {**_PR, "action": "ready_for_review", "labels": ["full-sweep-enabled"],
      "reuse_auth": False}, ("skipped", "skipped", "SKIP")),
    ("PR-sync-validation-requests-skip",
     {**_PR, "action": "synchronize", "labels": ["full-sweep-enabled"],
      "check_skip": "true"},
     ("success", "success", "SKIP")),
    ("push-additions-no-skip",
     {"event": "push", "msg": "feat: add model"},
     ("skipped", "skipped", "RUN")),
    ("push-skip-sweep-tag-ignored",
     {"event": "push", "msg": "fix: x [skip-sweep]"},
     ("skipped", "skipped", "RUN")),
]


@pytest.mark.parametrize("scenario,expected", [(c[1], c[2]) for c in CASES],
                         ids=[c[0] for c in CASES])
def test_gating_decision(
    scenario: dict,
    expected: tuple[str, str, str],
) -> None:
    assert run_dag(scenario) == expected


@pytest.mark.parametrize("draft", [False, True])
@pytest.mark.parametrize("action", ["synchronize", "labeled", "unlabeled"])
@pytest.mark.parametrize("head_repo", ["external/InferenceX", None])
def test_external_or_missing_head_cannot_enter_the_sweep_pipeline(draft, action, head_repo) -> None:
    scenario = {**_PR, "draft": draft, "action": action, "head_repo": head_repo,
                "labels": ["full-sweep-enabled"], "label_name": "full-sweep-enabled"}
    assert run_dag(scenario) == ("skipped", "skipped", "SKIP")


def test_changelog_validation_has_no_write_token_or_persisted_credential() -> None:
    job = _WF["jobs"]["check-changelog"]
    assert job["permissions"] == {"contents": "read"}
    checkout = next(step for step in job["steps"] if step.get("uses", "").startswith("actions/checkout@"))
    assert checkout["with"]["persist-credentials"] == "false"


def test_benchmark_checkout_falls_back_to_the_read_only_workflow_token() -> None:
    workflow = yaml.load(
        (REPO_ROOT / ".github/workflows/benchmark-tmpl.yml").read_text(),
        Loader=yaml.BaseLoader,
    )
    checkout = next(
        step
        for step in workflow["jobs"]["benchmark"]["steps"]
        if step.get("uses", "").startswith("actions/checkout@")
    )

    assert workflow["permissions"] == {"contents": "read"}
    assert checkout["with"]["token"] == "${{ secrets.REPO_PAT || github.token }}"


def test_benchmark_templates_preserve_unrelated_docker_containers() -> None:
    single_node = (REPO_ROOT / ".github/workflows/benchmark-tmpl.yml").read_text()
    multi_node = (REPO_ROOT / ".github/workflows/benchmark-multinode-tmpl.yml").read_text()

    assert "# docker ps -aq | xargs -r docker rm -f" in single_node
    assert "# docker network prune -f" in single_node
    assert "# while [ -n \"$(docker ps -aq)\" ]; do" in single_node
    assert "            docker ps -aq | xargs -r docker rm -f" not in single_node
    assert "            docker network prune -f" not in single_node
    assert "Skipping host-wide container and network cleanup." in single_node
    assert 'scancel --user="$USER" --name="${{ runner.name }}"' in single_node
    assert 'squeue --user="$USER" --name=' in single_node
    assert 'scancel --name="${{ runner.name }}"' not in single_node
    assert "host-wide Docker cleanup" in multi_node
    assert "docker ps -aq | xargs -r docker rm -f" not in multi_node
    assert "docker network prune -f" not in multi_node


def test_dcu_launcher_mounts_read_only_hugging_face_cache_layers() -> None:
    launcher = (REPO_ROOT / "runners/launch_dcu-hygon.sh").read_text()

    assert 'HF_CACHE_ROOT_HOST_PATH="${HF_CACHE_ROOT_HOST_PATH:-/ai_data/datasets/huggingface}"' in launcher
    assert 'HF_HUB_CACHE_HOST_PATH="${HF_HUB_CACHE_HOST_PATH:-$HF_CACHE_ROOT_HOST_PATH/hub}"' in launcher
    assert 'HF_DATASETS_CACHE_HOST_PATH="${HF_DATASETS_CACHE_HOST_PATH:-$HF_CACHE_ROOT_HOST_PATH/datasets}"' in launcher
    assert 'HF_HUB_CACHE="${HF_HUB_CACHE:-/mnt/hf_hub_cache}"' in launcher
    assert 'HF_DATASETS_CACHE="${HF_DATASETS_CACHE:-/mnt/hf_datasets_cache}"' in launcher
    assert '"$HF_HUB_CACHE_HOST_PATH:$HF_HUB_CACHE:none:x-create=dir,bind,ro"' in launcher
    assert '"$HF_DATASETS_CACHE_HOST_PATH:$HF_DATASETS_CACHE:none:x-create=dir,bind,ro"' in launcher
    assert '--env "HF_HUB_CACHE=$HF_HUB_CACHE"' in launcher
    assert '--env "HF_DATASETS_CACHE=$HF_DATASETS_CACHE"' in launcher
    assert '--env "HF_HUB_OFFLINE=1"' in launcher
    assert 'Hugging Face cache is not readable: $cache_dir' in launcher
    assert 'RDMA_DEVICE_NAMES="${RDMA_DEVICE_NAMES:-shca_0,shca_1,shca_2,shca_3}"' in launcher
    assert 'MOONCAKE_DEVICE="${MOONCAKE_DEVICE:-$RDMA_DEVICE_NAMES}"' in launcher
    assert 'RDMA_DEVICES_HOST_PATH="${RDMA_DEVICES_HOST_PATH:-/dev/infiniband}"' in launcher
    assert 'RDMA_SYSFS_HOST_PATH="${RDMA_SYSFS_HOST_PATH:-/sys/class/infiniband}"' in launcher
    assert 'Required RDMA HCA is unavailable: $RDMA_SYSFS_HOST_PATH/$rdma_device' in launcher
    assert '"$RDMA_DEVICES_HOST_PATH:$RDMA_DEVICES_HOST_PATH:none:x-create=dir,rbind,rw"' in launcher
    assert '"$RDMA_SYSFS_HOST_PATH:$RDMA_SYSFS_HOST_PATH:none:x-create=dir,rbind,ro"' in launcher
    assert 'Mooncake DFS root is not container-${access}-accessible: $DFS_ROOT_DIR' in launcher
    assert 'DFS_PROBE_NAME=.inferencex-dfs-probe-${container_name}' in launcher
    assert 'mkdir "$dfs_probe"' in launcher
    assert 'printf "inferencex-dfs-probe\\\\n" > "$dfs_probe/write-test"' in launcher
    assert 'rm -rf -- "$dfs_probe"' in launcher
    assert 'Mooncake preflight passed: DFS root is container-readable/writable/searchable' in launcher
    assert '--env "MOONCAKE_DEVICE=$MOONCAKE_DEVICE"' in launcher


def test_dcu_agentic_services_use_targeted_process_group_cleanup() -> None:
    script = (
        REPO_ROOT / "benchmarks/single_node/agentic/dsv4flash_w4a8_dcu-hygon_sglang.sh"
    ).read_text()

    assert "setsid mooncake_master" in script
    assert "setsid mooncake_client" in script
    assert 'setsid sglang serve "${SGLANG_ARGS[@]}"' in script
    assert 'export MOONCAKE_DEVICE="${MOONCAKE_DEVICE:-shca_0,shca_1,shca_2,shca_3}"' in script
    assert 'unset PYTHONPYCACHEPREFIX' in script
    assert '--protocol="$MOONCAKE_PROTOCOL"' in script
    assert '--device_names="$MOONCAKE_DEVICE"' in script
    assert 'kill -- "-$pgid"' in script
    assert 'kill -KILL -- "-$pgid"' in script
    assert 'stop_service "$SERVER_PID" SGLang' in script
    assert 'stop_service "$CLIENT_PID" Mooncake-client' in script
    assert 'stop_service "$MASTER_PID" Mooncake-master' in script
    assert "pkill" not in script
    assert "killall" not in script


def test_setup_logs_and_publishes_generated_test_matrix() -> None:
    workflow_text = (REPO_ROOT / ".github/workflows/run-sweep.yml").read_text()

    assert 'echo "Generated test matrix:"' in workflow_text
    assert "printf '%s\\n' \"$CONFIG_JSON\" | python3 -m json.tool" in workflow_text
    assert 'echo "search-space-config=$CONFIG_JSON" >> "$GITHUB_OUTPUT"' in workflow_text


def test_sweep_results_archive_locally_without_app_dispatch() -> None:
    workflow_text = (REPO_ROOT / ".github/workflows/run-sweep.yml").read_text()
    job = _WF["jobs"]["archive-sweep-results"]

    assert "trigger-ingest" not in _WF["jobs"]
    assert "trigger-agentic-ingest" not in _WF["jobs"]
    assert "InferenceX-app/dispatches" not in workflow_text
    assert "ingest-agentic-results" not in workflow_text
    assert job["runs-on"] == "dcu-hygon_00"
    assert job["env"]["ARCHIVE_ROOT"] == "/stortest/lium_space/agentX/InferenceX_result"
    assert "json.loads(os.environ.get('NEEDS_JSON') or '{}')" in workflow_text
    assert "matrix = json.loads(matrix_raw) if matrix_raw else {}" in workflow_text
    assert "'matrix-generated': bool(matrix)" in workflow_text
    assert "upload-changelog-metadata" in job["needs"]
    assert "calc-success-rate" in job["needs"]
    assert any(
        step.get("uses", "").startswith("actions/download-artifact@")
        for step in job["steps"]
    )


@pytest.mark.parametrize("is_pr,body,previous,expected", [
    (True, "/reuse-sweep-run 123", None, True),
    (True, "withdrawn", "/reuse-sweep-run 123", True),
    (True, "unrelated", None, False),
    (False, "/reuse-sweep-run 123", None, False),
])
def test_reuse_acknowledgment_routes_new_and_edited_pr_commands(is_pr, body, previous, expected):
    workflow = yaml.load(
        (REPO_ROOT / ".github/workflows/reuse-sweep-comment.yml").read_text(),
        Loader=yaml.BaseLoader,
    )
    assert _eval(workflow["jobs"]["acknowledge"]["if"], {
        "github.event.issue.pull_request": is_pr,
        "github.event.comment.body": body,
        "github.event.changes.body.from": previous,
    }) == expected


def test_reuse_reactions_use_trusted_code_with_no_repository_write_token():
    workflow = yaml.load(
        (REPO_ROOT / ".github/workflows/reuse-sweep-comment.yml").read_text(),
        Loader=yaml.BaseLoader,
    )
    assert workflow["on"] == {"issue_comment": {"types": ["created", "edited"]}}
    job = workflow["jobs"]["acknowledge"]
    assert job["permissions"] == {
        "actions": "read", "contents": "read", "issues": "write", "pull-requests": "read",
    }
    checkout = next(step for step in job["steps"] if step.get("uses", "").startswith("actions/checkout@"))
    assert checkout["with"] == {"ref": "${{ github.sha }}", "persist-credentials": "false"}


def test_priority_classifier_runs_only_for_enabled_pull_requests() -> None:
    scenario = {
        **_PR,
        "action": "synchronize",
        "labels": ["full-sweep-enabled"],
    }
    disabled = _ctx({**scenario, "scheduler_enabled": "false"})
    enabled_pr = _ctx({**scenario, "scheduler_enabled": "true"})
    enabled_push = _ctx({"event": "push", "scheduler_enabled": "true"})

    assert not _eval(CLASSIFIER_IF, disabled)
    assert _eval(CLASSIFIER_IF, enabled_pr)
    assert not _eval(CLASSIFIER_IF, enabled_push)


@pytest.mark.parametrize("failed_job", ["check-changelog", "reuse-sweep-gate"])
@pytest.mark.parametrize("result", ["failure", "cancelled"])
def test_setup_does_not_run_when_a_prerequisite_fails(failed_job, result) -> None:
    ctx = _ctx({**_PR, "action": "synchronize", "labels": ["full-sweep-enabled"]})
    ctx.update({
        "needs.check-changelog.result": "success",
        "needs.check-changelog.outputs.skip-pr-sweep": "false",
        "needs.reuse-sweep-gate.result": "success",
        "needs.reuse-sweep-gate.outputs.skip-pr-sweep": "false",
        f"needs.{failed_job}.result": result,
    })

    assert not _eval(SETUP_IF, ctx)


@pytest.mark.parametrize("labels,returncode", [
    ([], 0),
    (["full-sweep-enabled", "all-evals"], 0),
    (["full-sweep-enabled", "sweep-enabled"], 1),
])
def test_conflicting_sweep_labels_are_rejected(labels, returncode) -> None:
    step = next(step for step in _WF["jobs"]["check-changelog"]["steps"]
                if step.get("name") == "Reject conflicting sweep labels")
    result = subprocess.run(
        ["bash", "-e", "-c", step["run"]],
        env={**os.environ, "SWEEP_LABELS": json.dumps(labels)},
        capture_output=True, text=True,
    )

    assert result.returncode == returncode, result.stderr


@pytest.mark.parametrize("message,expected", [
    ("fix: normal change", "false"),
    ("fix: docs\n\n[skip-sweep]", "true"),
])
def test_skip_policy_reads_the_commit_message(tmp_path, message, expected) -> None:
    step = next(step for step in _WF["jobs"]["check-changelog"]["steps"]
                if step.get("id") == "sweep_policy")
    output = tmp_path / "outputs"
    result = subprocess.run(
        ["bash", "-e", "-c", 'git() { printf "%s\\n" "$TEST_COMMIT_MESSAGE"; };\n' + step["run"]],
        env={**os.environ, "HEAD_SHA": "test-head", "TEST_COMMIT_MESSAGE": message,
             "GITHUB_OUTPUT": str(output)},
        capture_output=True, text=True,
    )

    assert result.returncode == 0, result.stderr
    assert output.read_text().strip() == f"skip-pr-sweep={expected}"
