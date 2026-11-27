"""A failed compensation must not strand newly published project entries."""
import os
from pathlib import Path

import pytest

from horizun_pbi_mcp.pbip import pbip_scaffold
from horizun_pbi_mcp.services import txn


@pytest.mark.parametrize('fail_at', [2, 3])
def test_flat_publish_and_compensation_failure_removes_only_owned_entries(
        tmp_path, monkeypatch, fail_at):
    sentinel = tmp_path / 'existing.txt'
    sentinel.write_bytes(b'keep')
    (tmp_path / 'existing-dir').mkdir()
    replace = os.replace
    calls = 0

    def fail(source, target):
        nonlocal calls
        source, target = Path(source), Path(target)
        if source.parent.name.startswith('.hz_stage_') and target.parent == tmp_path:
            calls += 1
            if calls == fail_at:
                raise OSError('publication failed')
        if source.parent == tmp_path and target.parent.name.startswith('.hz_stage_'):
            raise OSError('compensation failed')
        return replace(source, target)

    monkeypatch.setattr(os, 'replace', fail)
    with pytest.raises(OSError, match='publication failed'):
        pbip_scaffold.crear_proyecto(tmp_path, 'Demo', subfolder=False)
    assert sorted(p.name for p in tmp_path.iterdir()) == ['existing-dir', 'existing.txt']
    assert sentinel.read_bytes() == b'keep'


def test_nested_failed_rollback_removes_new_project(tmp_path, monkeypatch):
    sentinel = tmp_path / 'existing.txt'
    sentinel.write_bytes(b'keep')
    write = txn.Transaction.write_bytes
    calls = 0

    def fail(self, *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise OSError('publication failed')
        return write(self, *args, **kwargs)

    monkeypatch.setattr(txn.Transaction, 'write_bytes', fail)
    monkeypatch.setattr(txn.Transaction, 'rollback',
                        lambda *a, **k: (_ for _ in ()).throw(OSError('compensation failed')))
    with pytest.raises(OSError, match='compensation failed'):
        pbip_scaffold.crear_proyecto(tmp_path, 'Demo')
    assert not (tmp_path / 'Demo').exists()
    assert sentinel.read_bytes() == b'keep'
