"""智能体基类"""
from abc import ABC, abstractmethod
import time


class BaseAgent(ABC):
    """所有智能体的基类"""

    def __init__(self, name: str, description: str, context: dict = None):
        self.name = name
        self.description = description
        self.context = context or {}

    @abstractmethod
    def run(self, input_data: dict) -> dict:
        """执行智能体逻辑，返回结果"""
        pass

    def log(self, message: str):
        ts = time.strftime("%H:%M:%S")
        print(f"  [{ts}] 🤖 {self.name}: {message}")
