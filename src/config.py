"""
OpenSub Live - Centralized Configuration
"""
from dataclasses import dataclass, field
import json
import os
from pathlib import Path


@dataclass
class AppConfig:
    # Server & IPC Settings
    server_host: str = "127.0.0.1"
    server_port: int = 9876

    # ASR (Speech-to-Text) Settings
    model_size: str = "base"  # "tiny", "base", "small", "medium", "large-v3"
    device: str = "auto"       # "auto", "cuda", "cpu"
    compute_type: str = "auto" # "auto", "float16", "int8", "int8_float16"
    task: str = "translate"    # "transcribe" (same language) or "translate" (to English)
    target_language: str = "en"
    beam_size: int = 2
    language: str | None = None  # None for auto-detection

    # Subtitle Typography & Styling
    font_family: str = "Segoe UI"
    font_size: int = 26
    font_weight: str = "Bold"
    text_color: str = "#FFFFFF"          # High-contrast white
    alt_text_color: str = "#FFE600"      # Cinema yellow
    stroke_color: str = "#000000"        # Solid black outline
    stroke_width: float = 2.8            # Outline thickness
    pill_bg_color: str = "rgba(0, 0, 0, 0.50)"  # Translucent background pill
    enable_pill_bg: bool = True
    max_chars_per_line: int = 44
    max_lines: int = 2
    fade_duration_ms: int = 150
    min_display_time_sec: float = 1.6

    # Window Geometry & Interactivity
    window_width: int = 860
    window_height: int = 110
    bottom_margin: int = 70
    is_locked: bool = False             # Click-through mode
    lock_hotkey: str = "ctrl+shift+l"

    # Audio & Fallback Stream Settings
    sample_rate: int = 16000
    chunk_buffer_sec: float = 30.0
    vad_threshold: float = 0.5
    wasapi_fallback_enabled: bool = True

    # Persist config to user data directory
    @classmethod
    def get_config_path(cls) -> Path:
        config_dir = Path.home() / ".opensub_live"
        config_dir.mkdir(parents=True, exist_ok=True)
        return config_dir / "config.json"

    def save(self):
        try:
            path = self.get_config_path()
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.__dict__, f, indent=2)
        except Exception as e:
            print(f"[Config] Error saving config: {e}")

    @classmethod
    def load(cls) -> "AppConfig":
        path = cls.get_config_path()
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
            except Exception as e:
                print(f"[Config] Error loading config, using defaults: {e}")
        config = cls()
        config.save()
        return config


# Global active config instance
CONFIG = AppConfig.load()
