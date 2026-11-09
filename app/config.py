"""Load only this project's private .env; never print configuration values."""

from pathlib import Path

from dotenv import load_dotenv


PROJECT_ENV = Path(__file__).resolve().parent.parent / ".env"


def load_environment() -> None:
    load_dotenv(dotenv_path=PROJECT_ENV, override=False, encoding="utf-8-sig")
