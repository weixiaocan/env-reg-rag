"""Terminate a hung/crashed native table runtime without losing page checkpoints."""
import json
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path


PREFIX = 'LOCAL_TABLE_RESULT '


class LocalTableRuntimeError(RuntimeError):
    def __init__(self, reason_code, exit_code=None):
        super().__init__('local native table processing failed')
        self.reason_code = reason_code
        self.exit_code = exit_code


class LocalTableProcess:
    def __init__(self, root, *, timeout_seconds=180):
        self.root = Path(root).resolve()
        self.timeout_seconds = timeout_seconds
        self.process = None
        self.responses = None
        self.reader_thread = None

    def close(self):
        process = self.process
        self.process = None
        reader_thread = self.reader_thread
        self.reader_thread = None
        if process is not None:
            if process.poll() is None:
                if os.name == 'nt' and isinstance(process.pid, int):
                    # Windows venv launchers own a second Python process. Killing
                    # only the launcher would leave native inference running.
                    subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10,
                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                else:
                    process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            if reader_thread is not None and reader_thread is not threading.current_thread():
                reader_thread.join(timeout=5)
            if process.stdin is not None:
                process.stdin.close()
            # Never close a buffered reader while its daemon thread owns the
            # stream lock. The OS will release it when a stuck reader exits.
            if process.stdout is not None and not (reader_thread and reader_thread.is_alive()):
                process.stdout.close()

    def _start(self):
        self.responses = queue.Queue(maxsize=2)
        env = {**os.environ, 'PYTHONUTF8': '1', 'PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK': 'True',
               'OMP_NUM_THREADS': '1', 'MKL_NUM_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1'}
        self.process = subprocess.Popen([sys.executable, '-u', '-X', 'utf8',
            str(Path(__file__).resolve().parents[2] / 'scripts/local_table_worker.py'),
            '--data-root', str(self.root)], cwd=self.root, env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding='utf-8', creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        process, responses = self.process, self.responses
        def read():
            try:
                for line in process.stdout:
                    if line.startswith(PREFIX):
                        responses.put(json.loads(line[len(PREFIX):]))
            except (ValueError, OSError):
                pass
            finally:
                try:
                    responses.put_nowait({'status': 'failed', 'reason': 'native_process_exit',
                                         'exit_code': process.wait()})
                except queue.Full:
                    pass
        self.reader_thread = threading.Thread(target=read, daemon=True)
        self.reader_thread.start()

    def parse_page(self, path, *, physical_page, regions=None):
        if self.process is None or self.process.poll() is not None:
            self.close()
            self._start()
        path = Path(path).resolve()
        if not path.is_relative_to(self.root / 'data/raw'):
            raise ValueError('table source outside registered raw directory')
        request = {'path': path.relative_to(self.root).as_posix(), 'physical_page': physical_page}
        if regions is not None:
            request['regions'] = regions
        try:
            self.process.stdin.write(json.dumps(request, ensure_ascii=False) + '\n')
            self.process.stdin.flush()
            result = self.responses.get(timeout=self.timeout_seconds)
        except queue.Empty:
            self.close()
            raise TimeoutError('local table processing exceeded time limit') from None
        except (OSError, BrokenPipeError):
            self.close()
            raise RuntimeError('local native table process unavailable') from None
        if result.get('status') != 'completed':
            self.close()
            reason = result.get('reason', 'native_process_failure')
            import re
            if not isinstance(reason, str) or not re.fullmatch(r'[A-Za-z_]{1,60}', reason):
                reason = 'native_process_failure'
            raise LocalTableRuntimeError(reason, result.get('exit_code'))
        return result['page']

    def recognize_regions(self, path, *, physical_page, regions):
        return self.parse_page(path, physical_page=physical_page, regions=regions)

    def __del__(self):
        if getattr(sys, 'is_finalizing', lambda: False)():
            return
        try:
            self.close()
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
