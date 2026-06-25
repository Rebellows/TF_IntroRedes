"""
config.py - Load and validate the configuration file.
"""

import configparser
import os


class Config:
    """Holds all runtime parameters read from config.ini."""

    def __init__(self, path: str = "config.ini"):
        if not os.path.exists(path):
            raise FileNotFoundError(f"Config file not found: {path}")

        # Caminho absoluto do arquivo REALMENTE aberto — ajuda a flagrar quando
        # você editou uma cópia mas executou de outra pasta.
        self.path: str = os.path.abspath(path)

        parser = configparser.ConfigParser()
        parser.read(path)

        # [machine]
        self.nickname: str = parser.get("machine", "nickname").strip().upper()
        if not self.nickname.isalpha():
            raise ValueError("nickname must contain only letters")

        # Optional manual IP override — useful when auto-detection picks the wrong interface
        self.ip: str = parser.get("machine", "ip", fallback="").strip()

        # [timing]
        self.token_delay: float        = parser.getfloat("timing", "token_delay")
        self.data_delay: float         = parser.getfloat("timing", "data_delay")
        self.token_timeout: float      = parser.getfloat("timing", "token_timeout")
        self.min_token_interval: float = parser.getfloat("timing", "min_token_interval")

        # [faults]
        self.error_probability: float = parser.getfloat("faults", "error_probability")
        if not (0.0 <= self.error_probability <= 1.0):
            raise ValueError("error_probability must be in [0, 1]")

    def __repr__(self) -> str:
        return (
            f"Config(nickname={self.nickname!r}, ip={self.ip!r}, "
            f"token_delay={self.token_delay}, data_delay={self.data_delay}, "
            f"token_timeout={self.token_timeout}, "
            f"min_token_interval={self.min_token_interval}, "
            f"error_probability={self.error_probability})"
        )
