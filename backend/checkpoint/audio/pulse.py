"""PulseAudio / PipeWire capture with the small slice of the PyAudioWPatch API the recorder uses.

Linux has no WASAPI, so this module stands in for `pyaudiowpatch`: devices come from
`pactl`, and each open stream is a `parec` process whose raw int16 output is delivered to
the callback in fixed-size buffers. Both tools ship with PulseAudio and with pipewire-pulse.

Output devices are recorded through their monitor sources and reported as loopback devices
named "<output> [Loopback]", like WASAPI. A monitor produces digital silence while nothing
plays; those buffers are dropped so, as on Windows, a silent output delivers no callbacks.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading
from typing import Callable, Optional

paWASAPI = 13  # the host API the device code filters by; there is only one here
paInt16 = 8
paContinue = 0
paComplete = 1

HOST_API = {"index": 0, "name": "PulseAudio", "defaultInputDevice": -1}
LOOPBACK_SUFFIX = " [Loopback]"

if not (shutil.which("pactl") and shutil.which("parec")):
    raise ImportError("pactl/parec not found (install PulseAudio utilities, e.g. pulseaudio-utils or pipewire-pulse)")


def _pactl_json(*args: str):  # noqa: ANN202
    out = subprocess.run(["pactl", "-f", "json", *args], capture_output=True, text=True, timeout=5, check=True).stdout
    return json.loads(out) if out.strip() else None


def _spec(text: str) -> tuple[int, int]:
    """(channels, rate) from a sample spec such as "s16le 2ch 48000Hz"."""
    m = re.search(r"(\d+)ch\s+(\d+)Hz", text or "")
    return (int(m.group(1)), int(m.group(2))) if m else (2, 48000)


def _devices() -> tuple[list[dict], Optional[str], Optional[str]]:
    """(devices, default source name, default sink name) from the sound server."""
    try:
        info = _pactl_json("info") or {}
        sources = _pactl_json("list", "sources") or []
        sinks = {s["name"]: s for s in (_pactl_json("list", "sinks") or [])}
    except (OSError, subprocess.SubprocessError, ValueError) as e:
        raise OSError(f"Couldn't list audio devices with pactl: {e}") from e
    devices = []
    for s in sources:
        sink = sinks.get(s.get("monitor_source") or s["name"][:-len(".monitor")]) if s["name"].endswith(".monitor") else None
        channels, rate = _spec(s.get("sample_specification", ""))
        devices.append({
            "index": len(devices),
            "name": (sink.get("description") or sink["name"]) + LOOPBACK_SUFFIX if sink else s.get("description") or s["name"],
            "pa_name": s["name"],
            "hostApi": HOST_API["index"],
            "maxInputChannels": min(2, channels),
            "defaultSampleRate": float(rate),
            "isLoopbackDevice": sink is not None,
            "sink_name": sink["name"] if sink else None,
        })
    return devices, info.get("default_source_name"), info.get("default_sink_name")


class Stream:
    def __init__(self, device: dict, channels: int, rate: int, frames_per_buffer: int, callback: Callable):
        self.callback = callback
        self.frames = frames_per_buffer
        self.size = frames_per_buffer * channels * 2
        self.drop_silence = bool(device.get("isLoopbackDevice"))
        latency_ms = max(20, int(frames_per_buffer * 1000 / rate))
        self.proc = subprocess.Popen(
            ["parec", "--raw", "--format=s16le", f"--rate={rate}", f"--channels={channels}", f"--device={device['pa_name']}",
             f"--latency-msec={latency_ms}", "--client-name=Checkpoint", "--stream-name=Checkpoint recording"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        self._stopped = threading.Event()
        self._thread = threading.Thread(target=self._pump, daemon=True, name="parec")
        self._thread.start()

    def _pump(self) -> None:
        out = self.proc.stdout
        silence = bytes(self.size)
        while not self._stopped.is_set():
            data = out.read(self.size)
            if len(data) < self.size:
                break  # parec exited: device gone or server restarted
            if self.drop_silence and data == silence:
                continue
            result = self.callback(data, self.frames, None, 0)
            if isinstance(result, tuple) and len(result) > 1 and result[1] == paComplete:
                break  # the callback has all it needs, as PortAudio's paComplete

    def is_active(self) -> bool:
        return self.proc.poll() is None and self._thread.is_alive()

    def stop_stream(self) -> None:
        self._stopped.set()
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(2)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def close(self) -> None:
        self.stop_stream()
        self._thread.join(1)
        if self.proc.stdout:
            self.proc.stdout.close()


class PyAudio:
    def __init__(self) -> None:
        self._devices, self._default_source, self._default_sink = _devices()

    def terminate(self) -> None:
        pass

    def get_host_api_info_by_type(self, _type: int) -> dict:
        default = next((d["index"] for d in self._devices if d["pa_name"] == self._default_source), -1)
        return {**HOST_API, "defaultInputDevice": default}

    def get_device_count(self) -> int:
        return len(self._devices)

    def get_device_info_by_index(self, i: int) -> dict:
        if not 0 <= i < len(self._devices):
            raise OSError("Invalid device index")
        return self._devices[i]

    def get_default_wasapi_loopback(self) -> dict:
        for d in self._devices:
            if d["isLoopbackDevice"] and d["sink_name"] == self._default_sink:
                return d
        raise OSError("No default output device")

    def open(self, format: int, channels: int, rate: int, input: bool, input_device_index: int,  # noqa: A002
             frames_per_buffer: int, stream_callback: Callable) -> Stream:
        return Stream(self.get_device_info_by_index(input_device_index), channels, rate, frames_per_buffer, stream_callback)
