import os
import json
import glob
import threading
import time

import numpy as np
import soundfile as sf
from loguru import logger

from .tts_interface import TTSInterface

try:
    from .sherpa_onnx_tts import sanitize_for_tts
except Exception:  # 兜底：净化函数不可用时原样返回
    def sanitize_for_tts(t):
        return t


class TTSEngine(TTSInterface):
    """ZipVoice 零样本声音克隆 TTS（sherpa-onnx）。

    声线库：`voice_dir/<名字>/voice.wav + voice.json`（json: {"text": 参考音频说的话, "speed": 可选}）
    - 参考音频 3~10 秒、24kHz 单声道最佳（其他采样率也可，内部会重采样）
    - 运行时通过 set_voice(name) 热切换声线（每次合成时传入对应参考音频，无需重建引擎）
    - 多客户端共享同一个底层引擎实例（类级单例），显存/内存只占一份
    """

    _shared_tts = None            # 类级共享 OfflineTts 实例
    _shared_key = None            # 记录该实例对应的模型配置
    _init_lock = threading.Lock()

    def __init__(
        self,
        model_dir: str = "./models/sherpa-onnx-zipvoice-distill-int8-zh-en-emilia",
        vocoder: str = "./models/vocos_24khz.onnx",
        voice_dir: str = "./tts_voices",
        default_voice: str = "露露",
        num_steps: int = 4,
        num_threads: int = 4,
        speed: float = 1.0,
        provider: str = "cpu",
        debug: bool = False,
        **kwargs,
    ):
        self.model_dir = model_dir
        self.vocoder = vocoder
        self.voice_dir = voice_dir
        self.num_steps = int(num_steps)
        self.num_threads = int(num_threads)
        self.provider = provider
        self.debug = bool(debug)
        self.speed = float(speed)
        self.file_extension = "wav"

        os.makedirs(self.voice_dir, exist_ok=True)
        self.current_voice = self._resolve_voice(default_voice)
        self._ref_cache = {}  # name -> (wav_path, text)

    # ---------------- 声线库管理 ----------------

    def _resolve_voice(self, name: str) -> str:
        """找声线；找不到就回退到库里第一个，再不行报错。"""
        voices = self.list_voices()
        if not voices:
            raise RuntimeError(
                f"声线库为空（{self.voice_dir}）。请先添加声线："
                f"放入 <名字>/voice.wav + voice.json，或用界面「录制/导入」。"
            )
        if name and any(v["name"] == name for v in voices):
            return name
        logger.warning(f"声线 {name!r} 不存在，回退到 {voices[0]['name']!r}")
        return voices[0]["name"]

    @classmethod
    def list_voices(cls, voice_dir: str = "./tts_voices") -> list:
        """列出声线库：[{name, text, speed, created}]"""
        out = []
        for jp in sorted(glob.glob(os.path.join(voice_dir, "*", "voice.json"))):
            try:
                meta = json.load(open(jp, encoding="utf-8"))
                name = os.path.basename(os.path.dirname(jp))
                out.append(
                    {
                        "name": name,
                        "text": meta.get("text", ""),
                        "speed": meta.get("speed", 1.0),
                        "engine": meta.get("engine", "zipvoice"),
                        "created": meta.get("created", ""),
                    }
                )
            except Exception as e:
                logger.warning(f"读取声线元数据失败 {jp}: {e}")
        return out

    def get_voice(self) -> str:
        return self.current_voice

    def set_voice(self, name: str) -> str:
        self.current_voice = self._resolve_voice(name)
        logger.info(f"ZipVoice 声线已切换 -> {self.current_voice}")
        return self.current_voice

    def _reference(self, name: str):
        """取某声线的 (wav_path, text)，带缓存。"""
        if name in self._ref_cache:
            return self._ref_cache[name]
        d = os.path.join(self.voice_dir, name)
        wav = os.path.join(d, "voice.wav")
        if not os.path.exists(wav):
            raise RuntimeError(f"声线 {name} 缺少 voice.wav")
        meta = {}
        jp = os.path.join(d, "voice.json")
        if os.path.exists(jp):
            meta = json.load(open(jp, encoding="utf-8"))
        text = (meta.get("text") or "").strip()
        if not text:
            raise RuntimeError(
                f"声线 {name} 的 voice.json 缺少 text（参考音频对应的文字）"
            )
        val = (wav, text)
        self._ref_cache[name] = val
        return val

    # ---------------- 引擎（懒加载，类级共享） ----------------

    def _engine(self):
        key = (self.model_dir, self.vocoder, self.num_threads, self.provider)
        if TTSEngine._shared_tts is not None and TTSEngine._shared_key == key:
            return TTSEngine._shared_tts
        with TTSEngine._init_lock:
            if TTSEngine._shared_tts is not None and TTSEngine._shared_key == key:
                return TTSEngine._shared_tts
            import sherpa_onnx

            vits_cfg = sherpa_onnx.OfflineTtsZipvoiceModelConfig(
                encoder=os.path.join(self.model_dir, "encoder.int8.onnx"),
                decoder=os.path.join(self.model_dir, "decoder.int8.onnx"),
                vocoder=self.vocoder,
                lexicon=os.path.join(self.model_dir, "lexicon.txt"),
                tokens=os.path.join(self.model_dir, "tokens.txt"),
                data_dir=os.path.join(self.model_dir, "espeak-ng-data"),
            )
            model_cfg = sherpa_onnx.OfflineTtsModelConfig(
                zipvoice=vits_cfg,
                num_threads=self.num_threads,
                provider=self.provider,
                debug=self.debug,
            )
            tts_cfg = sherpa_onnx.OfflineTtsConfig(model=model_cfg)
            t0 = time.time()
            engine = sherpa_onnx.OfflineTts(tts_cfg)
            logger.info(
                f"ZipVoice 引擎加载完成（{time.time()-t0:.1f}s，采样率 {engine.sample_rate}）"
            )
            TTSEngine._shared_tts = engine
            TTSEngine._shared_key = key
            return engine

    # ---------------- 合成 ----------------

    def generate_audio(self, text: str, file_name_no_ext=None) -> str:
        text = sanitize_for_tts(text)
        if not text:
            raise RuntimeError("TTS 文本为空")

        engine = self._engine()
        wav_path, ref_text = self._reference(self.current_voice)

        import sherpa_onnx

        ref_samples, ref_sr = sf.read(wav_path, dtype="float32", always_2d=False)
        if ref_samples.ndim > 1:  # 多声道 -> 取均值变单声道
            ref_samples = ref_samples.mean(axis=1)

        gen_cfg = sherpa_onnx.GenerationConfig()
        gen_cfg.num_steps = self.num_steps
        gen_cfg.speed = self.speed
        gen_cfg.reference_audio = ref_samples
        gen_cfg.reference_sample_rate = int(ref_sr)
        gen_cfg.reference_text = ref_text
        t0 = time.time()
        audio = engine.generate(text, gen_cfg)
        dt = time.time() - t0

        file_name = self.generate_cache_file_name(file_name_no_ext, self.file_extension)
        sf.write(
            file_name,
            np.asarray(audio.samples, dtype=np.float32),
            audio.sample_rate,
        )
        n = len(audio.samples)
        logger.info(
            f"[ZipVoice] 声线={self.current_voice} {len(text)}字 -> {n/audio.sample_rate:.1f}s 音频，"
            f"耗时 {dt:.1f}s (RTF {dt/(n/audio.sample_rate):.2f})"
        )
        return file_name
