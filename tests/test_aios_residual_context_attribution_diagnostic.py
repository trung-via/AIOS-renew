"""Contract regressions use synthetic observations; no diagnosed tests execute."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from scripts import aios_residual_context_attribution_diagnostic as diagnostic
from tests import aios_residual_context_attribution_plugin as plugin

RAW = [diagnostic.GIT_TARGET] + [f'tests/test_operator.py::test_residual_{i}[private://operand-{i}]' for i in range(32)]
SAFE = [diagnostic._safe_nodeid(n) for n in RAW]
EXTRA = 'tests/test_other.py::test_extra'
SUBJECT = {'kind': 'git-commit', 'head_sha': 'a' * 40, 'worktree_clean': True}


def digest(value):
    return 'sha256:' + hashlib.sha256(value.encode()).hexdigest()


def pushes():
    return [{'ordinal': i, 'outcome': 'success', 'return_code': 0, 'ref_length': 99,
             'remote_root_length': 175, 'remote_lock_path_length': 280} for i in range(1, 5)]


def observation(phase, root, raw, failed=()):
    nodes = [diagnostic._safe_nodeid(n) for n in raw]
    collection = phase == 'collection'
    workers = [] if collection else [f'gw{i}' for i in range(12)]
    assignment = {n: workers[i % 12] for i, n in enumerate(nodes)} if workers else {}
    executed = [n for w in sorted(workers) for n in nodes if assignment[n] == w]
    reports = {n: {'worker': assignment[n], 'stages': {'setup': 'passed',
               'call': 'failed' if n in failed else 'passed', 'teardown': 'passed'}} for n in executed}
    git = {n: {'git_failures': [], 'pushes': pushes() if n == SAFE[0] else []}
           for n in executed if n in diagnostic.targets()}
    facts = []
    for node in failed:
        context = None
        if phase == 'full-n12' and node in diagnostic.targets():
            history = [n for n in executed if assignment[n] == assignment[node]]
            index = history.index(node)
            context = {'predecessors': history[max(0, index - 4):index], 'coactive': {},
                       'installed': {k: 'BASELINE_EXACT' for k in diagnostic.PROVENANCE}}
        facts.append({'nodeid': node, 'phase': 'call', 'worker_id': assignment[node],
                      'cause': {'exception_type': 'OperatorError', 'message_fingerprint': digest('cause'), 'source_locus': None},
                      'context': context, 'git_failures': [], 'pushes': git.get(node, {}).get('pushes', [])})
    processes = {}
    for index, label in enumerate(['controller', *workers]):
        temp = root / ('collection-tmp' if collection else 'parallel-12-tmp')
        if label != 'controller':
            temp /= 'popen-' + label
        processes[label] = {'pid': 100 + index, 'temp': digest(os.path.normcase(str(temp.resolve()))),
                            'cache': digest(str(root / ('cache-' + label))), 'isolated': True}
    return {'schema': diagnostic.SCHEMA, 'version': 1, 'phase': phase, 'exit_status': int(bool(failed)),
            'integrity': True, 'over_bound': False, 'collection': nodes,
            'executed': executed, 'reports': reports, 'failures': facts, 'residual_git': git,
            'processes': processes, 'worker_collections': {w: nodes for w in workers}, 'worker_reports': sorted(workers)}


@pytest.fixture
def population(monkeypatch):
    monkeypatch.setattr(diagnostic, 'targets', lambda: frozenset(SAFE))
    return RAW


def experiment(population, a=(), b=(), raw=None, mutate=None, identity=None):
    calls = []
    canonical = list(population if raw is None else raw)
    def runner(repo, root, *, phase, selectors, installed_baseline):
        calls.append((phase, selectors, root))
        selected = list(selectors) if phase == 'residual-n12' else canonical
        failed = a if phase == 'residual-n12' else b if phase == 'full-n12' else ()
        value = observation(phase, root, selected, failed)
        if mutate:
            mutate(phase, value)
        return value['exit_status'], value, selected if phase == 'collection' else None
    result = diagnostic.diagnose(Path.cwd(), runner=runner, identity=identity or (lambda p: SUBJECT), baseline_loader=lambda: {})
    return result, calls


def test_historical_evidence_derivation_has_only_three_subtractions():
    from scripts.aios_serial_context_diagnostic import _targets
    expected = (_targets() | {diagnostic._safe_nodeid(diagnostic.GIT_TARGET)}) - diagnostic.FIXED
    assert diagnostic.targets() == expected
    assert len(expected) == 33
    assert diagnostic.HISTORICAL_EVIDENCE == 'RUN-233-002-V002'
    assert len(diagnostic.HISTORICAL) == 36


@pytest.mark.parametrize('change', ['missing', 'changed', 'duplicate'])
def test_historical_drift_is_bounded_and_stops_before_execution(population, change):
    raw = list(population)
    if change == 'missing':
        raw.pop()
    elif change == 'changed':
        raw[-1] += '[changed]'
    else:
        raw.append(raw[-1])
    result, calls = experiment(population, raw=raw)
    assert result['classification'] == 'HISTORICAL_IDENTITY_DRIFT'
    assert [c[0] for c in calls] == ['collection']
    assert result['profiles'] == []
    assert result['identity_drift']['resolved_count'] == 32
    assert 'private://' not in json.dumps(result)


def test_residual_n12_reproduction_stops_with_exact_population(population):
    result, calls = experiment(population, a=SAFE[:2])
    assert result['classification'] == 'RESIDUAL_SET_N12_REPRODUCED'
    assert [(c[0], c[1]) for c in calls] == [('collection', ()), ('residual-n12', tuple(population))]
    assert len(result['profiles']) == 1
    assert len(result['profiles'][0]['residual_git']) == 33
    assert 'private://' not in json.dumps(result)


@pytest.mark.parametrize(('failed', 'classification'), [
    ([], 'FULL_SUITE_PASS_DRIFT'),
    (SAFE, 'FULL_SUITE_CONTEXT_REPRODUCED'),
    (SAFE[:1], 'FULL_SUITE_POPULATION_DRIFT'),
    ([diagnostic._safe_nodeid(EXTRA)], 'FULL_SUITE_POPULATION_DRIFT'),
])
def test_conditional_full_suite_exact_context_and_population_drift(population, failed, classification):
    result, calls = experiment(population, b=failed, raw=population + [EXTRA])
    assert result['classification'] == classification
    assert [c[0] for c in calls] == ['collection', 'residual-n12', 'full-n12']
    assert calls[-1][1] == ()
    assert len(set(c[2] for c in calls)) == 3
    assert all(c[2].name.startswith('aios-parallel-full-suite-') for c in calls)
    for failure in result['profiles'][-1]['failures']:
        assert (failure['context'] is not None) == (failure['nodeid'] in SAFE)


@pytest.mark.parametrize('defect', [
    'worker_missing', 'worker_pid', 'root_shared', 'root_geometry', 'cache_missing', 'isolation',
    'collection_changed', 'execution_duplicate', 'execution_missing', 'report_missing', 'skipped',
    'unsafe_cause', 'unsafe_git', 'extra_key', 'integrity', 'failure_missing', 'failure_duplicate',
    'over_bound', 'exit_bool', 'predecessors', 'coactive', 'provenance', 'wrong_predecessor_worker',
])
def test_integrity_and_privacy_defects_fail_closed(population, defect):
    def mutate(phase, value):
        if phase != 'full-n12':
            return
        if defect == 'worker_missing':
            value['processes'].pop('gw11')
        elif defect == 'worker_pid':
            value['processes']['gw0']['pid'] = value['processes']['gw1']['pid']
        elif defect == 'root_shared':
            value['processes']['gw0']['cache'] = value['processes']['gw1']['cache']
        elif defect == 'root_geometry':
            value['processes']['gw0']['temp'] = digest('shorter-path')
        elif defect == 'cache_missing':
            value['processes']['gw0']['cache'] = None
        elif defect == 'isolation':
            value['processes']['gw0']['isolated'] = False
        elif defect == 'collection_changed':
            value['worker_collections']['gw0'] = value['collection'][:-1]
        elif defect == 'execution_duplicate':
            value['executed'].append(value['executed'][0])
        elif defect == 'execution_missing':
            value['executed'].pop()
        elif defect == 'report_missing':
            value['reports'].pop(SAFE[0])
        elif defect == 'skipped':
            value['reports'][SAFE[1]]['stages'].pop('call')
        elif defect == 'unsafe_cause':
            value['failures'][0]['cause']['message'] = 'private://secret'
        elif defect == 'unsafe_git':
            value['residual_git'][SAFE[0]]['pushes'][0]['remote'] = 'private://secret'
        elif defect == 'extra_key':
            value['private'] = 'private://secret'
        elif defect == 'integrity':
            value['integrity'] = False
        elif defect == 'failure_missing':
            value['failures'].clear()
        elif defect == 'failure_duplicate':
            value['failures'].append(value['failures'][0])
        elif defect == 'over_bound':
            value['over_bound'] = True
        elif defect == 'exit_bool':
            value['exit_status'] = True
        elif defect == 'predecessors':
            value['failures'][0]['context']['predecessors'] = SAFE[1:6]
        elif defect == 'coactive':
            value['failures'][0]['context']['coactive'] = {f'gw{i}': SAFE[i] for i in range(12)}
        elif defect == 'provenance':
            value['failures'][0]['context']['installed']['direct_url'] = 'private://secret'
        elif defect == 'wrong_predecessor_worker':
            value['failures'][0]['context']['predecessors'] = [SAFE[1]]
    with pytest.raises(diagnostic.DiagnosticError):
        experiment(population, b=SAFE[:1], mutate=mutate)


def test_subject_change_dirty_and_mutation_on_runner_error(population):
    count = 0
    def identity(p):
        nonlocal count
        count += 1
        return SUBJECT if count < 5 else {**SUBJECT, 'head_sha': 'b' * 40}
    with pytest.raises(diagnostic.DiagnosticError, match='subject changed'):
        experiment(population, identity=identity)
    with pytest.raises(diagnostic.DiagnosticError, match='clean'):
        experiment(population, identity=lambda p: {**SUBJECT, 'worktree_clean': False})
    subject = dict(SUBJECT)
    def broken(*args, **kwargs):
        subject['head_sha'] = 'c' * 40
        raise RuntimeError('private detail')
    with pytest.raises(diagnostic.DiagnosticError, match='subject changed'):
        diagnostic.diagnose(Path.cwd(), runner=broken, identity=lambda p: dict(subject), baseline_loader=lambda: {})


def test_full_failure_population_64_bound(population):
    extra = [f'tests/test_other.py::test_{i}' for i in range(65)]
    safe = [diagnostic._safe_nodeid(n) for n in extra]
    result, _ = experiment(population, b=safe[:64], raw=population + extra)
    assert len(result['profiles'][-1]['failures']) == 64
    with pytest.raises(diagnostic.DiagnosticError, match='bound'):
        experiment(population, b=safe, raw=population + extra)


@pytest.mark.parametrize('relation', sorted(diagnostic.RELATIONS))
def test_provenance_relation_allowlist_in_context(population, relation):
    def mutate(phase, value):
        if phase == 'full-n12':
            value['failures'][0]['context']['installed'] = {k: relation for k in diagnostic.PROVENANCE}
    result, _ = experiment(population, b=SAFE[:1], mutate=mutate)
    assert set(result['profiles'][-1]['failures'][0]['context']['installed'].values()) == {relation}


def test_read_only_installed_provenance_relations(tmp_path):
    distribution = tmp_path / 'aios_renew-0.0.dist-info'
    distribution.mkdir()
    direct = distribution / 'direct_url.json'
    direct.write_bytes(b'{"private":"credential://value"}')
    before = plugin.provenance_snapshot(tmp_path)
    assert set(plugin.provenance_relation(before, plugin.provenance_snapshot(tmp_path)).values()) == {'BASELINE_EXACT'}
    assert direct.read_bytes() == b'{"private":"credential://value"}'
    direct.write_bytes(b'changed')
    assert plugin.provenance_relation(before, plugin.provenance_snapshot(tmp_path))['direct_url'] == 'CHANGED'
    direct.unlink()
    assert plugin.provenance_relation(before, plugin.provenance_snapshot(tmp_path))['direct_url'] == 'MISSING'
    (tmp_path / 'sitecustomize.py').write_text('private-content')
    assert plugin.provenance_relation(before, plugin.provenance_snapshot(tmp_path))['sitecustomize'] == 'CHANGED'
    (tmp_path / 'aios_renew-0.1.dist-info').mkdir()
    relation = plugin.provenance_relation(before, plugin.provenance_snapshot(tmp_path))
    assert relation['direct_url'] == relation['distribution_multiplicity'] == 'AMBIGUOUS'
    unavailable = plugin.provenance_snapshot(tmp_path / 'missing')
    assert set(plugin.provenance_relation(before, unavailable).values()) == {'UNAVAILABLE'}
    assert 'private' not in json.dumps(relation)


def test_missing_installed_distribution_is_valid_across_interpreter_boundary(tmp_path):
    # The live repair environment has this state. Exercise the real plugin
    # import and snapshot in a fresh interpreter, with only site discovery
    # redirected to a controlled empty installed directory.
    script = '''
import json, subprocess, sys
from pathlib import Path
from types import SimpleNamespace
from scripts import aios_residual_context_attribution_diagnostic as diagnostic
from tests.aios_residual_context_attribution_plugin import provenance_relation
site = Path(sys.argv[1])
diagnostic.subprocess.run = lambda *args, **kwargs: SimpleNamespace(stdout=json.dumps([str(site)]))
result = diagnostic.baseline()
print(json.dumps(provenance_relation(result['snapshot'], result['snapshot']), sort_keys=True))
'''
    child = subprocess.run([sys.executable, '-c', script, str(tmp_path)],
                           capture_output=True, text=True, check=True)
    assert json.loads(child.stdout) == {
        'direct_url': 'MISSING', 'distribution_multiplicity': 'MISSING',
        'sitecustomize': 'BASELINE_EXACT'}


def test_valid_nonideal_baselines_and_unusable_baseline(tmp_path):
    distribution = tmp_path / 'aios_renew-0.0.dist-info'
    distribution.mkdir()
    (tmp_path / 'aios_renew-0.1.dist-info').mkdir()
    ambiguous = plugin.provenance_snapshot(tmp_path)
    diagnostic.validate_baseline_snapshot(ambiguous)
    assert plugin.provenance_relation(ambiguous, ambiguous)['direct_url'] == 'AMBIGUOUS'
    with pytest.raises(diagnostic.DiagnosticError, match='unusable'):
        diagnostic.validate_baseline_snapshot(plugin.provenance_snapshot(tmp_path / 'unreadable'))
    with pytest.raises(diagnostic.DiagnosticError, match='inconsistent'):
        diagnostic.validate_baseline_snapshot({'direct_url': 'AMBIGUOUS',
            'sitecustomize': False, 'distribution_multiplicity': 0})


def test_registry_coactive_and_immediately_preceding_four(tmp_path, monkeypatch, population):
    (tmp_path / 'registry').mkdir()
    for i in range(12):
        (tmp_path / 'registry' / f'gw{i}.json').write_text(json.dumps({'worker': f'gw{i}', 'nodeid': None}))
    site = tmp_path / 'installed'; site.mkdir()
    baseline = plugin.provenance_snapshot(site)
    monkeypatch.setenv(diagnostic.PHASE_ENV, 'full-n12')
    monkeypatch.setenv(diagnostic.ROOT_ENV, str(tmp_path))
    monkeypatch.setenv(diagnostic.BASELINE_ENV, json.dumps({'site': str(site), 'snapshot': baseline}))
    observer = plugin.Observer(SimpleNamespace(workerinput={'workerid': 'gw0'}))
    for node in SAFE[1:7]:
        observer.predecessors.append(node)
    for i in range(1, 12):
        node = SAFE[i]
        (tmp_path / 'registry' / f'gw{i}.json').write_text(json.dumps({'worker': f'gw{i}', 'nodeid': node}))
    context = observer.context(SAFE[0])
    assert len(context['coactive']) == 11
    assert context['predecessors'] == SAFE[3:7]
    observer.registry(SAFE[0])
    assert json.loads((tmp_path / 'registry' / 'gw0.json').read_text())['nodeid'] == SAFE[0]
    observer.registry(None)
    assert json.loads((tmp_path / 'registry' / 'gw0.json').read_text())['nodeid'] is None
    assert observer.integrity is True
    (tmp_path / 'registry' / 'gw1.json').write_text('{"private":"secret"}')
    assert observer.context(SAFE[0]) is None
    assert observer.integrity is False


def test_installed_mutators_exposed_only_in_coactive_snapshot(tmp_path, monkeypatch, population):
    mutator = diagnostic._safe_nodeid(next(iter(diagnostic.MUTATORS)))
    (tmp_path / 'registry').mkdir()
    for i in range(12):
        (tmp_path / 'registry' / f'gw{i}.json').write_text(json.dumps({'worker': f'gw{i}', 'nodeid': None}))
    (tmp_path / 'registry' / 'gw1.json').write_text(json.dumps({'worker': 'gw1', 'nodeid': mutator}))
    monkeypatch.setenv(diagnostic.PHASE_ENV, 'full-n12')
    monkeypatch.setenv(diagnostic.ROOT_ENV, str(tmp_path))
    monkeypatch.setenv(diagnostic.BASELINE_ENV, json.dumps({'site': str(tmp_path), 'snapshot': plugin.provenance_snapshot(tmp_path)}))
    observer = plugin.Observer(SimpleNamespace(workerinput={'workerid': 'gw0'}))
    observer.predecessors.append(mutator)
    context = observer.context(SAFE[0])
    assert context['predecessors'] == []
    assert context['coactive'] == {'gw1': mutator}


@pytest.mark.parametrize('raises', [False, True])
def test_git_failure_delegation_preserves_semantics_and_redacts_operands(raises):
    observer = plugin.GitObserver(SAFE[1], 'gw0')
    command = ['git', '-C', 'private/permission denied', 'fetch', 'credential://remote']
    environment = {'PRIVATE': 'secret'}
    kwargs = {'cwd': 'private/cwd', 'env': environment, 'check': raises, 'text': False, 'capture_output': True}
    exception = subprocess.CalledProcessError(17, command, output=b'private-output', stderr=b'private/permission denied: fatal: cannot lock ref secret')
    calls = []
    result = SimpleNamespace(returncode=17, stderr=exception.stderr, stdout=b'private-output')
    def original(*args, **kw):
        calls.append((args, kw))
        if raises:
            raise exception
        return result
    wrapper = observer.wrap(original)
    if raises:
        with pytest.raises(subprocess.CalledProcessError) as caught:
            wrapper(command, **kwargs)
        assert caught.value is exception
    else:
        assert wrapper(command, **kwargs) is result
    assert calls == [((command,), kwargs)]
    assert calls[0][0][0] is command
    assert calls[0][1]['env'] is environment
    assert observer.integrity is True
    assert observer.failures[0]['command_family'] == 'FETCH'
    assert observer.failures[0]['stderr_category'] == 'LOCK_OR_REF_UPDATE'
    assert 'private' not in json.dumps(observer.failures)
    assert 'credential' not in json.dumps(observer.failures)


def test_operand_signature_does_not_become_stderr_cause():
    observer = plugin.GitObserver(SAFE[1], 'gw0')
    command = ['git', 'show', 'permission denied']
    observer.wrap(lambda *a, **kw: SimpleNamespace(returncode=1, stderr='permission denied'))(command, check=False, text=True)
    assert observer.failures[0]['stderr_category'] == 'OTHER'


def test_observer_defect_never_obstructs_original_git_call():
    observer = plugin.GitObserver(SAFE[0], 'gw0')
    calls = []
    result = SimpleNamespace(returncode=0, stderr='')
    def original(*args, **kwargs):
        calls.append((args, kwargs)); return result
    command = ['git', 'push', 'private://operand']
    assert observer.wrap(original)(command, check=False) is result
    assert len(calls) == 1
    assert observer.integrity is False


@pytest.mark.parametrize('failure', [False, True])
def test_specialized_push_retains_task240_geometry_and_ordinal(tmp_path, failure):
    # Build only a private config, without invoking Git or the diagnosed target.
    repo = tmp_path / ('x' * 175) / 'sandbox-a' / 'repo'
    (repo / '.git').mkdir(parents=True)
    remote = repo.parent / 'upstream.git'
    (repo / '.git' / 'config').write_text('[remote "origin"]\nurl = ' + str(remote).replace('\\', '/') + '\n')
    identity = hashlib.sha256(b'integration-0').hexdigest()
    ref = f'refs/heads/aios/integration/{identity}'
    command = ['git', '-C', str(repo), 'push', '--quiet', 'origin', f'{ref}:{ref}']
    observer = plugin.GitObserver(SAFE[0], 'gw0')
    calls = []
    def original(*args, **kwargs):
        calls.append((args, kwargs))
        if failure:
            raise subprocess.CalledProcessError(1, command, stderr='cannot lock ref ' + ref)
        return SimpleNamespace(returncode=0, stderr='')
    wrapper = observer.wrap(original)
    if failure:
        with pytest.raises(subprocess.CalledProcessError):
            wrapper(command, capture_output=True, text=True, check=True)
    else:
        wrapper(command, capture_output=True, text=True, check=True)
    assert len(calls) == 1
    assert observer.integrity is True
    fact = observer.push.pushes[0]
    assert fact['ordinal'] == 1
    assert fact['ref_length'] == len(ref)
    assert fact['remote_lock_path_length'] == len(str(remote / f'{ref}.lock'))
    assert fact['outcome'] == ('failure' if failure else 'success')
    assert str(repo) not in json.dumps(fact)
    assert ref not in json.dumps(fact)


def test_fixed_runner_n12_geometry_and_argument_rejection(tmp_path, monkeypatch, population):
    calls = []
    monkeypatch.setattr(diagnostic, 'read', lambda p: {})
    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(diagnostic.subprocess, 'run', run)
    status, _, _ = diagnostic.run_pytest(Path.cwd(), tmp_path, phase='residual-n12', selectors=tuple(population), installed_baseline={})
    command, kwargs = calls[0]
    assert status == 0
    assert command[-33:] == population
    assert command[command.index('--basetemp') + 1] == str(tmp_path / 'parallel-12-tmp')
    assert command[command.index('-n'):] == ['-n', '12', '--dist', 'load', '--max-worker-restart', '0', *population]
    assert kwargs['stdout'] == kwargs['stderr'] == subprocess.DEVNULL
    assert kwargs['env']['TMP'] == str(tmp_path)
    for phase, selectors in [('serial', ()), ('full-n12', tuple(population)), ('residual-n12', tuple(population[:-1]))]:
        with pytest.raises(diagnostic.DiagnosticError):
            diagnostic.run_pytest(Path.cwd(), tmp_path, phase=phase, selectors=selectors, installed_baseline={})
    monkeypatch.setattr(diagnostic, 'diagnose', lambda p: pytest.fail('arguments launched pytest'))
    assert diagnostic.main(['--help']) == diagnostic.main(['operand']) == 2


def test_main_observed_failure_exits_zero_and_integrity_exits_nonzero(monkeypatch, population, capsys):
    result, _ = experiment(population, a=SAFE[:1])
    monkeypatch.setattr(diagnostic, 'diagnose', lambda p: result)
    assert diagnostic.main([]) == 0
    assert json.loads(capsys.readouterr().out)['classification'] == 'RESIDUAL_SET_N12_REPRODUCED'
    def broken(p):
        raise diagnostic.DiagnosticError('private://error')
    monkeypatch.setattr(diagnostic, 'diagnose', broken)
    assert diagnostic.main([]) == 2
    output = capsys.readouterr()
    assert 'private' not in output.out + output.err

def test_historical_identity_drift_command_exits_nonzero(monkeypatch, population, capsys):
    result, _ = experiment(population, raw=population[:-1])
    monkeypatch.setattr(diagnostic, 'diagnose', lambda p: result)
    assert diagnostic.main([]) == 2
    assert json.loads(capsys.readouterr().out)['classification'] == 'HISTORICAL_IDENTITY_DRIFT'


@pytest.mark.parametrize('defect', ['none', 'not_main', 'wrong_lineage', 'failed_candidate'])
def test_subject_requires_exact_canonical_main_lineage(monkeypatch, defect):
    monkeypatch.setattr(diagnostic, 'clean_subject', lambda p: 'a' * 40)
    def git(command, **kwargs):
        if command[1] == 'rev-parse':
            return SimpleNamespace(returncode=0, stdout=('b' if defect == 'not_main' else 'a') * 40)
        if command[-2] == diagnostic.CANONICAL_MAIN:
            return SimpleNamespace(returncode=int(defect == 'wrong_lineage'))
        return SimpleNamespace(returncode=int(defect != 'failed_candidate'))
    monkeypatch.setattr(diagnostic.subprocess, 'run', git)
    if defect == 'none':
        assert diagnostic.subject_identity(Path.cwd())['canonical_main_ancestor'] == diagnostic.CANONICAL_MAIN
    else:
        with pytest.raises(diagnostic.DiagnosticError):
            diagnostic.subject_identity(Path.cwd())


def test_maximum_full_context_with_coactive_mutator_is_safe(population):
    preceding = [f'tests/test_other.py::test_predecessor_{i}' for i in range(72)]
    preceding[1] = next(iter(diagnostic.MUTATORS))
    def mutate(phase, value):
        if phase != 'full-n12':
            return
        context = value['failures'][0]['context']
        context['coactive'] = {f'gw{i}': diagnostic._safe_nodeid(preceding[i]) for i in range(1, 12)}
    result, _ = experiment(population, raw=preceding + population, b=SAFE[:1], mutate=mutate)
    context = result['profiles'][-1]['failures'][0]['context']
    assert len(context['coactive']) == 11
    assert len(context['predecessors']) == 4
    assert set(context['coactive'].values()) & {diagnostic._safe_nodeid(n) for n in diagnostic.MUTATORS}
    assert not set(context['predecessors']) & {diagnostic._safe_nodeid(n) for n in diagnostic.MUTATORS}


def test_git_observation_bound_preserves_all_original_calls():
    observer = plugin.GitObserver(SAFE[1], 'gw0')
    calls = []
    def original(*args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=1, stderr='unknown error')
    wrapped = observer.wrap(original)
    for i in range(diagnostic.MAX_GIT + 1):
        wrapped(['git', 'show', 'private-value'], check=False)
    assert len(calls) == diagnostic.MAX_GIT + 1
    assert len(observer.failures) == diagnostic.MAX_GIT
    assert observer.integrity is False


def test_failure_cause_retains_only_typed_safe_details(tmp_path, monkeypatch):
    monkeypatch.setenv('AIOS_BP_V4_DIAGNOSTIC_PREFIXES', json.dumps({
        'subject': str(Path.cwd()), 'diagnostic_temp': str(tmp_path),
        'pytest_basetemp': str(tmp_path / 'parallel-12-tmp'), 'user_home': str(Path.home())}))
    exc = OSError(13, 'private-secret-message', str(tmp_path / 'credential'))
    detail = plugin.cause(exc, None, diagnostic.GIT_TARGET)
    diagnostic.validate_cause(detail, SAFE[0])
    assert set(detail) == {'exception_type', 'message_fingerprint', 'source_locus', 'errno'}
    assert detail['errno'] == 13
    assert str(tmp_path) not in json.dumps(detail)
    assert 'private-secret' not in json.dumps(detail)
    assert 'credential' not in json.dumps(detail)
    first = plugin.cause(RuntimeError(str(tmp_path / 'same-message')), None, diagnostic.GIT_TARGET)
    changed_root = tmp_path / 'other-root'
    monkeypatch.setenv('AIOS_BP_V4_DIAGNOSTIC_PREFIXES', json.dumps({
        'subject': str(Path.cwd()), 'diagnostic_temp': str(changed_root),
        'pytest_basetemp': str(changed_root / 'parallel-12-tmp'), 'user_home': str(Path.home())}))
    second = plugin.cause(RuntimeError(str(changed_root / 'same-message')), None, diagnostic.GIT_TARGET)
    assert first['message_fingerprint'] == second['message_fingerprint']


def test_protocol_duplicate_json_keys_and_size_fail_closed(tmp_path, monkeypatch):
    path = tmp_path / 'observation.json'
    path.write_text('{"schema":"one","schema":"two"}')
    with pytest.raises(diagnostic.DiagnosticError, match='duplicate'):
        diagnostic.read(path)
    monkeypatch.setattr(diagnostic, 'MAX_BYTES', 8)
    with pytest.raises(diagnostic.DiagnosticError, match='size'):
        diagnostic.read(path)
