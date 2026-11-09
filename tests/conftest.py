"""Every test files digests in a store of its own.

A figure a notebook kernel displays has its digest filed in the store
under the cache that `FIGTOOLS_DIGEST_DIR` names, `~/.local/share/
render-digests` when it is unset; tests that display figures would
otherwise write into the store of whoever runs them. Subprocesses inherit
the variable.
"""

import pytest


@pytest.fixture(autouse=True)
def private_store(tmp_path_factory, monkeypatch):
    cache = tmp_path_factory.mktemp('digests')
    monkeypatch.setenv('FIGTOOLS_DIGEST_DIR', str(cache))
    return cache
