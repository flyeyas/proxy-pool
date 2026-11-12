# -*- coding: utf-8 -*-
"""
-------------------------------------------------
   File Name：     proxyScheduler
   Description :
   Author :        JHao
   date：          2019/8/5
-------------------------------------------------
   Change Activity:
                   2019/08/05: proxyScheduler
                   2021/02/23: runProxyCheck时,剩余代理少于POOL_SIZE_MIN时执行抓取
                   2026/10/29: 移除 six 兼容层
                   2026/11/11: 启动与每小时整点清理七天以前的日志
                   2026/11/12: 向 WebUI 提供调度器心跳与任务执行状态
-------------------------------------------------
"""
__author__ = 'JHao'

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.executors.pool import ProcessPoolExecutor

from queue import Queue
from helper.fetch import Fetcher
from helper.check import Checker
from handler.logHandler import LogHandler, cleanupExpiredLogs
from handler.proxyHandler import ProxyHandler
from handler.configHandler import ConfigHandler
from helper.schedulerStatus import SCHEDULED_JOBS, SchedulerMonitor


def __runProxyFetch():
    proxy_queue = Queue()
    proxy_fetcher = Fetcher()

    for proxy in proxy_fetcher.run():
        proxy_queue.put(proxy)

    Checker("raw", proxy_queue)


def __runProxyCheck():
    proxy_handler = ProxyHandler()
    proxy_queue = Queue()
    if proxy_handler.db.getCount().get("total", 0) < proxy_handler.conf.poolSizeMin:
        __runProxyFetch()
    for proxy in proxy_handler.getAll():
        proxy_queue.put(proxy)
    Checker("use", proxy_queue)


def runScheduler():
    timezone = ConfigHandler().timezone
    scheduler_log = LogHandler("scheduler")
    scheduler = BlockingScheduler(logger=scheduler_log, timezone=timezone)

    executors = {
        'default': {'type': 'threadpool', 'max_workers': 20},
        'processpool': ProcessPoolExecutor(max_workers=5)
    }
    job_defaults = {
        'coalesce': False,
        'max_instances': 10
    }

    scheduler.configure(executors=executors, job_defaults=job_defaults,
                        logger=scheduler_log, timezone=timezone)
    monitor = SchedulerMonitor(scheduler, scheduler_log)
    functions = {'proxy_fetch': __runProxyFetch, 'proxy_check': __runProxyCheck,
                 'log_cleanup': cleanupExpiredLogs}
    scheduler.add_listener(monitor.onEvent, monitor.events)
    monitor.start()
    try:
        monitor.runJob('log_cleanup', cleanupExpiredLogs, source='startup')
        monitor.runJob('proxy_fetch', __runProxyFetch, source='startup')
        for job in SCHEDULED_JOBS:
            scheduler.add_job(monitor.wrap(job['id'], functions[job['id']]), job['trigger'],
                              id=job['id'], name=job['name'],
                              **job['trigger_args'], **job['options'])
        scheduler.start()
    except Exception as exc:
        monitor.stop(error=exc)
        raise
    finally:
        monitor.stop()


if __name__ == '__main__':
    runScheduler()
