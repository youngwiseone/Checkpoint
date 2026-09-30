"""WASAPI device discovery (PyAudioWPatch) and the explicit short level test.

Devices are referred to by name, not index: PortAudio indices change when devices
are plugged/unplugged or the process restarts.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional

import numpy as np

log = logging.getLogger(__name__)

# PortAudio initialisation/opening is not safe to run concurrently from threads.
PA_LOCK = threading.RLock()


class ComScope:
    """WASAPI needs COM on every thread that opens a stream. PortAudio only initialises
    COM on the thread that first calls Pa_Initialize, so each audio thread does it itself."""

    def __enter__(self) -> "ComScope":
        self.ok = False
        try:
            import ctypes

            hr = ctypes.windll.ole32.CoInitializeEx(None, 0x2)  # COINIT_APARTMENTTHREADED
            self.ok = hr in (0, 1)  # S_OK / S_FALSE
        except (AttributeError, OSError):
            pass
        return self

    def __exit__(self, *exc) -> None:  # noqa: ANN002
        if self.ok:
            import ctypes

            ctypes.windll.ole32.CoUninitialize()


def _pa_module():
    import pyaudiowpatch as pyaudio  # Windows-only; imported lazily

    return pyaudio


def audio_available() -> tuple[bool, str]:
    try:
        _pa_module()
        return True, ""
    except Exception as e:  # noqa: BLE001
        return False, f"PyAudioWPatch is not available: {e}"


def list_devices() -> dict:
    """Returns {"mic": [...], "loopback": [...], "default_mic": name, "default_loopback": name}."""
    ok, why = audio_available()
    if not ok:
        return {"mic": [], "loopback": [], "default_mic": None, "default_loopback": None, "error": why}
    pyaudio = _pa_module()
    with PA_LOCK, ComScope():
        p = pyaudio.PyAudio()
        try:
            try:
                wasapi = p.get_host_api_info_by_type(pyaudio.paWASAPI)
            except OSError:
                return {"mic": [], "loopback": [], "default_mic": None, "default_loopback": None, "error": "WASAPI is not available"}
            mics, loops = [], []
            for i in range(p.get_device_count()):
                d = p.get_device_info_by_index(i)
                if d["hostApi"] != wasapi["index"] or d["maxInputChannels"] < 1:
                    continue
                entry = {
                    "name": d["name"],
                    "channels": int(d["maxInputChannels"]),
                    "sample_rate": int(d["defaultSampleRate"]),
                }
                if d.get("isLoopbackDevice"):
                    entry["output_name"] = d["name"].replace(" [Loopback]", "")
                    loops.append(entry)
                else:
                    mics.append(entry)
            default_mic = None
            if wasapi.get("defaultInputDevice", -1) >= 0:
                try:
                    default_mic = p.get_device_info_by_index(wasapi["defaultInputDevice"])["name"]
                except OSError:
                    pass
            default_loop = None
            try:
                default_loop = p.get_default_wasapi_loopback()["name"]
            except Exception:  # noqa: BLE001
                pass
            return {"mic": mics, "loopback": loops, "default_mic": default_mic, "default_loopback": default_loop}
        finally:
            p.terminate()


def find_device(p, kind: str, name: Optional[str]) -> Optional[dict]:  # noqa: ANN001
    pyaudio = _pa_module()
    wasapi = p.get_host_api_info_by_type(pyaudio.paWASAPI)
    candidates = []
    for i in range(p.get_device_count()):
        d = p.get_device_info_by_index(i)
        if d["hostApi"] != wasapi["index"] or d["maxInputChannels"] < 1:
            continue
        if bool(d.get("isLoopbackDevice")) != (kind == "loopback"):
            continue
        candidates.append(d)
    if name:
        for d in candidates:
            if d["name"] == name:
                return d
        return None
    # No explicit name: system default.
    if kind == "loopback":
        try:
            return p.get_default_wasapi_loopback()
        except Exception:  # noqa: BLE001
            return None
    idx = wasapi.get("defaultInputDevice", -1)
    return p.get_device_info_by_index(idx) if idx >= 0 else None


def level_of(data: bytes, channels: int) -> tuple[float, float]:
    """(rms, peak) normalised 0..1 for int16 interleaved PCM."""
    if not data:
        return 0.0, 0.0
    a = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
    if a.size == 0:
        return 0.0, 0.0
    rms = float(np.sqrt(np.mean(a * a)))
    peak = float(np.max(np.abs(a)))
    return rms, peak


def meter_value(rms: float) -> float:
    """Map RMS to a 0..1 meter on a -60..0 dBFS scale."""
    if rms <= 1e-6:
        return 0.0
    db = 20 * np.log10(rms)
    return float(max(0.0, min(1.0, (db + 60) / 60)))


class LevelTest:
    """Explicit, short (default 3 s) device test. Nothing is written to disk."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.state: dict = {"running": False}

    def start(self, kind: str, device: Optional[str], seconds: float = 3.0) -> None:
        with self._lock:
            if self.state.get("running"):
                return
            self.state = {"running": True, "kind": kind, "device": device, "level": 0.0, "peak": 0.0,
                          "callbacks": 0, "result": None, "error": None}
        threading.Thread(target=self._run, args=(kind, device, seconds), daemon=True, name="audio-test").start()

    def _run(self, kind: str, device: Optional[str], seconds: float) -> None:
        with ComScope():
            self._run_inner(kind, device, seconds)

    def _run_inner(self, kind: str, device: Optional[str], seconds: float) -> None:
        pyaudio = _pa_module()
        stream = None
        p = None
        try:
            with PA_LOCK:
                p = pyaudio.PyAudio()
                d = find_device(p, kind, device)
                if d is None:
                    raise RuntimeError(f"Device not found: {device or 'system default'}")
                channels = int(d["maxInputChannels"])
                rate = int(d["defaultSampleRate"])

                def cb(in_data, frame_count, time_info, status):  # noqa: ANN001
                    rms, peak = level_of(in_data, channels)
                    self.state["callbacks"] += 1
                    self.state["level"] = meter_value(rms)
                    self.state["peak"] = max(self.state["peak"], peak)
                    return (None, pyaudio.paContinue)

                stream = p.open(format=pyaudio.paInt16, channels=channels, rate=rate, input=True,
                                input_device_index=int(d["index"]), frames_per_buffer=int(rate * 0.05),
                                stream_callback=cb)
            t0 = time.monotonic()
            while time.monotonic() - t0 < seconds:
                time.sleep(0.05)
            cbs = self.state["callbacks"]
            peak = self.state["peak"]
            if cbs == 0 and kind == "loopback":
                result = "No sound was playing on this output during the test. That's normal when it's silent — play something and test again."
            elif cbs == 0:
                result = "The device opened but delivered no audio. It may be disabled, in use exclusively, or blocked by Windows privacy settings."
            elif peak < 0.003:
                result = "Receiving audio, but it was silent. Check the device isn't muted."
            else:
                result = "Working — signal detected."
            self.state["result"] = result
        except Exception as e:  # noqa: BLE001
            self.state["error"] = str(e)
        finally:
            with PA_LOCK:
                try:
                    if stream is not None:
                        stream.stop_stream()
                        stream.close()
                finally:
                    if p is not None:
                        p.terminate()
            self.state["running"] = False
            self.state["level"] = 0.0


level_test = LevelTest()
