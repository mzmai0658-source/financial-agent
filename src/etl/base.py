from abc import ABC, abstractmethod
from typing import Any
from loguru import logger

from .state import AgentState
from src.agent.llm_client import LLMClient


class BaseAgent(ABC):
    def __init__(self, name: str, llm_client: LLMClient):
        self.name = name
        self.llm = llm_client
        self.state = AgentState()

    def update_state(self, new_state: AgentState):
        self.state = new_state

    @abstractmethod
    def run(self, input_data: Any) -> Any:
        pass

    def log(self, message: str):
        logger.info(f"[{self.name}] {message}")
