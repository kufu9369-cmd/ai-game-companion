import sys
import os
import re

import sherpa_onnx
import soundfile as sf
from loguru import logger
from .tts_interface import TTSInterface

current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(current_dir)

# 表情/动作标签，例如 [smirk] [neutral]
_TAG_RE = re.compile(r"\[[^\]\n]{1,24}\]|【[^】\n]{1,24}】")

# Melo 的词典是白名单式的：全角引号 “ ” ‘ ’、破折号 —、省略号 …、各种 emoji
# 都属于 OOV，会直接让 **整段** 合成失败（offline-tts-vits-impl.h: Generate:214
# Failed to convert ” to token IDs → audio.samples 为空 → 这一句完全没有声音）。
# 所以这里做白名单过滤：只保留中日韩文字、字母数字和常用中英标点。
_OOV_RE = re.compile(
    r"[^\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af"
    r"0-9A-Za-z"
    r"，。！？、；：,.!?;:%~\-\u0020]"
)


def sanitize_for_tts(text: str) -> str:
    """把 TTS 不支持（OOV）的字符清掉，避免整段合成失败。"""
    if not text:
        return text
    cleaned = _TAG_RE.sub("", text)
    cleaned = _OOV_RE.sub("", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _strip_aggressive(text: str) -> str:
    """更强的兜底：只留中日韩文字与字母数字。"""
    return re.sub(r"[^\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af0-9A-Za-z]", "", text)


class TTSEngine(TTSInterface):
    def __init__(
        self,
        vits_model,
        vits_lexicon="",
        vits_tokens="",
        vits_data_dir="",
        vits_dict_dir="",
        tts_rule_fsts="",
        max_num_sentences=2,
        sid=0,
        provider="cpu",
        num_threads=1,
        speed=1.0,
        debug=False,
    ):
        self.vits_model = vits_model
        self.vits_lexicon = vits_lexicon
        self.vits_tokens = vits_tokens
        self.vits_data_dir = vits_data_dir
        self.vits_dict_dir = vits_dict_dir
        self.tts_rule_fsts = tts_rule_fsts
        self.max_num_sentences = max_num_sentences
        self.sid = sid  # Speaker ID
        self.provider = provider  # Computation provider (e.g., "cpu", "cuda")
        self.num_threads = num_threads
        self.speed = speed  # Speech speed
        self.debug = debug  # Debug mode flag

        self.file_extension = "wav"
        self.new_audio_dir = "cache"

        if not os.path.exists(self.new_audio_dir):
            os.makedirs(self.new_audio_dir)

        self.tts = self.initialize_tts()

    def initialize_tts(self):
        """
        Initialize the sherpa-onnx TTS engine.
        """
        # Construct the configuration for the TTS engine
        tts_config = sherpa_onnx.OfflineTtsConfig(
            model=sherpa_onnx.OfflineTtsModelConfig(
                vits=sherpa_onnx.OfflineTtsVitsModelConfig(
                    model=self.vits_model,
                    lexicon=self.vits_lexicon,
                    data_dir=self.vits_data_dir,
                    dict_dir=self.vits_dict_dir,
                    tokens=self.vits_tokens,
                ),
                provider=self.provider,
                debug=self.debug,
                num_threads=self.num_threads,
            ),
            rule_fsts=self.tts_rule_fsts,
            max_num_sentences=self.max_num_sentences,
        )

        # Validate the configuration
        if not tts_config.validate():
            raise ValueError("Please check your sherpa-onnx TTS config")

        # Create and return the sherpa-onnx OfflineTts object
        return sherpa_onnx.OfflineTts(tts_config)

    def generate_audio(self, text, file_name_no_ext=None):
        """
        Generate speech audio file using sherpa-onnx TTS.

        Parameters:
            text (str): The text to speak.
            file_name_no_ext (str, optional): Name of the file without extension.

        Returns:
            str: The path to the generated audio file.
        """
        file_name = self.generate_cache_file_name(file_name_no_ext, self.file_extension)

        try:
            safe_text = sanitize_for_tts(text)
            if safe_text != text:
                logger.debug(f"TTS 文本 OOV 过滤: {text!r} -> {safe_text!r}")
            if not safe_text:
                logger.error(f"TTS 文本过滤后为空，跳过合成：{text!r}")
                return None

            audio = self.tts.generate(safe_text, sid=self.sid, speed=self.speed)

            # 兜底：仍失败就再激进地清一遍（只留文字），避免整句没声音
            if len(audio.samples) == 0:
                fallback_text = _strip_aggressive(safe_text)
                if fallback_text and fallback_text != safe_text:
                    logger.warning(f"TTS 首次合成失败，用纯文本重试：{fallback_text!r}")
                    audio = self.tts.generate(fallback_text, sid=self.sid, speed=self.speed)

            if len(audio.samples) == 0:
                logger.error(
                    "Error in generating audios. Please read previous error messages."
                )
                return None

            sf.write(
                file_name,
                audio.samples,
                samplerate=audio.sample_rate,
                subtype="PCM_16",
            )

            return file_name

        except Exception as e:
            logger.critical(f"\nError: sherpa-onnx unable to generate audio: {e}")
            return None
