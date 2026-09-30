"""Fixed cold n12 residual experiment; observations never select a remedy."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Any, Callable, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.aios_full_suite_contention_diagnostic import DiagnosticError, _nodeid, _safe_nodeid
from scripts.aios_parallel_git_fixture_push_diagnostic import subject_identity as clean_subject

FORMAT = "AIOS_RESIDUAL_CONTEXT_ATTRIBUTION_DIAGNOSTIC"
SCHEMA = "AIOS_RESIDUAL_CONTEXT_ATTRIBUTION_OBSERVATION"
OUTPUT_ENV = "AIOS_RESIDUAL_CONTEXT_OUTPUT"
ROOT_ENV = "AIOS_RESIDUAL_CONTEXT_ROOT"
BASELINE_ENV = "AIOS_RESIDUAL_CONTEXT_BASELINE"
PHASE_ENV = "AIOS_RESIDUAL_CONTEXT_PHASE"
SELECTORS_MARKER = "AIOS_RESIDUAL_SELECTORS="
CANONICAL_MAIN = "623a22fbfda38e6e751c9f10066cec9b57db8c90"
FAILED_CANDIDATE = "8908a7be6f190eeba0dff7d8fce03f9e892a4ecb"
HISTORICAL_ARTIFACT = "f4db25e086cc41388c0c4bb198378969bea6f0c3"
HISTORICAL_EVIDENCE = "RUN-233-002-V002"
# Verbatim full_n12_failed_nodeids from the immutable canonical RESULT above.
HISTORICAL = (
    'tests/test_git_fixture_isolation.py::test_depth_amplified_sandboxes_push_and_resolve_full_aios_refs#sha256:81ddac6f53a82cf133b6a65e432ac9acdcb974e94a079173ef9914e4c8951e44',
    'tests/test_hot_swap_conformance.py::test_scenario_2_fresh_correction_accepts_only_current_finding#sha256:6ddd555377a73d5550ca6121173981d9ee2d8eeb5b6a4ef76ba68700debc9579',
    'tests/test_operator.py::test_bootstrap_ambiguous_reentry_fails_closed#sha256:ccf3f5a23963446dec354d1deaa9cff3b8a484ac4d90899b38c9eab1b2a42af0',
    'tests/test_operator.py::test_bootstrap_exact_edge_reentry_and_target_ownership#sha256:518f53515fcde7399f1bb16cb2921b90a68f74adf73a003ed7d789c4cd16c76e',
    'tests/test_operator.py::test_migration_exact_handoff_and_replay_rejection#sha256:11da76f7e59bc2f9a77e0aa42515db54f6c512a1e8ca26e0d02827068c13a9c9',
    'tests/test_operator.py::test_migration_reconciles_reserved_run_without_executor_reentry#sha256:39ccb5e352a4c0304d2994dfbaa104544a166daf5af80e25c44f6ec7fea21d47',
    'tests/test_operator.py::test_migration_reconciles_reserved_run_without_executor_reentry#sha256:a64d496475ab5277a1d9fa53c7b0f9a91b4d30cf40a916c7d9c5d758ad8ac568',
    'tests/test_operator.py::test_migration_reconciles_reserved_run_without_executor_reentry#sha256:fab7b4db43499a0b1a294925bd8baef21864e7c032ad0d116151448e09d5f5be',
    'tests/test_operator.py::test_migration_resumes_consumed_handoff_before_run#sha256:500b4245521ef8bb43083ea1969b203a37ce008764be4256b054b7b0985902bb',
    'tests/test_operator.py::test_migration_resumes_consumed_handoff_before_run#sha256:f053e319cd4429886b210584eab559200672947877dd7004769ded2bfd915d80',
    'tests/test_operator.py::test_source_bootstrap_injected_exact_staging#sha256:07b0b390ae8981b52f2358bd6b9a543fc3b752f8f9165ebd27752f626561f65a',
    'tests/test_operator.py::test_source_bootstrap_production_rejects_retired_replacement_target#sha256:cab8be81070545f971d866fadd8d30fe0f0c11bc68925ed4ebbba38928a5e4e9',
    'tests/test_operator.py::test_source_bootstrap_recovery_exact_replay_and_history#sha256:5d7d9bec58b1c73b51724061609da22debdc86990b672f66e93ac25b9339d22b',
    'tests/test_operator.py::test_source_bootstrap_recovery_exact_replay_and_history#sha256:613c1e4b49282b5b96b2faee88945529e77a16c1432b148b18f1453bd2e0867d',
    'tests/test_operator.py::test_source_bootstrap_recovery_interruption_keeps_old_authority#sha256:f2b8a13e5a3c46d59d08c3967377fca8d1307245bef234e1206b6e58d9c0ab57',
    'tests/test_operator.py::test_source_bootstrap_recovery_preserves_completed_migration_history#sha256:4bb046fc913874cd49d4508a5c7f1c99975c476e780cb0d4a6cfdb5fea61dee7',
    'tests/test_operator.py::test_source_bootstrap_recovery_rejects_cross_boundary#sha256:1633d54bc82b03494a6b2bdde16b249628f08b87b0b596db5c80d7c3cfd3510e',
    'tests/test_operator.py::test_source_bootstrap_recovery_rejects_cross_boundary#sha256:1a41811d5d1cbea16f93376fcea61153e30a3767b93f1823f5d12a855d63bda0',
    'tests/test_operator.py::test_source_bootstrap_recovery_rejects_cross_boundary#sha256:2a9536e6225cf9b79ed8bec9e718e22113751ccd175af7bf383ffcc978689608',
    'tests/test_operator.py::test_source_bootstrap_recovery_rejects_cross_boundary#sha256:3072dc33ab3c8fc966d509425a5c2efdd43d8907e3b466d1dfc4405d7f7ae6e8',
    'tests/test_operator.py::test_source_bootstrap_recovery_rejects_cross_boundary#sha256:507b3ac4dad6642ee845ab8ff8b7435562e0abc41463b4d27a8cfccbc55a1972',
    'tests/test_operator.py::test_source_bootstrap_recovery_rejects_cross_boundary#sha256:7f751a31366e865ca263e2b71a2c73bd6d503986235d58af62ade085aca6f7c7',
    'tests/test_operator.py::test_source_bootstrap_recovery_rejects_cross_boundary#sha256:ad37ee143dceacab00bac6b5fe543378d05582abd0cee39aa39436b2cbf9b656',
    'tests/test_operator.py::test_source_bootstrap_recovery_rejects_cross_boundary#sha256:b58ab6ed3fda7d1af5440ad40d7d813350ad1ced4dd6e21bac7841916599d882',
    'tests/test_operator.py::test_source_bootstrap_recovery_rejects_cross_boundary#sha256:ba65bd02a377e0d4ce20a31219a1f34fd4a7bdba8aaebddcf2905434a4a043fd',
    'tests/test_operator.py::test_source_bootstrap_recovery_rejects_cross_boundary#sha256:c0155bb174815fb00236ba9b3d2660293c68825ecef69b933c0368cb19f593cc',
    'tests/test_operator.py::test_source_bootstrap_recovery_rejects_cross_boundary#sha256:c45cf9fec37bafec93cf68f253b9ef01ed7347a2db37c4789a68820c8675c681',
    'tests/test_operator.py::test_source_bootstrap_recovery_rejects_cross_boundary#sha256:ccd3947f64ff07997523913f752496b35b85bfdd8ac6bd083c9f69c70048912b',
    'tests/test_operator.py::test_source_bootstrap_rejects_replayed_or_mismatched_handoff#sha256:88077a0303444750ffd8f283e3bacc8eb692f5db9894b9eb2b03869479bb021e',
    'tests/test_operator.py::test_source_bootstrap_stale_upstream_after_edge_has_no_run#sha256:63a41b73d7ef5fc3ad33e970c17a3e99b032e38956dc3bacea11517931d267ca',
    'tests/test_operator.py::test_source_bootstrap_target_reconciles_one_run#sha256:035796171affea24d68846133412af0946e0b62ee02f13ca27246698faefb737',
    'tests/test_operator.py::test_source_bootstrap_target_reconciles_one_run#sha256:e3a3e5f375f09ef834bccf4f6ed69d440c5173a68710373b96dd5f8b2a2e869d',
    'tests/test_operator.py::test_source_bootstrap_target_reconciles_one_run#sha256:ffe970b59f63ae64d3a11d0b8ac584488f8f16b42b49b9406e37a7f44b0ea16d',
    'tests/test_operator.py::test_source_repair_bootstrap_closed_and_exact_replay#sha256:6a39907343d56414e42e85d6eb786eb8d25628580ab235403baa75dc05536977',
    'tests/test_operator.py::test_source_repair_bootstrap_requires_completed_failed_v2_lineage#sha256:0d5b3fc36d52c0957a3c613eb534d2c61b7d2d0010d488c31dff9fa894bf108d',
    'tests/test_operator.py::test_source_repair_bound_target_consumes_without_matching_activation#sha256:be60bee57256685b75d55b33155692fa7e2d39afc87e75ade33e78e16f798a7a',
)
HISTORICAL_DIGEST = '04b95f5eced5dbdbd7193383c4883a8599fd87a35ff0c8fbcbd5688bb928597f'
FIXED = frozenset({
    "tests/test_hot_swap_conformance.py::test_scenario_2_fresh_correction_accepts_only_current_finding#sha256:6ddd555377a73d5550ca6121173981d9ee2d8eeb5b6a4ef76ba68700debc9579",
    "tests/test_operator.py::test_source_repair_bootstrap_closed_and_exact_replay#sha256:6a39907343d56414e42e85d6eb786eb8d25628580ab235403baa75dc05536977",
    "tests/test_operator.py::test_source_repair_bound_target_consumes_without_matching_activation#sha256:be60bee57256685b75d55b33155692fa7e2d39afc87e75ade33e78e16f798a7a",
})
GIT_TARGET = "tests/test_git_fixture_isolation.py::test_depth_amplified_sandboxes_push_and_resolve_full_aios_refs"
MUTATORS = frozenset("tests/test_operator.py::" + name for name in (
    "test_source_successor_final_published_activation_composes_once",
    "test_source_successor_published_target_consumes_without_activation",
    "test_source_successor_isolated_installed_provenance_rejects_before_run",
))
LIMIT = 64
MAX_GIT = 128
MAX_BYTES = 32_000_000
HEX = re.compile(r"sha256:[0-9a-f]{64}\Z")
SAFE = re.compile(r"tests/(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_-]+\.py(?:::[A-Za-z_][A-Za-z0-9_]*)+#sha256:[0-9a-f]{64}\Z")
STAGES = ("setup", "call", "teardown")
RELATIONS = frozenset({"BASELINE_EXACT", "CHANGED", "MISSING", "AMBIGUOUS", "UNAVAILABLE"})
CATEGORIES = frozenset({"LOCK_OR_REF_UPDATE", "PATH_OR_FILENAME", "ACCESS_OR_PERMISSION",
                        "REPOSITORY_STATE", "TRANSPORT_OR_REMOTE", "UNKNOWN_REVISION_OR_OBJECT", "OTHER"})
FAMILIES = {name: name.upper().replace('-', '_') for name in (
    "rev-parse", "clone", "checkout", "status", "fetch", "ls-remote", "show", "ls-tree",
    "merge-base", "update-ref", "cat-file", "push", "init", "config", "add", "commit",
    "merge-tree", "commit-tree", "show-ref", "rev-list", "remote", "reset", "branch",
)}
PROVENANCE = {"direct_url", "sitecustomize", "distribution_multiplicity"}
FIELDS = {"schema", "version", "phase", "exit_status", "integrity", "collection", "executed",
          "reports", "failures", "processes", "worker_collections", "worker_reports", "over_bound", "residual_git"}


def integer(value: object, low: int = -(2**31), high: int = 2**32 - 1) -> bool:
    return type(value) is int and low <= value <= high


def safe(value: object) -> str:
    if not isinstance(value, str) or len(value) > 512 or not SAFE.fullmatch(value):
        raise DiagnosticError("unsafe identity")
    return value


def identities(value: object) -> list[str]:
    if not isinstance(value, list) or len(value) > 10000:
        raise DiagnosticError("identity bound exceeded")
    nodes = [safe(node) for node in value]
    if len(set(nodes)) != len(nodes):
        raise DiagnosticError("duplicate identity")
    return nodes


def targets() -> frozenset[str]:
    nodes = identities(list(HISTORICAL))
    digest = hashlib.sha256(''.join(node + '\n' for node in nodes).encode()).hexdigest()
    residual = frozenset(nodes) - FIXED
    if (len(nodes) != 36 or digest != HISTORICAL_DIGEST or not FIXED <= set(nodes)
            or len(residual) != 33 or sum(n.startswith('tests/test_operator.py::') for n in residual) != 32
            or _safe_nodeid(GIT_TARGET) not in residual):
        raise DiagnosticError("historical binding mismatch")
    return residual


def resolve(raw: object, collected: list[str]) -> tuple[list[str], dict[str, Any]]:
    wanted = targets()
    if not isinstance(raw, list) or len(raw) > 10000:
        raise DiagnosticError("missing transient selectors")
    selectors = [_nodeid(node) for node in raw]
    current = [_safe_nodeid(node) for node in selectors]
    # Invalid canonical collection is an integrity error. Historical absence is
    # an explicit bounded drift observation, with no alternative population.
    if current != collected:
        raise DiagnosticError("transient collection identity mismatch")
    missing = sorted(wanted - set(current))
    ambiguous = sorted(n for n in wanted if current.count(n) > 1)
    if any(current.count(n) > 1 for n in set(current) - wanted):
        raise DiagnosticError('duplicate canonical identity outside historical population')
    if missing or ambiguous:
        return [], {"missing": missing, "ambiguous": ambiguous,
                    "resolved_count": len(wanted) - len(missing) - len(ambiguous)}
    return [node for node in selectors if _safe_nodeid(node) in wanted], {}


def subject_identity(repository: Path) -> dict[str, Any]:
    sha = clean_subject(repository)
    main = subprocess.run(['git', 'rev-parse', '--verify', 'refs/heads/main'], cwd=repository,
                          capture_output=True, text=True, check=False)
    if main.returncode != 0 or main.stdout.strip() != sha:
        raise DiagnosticError('subject is not exact canonical main')
    for ancestor, permitted in ((CANONICAL_MAIN, True), (FAILED_CANDIDATE, False)):
        code = subprocess.run(["git", "merge-base", "--is-ancestor", ancestor, sha],
                              cwd=repository, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode
        if code not in {0, 1} or (code == 0) != permitted:
            raise DiagnosticError("subject outside canonical main lineage")
    return {"kind": "git-commit", "head_sha": sha, "worktree_clean": True,
            "canonical_main_ancestor": CANONICAL_MAIN}


def baseline() -> dict:
    # Isolated interpreter lookup matches the real installed interpreter used by
    # successor tests. This private response stays in memory/environment only.
    response = subprocess.run([sys.executable, "-I", "-c", "import site,json; print(json.dumps(site.getsitepackages()))"],
                              capture_output=True, text=True, check=True)
    from tests.aios_residual_context_attribution_plugin import provenance_snapshot
    sites = json.loads(response.stdout)
    if not isinstance(sites, list) or not sites or any(not isinstance(p, str) for p in sites):
        raise DiagnosticError("installed baseline unavailable")
    site = str(Path(sites[0]).resolve())
    snapshot = provenance_snapshot(Path(site))
    if (snapshot['distribution_multiplicity'] != 1 or not isinstance(snapshot['sitecustomize'], bool)
            or not isinstance(snapshot['direct_url'], str) or not re.fullmatch('[0-9a-f]{64}', snapshot['direct_url'])):
        raise DiagnosticError('installed baseline is not clean and exact')
    return {"site": site, "snapshot": snapshot}


def unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise DiagnosticError("duplicate protocol key")
        value[key] = item
    return value


def read(path: Path) -> dict:
    with path.open('rb') as stream:
        data = stream.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise DiagnosticError("protocol size bound exceeded")
    value = json.loads(data, object_pairs_hook=unique)
    if not isinstance(value, dict) or set(value) != FIELDS:
        raise DiagnosticError("incompatible observation")
    return value


def run_pytest(repository: Path, root: Path, *, phase: str, selectors: tuple[str, ...],
               installed_baseline: dict) -> tuple[int, dict, list[str] | None]:
    if (phase not in {"collection", "residual-n12", "full-n12"}
            or (phase != "residual-n12" and selectors)
            or (phase == "residual-n12" and (len(selectors) != 33
                or {_safe_nodeid(node) for node in selectors} != targets()))):
        raise DiagnosticError("unsupported invocation")
    basetemp = root / ("collection-tmp" if phase == "collection" else "parallel-12-tmp")
    output = root / "observation.json"
    (root / "registry").mkdir()
    command = [sys.executable, "-m", "pytest", "-p", "xdist.plugin", "-p",
               "aios_residual_context_attribution_plugin", "-p", "no:cacheprovider",
               "--basetemp", str(basetemp), "-q"]
    command += (["--collect-only"] if phase == "collection" else
                ["-n", "12", "--dist", "load", "--max-worker-restart", "0"])
    command.extend(selectors)
    env = os.environ.copy()
    for key in ("PYTEST_ADDOPTS", "PYTEST_PLUGINS", "PYTEST_XDIST_AUTO_NUM_WORKERS"):
        env.pop(key, None)
    env.update({"PYTHONDONTWRITEBYTECODE": "1", "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
                "PYTHONPATH": str(repository / "tests") + os.pathsep + str(repository),
                "TMP": str(root), "TEMP": str(root), "TMPDIR": str(root),
                OUTPUT_ENV: str(output), ROOT_ENV: str(root), PHASE_ENV: phase,
                BASELINE_ENV: json.dumps(installed_baseline),
                'AIOS_BP_V4_DIAGNOSTIC_PREFIXES': json.dumps({'subject': str(repository),
                    'diagnostic_temp': str(root), 'pytest_basetemp': str(basetemp), 'user_home': str(Path.home())})})
    raw = None
    if phase == "collection":
        with subprocess.Popen(command, cwd=repository, env=env, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL) as process:
            data = process.stdout.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                process.kill()
                raise DiagnosticError("transient collection exceeds bound")
            status = process.wait()
        lines = [line[len(SELECTORS_MARKER):] for line in data.decode('utf-8').splitlines()
                 if line.startswith(SELECTORS_MARKER)]
        if len(lines) != 1:
            raise DiagnosticError("missing transient canonical collection")
        raw = json.loads(lines[0], object_pairs_hook=unique)
    else:
        status = subprocess.run(command, cwd=repository, env=env, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, check=False).returncode
    return status, read(output), raw


def validate_git(value: object, *, push: bool = False) -> None:
    base = {"ordinal", "outcome", "return_code", "ref_length", "remote_root_length", "remote_lock_path_length"}
    if not push:
        base = {"command_family", "return_code", "stderr_category", "cwd_length", "max_operand_length"}
    if not isinstance(value, dict):
        raise DiagnosticError("malformed Git fact")
    failed = value.get('outcome') == 'failure' if push else True
    if set(value) != (base | ({"stderr_category"} if push and failed else set())):
        raise DiagnosticError("unsafe Git fields")
    if not integer(value['return_code']) or (not push and value['return_code'] == 0):
        raise DiagnosticError("invalid Git return code")
    if failed and value['stderr_category'] not in CATEGORIES:
        raise DiagnosticError("unsafe stderr category")
    if push:
        if (not integer(value['ordinal'], 1, 4) or value['outcome'] not in {'success', 'failure'}
                or failed != (value['return_code'] != 0)):
            raise DiagnosticError("invalid PUSH outcome")
        for key in ('ref_length', 'remote_root_length', 'remote_lock_path_length'):
            if not integer(value[key], 1, 32767):
                raise DiagnosticError("invalid PUSH path shape")
        if value['remote_lock_path_length'] <= 260 or not (
                value['remote_root_length'] + value['ref_length'] + 6 == value['remote_lock_path_length']):
            raise DiagnosticError("inconsistent PUSH lock geometry")
    else:
        if value['command_family'] not in set(FAMILIES.values()) | {'OTHER'}:
            raise DiagnosticError("unsafe Git family")
        for key in ('cwd_length', 'max_operand_length'):
            if value[key] is not None and not integer(value[key], 1, 32767):
                raise DiagnosticError("invalid Git path shape")


def validate_cause(value: object, node: str) -> None:
    base = {"exception_type", "message_fingerprint", "source_locus"}
    if not isinstance(value, dict) or not base <= set(value) or set(value) - base - {'errno', 'winerror'}:
        raise DiagnosticError("unsafe cause fields")
    if (not isinstance(value['exception_type'], str)
            or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,79}', value['exception_type'])
            or not isinstance(value['message_fingerprint'], str) or not HEX.fullmatch(value['message_fingerprint'])):
        raise DiagnosticError("invalid cause identity")
    locus = value['source_locus']
    if locus is not None and (not isinstance(locus, dict) or set(locus) != {'path', 'line'}
            or locus['path'] not in {node.split('::', 1)[0], 'src/aios_renew/operator.py', 'src/aios_renew/authoring_ingress.py'}
            or not integer(locus['line'], 1, 1_000_000)):
        raise DiagnosticError("unsafe source locus")
    if any(not integer(value[k]) for k in set(value) & {'errno', 'winerror'}):
        raise DiagnosticError("invalid OS cause number")


def validate(phase: str, status: int, value: dict, expected: list[str] | None, root: Path | None = None) -> dict:
    collection_only = phase == 'collection'
    if (not isinstance(value, dict) or set(value) != FIELDS or value['schema'] != SCHEMA
            or type(value['version']) is not int or value['version'] != 1 or value['phase'] != phase
            or not integer(status, 0, 1) or type(value['exit_status']) is not int or status != value['exit_status']
            or (collection_only and status != 0) or value['integrity'] is not True or value['over_bound'] is not False):
        raise DiagnosticError("instrumentation or phase integrity failure")
    nodes = ([safe(n) for n in value['collection']] if collection_only
             and isinstance(value['collection'], list) and len(value['collection']) <= 10000
             else identities(value['collection']))
    if not nodes or (expected is not None and nodes != expected):
        raise DiagnosticError("exact collection mismatch")
    executed = identities(value['executed'])
    if set(executed) != (set() if collection_only else set(nodes)):
        raise DiagnosticError("exact execution mismatch")
    labels = {'controller'} if collection_only else {'controller'} | {f'gw{i}' for i in range(12)}
    processes = value['processes']
    if not isinstance(processes, dict) or set(processes) != labels:
        raise DiagnosticError("missing worker processes")
    for label, process in processes.items():
        if (not isinstance(process, dict) or set(process) != {'pid', 'temp', 'cache', 'isolated'}
                or not integer(process['pid'], 1) or process['isolated'] is not True
                or any(not isinstance(process[k], str) or not HEX.fullmatch(process[k]) for k in ('temp', 'cache'))):
            raise DiagnosticError("nonisolated process")
        if root is not None:
            temporary = root / ('collection-tmp' if collection_only else 'parallel-12-tmp')
            if label != 'controller':
                temporary /= 'popen-' + label
            digest = 'sha256:' + hashlib.sha256(os.path.normcase(str(temporary.resolve())).encode()).hexdigest()
            if process['temp'] != digest:
                raise DiagnosticError('wrapper temporary geometry mismatch')
    if len({p[k] for p in processes.values() for k in ('temp', 'cache')}) != 2 * len(labels):
        raise DiagnosticError('overlapping mutable roots')
    for key in ('pid', 'temp', 'cache'):
        if len({p[key] for p in processes.values()}) != len(labels):
            raise DiagnosticError("shared process or mutable root")
    workers = labels - {'controller'}
    if (not isinstance(value['worker_collections'], dict) or set(value['worker_collections']) != workers
            or any(v != nodes for v in value['worker_collections'].values())
            or value['worker_reports'] != sorted(workers)):
        raise DiagnosticError("worker collection/report mismatch")
    reports = value['reports']
    if not isinstance(reports, dict) or set(reports) != set(executed):
        raise DiagnosticError("missing execution stages")
    assignments = {}
    for node, report in reports.items():
        if not isinstance(report, dict) or set(report) != {'worker', 'stages'} or report['worker'] not in (workers or {'controller'}):
            raise DiagnosticError("invalid execution assignment")
        stages = report['stages']
        if (not isinstance(stages, dict) or set(stages) != (set(STAGES) if stages.get('setup') == 'passed' else {'setup', 'teardown'})
                or any(outcome not in {'passed', 'failed', 'skipped'} for outcome in stages.values())
                or (phase == 'residual-n12' and 'skipped' in stages.values())):
            raise DiagnosticError("incomplete execution protocol")
        assignments[node] = report['worker']
    residual_git = value['residual_git']
    if not isinstance(residual_git, dict) or set(residual_git) != set(executed) & targets():
        raise DiagnosticError('incomplete residual Git observations')
    for node, details in residual_git.items():
        if not isinstance(details, dict) or set(details) != {'git_failures', 'pushes'}:
            raise DiagnosticError('unsafe residual Git fields')
        git, pushes = details['git_failures'], details['pushes']
        if not isinstance(git, list) or len(git) > MAX_GIT or not isinstance(pushes, list) or len(pushes) > 4:
            raise DiagnosticError('residual Git bound exceeded')
        for detail in git:
            validate_git(detail)
        for ordinal, detail in enumerate(pushes, 1):
            validate_git(detail, push=True)
            if node != _safe_nodeid(GIT_TARGET) or detail['ordinal'] != ordinal:
                raise DiagnosticError('PUSH target/ordinal mismatch')
        if node == _safe_nodeid(GIT_TARGET) and all(o == 'passed' for o in reports[node]['stages'].values()) and len(pushes) != 4:
            raise DiagnosticError('incomplete successful PUSH protocol')
    failures = value['failures']
    if not isinstance(failures, list) or len(failures) > LIMIT * 3:
        raise DiagnosticError("over-bound failures")
    seen = set()
    for fact in failures:
        fields = {'nodeid', 'phase', 'worker_id', 'cause', 'context', 'git_failures', 'pushes'}
        if not isinstance(fact, dict) or set(fact) != fields:
            raise DiagnosticError("unsafe failure fact")
        node = safe(fact['nodeid'])
        if node.split('#', 1)[0] in MUTATORS:
            raise DiagnosticError('installed mutator identity outside coactive snapshot')
        worker, stage = fact['worker_id'], fact['phase']
        if (node not in assignments or assignments[node] != worker or stage not in STAGES
                or reports[node]['stages'].get(stage) != 'failed' or (node, stage) in seen):
            raise DiagnosticError("failure/report contradiction")
        seen.add((node, stage))
        validate_cause(fact['cause'], node)
        residual = node in targets()
        context = fact['context']
        if residual and phase == 'full-n12':
            if not isinstance(context, dict) or set(context) != {'predecessors', 'coactive', 'installed'}:
                raise DiagnosticError("missing full-suite context")
            predecessors = identities(context['predecessors'])
            coactive = context['coactive']
            if (len(predecessors) > 4 or node in predecessors
                    or any(n.split('#', 1)[0] in MUTATORS for n in predecessors)
                    or not isinstance(coactive, dict) or len(coactive) > 11 or not set(coactive) <= workers - {worker}):
                raise DiagnosticError("context bounds violated")
            active = identities(list(coactive.values()))
            if node in active or not set(active + predecessors) <= set(nodes):
                raise DiagnosticError("context outside collection")
            history = [n for n in executed if assignments[n] == worker]
            previous = history[max(0, history.index(node) - 4):history.index(node)]
            previous = [n for n in previous if n.split('#', 1)[0] not in MUTATORS]
            if predecessors != previous or any(assignments[n] != w for w, n in coactive.items()):
                raise DiagnosticError('context worker/history contradiction')
            installed = context['installed']
            if not isinstance(installed, dict) or set(installed) != PROVENANCE or any(v not in RELATIONS for v in installed.values()):
                raise DiagnosticError("unsafe provenance relation")
        elif context is not None:
            raise DiagnosticError("unexpected context")
        git, pushes = fact['git_failures'], fact['pushes']
        if not isinstance(git, list) or len(git) > MAX_GIT or not isinstance(pushes, list) or len(pushes) > 4:
            raise DiagnosticError("Git bound exceeded")
        if not residual and (git or pushes):
            raise DiagnosticError("Git observation outside residual protocol")
        if residual:
            complete = residual_git[node]
            if git != complete['git_failures'][:len(git)] or pushes != complete['pushes'][:len(pushes)]:
                raise DiagnosticError('contradictory Git protocol snapshot')
        for detail in git:
            validate_git(detail)
        for ordinal, detail in enumerate(pushes, 1):
            validate_git(detail, push=True)
            if detail['ordinal'] != ordinal or node != _safe_nodeid(GIT_TARGET):
                raise DiagnosticError("PUSH target or sequence mismatch")
    expected_failures = {(n, s) for n, r in reports.items() for s, o in r['stages'].items() if o == 'failed'}
    if len({n for n, _ in seen}) > LIMIT:
        raise DiagnosticError('failure identity bound exceeded')
    if seen != expected_failures or bool(seen) != bool(status):
        raise DiagnosticError("incomplete failure population")
    return {'phase': phase, 'workers': 0 if collection_only else 12,
            'pytest_exit_status': status, 'collection_count': len(nodes),
            'collection_digest': hashlib.sha256(''.join(n + '\n' for n in nodes).encode()).hexdigest(),
            'failures': sorted(failures, key=lambda f: (f['nodeid'], STAGES.index(f['phase']))),
            'residual_git': residual_git}


def diagnose(repository: Path, *, runner: Callable = run_pytest, identity: Callable = subject_identity,
             baseline_loader: Callable = baseline) -> dict:
    repository = repository.resolve()
    subject = identity(repository)
    if (not isinstance(subject, dict) or subject.get('worktree_clean') is not True
            or subject.get('kind') != 'git-commit' or not re.fullmatch('[0-9a-f]{40}', subject.get('head_sha', ''))):
        raise DiagnosticError('subject is not clean and exact')
    installed = baseline_loader()
    profiles = []
    seen_roots = set()

    def observe(phase, selectors, expected):
        if identity(repository) != subject:
            raise DiagnosticError('subject changed before phase')
        if phase != 'collection' and installed:
            from tests.aios_residual_context_attribution_plugin import provenance_snapshot, provenance_relation
            current = provenance_snapshot(Path(installed['site']))
            if set(provenance_relation(installed['snapshot'], current).values()) != {'BASELINE_EXACT'}:
                raise DiagnosticError('installed baseline changed before execution')
        # Each subprocess has an independent wrapper-equivalent temp geometry.
        with tempfile.TemporaryDirectory(prefix='aios-parallel-full-suite-') as directory:
            try:
                status, value, raw = runner(repository, Path(directory), phase=phase,
                                            selectors=tuple(selectors), installed_baseline=installed)
            finally:
                if identity(repository) != subject:
                    raise DiagnosticError('subject changed during phase')
            record = validate(phase, status, value, expected, Path(directory))
            roots = {p[k] for p in value['processes'].values() for k in ('temp', 'cache')}
            if roots & seen_roots:
                raise DiagnosticError('reused phase mutable roots')
            seen_roots.update(roots)
            return record, value['collection'], raw

    collection, collected, raw = observe('collection', (), None)
    selectors, drift = resolve(raw, collected)
    if identity(repository) != subject:
        raise DiagnosticError('subject changed after collection resolution')
    envelope = {'format': FORMAT, 'version': 1, 'subject': subject,
                'historical_evidence': HISTORICAL_EVIDENCE, 'historical_artifact': HISTORICAL_ARTIFACT,
                'historical_residual_identities': sorted(targets()), 'collection': collection,
                'profiles': profiles, 'identity_drift': drift}
    if drift:
        envelope['classification'] = 'HISTORICAL_IDENTITY_DRIFT'
        return envelope
    residual, _, _ = observe('residual-n12', selectors, [_safe_nodeid(n) for n in selectors])
    profiles.append(residual)
    if residual['failures']:
        envelope['classification'] = 'RESIDUAL_SET_N12_REPRODUCED'
        return envelope
    full, _, _ = observe('full-n12', (), collected)
    profiles.append(full)
    failed = {f['nodeid'] for f in full['failures']}
    envelope['classification'] = ('FULL_SUITE_PASS_DRIFT' if not failed else
                                  'FULL_SUITE_CONTEXT_REPRODUCED' if failed == targets() else
                                  'FULL_SUITE_POPULATION_DRIFT')
    return envelope


def main(argv: Sequence[str] | None = None) -> int:
    if list(sys.argv[1:] if argv is None else argv):
        print('residual context diagnostic accepts no arguments', file=sys.stderr)
        return 2
    try:
        result = diagnose(Path.cwd())
    except (DiagnosticError, OSError, subprocess.SubprocessError, KeyError, TypeError, ValueError, AttributeError):
        print('residual context diagnostic integrity failure', file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, separators=(',', ':')))
    return 2 if result['classification'] == 'HISTORICAL_IDENTITY_DRIFT' else 0


if __name__ == '__main__':
    raise SystemExit(main())
