"""
Состояния для обработки запросов пользователей.
"""
from enum import Enum


class UserState(Enum):
    """Состояния пользователя в процессе создания запроса."""
    WAITING_FOR_PHOTO = "waiting_for_photo"
    WAITING_FOR_PROMPT = "waiting_for_prompt"
    WAITING_FOR_ASPECT_RATIO = "waiting_for_aspect_ratio"
    IDLE = "idle"
