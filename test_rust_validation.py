"""Offline parser/acceptance checks; these are not actual Rust execution."""
import copy
import unittest
from validate_rust import EXPECTED_BASELINE_FAILURES, PATCH_PREFIX, compare_suites, parse_suite


def fixture(patched=False):
    failures = sorted(EXPECTED_BASELINE_FAILURES)
    names = ['unrelated::test'] + ([PATCH_PREFIX + 'case_' + str(i) for i in range(13)] if patched else [])
    lines = [f'test {name} ... ok' for name in names]
    lines += [f'test {name} ... FAILED' for name in failures]
    lines += ['test ignored::test ... ignored']
    for name in failures:
        lines += [f'---- {name} stdout ----', 'assertion `left == right` failed', '  left: 11', ' right: 10']
    lines += [f'test result: FAILED. {len(names)} passed; 4 failed; 1 ignored; 0 measured; 0 filtered out; finished in 1.00s']
    return parse_suite('\n'.join(lines), 101)


class ValidationTests(unittest.TestCase):
    def test_identical_baseline_failures_are_explicitly_reported(self):
        report = compare_suites(fixture(), fixture(True))
        self.assertEqual(report['patch_tests_passed'], 13)
        self.assertEqual(len(report['baseline_reproduced_failures']), 4)
        self.assertEqual(report['patched']['failed'], 4)

    def test_new_failures_are_refused(self):
        patched = fixture(True)
        patched['outcomes']['unrelated::test'] = 'FAILED'
        with self.assertRaises(ValueError): compare_suites(fixture(), patched)

    def test_missing_or_failing_patch_tests_are_refused(self):
        for mode in ['missing', 'failed']:
            patched = fixture(True)
            name = PATCH_PREFIX + 'case_0'
            if mode == 'missing': del patched['outcomes'][name]
            else: patched['outcomes'][name] = 'FAILED'
            with self.assertRaises(ValueError): compare_suites(fixture(), patched)

    def test_changed_baseline_assertion_is_refused(self):
        patched = fixture(True)
        name = sorted(EXPECTED_BASELINE_FAILURES)[0]
        patched['assertion_values'][name] = ['12', '10']
        with self.assertRaises(ValueError): compare_suites(fixture(), patched)

    def test_unrecognized_baseline_failure_is_refused(self):
        baseline = fixture()
        baseline['outcomes']['unknown::failure'] = 'FAILED'
        with self.assertRaises(ValueError): compare_suites(baseline, fixture(True))

    def test_duplicate_missing_filtered_and_bad_exit_results_are_refused(self):
        valid = 'test a ... ok\ntest result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0s'
        self.assertEqual(parse_suite(valid, 0)['counts']['passed'], 1)
        for output, code in [(valid, 1), (valid + '\n' + valid, 0),
                             (valid.replace('0 filtered out', '1 filtered out'), 0),
                             (valid.replace('test a ... ok\n', ''), 0), ('compilation failed', 101)]:
            with self.assertRaises(ValueError): parse_suite(output, code)


if __name__ == '__main__': unittest.main(verbosity=2)
