import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from src.connectivity import industrial_protocols as p


@pytest.mark.parametrize('running_loop', [False, True])
@pytest.mark.parametrize('value', [False, True])
def test_knx_write_awaits_transport_before_success(monkeypatch, running_loop, value):
    monkeypatch.setattr(p, 'KNX_OK', True)
    session = Mock(start=AsyncMock(), stop=AsyncMock())
    switch = Mock(set_on=AsyncMock(), set_off=AsyncMock())
    factory = Mock(return_value=switch)
    monkeypatch.setattr(p, 'Switch', factory, raising=False)
    bridge = p.KNXBridge()
    bridge._xknx = session
    async def invoke():
        return bridge.write_group('1/2/3', value)
    result = asyncio.run(invoke()) if running_loop else bridge.write_group('1/2/3', value)
    assert result.startswith('✅')
    factory.assert_called_once_with(session, 'sw', group_address='1/2/3')
    session.start.assert_awaited_once()
    session.stop.assert_awaited_once()
    (switch.set_on if value else switch.set_off).assert_awaited_once()
    (switch.set_off if value else switch.set_on).assert_not_awaited()


@pytest.mark.parametrize('running_loop', [False, True])
def test_knx_failed_write_reports_failure_and_closes_transport(monkeypatch, running_loop):
    monkeypatch.setattr(p, 'KNX_OK', True)
    session = Mock(start=AsyncMock(), stop=AsyncMock())
    switch = Mock(set_on=AsyncMock(side_effect=RuntimeError('synthetic rejected write')))
    monkeypatch.setattr(p, 'Switch', Mock(return_value=switch), raising=False)
    bridge = p.KNXBridge()
    bridge._xknx = session
    async def invoke():
        return bridge.write_group('1/2/3', True)
    result = asyncio.run(invoke()) if running_loop else bridge.write_group('1/2/3', True)
    assert not result.startswith('✅')
    assert 'synthetic rejected write' in result
    switch.set_on.assert_awaited_once()
    session.stop.assert_awaited_once()
