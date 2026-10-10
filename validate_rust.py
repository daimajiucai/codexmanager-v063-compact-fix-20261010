#!/usr/bin/env python3
"""Compare the complete, untouched v0.6.3 library suite with the patched suite.

No tests are skipped or edited. Only four exact, baseline-reproduced catalog
assertion failures may remain; new failures or changed existing outcomes abort.
"""
from pathlib import Path
import json
import os
import re
import subprocess
import sys

from apply_patch import patch

EXPECTED_BASELINE_FAILURES = {
    'gateway::local_models::tests::local_models_lists_each_image_model_once_with_image_only_capabilities',
    'quota::model_pricing::tests::catalog_prices_are_exact_and_missing_prices_do_not_fallback',
    'quota::read::tests::api_available_model_slugs_does_not_seed_legacy_price_rules',
    'quota::read::tests::api_available_model_slugs_preserves_catalog_sort_order',
}
PATCH_PREFIX = 'gateway::web_search_history::tests::'
SOURCE_COMMIT = '764531421428e35b0fbb3aab8f4f777887e7a44a'


def parse_suite(output: str, returncode: int) -> dict:
    outcomes = {}
    for name, status in re.findall(r'^test (\S+) \.\.\. (ok|FAILED|ignored)(?:\b|$)', output, re.M):
        if name in outcomes:
            raise ValueError('Duplicate test result: ' + name)
        outcomes[name] = status
    summaries = re.findall(
        r'^test result: (ok|FAILED)\. (\d+) passed; (\d+) failed; (\d+) ignored; '
        r'(\d+) measured; (\d+) filtered out;', output, re.M)
    if len(summaries) != 1:
        raise ValueError('Missing or ambiguous complete Rust library test summary')
    result, passed, failed, ignored, measured, filtered = summaries[0]
    counts = dict(passed=int(passed), failed=int(failed), ignored=int(ignored))
    if int(measured) or int(filtered):
        raise ValueError('Refusing filtered tests or benchmarks')
    for status, key in [('ok', 'passed'), ('FAILED', 'failed'), ('ignored', 'ignored')]:
        if sum(value == status for value in outcomes.values()) != counts[key]:
            raise ValueError('Individual outcomes do not match summary: ' + key)
    if (failed != '0') != (result == 'FAILED'):
        raise ValueError('Inconsistent summary status')
    if returncode != (101 if counts['failed'] else 0):
        raise ValueError('Unexpected cargo exit code: ' + str(returncode))
    signatures = {}
    for name, details in re.findall(r'^---- (\S+) stdout ----\n(.*?)(?=^---- |\Z)', output, re.M | re.S):
        assertion = re.search(r'assertion `left == right` failed\s+left: (\d+)\s+right: (\d+)', details)
        if assertion:
            signatures[name] = list(assertion.groups())
    return dict(counts=counts, outcomes=outcomes, assertion_values=signatures)


def compare_suites(baseline: dict, patched: dict) -> dict:
    before, after = baseline['outcomes'], patched['outcomes']
    failures = {name for name, result in before.items() if result == 'FAILED'}
    if failures != EXPECTED_BASELINE_FAILURES:
        raise ValueError('Untouched source does not reproduce the four exact known failures')
    if any(name.startswith(PATCH_PREFIX) for name in before):
        raise ValueError('Baseline is already patched')
    for name, result in before.items():
        if after.get(name) != result:
            raise ValueError('Changed or missing upstream test outcome: ' + name)
    additions = set(after) - set(before)
    if len(additions) != 13 or any(not name.startswith(PATCH_PREFIX) or after[name] != 'ok' for name in additions):
        raise ValueError('Exactly 13 passing patch tests are required; no unrelated additions')
    for name in failures:
        values = baseline['assertion_values'].get(name)
        if not values or values != patched['assertion_values'].get(name):
            raise ValueError('Baseline assertion changed: ' + name)
    return dict(verification='passed; no new failures against untouched source',
                upstream_source_commit=SOURCE_COMMIT, baseline=baseline['counts'],
                patched=patched['counts'], patch_tests_passed=len(additions),
                baseline_reproduced_failures=sorted(failures),
                caveat='Four upstream v0.6.3 catalog-count tests fail identically before and after the patch; no tests skipped or edited.')


def run_suite(source: Path, label: str, logs: Path) -> dict:
    logfile = logs / (label + '.log')
    env = dict(os.environ, CARGO_BUILD_JOBS=os.environ.get('BUILD_JOBS', '2'))
    print('Running complete ' + label + ' library suite (unfiltered, single test thread).', flush=True)
    with logfile.open('w') as f:
        result = subprocess.run(['cargo', 'test', '--locked', '-p', 'codexmanager-service',
                                 '--lib', '--', '--test-threads=1'], cwd=source, env=env,
                                stdout=f, stderr=subprocess.STDOUT)
    output = logfile.read_text()
    print(output, flush=True)
    return parse_suite(output, result.returncode)


def main(source: Path, logs: Path):
    source = source.resolve()
    logs.mkdir(parents=True, exist_ok=False)
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=source, text=True).strip()
    clean = subprocess.check_output(['git', 'status', '--porcelain'], cwd=source, text=True).strip()
    if commit != SOURCE_COMMIT or clean:
        raise ValueError('Baseline must be the pinned, completely untouched official source')
    baseline = run_suite(source, 'baseline', logs)
    # Check known failure set before touching any source.
    if {name for name, result in baseline['outcomes'].items() if result == 'FAILED'} != EXPECTED_BASELINE_FAILURES:
        raise ValueError('Unexpected baseline failures; refusing patch/build')
    patch(source, Path(__file__).with_name('web_search_history.rs'))
    subprocess.run(['git', 'diff', '--check'], cwd=source, check=True)
    patched = run_suite(source, 'patched', logs)
    report = compare_suites(baseline, patched)
    env = dict(os.environ, CARGO_BUILD_JOBS=os.environ.get('BUILD_JOBS', '2'))
    metadata = json.loads(subprocess.check_output(
        ['cargo', 'metadata', '--no-deps', '--format-version', '1', '--locked'], cwd=source, env=env, text=True))
    package = next(p for p in metadata['packages'] if p['name'] == 'codexmanager-service')
    other_targets = []
    for target in package['targets']:
        kind = target['kind'][0]
        flag = {'bin': '--bin', 'test': '--test', 'example': '--example'}.get(kind)
        if flag and target.get('test', True):
            command = ['cargo', 'test', '--locked', '-p', 'codexmanager-service', flag,
                       target['name'], '--', '--test-threads=1']
            subprocess.run(command, cwd=source, env=env, check=True)
            other_targets.append(kind + ':' + target['name'])
    subprocess.run(['cargo', 'test', '--locked', '-p', 'codexmanager-service', '--doc'],
                   cwd=source, env=env, check=True)
    report['other_test_targets_passed'] = other_targets
    report['doc_tests'] = 'passed'
    (logs / 'TEST-RESULTS.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2), flush=True)


if __name__ == '__main__':
    try:
        main(Path(sys.argv[1]), Path(sys.argv[2]))
    except (ValueError, OSError, subprocess.CalledProcessError, KeyError, StopIteration) as exc:
        raise SystemExit('Rust validation refused: ' + str(exc))
