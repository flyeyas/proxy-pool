# -*- coding: utf-8 -*-
"""
-------------------------------------------------
   File Name：     schedulerStatus.py
   Description :   调度器心跳与任务执行状态, 供 WebUI 跨进程读取
   Author :        flyeyas
   date：          2026/11/12
-------------------------------------------------
   Change Activity:
                   2026/11/12: 原子写入状态快照, 跟踪执行结果和调度异常
                   2026/11/12: 执行记录按记录时间滚动保留最近一小时
-------------------------------------------------
"""
__author__ = 'flyeyas'

import json
import math
import os
import tempfile
import threading
import time
from datetime import datetime
from functools import wraps
from uuid import uuid4
from zoneinfo import ZoneInfo

from apscheduler.events import (
    EVENT_JOB_MAX_INSTANCES, EVENT_JOB_MISSED, EVENT_JOB_SUBMITTED,
    EVENT_SCHEDULER_STARTED, EVENT_SCHEDULER_SHUTDOWN,
    EVENT_SCHEDULER_PAUSED, EVENT_SCHEDULER_RESUMED,
)
from apscheduler.schedulers.base import STATE_PAUSED

from handler.configHandler import ConfigHandler

STATUS_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                           'log', 'scheduler_status.json')
HEARTBEAT_INTERVAL = 10
STALE_AFTER = 30
HISTORY_RETENTION_SECONDS = 60 * 60

# 调度器注册和未启动时的页面展示共用定义, 保证执行频率一致。
SCHEDULED_JOBS = (
    {'id': 'proxy_fetch', 'name': '代理采集',
     'description': '采集已启用的代理源, 验证后加入代理池',
     'schedule': '每 5 分钟', 'trigger': 'interval',
     'trigger_args': {'minutes': 5}, 'options': {}},
    {'id': 'proxy_check', 'name': '代理校验',
     'description': '复检代理池, 代理不足时补充采集',
     'schedule': '每 2 分钟', 'trigger': 'interval',
     'trigger_args': {'minutes': 2}, 'options': {}},
    {'id': 'log_cleanup', 'name': '过期日志清理',
     'description': '按小时切割日志, 清理七天以前的归档',
     'schedule': '每小时整点', 'trigger': 'cron',
     'trigger_args': {'minute': 0},
     'options': {'max_instances': 1, 'coalesce': True}},
)


def _now():
    return time.time()


def _timestamp(value, timezone):
    return datetime.fromtimestamp(value, ZoneInfo(timezone)).isoformat(timespec='seconds')


def _recent_history(history, now):
    cutoff = now - HISTORY_RETENTION_SECONDS
    recent = []
    for record in history:
        if not isinstance(record, dict):
            continue
        try:
            recorded = record.get('recorded_timestamp')
            if recorded is None:
                # 兼容原快照中只有完成时间的执行记录。
                recorded = datetime.fromisoformat(record['finished_at']).timestamp()
            if (isinstance(recorded, (int, float)) and math.isfinite(recorded)
                    and cutoff <= recorded <= now):
                recent.append(record)
        except (KeyError, ValueError, TypeError, OverflowError, OSError):
            continue
    return recent


def _empty_status(timezone):
    jobs = []
    for definition in SCHEDULED_JOBS:
        jobs.append({
            **{key: definition[key] for key in ('id', 'name', 'description', 'schedule')},
            'next_run_time': None, 'running_count': 0, 'running_since': None,
            'last_started_at': None, 'last_finished_at': None,
            'last_duration': None, 'last_result': None, 'last_error': None,
            'run_count': 0, 'success_count': 0, 'error_count': 0,
            'missed_count': 0, 'skipped_count': 0,
        })
    return {
        'schema_version': 1, 'status': 'not_started', 'timezone': timezone,
        'started_at': None, 'stopped_at': None, 'heartbeat_at': None,
        'heartbeat_timestamp': None, 'heartbeat_interval': HEARTBEAT_INTERVAL,
        'stale_after': STALE_AFTER, 'error': None, 'jobs': jobs, 'history': [],
        'history_retention_seconds': HISTORY_RETENTION_SECONDS,
    }


def getSchedulerStatus(now=None):
    """读取快照并根据心跳判断在线状态, 读取本身不会修改任务或文件。"""
    now = _now() if now is None else now
    data = _empty_status(ConfigHandler().timezone)
    try:
        with open(STATUS_FILE, encoding='utf-8') as stream:
            snapshot = json.load(stream)
        if (not isinstance(snapshot, dict) or snapshot.get('schema_version') != 1
                or snapshot.get('status') not in ('starting', 'running', 'paused', 'stopped', 'error')
                or not isinstance(snapshot.get('timezone'), str)
                or not isinstance(snapshot.get('history'), list)
                or not isinstance(snapshot.get('jobs'), list)
                or not isinstance(snapshot.get('heartbeat_timestamp'), (int, float))
                or not math.isfinite(snapshot['heartbeat_timestamp'])
                or any(not isinstance(job, dict) or 'running_count' not in job
                       for job in snapshot['jobs'])):
            raise ValueError('invalid scheduler status')
        ZoneInfo(snapshot['timezone'])
        data = snapshot
    except FileNotFoundError:
        pass
    except (OSError, ValueError, TypeError, KeyError):
        data['status'] = 'unavailable'
        data['error'] = '无法读取调度器状态, 请检查服务日志和日志目录权限'

    heartbeat = data.get('heartbeat_timestamp')
    age = max(0, now - heartbeat) if heartbeat is not None else None
    active = data['status'] in ('starting', 'running', 'paused')
    data['online'] = active and age is not None and age <= STALE_AFTER
    if active and not data['online']:
        data['status'] = 'offline'
    data['heartbeat_age'] = round(age, 1) if age is not None else None
    data['server_time'] = _timestamp(now, data['timezone'])
    data['history'] = _recent_history(data['history'], now)
    data['history_retention_seconds'] = HISTORY_RETENTION_SECONDS
    for job in data['jobs']:
        if data['online']:
            job['status'] = 'running' if job['running_count'] else 'waiting'
        else:
            job['status'] = 'not_started' if data['status'] == 'not_started' else 'unknown'
            job['next_run_time'] = None
    return data


class SchedulerMonitor:
    """一个调度器负责写快照, API 的多个 worker 只读原子替换后的完整文件。"""

    events = (EVENT_SCHEDULER_STARTED | EVENT_SCHEDULER_SHUTDOWN
              | EVENT_SCHEDULER_PAUSED | EVENT_SCHEDULER_RESUMED
              | EVENT_JOB_SUBMITTED | EVENT_JOB_MISSED | EVENT_JOB_MAX_INSTANCES)

    def __init__(self, scheduler, logger):
        self.scheduler = scheduler
        self.logger = logger
        self.timezone = str(scheduler.timezone)
        self._state = _empty_status(self.timezone)
        self._state['history'] = getSchedulerStatus()['history']
        self._jobs = {job['id']: job for job in self._state['jobs']}
        self._active = {job_id: {} for job_id in self._jobs}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._write_failed = False

    def _write_locked(self):
        now = _now()
        self._state['history'] = _recent_history(self._state['history'], now)
        self._state['heartbeat_timestamp'] = now
        self._state['heartbeat_at'] = _timestamp(now, self.timezone)
        temporary = None
        try:
            directory = os.path.dirname(STATUS_FILE)
            os.makedirs(directory, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8',
                                             dir=directory, prefix='.scheduler-',
                                             delete=False) as stream:
                temporary = stream.name
                json.dump(self._state, stream, ensure_ascii=False)
            os.replace(temporary, STATUS_FILE)
            self._write_failed = False
        except OSError:
            # 状态写入失败不应阻止采集、校验或日志清理。
            if not self._write_failed:
                self.logger.warning('无法写入调度器状态', exc_info=True)
            self._write_failed = True
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass

    def refresh(self):
        # 必须先释放 APScheduler 的锁再获取状态锁, 避免事件回调和心跳互相等待。
        next_runs = {job.id: getattr(job, 'next_run_time', None)
                     for job in self.scheduler.get_jobs()}
        with self._lock:
            for job_id, next_run in next_runs.items():
                if job_id in self._jobs:
                    self._jobs[job_id]['next_run_time'] = (
                        next_run.isoformat(timespec='seconds') if next_run else None)
            self._write_locked()

    def start(self):
        with self._lock:
            self._state['status'] = 'starting'
            self._state['started_at'] = _timestamp(_now(), self.timezone)
            self._write_locked()
        self._thread = threading.Thread(target=self._heartbeat,
                                        name='scheduler-status', daemon=True)
        self._thread.start()

    def _heartbeat(self):
        while not self._stop.wait(HEARTBEAT_INTERVAL):
            try:
                self.refresh()
            except Exception:
                self.logger.warning('无法更新调度器心跳', exc_info=True)

    def stop(self, error=None):
        self._stop.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=1)
        with self._lock:
            if error is not None:
                self._state['status'] = 'error'
                self._state['error'] = str(error)[:500]
            elif self._state['status'] != 'error':
                self._state['status'] = 'stopped'
            self._state['stopped_at'] = _timestamp(_now(), self.timezone)
            self._write_locked()

    def wrap(self, job_id, function):
        @wraps(function)
        def monitored():
            return self.runJob(job_id, function)
        return monitored

    def runJob(self, job_id, function, source='scheduled'):
        run_id = uuid4().hex
        started = _timestamp(_now(), self.timezone)
        monotonic_start = time.monotonic()
        with self._lock:
            job = self._jobs[job_id]
            self._active[job_id][run_id] = started
            job['running_count'] = len(self._active[job_id])
            job['running_since'] = min(self._active[job_id].values())
            job['last_started_at'] = started
            job['run_count'] += 1
            self._write_locked()
        result, error = 'success', None
        try:
            return function()
        except BaseException as exc:
            result, error = 'error', str(exc)[:500] or type(exc).__name__
            raise
        finally:
            finished = _timestamp(_now(), self.timezone)
            duration = round(time.monotonic() - monotonic_start, 3)
            with self._lock:
                self._active[job_id].pop(run_id)
                job['running_count'] = len(self._active[job_id])
                job['running_since'] = min(self._active[job_id].values(), default=None)
                job['last_finished_at'] = finished
                job['last_duration'] = duration
                job['last_result'] = result
                job['last_error'] = error
                job['success_count' if result == 'success' else 'error_count'] += 1
                self._add_history({
                    'id': run_id, 'job_id': job_id, 'name': job['name'],
                    'result': result, 'source': source, 'started_at': started,
                    'finished_at': finished, 'duration': duration, 'error': error,
                })
                self._write_locked()

    def _add_history(self, record):
        record['recorded_timestamp'] = _now()
        self._state['history'].insert(0, record)

    def onEvent(self, event):
        with self._lock:
            states = {
                EVENT_SCHEDULER_STARTED: 'running', EVENT_SCHEDULER_PAUSED: 'paused',
                EVENT_SCHEDULER_RESUMED: 'running', EVENT_SCHEDULER_SHUTDOWN: 'stopped',
            }
            if event.code in states:
                self._state['status'] = states[event.code]
                if event.code == EVENT_SCHEDULER_STARTED and self.scheduler.state == STATE_PAUSED:
                    self._state['status'] = 'paused'
                if event.code == EVENT_SCHEDULER_SHUTDOWN:
                    self._state['stopped_at'] = _timestamp(_now(), self.timezone)
                    self._stop.set()
            elif event.code in (EVENT_JOB_MISSED, EVENT_JOB_MAX_INSTANCES):
                job = self._jobs.get(event.job_id)
                if job is not None:
                    missed = event.code == EVENT_JOB_MISSED
                    scheduled = ([event.scheduled_run_time] if missed
                                 else event.scheduled_run_times)
                    job['missed_count' if missed else 'skipped_count'] += len(scheduled)
                    for run_time in scheduled:
                        self._add_history({
                            'id': uuid4().hex, 'job_id': job['id'], 'name': job['name'],
                            'result': 'missed' if missed else 'skipped', 'source': 'scheduled',
                            'scheduled_at': run_time.isoformat(timespec='seconds'),
                            'started_at': None, 'finished_at': _timestamp(_now(), self.timezone),
                            'duration': None, 'error': '错过执行时间' if missed else '达到并发上限',
                        })
            self._write_locked()
        self.refresh()
