"""Single-process resumable formula discovery; never publishes corpus artifacts."""
import json
import os
import re


def _save(path, payload):
    if path.is_symlink() or path.with_suffix('.tmp').is_symlink():
        raise ValueError('symlink checkpoint')
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temporary, path)


def run_batch(directory, jobs, config_hash, worker):
    directory.mkdir(parents=True, exist_ok=True)
    lock = directory / 'run.lock'
    # Exclusive creation prevents two runs from corrupting checkpoints.
    with lock.open('x', encoding='utf-8'):
        pass
    try:
        return _run_batch(directory, jobs, config_hash, worker)
    finally:
        lock.unlink()


def _run_batch(directory, jobs, config_hash, worker):
    directory.mkdir(parents=True, exist_ok=True)
    jobs = list(jobs)
    if len(jobs) != len(set(jobs)):
        raise ValueError('duplicate page jobs')
    for sha, page in jobs:
        if not re.fullmatch('[0-9a-f]{64}', sha) or type(page) is not int or page < 1:
            raise ValueError('invalid page identity')
    summary = {'total_pages': len(jobs), 'processed_pages': 0, 'reused_pages': 0,
               'failed_pages': 0, 'region_count': 0, 'transcribed_regions': 0,
               'config_hash': config_hash, 'status': 'running', 'publishable': False}
    for sha, page in jobs:
        target = directory / f'{sha}-{page}.json'
        if target.is_symlink():
            raise ValueError('symlink checkpoint')
        cached = None
        if target.is_file():
            try:
                cached = json.loads(target.read_text(encoding='utf-8'))
            except (ValueError, OSError):
                pass
        cached_pages = cached.get('result', {}).get('pages') if isinstance(cached, dict) and isinstance(cached.get('result'), dict) else None
        valid_pages = (isinstance(cached_pages, list) and len(cached_pages) == 1
                       and isinstance(cached_pages[0], dict) and cached_pages[0].get('physical_page') == page
                       and isinstance(cached_pages[0].get('regions'), list)
                       and all(isinstance(r, dict) and not r.get('failure') for r in cached_pages[0]['regions']))
        reused = (valid_pages and isinstance(cached, dict) and cached.get('status') == 'completed'
                  and cached.get('config_hash') == config_hash and cached.get('sha256') == sha
                  and cached.get('physical_page') == page)
        if reused:
            record = cached
            summary['reused_pages'] += 1
        else:
            record = {'sha256': sha, 'physical_page': page, 'config_hash': config_hash,
                      'status': 'failed', 'publishable': False, 'can_use_for_calculation': False}
            try:
                result = worker(sha, page)
                pages = result['pages']
                if len(pages) != 1 or pages[0]['physical_page'] != page:
                    raise ValueError('worker page mismatch')
                record['result'] = result
                record['status'] = 'completed' if not any(r.get('failure') for r in pages[0]['regions']) else 'failed'
            except Exception as exc:
                record['failure'] = type(exc).__name__
            _save(target, record)
        if record['status'] == 'failed':
            summary['failed_pages'] += 1
        regions = record.get('result', {}).get('pages', [{}])[0].get('regions', [])
        summary['region_count'] += len(regions)
        summary['transcribed_regions'] += sum(bool(r.get('raw_latex')) for r in regions)
        summary['processed_pages'] += 1
        _save(directory / 'summary.json', summary)
        print(f'{summary["processed_pages"]}/{len(jobs)} pages; {summary["region_count"]} regions; {summary["failed_pages"]} failed', flush=True)
    summary['status'] = 'finished_with_failures' if summary['failed_pages'] else 'finished_pending_review'
    _save(directory / 'summary.json', summary)
    return summary
