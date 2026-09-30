"""Private bounded observer. Original Git calls are delegated exactly once.

Worker registries contain safe identities only. There are no locks, waits,
barriers, retries, schedule hooks, or writes to installed Python state.
"""
from __future__ import annotations

from collections import deque
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any

import pytest

from scripts import aios_residual_context_attribution_diagnostic as contract
from tests.aios_parallel_git_fixture_push_probe_plugin import Recorder as PushRecorder, stderr_category

ROOT = Path(__file__).resolve().parents[1]


def provenance_snapshot(site: Path) -> dict:
    """Private read-only baseline/snapshot. No operands enter the file protocol."""
    try:
        distributions = []
        for path in site.iterdir():
            if re.fullmatch(r'aios[-_]renew-.*\.dist-info', path.name, re.I):
                distributions.append(path)
                if len(distributions) > 64:
                    return {'direct_url': None, 'sitecustomize': None, 'distribution_multiplicity': None}
        count = len(distributions)
        direct = 'MISSING' if count == 0 else 'AMBIGUOUS' if count > 1 else None
        if count == 1:
            path = distributions[0] / 'direct_url.json'
            if not path.exists():
                direct = 'MISSING'
            else:
                with path.open('rb') as stream:
                    content = stream.read(1_048_577)
                if len(content) <= 1_048_576:
                    direct = hashlib.sha256(content).hexdigest()
        return {'direct_url': direct, 'sitecustomize': (site / 'sitecustomize.py').exists(),
                'distribution_multiplicity': count}
    except (OSError, ValueError):
        return {'direct_url': None, 'sitecustomize': None, 'distribution_multiplicity': None}


def provenance_relation(baseline: dict, current: dict) -> dict:
    result = {}
    for key in contract.PROVENANCE:
        before, now = baseline.get(key), current.get(key)
        if before is None or now is None:
            relation = 'UNAVAILABLE'
        elif key == 'direct_url' and now in {'MISSING', 'AMBIGUOUS'}:
            relation = now
        elif key == 'distribution_multiplicity' and now != 1:
            relation = 'MISSING' if now == 0 else 'AMBIGUOUS'
        elif key == 'sitecustomize' and before is True and now is False:
            relation = 'MISSING'
        else:
            relation = 'BASELINE_EXACT' if now == before else 'CHANGED'
        result[key] = relation
    return result


def cause(exc: BaseException | None, excinfo: Any, node: str) -> dict:
    if exc is None:
        raise contract.DiagnosticError('missing exception')
    kind = type(exc).__name__
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,79}', kind):
        kind = 'UnknownException'
    # Existing normalization replaces known absolute roots before hashing.
    from tests.bp_v4_diagnostic_plugin import _normalized_cause_text
    result = {'exception_type': kind, 'message_fingerprint': 'sha256:' + hashlib.sha256(
        _normalized_cause_text(str(exc)).encode('utf-8', errors='replace')).hexdigest(), 'source_locus': None}
    if excinfo is not None:
        for entry in reversed(list(excinfo.traceback)):
            try:
                relative = Path(entry.path).resolve().relative_to(ROOT).as_posix()
            except (OSError, ValueError):
                continue
            if relative in {node.split('::', 1)[0], 'src/aios_renew/operator.py', 'src/aios_renew/authoring_ingress.py'}:
                result['source_locus'] = {'path': relative, 'line': entry.lineno + 1}
                break
    if isinstance(exc, OSError):
        for key in ('errno', 'winerror'):
            value = getattr(exc, key, None)
            if value is not None:
                if not contract.integer(value):
                    raise contract.DiagnosticError('invalid OS number')
                result[key] = value
    return result


class GitObserver:
    def __init__(self, node: str, worker: str):
        self.node = node
        self.integrity = True
        self.failures = []
        self.push = PushRecorder('n12', worker) if node == contract._safe_nodeid(contract.GIT_TARGET) else None

    def wrap(self, original):
        def observe(*args, **kwargs):
            command = args[0] if args else kwargs.get('args')
            is_git = False
            shape, operands = None, ()
            try:
                is_git = bool(isinstance(command, (list, tuple)) and command
                              and isinstance(command[0], (str, os.PathLike))
                              and Path(command[0]).name.lower() in {'git', 'git.exe'})
                if is_git:
                    if len(command) > 256 or any(not isinstance(p, (str, os.PathLike)) for p in command):
                        raise ValueError('unbounded Git invocation')
                    words = [os.fspath(p) for p in command[1:]]
                    index = 0
                    cwd = kwargs.get('cwd')
                    # Skip real Git global options without reinterpreting their operands.
                    while index < len(words) and words[index].startswith('-'):
                        option = words[index]
                        if option in {'-C', '-c', '--git-dir', '--work-tree', '--namespace'}:
                            if index + 1 >= len(words):
                                raise ValueError('malformed Git prefix')
                            if option == '-C':
                                cwd = words[index + 1]
                            index += 2
                        else:
                            index += 1
                    family = contract.FAMILIES.get(words[index], 'OTHER') if index < len(words) else 'OTHER'
                    operands = tuple(words[:index] + words[index + 1:] + ([os.fspath(cwd)] if cwd else []))
                    shape = {'command_family': family,
                             'cwd_length': len(os.fspath(cwd)) if cwd else None,
                             'max_operand_length': max((len(p) for p in operands), default=None)}
                    if self.push is not None and family == 'PUSH':
                        self.push.push_count += 1
                        # TASK-240's exact operand/remote/ref checks and geometry.
                        push_shape, push_operands = self.push._shape(command, tuple(args[1:]), kwargs)
                        push_shape.pop('phase')
                        operands += push_operands
                        shape['push'] = push_shape
            except Exception:
                self.integrity = False

            def record(code, stderr):
                if not is_git:
                    return
                try:
                    if shape is None or not contract.integer(code):
                        raise ValueError('invalid Git observation')
                    text = stderr.decode('utf-8', errors='replace') if isinstance(stderr, bytes) else stderr
                    category = stderr_category(text, operands)
                    if code:
                        if len(self.failures) >= contract.MAX_GIT:
                            raise ValueError('Git failure bound exceeded')
                        fact = {k: v for k, v in shape.items() if k != 'push'}
                        fact.update(return_code=code, stderr_category=category)
                        contract.validate_git(fact)
                        self.failures.append(fact)
                    if 'push' in shape:
                        fact = {**shape['push'], 'return_code': code, 'outcome': 'failure' if code else 'success'}
                        if code:
                            fact['stderr_category'] = category
                        contract.validate_git(fact, push=True)
                        self.push.pushes.append(fact)
                except Exception:
                    self.integrity = False

            # Observation defects never obstruct or replace the one original call.
            try:
                result = original(*args, **kwargs)
            except subprocess.CalledProcessError as exc:
                record(exc.returncode, exc.stderr)
                raise
            except BaseException:
                if is_git:
                    self.integrity = False
                raise
            record(getattr(result, 'returncode', None), getattr(result, 'stderr', None))
            if is_git and kwargs.get('check') is True and getattr(result, 'returncode', None):
                self.integrity = False
            return result
        return observe


class Observer:
    def __init__(self, config):
        self.config = config
        self.worker = config.workerinput['workerid'] if hasattr(config, 'workerinput') else 'controller'
        self.phase = os.environ[contract.PHASE_ENV]
        self.root = Path(os.environ[contract.ROOT_ENV]).resolve()
        self.baseline = json.loads(os.environ[contract.BASELINE_ENV])
        self.integrity = True
        self.over_bound = False
        self.collection = []
        self.raw_collection = []
        self.executed = []
        self.reports = {}
        self.failures = []
        self.residual_git = {}
        self.predecessors = deque(maxlen=4)
        self.active = None
        self.git = None
        self.worker_collections = {}
        self.workers = {}

    def registry(self, node):
        try:
            if self.worker != 'controller':
                path = self.root / 'registry' / (self.worker + '.json')
                pending = path.with_suffix('.pending')
                pending.write_text(json.dumps({'worker': self.worker, 'nodeid': node}), encoding='utf-8')
                os.replace(pending, path)
        except Exception:
            self.integrity = False

    def context(self, node):
        if self.phase != 'full-n12' or node not in contract.targets():
            return None
        coactive = {}
        try:
            for i in range(12):
                worker = f'gw{i}'
                if worker == self.worker:
                    continue
                path = self.root / 'registry' / (worker + '.json')
                if not path.exists():
                    raise ValueError('missing worker registry')
                with path.open('rb') as stream:
                    data = stream.read(1025)
                if len(data) > 1024:
                    raise ValueError('registry bound')
                value = json.loads(data, object_pairs_hook=contract.unique)
                if not isinstance(value, dict) or set(value) != {'worker', 'nodeid'} or value['worker'] != worker:
                    raise ValueError('malformed registry')
                if value['nodeid'] is not None:
                    coactive[worker] = contract.safe(value['nodeid'])
            current = provenance_snapshot(Path(self.baseline['site']))
            return {'predecessors': [n for n in self.predecessors if n.split('#', 1)[0] not in contract.MUTATORS],
                    'coactive': coactive, 'installed': provenance_relation(self.baseline['snapshot'], current)}
        except Exception:
            self.integrity = False
            return None

    def report(self, item, call, report):
        try:
            node = contract._safe_nodeid(report.nodeid)
            if node != self.active or report.when not in contract.STAGES:
                raise ValueError('unexpected protocol report')
            if report.when == 'setup':
                self.executed.append(node)
            stages = self.reports.setdefault(node, {'worker': self.worker, 'stages': {}})['stages']
            next_stage = ('setup' if not stages else 'call' if stages == {'setup': 'passed'} else
                          'teardown' if 'teardown' not in stages else None)
            if report.when != next_stage:
                raise ValueError('duplicate/out-of-order stage')
            stages[report.when] = report.outcome
            if report.failed:
                if len({f['nodeid'] for f in self.failures} | {node}) > contract.LIMIT:
                    self.over_bound = True
                    return
                fact = {'nodeid': node, 'phase': report.when, 'worker_id': self.worker,
                        'cause': cause(call.excinfo.value if call.excinfo else None, call.excinfo, report.nodeid),
                        'context': self.context(node), 'git_failures': list(self.git.failures) if self.git else [],
                        'pushes': list(self.git.push.pushes) if self.git and self.git.push else []}
                contract.validate_cause(fact['cause'], node)
                self.failures.append(fact)
        except Exception:
            self.integrity = False


def process_facts(observer):
    from tests import git_fixture_support
    temporary = observer.config._tmp_path_factory.getbasetemp().resolve()
    cache = git_fixture_support._cache_root.resolve()
    expected = observer.root / ('collection-tmp' if observer.phase == 'collection' else 'parallel-12-tmp')
    if observer.worker != 'controller':
        expected /= 'popen-' + observer.worker
    def fingerprint(path):
        return 'sha256:' + hashlib.sha256(os.path.normcase(str(path)).encode()).hexdigest()
    return {'pid': os.getpid(), 'temp': fingerprint(temporary), 'cache': fingerprint(cache),
            'isolated': temporary == expected and cache.parent == observer.root
                        and cache.name.startswith('aios-git-fixtures-') and cache.is_dir()}


_observer = None


def pytest_sessionstart(session):
    from tests import git_fixture_support
    del git_fixture_support
    global _observer
    _observer = Observer(session.config)
    if _observer.worker == 'controller' and _observer.phase != 'collection':
        try:
            for i in range(12):
                worker = f'gw{i}'
                (_observer.root / 'registry' / (worker + '.json')).write_text(
                    json.dumps({'worker': worker, 'nodeid': None}), encoding='utf-8')
        except Exception:
            _observer.integrity = False


def pytest_collection_finish(session):
    try:
        _observer.collection = [contract.safe(contract._safe_nodeid(item.nodeid)) for item in session.items]
        if _observer.phase == 'collection':
            _observer.raw_collection = [item.nodeid for item in session.items]
    except Exception:
        _observer.integrity = False


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item, nextitem):
    observer = _observer
    original = subprocess.run
    wrapper = None
    try:
        node = contract._safe_nodeid(item.nodeid)
        observer.active = node
        observer.registry(node)
        observer.git = GitObserver(node, observer.worker) if node in contract.targets() else None
        if observer.git is not None:
            wrapper = observer.git.wrap(original)
            subprocess.run = wrapper
    except Exception:
        observer.integrity = False
    try:
        yield
    finally:
        if wrapper is not None:
            if subprocess.run is not wrapper:
                observer.integrity = False
            subprocess.run = original
        if observer.git is not None:
            if not observer.git.integrity:
                observer.integrity = False
            observer.residual_git[observer.active] = {'git_failures': list(observer.git.failures),
                'pushes': list(observer.git.push.pushes) if observer.git.push else []}
        if observer.active is not None:
            observer.predecessors.append(observer.active)
        observer.registry(None)
        observer.active = None
        observer.git = None


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    _observer.report(item, call, outcome.get_result())


@pytest.hookimpl(optionalhook=True)
def pytest_xdist_node_collection_finished(node, ids):
    try:
        label = node.gateway.id
        if label in _observer.worker_collections:
            raise ValueError('duplicate worker collection')
        _observer.worker_collections[label] = contract.identities([contract._safe_nodeid(n) for n in ids])
    except Exception:
        _observer.integrity = False


@pytest.hookimpl(optionalhook=True)
def pytest_testnodedown(node, error):
    try:
        label = node.gateway.id
        if error is not None or label in _observer.workers:
            raise ValueError('worker lost or restarted')
        value = node.workeroutput['aios_residual_context']
        fields = {'collection', 'executed', 'reports', 'failures', 'process', 'integrity', 'over_bound', 'residual_git'}
        if not isinstance(value, dict) or set(value) != fields:
            raise ValueError('malformed worker report')
        _observer.workers[label] = value
    except Exception:
        _observer.integrity = False


def pytest_sessionfinish(session, exitstatus):
    observer = _observer
    local = {'collection': observer.collection, 'executed': observer.executed, 'reports': observer.reports,
             'failures': observer.failures, 'residual_git': observer.residual_git, 'process': process_facts(observer),
             'integrity': observer.integrity, 'over_bound': observer.over_bound}
    if hasattr(session.config, 'workerinput'):
        session.config.workeroutput['aios_residual_context'] = local
        return
    collection_only = observer.phase == 'collection'
    values = [local] if collection_only else [observer.workers[k] for k in sorted(observer.workers)]
    collection = local['collection'] if collection_only else next(iter(observer.worker_collections.values()), [])
    reports = {}
    residual_git = {}
    for value in values:
        if set(reports) & set(value['reports']):
            observer.integrity = False
        reports.update(value['reports'])
        if set(residual_git) & set(value['residual_git']):
            observer.integrity = False
        residual_git.update(value['residual_git'])
        if value['collection'] != collection:
            observer.integrity = False
    payload = {'schema': contract.SCHEMA, 'version': 1, 'phase': observer.phase, 'exit_status': int(exitstatus),
               'integrity': observer.integrity and all(v['integrity'] is True for v in values),
               'over_bound': any(v['over_bound'] for v in values), 'collection': collection,
               'executed': [n for v in values for n in v['executed']], 'reports': reports,
               'failures': [f for v in values for f in v['failures']], 'residual_git': residual_git,
               'processes': {'controller': local['process'], **{k: v['process'] for k, v in observer.workers.items()}},
               'worker_collections': observer.worker_collections, 'worker_reports': sorted(observer.workers)}
    # An observer defect emits only an invalid safe sentinel, never unchecked data.
    try:
        contract.validate(observer.phase, int(exitstatus), payload, collection, observer.root)
    except Exception:
        payload = {'schema': contract.SCHEMA, 'integrity': False}
    Path(os.environ[contract.OUTPUT_ENV]).write_text(json.dumps(payload, sort_keys=True, separators=(',', ':')),
                                                   encoding='utf-8')

    if collection_only:
        # Sessionfinish runs after pytest stops capture. This anonymous pipe is
        # transient; raw selectors are never part of a durable observation.
        print(contract.SELECTORS_MARKER + json.dumps(observer.raw_collection, ensure_ascii=True), flush=True)
