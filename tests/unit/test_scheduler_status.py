# -*- coding: utf-8 -*-
"""
-------------------------------------------------
   File Name：     test_scheduler_status.py
   Description :   调度器状态、并发执行与快照可靠性测试
   Author :        flyeyas
   date：          2026/11/12
-------------------------------------------------
   Change Activity:
                   2026/11/12: 新增心跳和任务状态测试
-------------------------------------------------
"""
__author__ = 'flyeyas'

import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest
from apscheduler.events import (
    EVENT_JOB_MISSED, EVENT_JOB_MAX_INSTANCES, EVENT_SCHEDULER_STARTED,
    EVENT_SCHEDULER_PAUSED, EVENT_SCHEDULER_RESUMED,
    JobExecutionEvent, JobSubmissionEvent, SchedulerEvent,
)
from apscheduler.schedulers.background import BackgroundScheduler

import helper.schedulerStatus as status_mod


@pytest.fixture
def status_file(tmp_path, monkeypatch):
    path = tmp_path / 'log' / 'scheduler_status.json'
    monkeypatch.setattr(status_mod, 'STATUS_FILE', str(path))
    return path


@pytest.fixture
def monitor(status_file):
    scheduler = BackgroundScheduler(timezone='Asia/Shanghai')
    result = status_mod.SchedulerMonitor(scheduler, logging.getLogger('status-test'))
    scheduler.add_listener(result.onEvent, result.events)
    result.start()
    yield result
    if scheduler.running:
        scheduler.shutdown(wait=True)
    result.stop()


def job(data, job_id='proxy_fetch'):
    return next(item for item in data['jobs'] if item['id'] == job_id)


def test_without_scheduler_is_not_started(status_file):
    data = status_mod.getSchedulerStatus()
    assert data['status'] == 'not_started'
    assert data['online'] is False
    assert {item['id'] for item in data['jobs']} == {'proxy_fetch', 'proxy_check', 'log_cleanup'}
    assert all(item['status'] == 'not_started' for item in data['jobs'])
    assert not status_file.exists()


@pytest.mark.parametrize('content', ['{', '[]', '{}',
    '{"schema_version": 1, "jobs": [], "heartbeat_timestamp": 1}'])
def test_invalid_snapshot_is_unavailable(status_file, content):
    status_file.parent.mkdir()
    status_file.write_text(content, encoding='utf-8')
    data = status_mod.getSchedulerStatus()
    assert data['status'] == 'unavailable'
    assert data['online'] is False
    assert data['error']


def test_startup_has_live_heartbeat(monitor):
    data = status_mod.getSchedulerStatus()
    assert data['status'] == 'starting'
    assert data['online'] is True
    assert data['heartbeat_at'].endswith('+08:00')
    assert data['started_at']


def test_stale_heartbeat_masks_running_and_next_time(monitor, status_file):
    snapshot = json.loads(status_file.read_text())
    snapshot['status'] = 'running'
    snapshot['jobs'][0]['running_count'] = 1
    snapshot['jobs'][0]['next_run_time'] = '2026-11-12T10:00:00+08:00'
    status_file.write_text(json.dumps(snapshot))
    data = status_mod.getSchedulerStatus(snapshot['heartbeat_timestamp'] + status_mod.STALE_AFTER + 1)
    assert data['status'] == 'offline'
    assert data['online'] is False
    assert job(data)['status'] == 'unknown'
    assert job(data)['next_run_time'] is None


def test_heartbeat_refresh_restores_online(monitor, monkeypatch):
    initial = status_mod.getSchedulerStatus()
    later = initial['heartbeat_timestamp'] + status_mod.STALE_AFTER + 1
    monkeypatch.setattr(status_mod, '_now', lambda: later)
    assert status_mod.getSchedulerStatus()['status'] == 'offline'
    monitor.refresh()
    assert status_mod.getSchedulerStatus()['online'] is True
    assert status_mod.getSchedulerStatus()['heartbeat_age'] == 0


def test_real_scheduler_publishes_trigger_times(monitor):
    for definition in status_mod.SCHEDULED_JOBS:
        monitor.scheduler.add_job(monitor.wrap(definition['id'], lambda: None),
                                  definition['trigger'], id=definition['id'],
                                  **definition['trigger_args'], **definition['options'])
    monitor.scheduler.start(paused=True)
    data = status_mod.getSchedulerStatus()
    assert data['status'] == 'paused'
    assert all(item['next_run_time'] for item in data['jobs'])
    cleanup_time = datetime.fromisoformat(job(data, 'log_cleanup')['next_run_time'])
    assert cleanup_time.minute == cleanup_time.second == 0
    assert cleanup_time.utcoffset() == timedelta(hours=8)


def test_pause_resume_keeps_heartbeat(monitor):
    monitor.onEvent(SchedulerEvent(EVENT_SCHEDULER_PAUSED))
    assert status_mod.getSchedulerStatus()['status'] == 'paused'
    assert status_mod.getSchedulerStatus()['online'] is True
    monitor.onEvent(SchedulerEvent(EVENT_SCHEDULER_RESUMED))
    assert status_mod.getSchedulerStatus()['status'] == 'running'


def test_success_preserves_return_and_records_completion(monitor):
    assert monitor.runJob('proxy_fetch', lambda: ['proxy'], source='startup') == ['proxy']
    data = status_mod.getSchedulerStatus()
    item = job(data)
    assert item['run_count'] == item['success_count'] == 1
    assert item['error_count'] == item['running_count'] == 0
    assert item['last_result'] == 'success'
    assert item['last_duration'] >= 0
    assert item['last_started_at'] and item['last_finished_at']
    assert data['history'][0]['source'] == 'startup'
    assert data['history'][0]['result'] == 'success'


def test_failure_is_visible_and_propagates_then_recovers(monitor):
    def fail():
        raise RuntimeError('fetch failed')

    with pytest.raises(RuntimeError, match='fetch failed'):
        monitor.runJob('proxy_fetch', fail)
    data = status_mod.getSchedulerStatus()
    assert job(data)['last_error'] == 'fetch failed'
    assert job(data)['error_count'] == 1
    assert job(data)['running_count'] == 0
    assert data['history'][0]['result'] == 'error'
    monitor.runJob('proxy_fetch', lambda: None)
    item = job(status_mod.getSchedulerStatus())
    assert item['last_error'] is None
    assert item['last_result'] == 'success'
    assert item['success_count'] == item['error_count'] == 1


def test_overlapping_runs_do_not_clear_each_other(monitor):
    barrier = threading.Barrier(3)
    release_first = threading.Event()
    release_second = threading.Event()

    def wait(release):
        barrier.wait(timeout=5)
        assert release.wait(timeout=5)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(monitor.runJob, 'proxy_fetch', lambda: wait(release_first))
        second = pool.submit(monitor.runJob, 'proxy_fetch', lambda: wait(release_second))
        try:
            barrier.wait(timeout=5)
            item = job(status_mod.getSchedulerStatus())
            assert item['status'] == 'running'
            assert item['running_count'] == 2
            release_first.set()
            first.result(timeout=5)
            assert job(status_mod.getSchedulerStatus())['running_count'] == 1
        finally:
            release_first.set()
            release_second.set()
        second.result(timeout=5)
    item = job(status_mod.getSchedulerStatus())
    assert item['running_count'] == 0
    assert item['success_count'] == 2


def test_all_records_within_recent_hour_are_kept(monitor):
    for _ in range(40):
        monitor.runJob('proxy_fetch', lambda: None)
    data = status_mod.getSchedulerStatus()
    assert len(data['history']) == 40
    assert job(data)['run_count'] == 40
    assert data['history_retention_seconds'] == 3600


def test_heartbeat_removes_expired_records_at_hour_boundary(monitor, status_file, monkeypatch):
    clock = [status_mod._now()]
    monkeypatch.setattr(status_mod, '_now', lambda: clock[0])
    monitor.runJob('proxy_fetch', lambda: None)
    clock[0] += 1800
    monitor.runJob('proxy_check', lambda: None)
    clock[0] += 1800
    monitor.refresh()
    assert len(status_mod.getSchedulerStatus()['history']) == 2
    clock[0] += 0.001
    monitor.refresh()
    data = status_mod.getSchedulerStatus()
    assert [record['job_id'] for record in data['history']] == ['proxy_check']
    assert len(json.loads(status_file.read_text())['history']) == 1
    assert job(data)['run_count'] == 1


def test_read_filters_expired_records_without_modifying_snapshot(monitor, status_file, monkeypatch):
    monitor.runJob('proxy_fetch', lambda: None)
    raw = status_file.read_bytes()
    now = status_mod._now() + status_mod.HISTORY_RETENTION_SECONDS + 1
    monkeypatch.setattr(status_mod, '_now', lambda: now)
    data = status_mod.getSchedulerStatus()
    assert data['history'] == []
    assert data['status'] == 'offline'
    assert status_file.read_bytes() == raw


def test_legacy_record_times_use_timezone_aware_hour_window(monitor, status_file, monkeypatch):
    now = datetime(2026, 11, 12, 10, tzinfo=ZoneInfo('Asia/Shanghai')).timestamp()
    snapshot = json.loads(status_file.read_text())
    snapshot['history'] = [
        {'id': 'recent', 'finished_at': '2026-11-12T01:00:01+00:00'},
        {'id': 'expired', 'finished_at': '2026-11-12T08:59:59+08:00'},
    ]
    status_file.write_text(json.dumps(snapshot))
    monkeypatch.setattr(status_mod, '_now', lambda: now)
    assert [record['id'] for record in status_mod.getSchedulerStatus()['history']] == ['recent']


def test_long_running_job_is_kept_when_recently_completed(monitor, monkeypatch):
    clock = [status_mod._now()]
    monkeypatch.setattr(status_mod, '_now', lambda: clock[0])
    def long_job():
        clock[0] += status_mod.HISTORY_RETENTION_SECONDS + 1
    monitor.runJob('proxy_fetch', long_job)
    data = status_mod.getSchedulerStatus()
    assert len(data['history']) == 1
    assert data['history'][0]['result'] == 'success'


def test_missed_execution_uses_record_time_instead_of_old_schedule(monitor):
    scheduled = datetime.now(ZoneInfo('Asia/Shanghai')) - timedelta(hours=2)
    monitor.onEvent(JobExecutionEvent(EVENT_JOB_MISSED, 'proxy_check', 'default', scheduled))
    data = status_mod.getSchedulerStatus()
    assert len(data['history']) == 1
    assert data['history'][0]['result'] == 'missed'


def test_recent_history_survives_monitor_restart(monitor):
    monitor.runJob('proxy_fetch', lambda: None)
    old_id = status_mod.getSchedulerStatus()['history'][0]['id']
    monitor.stop()
    restarted = status_mod.SchedulerMonitor(monitor.scheduler, monitor.logger)
    try:
        restarted.start()
        restarted.runJob('proxy_check', lambda: None)
        data = status_mod.getSchedulerStatus()
        assert len(data['history']) == 2
        assert data['history'][1]['id'] == old_id
        assert job(data)['success_count'] == 0
    finally:
        restarted.stop()


def test_restart_does_not_restore_expired_history(monitor, monkeypatch):
    monitor.runJob('proxy_fetch', lambda: None)
    monitor.stop()
    now = status_mod._now() + status_mod.HISTORY_RETENTION_SECONDS + 1
    monkeypatch.setattr(status_mod, '_now', lambda: now)
    restarted = status_mod.SchedulerMonitor(monitor.scheduler, monitor.logger)
    try:
        restarted.start()
        assert status_mod.getSchedulerStatus()['history'] == []
    finally:
        restarted.stop()


def test_atomic_replacement_allows_parallel_readers(monitor, status_file):
    def read():
        for _ in range(100):
            snapshot = json.loads(status_file.read_text())
            assert snapshot['schema_version'] == 1
            assert len(snapshot['jobs']) == 3

    with ThreadPoolExecutor(max_workers=4) as pool:
        readers = [pool.submit(read) for _ in range(3)]
        for _ in range(30):
            monitor.runJob('proxy_fetch', lambda: None)
        for reader in readers:
            reader.result(timeout=5)
    assert not list(status_file.parent.glob('.scheduler-*'))


def test_status_write_failure_does_not_stop_tasks(monitor, monkeypatch):
    logger = MagicMock()
    monitor.logger = logger
    def fail_replace(*args):
        raise PermissionError('read only')
    monkeypatch.setattr(status_mod.os, 'replace', fail_replace)
    assert monitor.runJob('proxy_fetch', lambda: 'done') == 'done'
    assert monitor._jobs['proxy_fetch']['success_count'] == 1
    logger.warning.assert_called_once()


def test_missed_and_max_instances_are_recorded(monitor):
    scheduled = datetime.now(ZoneInfo('Asia/Shanghai'))
    monitor.onEvent(JobExecutionEvent(EVENT_JOB_MISSED, 'proxy_check', 'default', scheduled))
    monitor.onEvent(JobSubmissionEvent(EVENT_JOB_MAX_INSTANCES, 'proxy_check', 'default',
                                      [scheduled, scheduled + timedelta(minutes=2)]))
    data = status_mod.getSchedulerStatus()
    item = job(data, 'proxy_check')
    assert item['missed_count'] == 1
    assert item['skipped_count'] == 2
    assert item['run_count'] == item['error_count'] == 0
    assert [record['result'] for record in data['history']] == ['skipped', 'skipped', 'missed']


def test_stopped_or_failed_scheduler_is_offline(monitor):
    monitor.stop(error=RuntimeError('startup failed'))
    monitor.stop()
    data = status_mod.getSchedulerStatus()
    assert data['status'] == 'error'
    assert data['online'] is False
    assert data['error'] == 'startup failed'
    assert data['stopped_at']


def test_jobs_execute_through_real_thread_executor(monitor):
    completed = threading.Event()
    monitor.scheduler.add_job(monitor.wrap('proxy_fetch', lambda: completed.set()),
                              'date', id='proxy_fetch')
    monitor.scheduler.start()
    assert completed.wait(timeout=5)
    monitor.scheduler.shutdown(wait=True)
    data = status_mod.getSchedulerStatus()
    assert data['status'] == 'stopped'
    assert job(data)['success_count'] == 1
    assert data['history'][0]['result'] == 'success'
