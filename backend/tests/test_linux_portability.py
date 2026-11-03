"""Linux integrations use fake portals and sound servers on every test platform."""
import importlib.util
import shlex
from pathlib import Path
from unittest.mock import Mock

import pytest
from PIL import Image

from checkpoint import trigger
from checkpoint.capture import linux
from checkpoint.desktop.shortcuts_linux import ShortcutListener


def test_shortcut_command_preserves_spaces_and_shell_characters(monkeypatch, tmp_path):
    executable = '/home/A user/bin/python'
    script = tmp_path / 'Checkpoint & notes' / 'trigger.py'
    monkeypatch.setattr(trigger.sys, 'executable', executable)
    monkeypatch.setattr(trigger, '__file__', str(script))
    assert shlex.split(trigger.command('capture')) == [executable, str(script.resolve()), 'capture']


def test_shortcut_listener_accepts_fragmented_socket_messages():
    actions = []
    listener = ShortcutListener(lambda: {}, actions.append)
    conn = Mock()
    conn.__enter__ = Mock(return_value=conn)
    conn.__exit__ = Mock(return_value=False)
    conn.recv.side_effect = [b'cap', b'ture\n']
    sock = Mock()

    def accept():
        listener._sock = None  # finish after this accepted command
        return conn, None

    sock.accept.side_effect = accept
    listener._sock = sock
    listener._run()
    conn.sendall.assert_called_once_with(b'ok\n')
    assert actions == ['press', 'capture']


def test_shortcut_listener_closes_socket_when_startup_fails(monkeypatch, tmp_path):
    from checkpoint.desktop import shortcuts_linux
    listener = ShortcutListener(lambda: {}, Mock())
    listener.path = tmp_path / 'shortcuts.sock'
    sock = Mock()
    sock.bind.side_effect = OSError('Cannot bind')
    monkeypatch.setattr(shortcuts_linux.socket, 'AF_UNIX', 1, raising=False)
    monkeypatch.setattr(shortcuts_linux.socket, 'socket', Mock(return_value=sock))
    listener.start()
    sock.close.assert_called_once()
    assert not listener.summary()['ok']
    assert 'Cannot bind' in listener.error


@pytest.fixture
def pulse(monkeypatch):
    # Import independently so a mocked availability check does not make Linux
    # audio appear available to unrelated tests later in this process.
    path = Path(__file__).parents[1] / 'checkpoint' / 'audio' / 'pulse.py'
    spec = importlib.util.spec_from_file_location('checkpoint_pulse_test', path)
    module = importlib.util.module_from_spec(spec)
    with monkeypatch.context() as patch:
        patch.setattr('shutil.which', lambda command: '/usr/bin/' + command)
        spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('source_mapping', [True, False])
def test_custom_loopback_names_follow_sound_server_mapping(pulse, monkeypatch, source_mapping):
    sink = {'name': 'speakers', 'description': 'Desktop speakers', 'monitor_source': 'desktop-audio'}
    source = {'name': 'desktop-audio', 'description': 'Custom monitor', 'sample_specification': 's16le 2ch 44100Hz'}
    if source_mapping:
        source['monitor_source'] = 'speakers'
        sink.pop('monitor_source')
    replies = {
        ('info',): {'default_source_name': 'microphone', 'default_sink_name': 'speakers'},
        ('list', 'sources'): [source, {'name': 'microphone', 'sample_specification': 's16le 1ch 48000Hz'}],
        ('list', 'sinks'): [sink],
    }
    monkeypatch.setattr(pulse, '_pactl_json', lambda *args: replies[args])
    audio = pulse.PyAudio()
    loopback = audio.get_default_wasapi_loopback()
    assert loopback['name'] == 'Desktop speakers [Loopback]'
    assert loopback['pa_name'] == 'desktop-audio'
    assert loopback['maxInputChannels'] == 2
    assert loopback['defaultSampleRate'] == 44100
    default_mic = audio.get_host_api_info_by_type(pulse.paWASAPI)['defaultInputDevice']
    assert audio.get_device_info_by_index(default_mic)['name'] == 'microphone'
    assert not audio.get_device_info_by_index(default_mic)['isLoopbackDevice']


def test_audio_discovery_without_sound_server_returns_available_device_error(pulse, monkeypatch):
    from checkpoint.audio import devices

    monkeypatch.setattr(devices, '_pa_module', lambda: pulse)
    monkeypatch.setattr(pulse, '_pactl_json', Mock(side_effect=OSError('Connection refused')))
    assert devices.audio_available() == (True, '')
    result = devices.list_devices()
    assert result['mic'] == result['loopback'] == []
    assert result['default_mic'] is None and result['default_loopback'] is None
    assert 'Connection refused' in result['error']


def test_audio_enumeration_failure_closes_backend(monkeypatch):
    from types import SimpleNamespace
    from checkpoint.audio import devices

    backend = Mock()
    backend.get_device_count.side_effect = OSError('Device disconnected')
    module = SimpleNamespace(PyAudio=Mock(return_value=backend), paWASAPI=13)
    monkeypatch.setattr(devices, '_pa_module', lambda: module)
    result = devices.list_devices()
    assert result['mic'] == result['loopback'] == []
    assert 'Device disconnected' in result['error']
    backend.terminate.assert_called_once()


def test_portal_crops_selected_scaled_display_with_negative_origin(monkeypatch):
    desktop = Image.new('RGB', (800, 200), 'red')
    desktop.paste('blue', (400, 0, 800, 200))
    monkeypatch.setattr(linux, '_screens', [
        {'name': 'left', 'left': -200, 'top': 0, 'width': 200, 'height': 100, 'primary': False},
        {'name': 'right', 'left': 0, 'top': 0, 'width': 200, 'height': 100, 'primary': True},
    ])
    monkeypatch.setattr(linux, 'grab_desktop', lambda: desktop)
    image = linux.grab({'left': 0, 'top': 0, 'width': 200, 'height': 100})
    assert image.size == (400, 200)
    assert image.getextrema() == ((0, 0), (0, 0), (255, 255))


def test_portal_reads_then_removes_temporary_screenshot(monkeypatch, tmp_path):
    path = tmp_path / 'portal shot.png'
    Image.new('RGBA', (32, 24), (30, 90, 150, 255)).save(path)
    monkeypatch.setattr(linux, '_desktop_size', None)
    monkeypatch.setattr(linux, '_portal_screenshot', lambda: path)
    image = linux.grab_desktop()
    assert image.mode == 'RGB' and image.size == (32, 24)
    assert image.getpixel((0, 0)) == (30, 90, 150)
    assert linux._desktop_size == (32, 24)
    assert not path.exists()


def test_portal_transport_failure_resets_connection_for_retry(monkeypatch):
    monkeypatch.setattr(linux._local, 'conn', object(), raising=False)
    monkeypatch.setattr(linux, '_portal_screenshot', Mock(side_effect=OSError('Bus disconnected')))
    with pytest.raises(linux.PortalError, match='Bus disconnected'):
        linux.grab_desktop()
    assert linux._local.conn is None


def test_notification_without_dbus_dependency_falls_back(monkeypatch):
    import builtins
    from checkpoint.desktop.notify import Notifier

    original = builtins.__import__

    def import_without_jeepney(name, *args, **kwargs):
        if name == 'jeepney' or name.startswith('jeepney.'):
            raise ImportError('D-Bus dependency unavailable')
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', import_without_jeepney)
    assert Notifier().show('success', 'Task saved', 2600) is False
