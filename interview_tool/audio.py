# -*- coding: utf-8 -*-
"""audio.py — 双轨录音链路：回环+麦克风采集 / TCP 音频 / WAV 落盘。"""
import os
import struct as _struct
import threading
import time
import traceback
import wave

import numpy as np
import pyaudiowpatch as pyaudio

from .config import BLOCK, SAMPLE_RATE

# ---------- 录音：WASAPI 回环 / 麦克风（pyaudiowpatch，统一重采样 16k） ----------
def resample_to_16k(audio, from_rate):
    if from_rate == SAMPLE_RATE:
        return audio
    n = int(len(audio) * SAMPLE_RATE / from_rate)
    return np.interp(np.linspace(0, len(audio) - 1, n),
                     np.arange(len(audio)), audio).astype(np.float32)

def pick_loopback_device(p):
    """按默认输出设备名精确匹配 loopback（蓝牙/音箱切换时仍正确）"""
    wasapi = p.get_host_api_info_by_type(pyaudio.paWASAPI)
    out_idx = wasapi["defaultOutputDevice"]
    out_name = p.get_device_info_by_index(out_idx)["name"]
    for i in range(p.get_device_count()):
        dev = p.get_device_info_by_index(i)
        if p.get_host_api_info_by_index(dev["hostApi"])["type"] == pyaudio.paWASAPI \
                and dev.get("isLoopbackDevice"):
            if dev["name"].replace(" [Loopback]", "") == out_name:
                return i, dev
    return None, None

class Recorder:
    """callback 模式录音。统一重采样 16k mono float32 喂 on_block（编排线程/VAD/转写）。
    两条数据流互不干扰：
      - on_block(block)：16k float32 块 → 编排线程（门控仲裁 + VAD）
      - _raw：原生格式 int16 块（设备采样率/声道原样）→ WavWriter 落盘（复盘要真相）
    callback 只做最小工作：不阻塞、不文件IO、不 print、异常全吞不 return paComplete（不杀流）。
    流常开不关；_rec/_bufs 保留 F1 手动兜底（攒 16k float32 整段）。"""
    def __init__(self, p, device_idx, device, on_block=None, mode="cb",
                 capture_raw=True):
        self.p = p
        self.device_idx = device_idx
        self.device = device
        self.on_block = on_block
        self.mode = mode
        self.capture_raw = bool(capture_raw)
        self._stream = None
        self._thread = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._raw = []                  # 原生 int16 块（落盘；writer 线程 take_raw 取走）
        self._rec = False               # F1 后 True：回调往 _bufs 里攒
        self._bufs = []
        self._fmt = None                # 数据格式探测结果（'f32'/'i16'），首块定
        self._sr = 44100                # 默认值兜底：tcp 模式 start() 跳过不设置，
        self._ch = 2                    # 缺 _sr/_ch 会让 _process 在降混处 AttributeError（全静默丢数据）

    def start(self):
        if self._stream is not None:
            return
        dev = self.device
        if dev is None or self.mode == "tcp":
            return   # tcp 模式：面试官音频由 loop_tcp_thread 直接喂 _process，不开声卡流
        # stop() 会置位该事件；重新打开流前必须清除，否则 callback/read 线程会立即丢弃数据。
        self._stop.clear()
        sr = int(dev["defaultSampleRate"])
        ch = int(dev["maxInputChannels"]) or 1
        self._sr, self._ch = sr, ch
        try:
            if self.mode == "read":
                # 坑（VB-Audio 驱动）：pyaudiowpatch callback 模式对 CABLE/Voicemeeter
                # 给 0.0/-NaN 全损数据（read() 模式正常）。回环轨必须 read 线程。
                # read() 数据格式 = 请求格式（请求 paInt16 得 int16），无需探测
                self._stream = self.p.open(
                    format=pyaudio.paInt16, channels=ch, rate=sr,
                    input=True, input_device_index=self.device_idx,
                    frames_per_buffer=BLOCK)
                self._thread = threading.Thread(target=self._read_loop, daemon=True)
                self._thread.start()
            else:
                # 坑：pyaudiowpatch callback 的 in_data 永远是设备混音格式
                # （Float32），format 参数只影响打开校验、不影响数据格式
                self._stream = self.p.open(
                    format=pyaudio.paFloat32, channels=ch, rate=sr,
                    input=True, input_device_index=self.device_idx,
                    frames_per_buffer=BLOCK, stream_callback=self._cb)
        except OSError as e:
            print(f"⚠️ 打开录音流失败 [{self.device_idx}]: {e}", flush=True)
            self._stream = None

    def _read_loop(self):
        """read 模式：节流读块 → 与 callback 相同的处理。
        坑：pyaudiowpatch 对 VB-Audio 设备的 read() 不阻塞——立即返回最新缓冲区，
        疯狂循环会奇偶丢块。必须按采集节奏 sleep 节流（实测 23.7ms/块满速率收全）。
        stop_stream 解阻塞并抛异常退出"""
        interval = BLOCK / self._sr if self._sr else 0.023
        while not self._stop.is_set():
            a = time.perf_counter()
            try:
                in_data = self._stream.read(BLOCK, exception_on_overflow=False)
            except Exception:
                break
            self._process(in_data)
            w = interval - (time.perf_counter() - a)
            if w > 0:
                time.sleep(w)

    def start_rec(self):
        """F1 手动兜底：开始攒（清掉上一轮残留，防误拼）"""
        with self._lock:
            self._bufs = []
            self._rec = True

    def stop_rec(self):
        """F1 手动兜底：停止并取走攒的 16k float32 块（没录到返回空列表）"""
        with self._lock:
            self._rec = False
            bufs, self._bufs = self._bufs, []
        return bufs

    def take_raw(self):
        """WavWriter 取走原生落盘块（已转 int16）"""
        with self._lock:
            out, self._raw = self._raw, []
        return out

    def _process(self, in_data):
        """块处理（callback / read 线程共用）：解析 → 降混 → raw/bufs/on_block。
        read 模式：数据格式 = 请求格式（paInt16），绝不走探测——首块静音会被误判 f32，
        int16 数据按 f32 解析出 2e36 巨值 → clip 满幅 + VAD 疯狂误触发。
        callback 模式：格式 = 设备混音格式（与请求无关），首块全静音无法判断 → 跳过等
        有信号块再定（丢开头 ~23ms 无影响）。"""
        if self._stop.is_set():
            return
        try:
            if self.mode == "read":
                block = np.frombuffer(in_data, dtype=np.int16).astype(np.float32) / 32768.0
            else:
                if self._fmt is None:
                    f32max = float(np.abs(np.frombuffer(in_data[:64], dtype=np.float32)).max())
                    i16max = int(np.abs(np.frombuffer(in_data[:64], dtype=np.int16)).max())
                    # 坑：int16 数据按 f32 解析是随机大值（数亿，字节模式组合出高指数），
                    # 不是恒小值；f32 数据按 f32 解析是真实电平（<1）。
                    # 判定：f32 解析 <1 且 i16 有大值 → 真 float32；f32 解析 >1 → 真 int16。
                    if f32max < 1.0 and i16max > 100:
                        self._fmt = "f32"
                    elif f32max > 1.0:
                        self._fmt = "i16"
                    else:
                        return          # 全静音块：格式无法判断，等下一块
                if self._fmt == "f32":
                    block = np.nan_to_num(np.frombuffer(in_data, dtype=np.float32),
                                          nan=0.0, posinf=0.0, neginf=0.0)   # 引擎瞬态 NaN → 0
                else:
                    block = np.frombuffer(in_data, dtype=np.int16).astype(np.float32) / 32768.0
            if self._ch > 1:
                block = block.reshape(-1, self._ch)[:, 0]   # VAD/转写/落盘统一单声道（ch0）
            i16 = np.clip(block * 32767, -32768, 32767).astype(np.int16)
            r16 = resample_to_16k(block, self._sr)
            with self._lock:
                if self.capture_raw:
                    self._raw.append(i16)
                if self._rec:
                    self._bufs.append(r16)
            if self.on_block is not None:
                self.on_block(r16, float(np.sqrt(np.mean(block ** 2))))
        except Exception:
            pass

    def _cb(self, in_data, frame_count, time_info, status):
        self._process(in_data)
        return (None, pyaudio.paContinue)

    def stop(self):
        self._stop.set()
        with self._lock:
            s, self._stream = self._stream, None
        if s:
            try:
                s.stop_stream()   # read 线程阻塞在 read() 上，stop_stream 解阻塞并抛异常退出
                s.close()
            except Exception:
                pass
        t, self._thread = self._thread, None
        if t is not None and t is not threading.current_thread():
            t.join(timeout=2)

def loop_tcp_thread(port, rec):
    """面试官音频直连：播放器进程 TCP 发送 int16 2ch 44100Hz 原始流（本机静音方案——
    VB-Audio 虚拟声卡采集端在本机不可用，音频全程不出声卡），按 1024 帧块喂 Recorder。
    播放器以实时节奏发送，断连即线程退出（不影响 mic 轨）。"""
    import socket
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        srv.bind(("127.0.0.1", port))
        srv.listen(1)
        srv.settimeout(1.0)
    except OSError as e:
        print(f"⚠️ loop-tcp 监听 :{port} 失败: {e}", flush=True)
        return
    print(f"🔌 面试官音频 TCP 监听 :{port}（等待播放器连接）…", flush=True)
    CHUNK = 1024 * 2 * 2          # 1024 帧 × 2ch × int16
    while not rec._stop.is_set():
        try:
            conn, _ = srv.accept()
        except socket.timeout:
            continue
        except OSError:
            return
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        print("🔌 播放器已连接，面试官音频直连就绪", flush=True)
        buf = b""
        while not rec._stop.is_set():
            try:
                data = conn.recv(65536)
            except Exception:
                break
            if not data:
                break
            buf += data
            while len(buf) >= CHUNK:
                rec._process(buf[:CHUNK])
                buf = buf[CHUNK:]
        try:
            conn.close()
        except OSError:
            pass
        print("🔌 播放器断开，TCP 面试官轨结束（继续等待重连）", flush=True)


WAV_FLUSH_SEC = 5.0

def _patch_wav_size(path):
    """按当前文件大小重写 RIFF size（偏移4）和 data size（偏移40），不重建流"""
    try:
        size = os.path.getsize(path)
        if size < 44:          # header 还没写（0 字节新文件）：pack 负数会 struct.error
            return
        with open(path, "r+b") as f:
            f.seek(4)
            f.write(_struct.pack("<I", size - 8))
            f.seek(40)
            f.write(_struct.pack("<I", size - 44))
    except OSError:
        pass

class WavWriter:
    """双轨 WAV 持续落盘（原始未门控，复盘要真相）。take_raw 线程安全；每 5s 刷盘+修 header。"""
    def __init__(self, paths, recorders):
        self.paths = paths              # {label: 文件路径}
        self.recorders = recorders      # {label: Recorder}
        self._stop = threading.Event()
        self._w = {}                    # {label: wave.Wave_write}
        self._io_lock = threading.Lock()

    def _ensure(self, label, rec):
        if label in self._w or rec.device is None:
            return
        dev = rec.device
        w = wave.open(self.paths[label], "wb")
        w.setnchannels(1)              # 落盘统一单声道（_cb 已降混）
        w.setsampwidth(2)
        w.setframerate(int(dev["defaultSampleRate"]))
        w.writeframes(b"")              # 写 44 字节空 header 占位
        self._w[label] = w

    def _flush(self):
        for label, rec in self.recorders.items():
            self._ensure(label, rec)
            w = self._w.get(label)
            if w is None:
                continue
            blocks = rec.take_raw()
            if blocks:
                w.writeframes(np.concatenate(blocks).tobytes())
            _patch_wav_size(self.paths[label])

    def run(self):
        while not self._stop.wait(WAV_FLUSH_SEC):
            try:
                with self._io_lock:
                    self._flush()
            except Exception:
                traceback.print_exc()   # writer 异常别吞：报错可排障，线程继续跑

    def close(self):
        self._stop.set()
        with self._io_lock:
            try:
                self._flush()           # 退出前写完最后一个 5 秒窗口内的原始块
            except Exception:
                traceback.print_exc()
            for w in list(self._w.values()):
                try:
                    w.close()           # close 时 wave 模块自动修正 header
                except Exception:
                    pass
            self._w.clear()
