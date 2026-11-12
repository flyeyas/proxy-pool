# -*- coding: utf-8 -*-
"""
-------------------------------------------------
   File Name：     test_runtime_config.py
   Description :   共享配置事务、绑定文件与并发写入测试
   Author :        flyeyas
   date：          2026/11/12
-------------------------------------------------
   Change Activity:
                   2026/11/12: 验证配置修改互不覆盖
                   2026/11/12: 验证自定义持久化路径与新进程恢复配置
-------------------------------------------------
"""
__author__ = 'flyeyas'

import json
import multiprocessing
import os
import runpy
import subprocess
import sys
import pytest

import helper.runtimeConfig as config_mod


@pytest.fixture
def config_file(tmp_path, monkeypatch):
    path = tmp_path / 'runtime.json'
    monkeypatch.setattr(config_mod, 'CONFIG_FILE', str(path))
    return path


def _writer(path, barrier, tokens):
    import helper.runtimeConfig as config
    from helper.apiToken import generateToken
    from helper.fetcherConfig import setFetcherUseProxy, recordFetchResult
    config.CONFIG_FILE = path
    barrier.wait(timeout=10)
    for index in range(20):
        if tokens:
            generateToken('test-' + str(index))
        else:
            setFetcherUseProxy('TestFetcher', bool(index % 2))
            recordFetchResult('TestFetcher', True)


@pytest.mark.parametrize('override', [None, ''])
def test_default_path_keeps_existing_local_configuration(monkeypatch, override):
    if override is None:
        monkeypatch.delenv('RUNTIME_CONFIG_FILE', raising=False)
    else:
        monkeypatch.setenv('RUNTIME_CONFIG_FILE', override)
    namespace = runpy.run_path(config_mod.__file__)
    assert namespace['CONFIG_FILE'] == os.path.join(config_mod.ROOT_PATH, 'runtime_config.json')


def test_configured_storage_survives_fresh_processes(tmp_path):
    # 模拟新挂载的空目录, 再启动另一个进程读取同一份持久化配置。
    config_path = tmp_path / 'persistent' / 'data' / 'runtime_config.json'
    env = dict(os.environ, RUNTIME_CONFIG_FILE=str(config_path))
    writer = '''
from helper.apiToken import generateToken
from helper.fetcherConfig import setFetcherUseProxy, toggleRuntimeExclude, recordFetchResult
generateToken('persisted-token')
setFetcherUseProxy('TestFetcher', False)
toggleRuntimeExclude('TestFetcher', False)
recordFetchResult('TestFetcher', True)
'''
    reader = '''
from helper.apiToken import listTokens, verifyApiToken
from helper.fetcherConfig import getFetcherUseProxy, getRuntimeExclude, getFetcherStats
tokens = listTokens()
assert len(tokens) == 1 and tokens[0]['name'] == 'persisted-token'
assert verifyApiToken(tokens[0]['token'])
assert getFetcherUseProxy('TestFetcher') is False
assert getRuntimeExclude() == ['TestFetcher']
assert getFetcherStats()['TestFetcher']['total_ok'] == 1
'''
    subprocess.run([sys.executable, '-c', writer], cwd=config_mod.ROOT_PATH,
                   env=env, check=True, capture_output=True, text=True, timeout=15)
    saved = config_path.read_bytes()
    subprocess.run([sys.executable, '-c', reader], cwd=config_mod.ROOT_PATH,
                   env=env, check=True, capture_output=True, text=True, timeout=15)
    assert config_path.read_bytes() == saved


def test_update_preserves_other_config_fields(config_file):
    config_mod.writeConfig({'api_tokens': [{'name': 'existing'}], 'fetcher_exclude': ['OtherFetcher']})
    config_mod.updateConfig(lambda config: config.update(fetcher_use_proxy={'TestFetcher': False}))
    assert config_mod.readConfig() == {'api_tokens': [{'name': 'existing'}],
        'fetcher_exclude': ['OtherFetcher'], 'fetcher_use_proxy': {'TestFetcher': False}}


def test_updates_preserve_inode_and_existing_file_handles(config_file):
    config_mod.writeConfig({'long': 'old value' * 20})
    inode = config_file.stat().st_ino
    with config_file.open() as handle:
        config_mod.updateConfig(lambda config: (config.clear(), config.update(short=True)))
        handle.seek(0)
        assert json.load(handle) == {'short': True}
    assert config_file.stat().st_ino == inode


def test_failed_update_keeps_saved_configuration(config_file):
    config_mod.writeConfig({'keep': True})
    def fail(config):
        config.clear()
        raise RuntimeError('failed')
    with pytest.raises(RuntimeError):
        config_mod.updateConfig(fail)
    assert config_mod.readConfig() == {'keep': True}


def test_concurrent_processes_preserve_proxy_switch_tokens_and_stats(config_file):
    context = multiprocessing.get_context('spawn')
    barrier = context.Barrier(3)
    workers = [context.Process(target=_writer, args=(str(config_file), barrier, tokens))
               for tokens in (True, False)]
    for worker in workers:
        worker.start()
    barrier.wait(timeout=10)
    for worker in workers:
        worker.join(timeout=15)
        assert worker.exitcode == 0
    data = config_mod.readConfig()
    assert len(data['api_tokens']) == 20
    assert data['fetcher_use_proxy']['TestFetcher'] is True
    assert data['fetcher_stats']['TestFetcher']['total_ok'] == 20
