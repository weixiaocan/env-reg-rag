import tempfile
import json
from pathlib import Path
import unittest
from src.application.formula_batch import run_batch


class FormulaBatchTests(unittest.TestCase):
    def test_corrupt_completed_checkpoint_is_reprocessed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            sha = 'd' * 64
            (path / f'{sha}-1.json').write_text(json.dumps({'sha256': sha, 'physical_page': 1,
                                                           'config_hash': 'one', 'status': 'completed'}))
            result = run_batch(path, [(sha, 1)], 'one',
                               lambda sha, page: {'pages': [{'physical_page': page, 'regions': []}]})
            self.assertEqual(result['reused_pages'], 0)
            self.assertEqual(result['failed_pages'], 0)

    def test_existing_run_lock_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / 'run.lock').touch()
            with self.assertRaises(FileExistsError):
                run_batch(path, [], 'one', lambda sha, page: None)

    def test_resume_and_failure_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            jobs = [('a' * 64, 1), ('a' * 64, 2)]
            calls = []
            def worker(sha, page):
                calls.append(page)
                if page == 2:
                    raise RuntimeError('private detail')
                return {'pages': [{'physical_page': page, 'regions': []}]}
            first = run_batch(Path(directory), jobs, 'config1', worker)
            self.assertEqual(first['failed_pages'], 1)
            second = run_batch(Path(directory), jobs, 'config1', worker)
            self.assertEqual(second['reused_pages'], 1)
            self.assertEqual(calls, [1, 2, 2])
            self.assertNotIn('private detail', (Path(directory) / ('a' * 64 + '-2.json')).read_text())

    def test_changed_configuration_reprocesses(self):
        with tempfile.TemporaryDirectory() as directory:
            calls = []
            def worker(sha, page):
                calls.append(page)
                return {'pages': [{'physical_page': page, 'regions': []}]}
            jobs = [('b' * 64, 1)]
            run_batch(Path(directory), jobs, 'one', worker)
            run_batch(Path(directory), jobs, 'two', worker)
            self.assertEqual(calls, [1, 1])

    def test_wrong_page_output_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            result = run_batch(Path(directory), [('c' * 64, 1)], 'one',
                               lambda sha, page: {'pages': [{'physical_page': 2, 'regions': []}]})
            self.assertEqual(result['failed_pages'], 1)
