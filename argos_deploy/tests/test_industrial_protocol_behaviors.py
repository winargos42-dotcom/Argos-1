from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

from src.connectivity import industrial_protocols as p


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    for flag in ('KNX_OK', 'MBUS_OK', 'OPCUA_OK'):
        monkeypatch.setattr(p, flag, False)
    for protocol in ('KNX', 'LON', 'MBUS', 'OPCUA'):
        monkeypatch.setenv(f'ARGOS_{protocol}_SIM', 'off')
    monkeypatch.setattr(p.socket, 'socket', Mock(side_effect=AssertionError('No network permitted')))


@pytest.mark.parametrize('bridge,reply,identifier', [
    (p.KNXBridge, b'\x06\x10\x02\x02\x00\x06', 'knx_192.0.2.1'),
    (p.LonWorksBridge, b'\x4f\x01\x02\x03\x04\x05\x06', 'lon_010203040506'),
])
def test_udp_discovery_filters_packets_and_retains_provenance(monkeypatch, bridge, reply, identifier):
    transport = Mock()
    transport.recvfrom.side_effect = [(b'bad', ('192.0.2.2', 100)), (reply, ('192.0.2.1', 3671)), p.socket.timeout()]
    monkeypatch.setattr(p.socket, 'socket', Mock(return_value=transport))
    instance = bridge()
    devices = instance.discover(timeout=1)
    assert len(devices) == 1
    assert devices[0].device_id == identifier
    assert devices[0].online is True
    assert instance.all_devices()[0]['device_id'] == identifier
    assert '192.0.2.1' in instance.all_devices()[0]['address']
    transport.close.assert_called_once()
    transport.sendto.assert_called_once()


@pytest.mark.parametrize('bridge,env,count', [(p.KNXBridge, 'KNX', 1), (p.LonWorksBridge, 'LON', 3)])
def test_discovery_simulation_requires_opt_in(monkeypatch, bridge, env, count):
    assert bridge().discover(timeout=0) == []
    monkeypatch.setenv(f'ARGOS_{env}_SIM', 'on')
    devices = bridge().discover(timeout=0)
    assert len(devices) == count
    assert all(d.device_type == 'simulated' for d in devices)


def test_lon_variables_are_separate_and_node_lifecycle_is_visible():
    bridge = p.LonWorksBridge()
    first, second = bridge._sim_nodes(2)
    assert 'error' in bridge.read_nv('missing', 1)
    assert bridge.write_nv('missing', 1, 20).startswith('❌')
    assert bridge.write_nv(first.device_id, 7, 42).startswith('✅')
    assert bridge.read_nv(first.device_id, 7)['value'] == 42
    assert bridge.read_nv(second.device_id, 7)['value'] == 0
    bridge.decommission_node(first.device_id)
    assert not first.online
    assert 'online: 1' in bridge.status()
    bridge.commission_node(first.device_id)
    assert first.online
    assert bridge.commission_node('missing').startswith('❌')
    assert bridge.decommission_node('missing').startswith('❌')


def frame(value=1234):
    return bytes(12) + bytes([0x12, 0x10]) + value.to_bytes(2, 'little') + bytes(2)


def test_mbus_parser_handles_supported_record_and_unsupported_length():
    bridge = p.MBusBridge()
    assert bridge._parse_frame(b'short') == []
    assert bridge._parse_frame(frame()) == [{'function': 1, 'unit': 'kWh', 'value': 12.34}]
    assert bridge._parse_frame(bytes(12) + bytes([0x0D, 0x10, 1, 0, 0])) == []
    assert p.MBusRecord(1, 'kWh', 12.34).to_dict() == bridge._parse_frame(frame())[0]


@pytest.mark.parametrize('unit,medium', [('kWh', 'electricity'), ('m³', 'water_gas'), ('°C', 'heat'), ('?', 'unknown')])
def test_mbus_medium_and_record_provenance(unit, medium):
    records = [{'unit': unit, 'value': 3}]
    device = p.MBusBridge()._make_device(7, records)
    assert device.device_type == medium
    assert device.properties == {'primary_address': 7, 'records': records}
    assert device.address == '7'


def test_mbus_scan_recovers_after_failed_address_and_refreshes_cache(monkeypatch):
    monkeypatch.setattr(p, 'MBUS_OK', True)
    transport = Mock()
    transport.recv_frame.side_effect = [OSError('synthetic disconnected meter'), b'', frame(), frame(2500)]
    bridge = p.MBusBridge()
    bridge._mbus = transport
    progress = []
    found = bridge.discover(1, 3, lambda address, total: progress.append((address, total)))
    assert progress == [(1, 3), (2, 3), (3, 3)]
    assert [d.address for d in found] == ['3']
    assert bridge.read_device(3)['records'][0]['value'] == 25
    assert bridge.all_devices()[0]['properties']['records'][0]['value'] == 25
    assert 'electricity:1' in bridge.status()
    transport.recv_frame.side_effect = OSError('synthetic offline')
    assert bridge.read_device(3) == {'error': 'synthetic offline'}


@pytest.mark.parametrize('method,args,expected', [('connect_serial', ('/dev/synthetic',), {'device': '/dev/synthetic', 'baudrate': 2400}), ('connect_tcp', ('192.0.2.1', 9999), {'host': '192.0.2.1', 'port': 9999})])
def test_mbus_connection_delegates_and_surfaces_failures(monkeypatch, method, args, expected):
    monkeypatch.setattr(p, 'MBUS_OK', True)
    transport = Mock()
    factory = Mock(return_value=transport)
    monkeypatch.setattr(p, 'MBus', factory, raising=False)
    assert getattr(p.MBusBridge(), method)(*args).startswith('✅')
    factory.assert_called_once_with(**expected)
    transport.connect.assert_called_once()
    transport.connect.side_effect = OSError('synthetic connection refused')
    assert 'synthetic connection refused' in getattr(p.MBusBridge(), method)(*args)


def test_mbus_simulated_scan_and_missing_address(monkeypatch):
    bridge = p.MBusBridge()
    assert bridge.discover(1, 5) == []
    monkeypatch.setenv('ARGOS_MBUS_SIM', 'on')
    assert [d.address for d in bridge.discover(1, 5)] == ['1', '5']
    assert bridge.read_device(1)['records']
    assert 'error' in bridge.read_device(9)


@pytest.fixture
def opc(monkeypatch):
    monkeypatch.setattr(p, 'OPCUA_OK', True)
    client = Mock()
    monkeypatch.setattr(p, 'OPCClient', Mock(return_value=client), raising=False)
    bridge = p.OPCUABridge()
    assert bridge.connect('opc.tcp://synthetic:4840', 'synthetic-user', 'synthetic-password').startswith('✅')
    client.set_user.assert_called_once_with('synthetic-user')
    client.set_password.assert_called_once_with('synthetic-password')
    client.connect.assert_called_once()
    return bridge, client


def test_opc_reads_writes_methods_and_disconnect(opc):
    bridge, client = opc
    node = client.get_node.return_value
    node.get_value.return_value = 17
    result = bridge.read_node('ns=2;i=9')
    assert result['value'] == 17 and result['type'] == 'int'
    assert result['node_id'] == 'ns=2;i=9'
    bridge.write_node('ns=2;i=9', 23)
    node.set_value.assert_called_once_with(23)
    node.call_method.return_value = 46
    assert bridge.call_method('object', 'method', 23) == {'result': 46, 'status': 'ok'}
    node.call_method.assert_called_once_with('method', 23)
    bridge.disconnect()
    client.disconnect.assert_called_once()
    assert bridge._client is None


@pytest.mark.parametrize('method,args', [('read_node', ('n',)), ('write_node', ('n', 1)), ('call_method', ('o', 'm'))])
def test_opc_transport_error_is_returned(opc, method, args):
    bridge, client = opc
    client.get_node.side_effect = OSError('synthetic offline')
    result = getattr(bridge, method)(*args)
    assert 'synthetic offline' in str(result)


def test_opc_browse_bounds_and_skips_broken_child(opc):
    bridge, client = opc
    children = [Mock(nodeid=f'ns=2;i={i}') for i in range(60)]
    for i, child in enumerate(children):
        child.get_browse_name.return_value = f'sensor-{i}'
    children[0].get_browse_name.side_effect = ValueError('synthetic inaccessible')
    client.get_node.return_value.get_children.return_value = children
    result = bridge.browse('root')
    assert len(result) == 49
    assert result[0] == {'node_id': 'ns=2;i=1', 'name': 'sensor-1', 'children': []}
    assert result[-1]['node_id'] == 'ns=2;i=49'


def test_opc_subscription_callback_isolation_and_release(opc):
    bridge, client = opc
    broken = Mock(side_effect=ValueError('synthetic callback failed'))
    good = Mock()
    bridge._callbacks['n'].append(broken)
    assert bridge.subscribe('n', good, 250).startswith('✅')
    interval, handler = client.create_subscription.call_args.args
    assert interval == 250
    handler.datachange_notification(SimpleNamespace(nodeid='n'), 31, None)
    good.assert_called_once_with('n', 31)
    sub = client.create_subscription.return_value
    handle = sub.subscribe_data_change.return_value
    bridge.unsubscribe('n')
    sub.unsubscribe.assert_called_once_with(handle)
    sub.delete.assert_called_once()
    assert 'n' not in bridge._subscriptions and 'n' not in bridge._callbacks


def test_opc_discovery_retains_server_metadata(opc):
    bridge, client = opc
    client.find_servers.return_value = [SimpleNamespace(DiscoveryUrls=['opc.tcp://synthetic:4840'], ApplicationName=SimpleNamespace(Text='Synthetic PLC'), ApplicationUri='urn:synthetic', ApplicationType=1)]
    found = bridge.discover('opc.tcp://synthetic:4840')
    assert len(found) == 1
    assert found[0].name == 'Synthetic PLC'
    assert found[0].properties['app_uri'] == 'urn:synthetic'
    assert len(bridge.all_devices()) == 1
    client.close_secure_channel.assert_called_once()


def test_manager_discovery_keeps_other_protocols_when_one_fails(monkeypatch):
    manager = p.IndustrialProtocolsManager()
    device = p.IndustrialDevice(p.ProtocolType.MBUS, 'meter-1', 'Synthetic meter', '1')
    manager.knx.discover = Mock(side_effect=OSError('synthetic KNX offline'))
    manager.lon.discover = Mock(return_value=[])
    manager.mbus.discover = Mock(return_value=[device])
    manager.opcua.discover = Mock(return_value=[])
    result = manager.discover_all(timeout=0.1)
    assert result['knx'] == []
    assert result['mbus'] == [device.to_dict()]
    assert manager.all_devices() == [device.to_dict()]
    assert 'Всего устройств: 1' in manager.status()
    assert 'Synthetic meter' in manager.handle_command('industrial устройства')
    assert 'найдено 1' in manager.handle_command('industrial discovery')
    manager.mbus.discover.assert_called_with()
    manager.lon.discover.assert_called_with(timeout=5.0)


@pytest.mark.parametrize('protocol,attribute,reader,writer,address,readargs,writeargs', [
    ('knx', 'knx', 'read_group', 'write_group', '1/2/3', ('1/2/3',), ('1/2/3', 19)),
    ('lonworks', 'lon', 'read_nv', 'write_nv', 'node', ('node', 7), ('node', 7, 19)),
    ('mbus', 'mbus', 'read_device', None, '12', (12,), None),
    ('opcua', 'opcua', 'read_node', 'write_node', 'ns=2;i=1', ('ns=2;i=1',), ('ns=2;i=1', 19)),
])
def test_manager_preserves_read_write_arguments(protocol, attribute, reader, writer, address, readargs, writeargs):
    manager = p.IndustrialProtocolsManager()
    transport = Mock()
    setattr(manager, attribute, transport)
    getattr(transport, reader).return_value = {'value': 19}
    assert manager.read(protocol, address, nv_index=7) == {'value': 19}
    getattr(transport, reader).assert_called_once_with(*readargs)
    result = manager.write(protocol, address, 19, nv_index=7)
    if writer:
        getattr(transport, writer).assert_called_once_with(*writeargs)
        assert result is getattr(transport, writer).return_value
    else:
        assert 'read-only' in result
        assert transport.method_calls == [call.read_device(12)]


def test_manager_unknown_protocol_and_empty_device_list():
    manager = p.IndustrialProtocolsManager()
    assert 'error' in manager.read('unknown', 'x')
    assert manager.write('unknown', 'x', 1).startswith('❌')
    assert 'не найдено' in manager.handle_command('industrial устройства')
    assert 'Команды:' in manager.handle_command('nonsense')
    assert 'ПРОМЫШЛЕННЫЕ ПРОТОКОЛЫ' in manager.handle_command('industrial статус')


@pytest.mark.parametrize('command,attribute,method,args,output', [
    ('knx подключи synthetic', 'knx', 'connect', ('synthetic',), 'connected'),
    ('opcua подключи opc.tcp://synthetic', 'opcua', 'connect', ('opc.tcp://synthetic',), 'connected'),
    ('mbus serial /dev/synthetic', 'mbus', 'connect_serial', ('/dev/synthetic',), 'connected'),
    ('opcua browse ns=2;i=1', 'opcua', 'browse', ('ns=2;i=1',), [{'node_id': 'n', 'name': 'sensor'}]),
    ('industrial читай knx 1/2/3', 'knx', 'read_group', ('1/2/3',), {'value': 9}),
])
def test_manager_commands_dispatch_to_expected_transport(command, attribute, method, args, output):
    manager = p.IndustrialProtocolsManager()
    transport = Mock()
    setattr(manager, attribute, transport)
    getattr(transport, method).return_value = output
    result = manager.handle_command(command)
    getattr(transport, method).assert_called_once_with(*args)
    assert result
