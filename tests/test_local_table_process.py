import queue
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from src.ingestion.local_table_process import LocalTableProcess


class LocalTableProcessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'data/raw').mkdir(parents=True)
        self.path = self.root / 'data/raw/source.pdf'
        self.path.write_bytes(b'fixture')
        self.worker = LocalTableProcess(self.root, timeout_seconds=.01)
        self.worker.process = Mock()
        self.worker.process.poll.return_value = None
        self.worker.responses = queue.Queue()
        self.addCleanup(self.worker.close)

    def test_timeout_terminates_only_native_worker(self):
        process = self.worker.process
        with self.assertRaises(TimeoutError):
            self.worker.parse_page(self.path, physical_page=1)
        process.terminate.assert_called_once()
        self.assertIsNone(self.worker.process)

    def test_native_exit_is_explicit_and_does_not_leak_exception(self):
        self.worker.responses.put({'status': 'failed', 'reason': 'native_process_exit', 'exit_code': 7})
        with self.assertRaises(RuntimeError):
            self.worker.parse_page(self.path, physical_page=1)

    def test_source_path_outside_raw_is_rejected(self):
        with self.assertRaises(ValueError):
            self.worker.parse_page(self.root / 'outside.pdf', physical_page=1)
