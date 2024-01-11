import json
from typing import Union, List, Optional
import pandas as pd
from ..helpers.logger import Logger
from ..helpers.memory import Memory
from ..prompts.base import AbstractPrompt
from ..prompts.clarification_questions_prompt import ClarificationQuestionPrompt
from ..prompts.explain_prompt import ExplainPrompt
from ..prompts.rephase_query_prompt import RephraseQueryPrompt
from ..prompts.check_if_relevant_to_conversation import (
    CheckIfRelevantToConversationPrompt,
)
from ..schemas.df_config import Config
from ..skills import Skill
from ..smart_datalake import SmartDatalake


class Agent:
    """
    Agent class to improve the conversational experience in PandasAI
    """

    def __init__(
        self,
        dfs: Union[pd.DataFrame, List[pd.DataFrame]],
        config: Optional[Union[Config, dict]] = None,
        logger: Optional[Logger] = None,
        memory_size: int = 10,
    ):
        """
        Args:
            df (Union[pd.DataFrame, List[pd.DataFrame]]): Pandas dataframe
            Polars or Database connectors
            memory_size (int, optional): Conversation history to use during chat.
            Defaults to 1.
        """

        # Get a list of dataframes, if only one dataframe is passed
        if not isinstance(dfs, list):
            dfs = [dfs]

        # Configure the smart datalake
        self.lake = SmartDatalake(dfs, config, logger, memory=Memory(memory_size))
        self.lake.set_instance_type(self.__class__.__name__)

        self.logger = self.lake.logger

    def add_skills(self, *skills: Skill):
        """
        Add Skills to PandasAI
        """
        self.lake.add_skills(*skills)

    def call_llm_with_prompt(self, prompt: AbstractPrompt):
        """
        Call LLM with prompt using error handling to retry based on config
        Args:
            prompt (AbstractPrompt): AbstractPrompt to pass to LLM's
        """
        retry_count = 0
        while retry_count < self.lake.config.max_retries:
            try:
                result: str = self.lake.llm.call(prompt)
                if prompt.validate(result):
                    return result
                else:
                    raise Exception("Response validation failed!")
            except Exception:
                if (
                    not self.lake.config.use_error_correction_framework
                    or retry_count >= self.lake.config.max_retries - 1
                ):
                    raise
                retry_count += 1

    def chat(self, query: str, output_type: Optional[str] = None):
        """
        Simulate a chat interaction with the assistant on Dataframe.
        """
        try:
            is_related = self.check_if_related_to_conversation(query)
            self.lake.is_related_query(is_related)
            return self.lake.chat(query, output_type=output_type)
        except Exception as exception:
            return (
                "Unfortunately, I was not able to get your answers, "
                "because of the following error:\n"
                f"\n{exception}\n"
            )

    def add_message(self, message, is_user=False):
        """
        Add message to the memory. This is useful when you want to add a message
        to the memory without calling the chat function (for example, when you
        need to add a message from the agent).
        """
        self.lake.memory.add(message, is_user=is_user)

    def check_if_related_to_conversation(self, query: str) -> bool:
        """
        Check if the query is related to the previous conversation
        """
        if self.lake.memory.count() == 0:
            return

        prompt = CheckIfRelevantToConversationPrompt(
            conversation=self.lake.memory.get_conversation(),
            query=query,
        )

        result = self.call_llm_with_prompt(prompt)

        is_related = "true" in result
        self.logger.log(
            f"""Check if the new message is related to the conversation: {is_related}"""
        )

        if not is_related:
            self.lake.clear_memory()

        return is_related

    def clarification_questions(self, query: str) -> List[str]:
        """
        Generate clarification questions based on the data
        """
        prompt = ClarificationQuestionPrompt(
            dataframes=self.lake.dfs,
            conversation=self.lake.memory.get_conversation(),
            query=query,
        )

        result = self.call_llm_with_prompt(prompt)
        self.logger.log(
            f"""Clarification Questions:  {result}
            """
        )
        result = result.replace("```json", "").replace("```", "")
        questions: list[str] = json.loads(result)
        return questions[:3]

    def start_new_conversation(self):
        """
        Clears the previous conversation
        """
        self.lake.clear_memory()

    def explain(self) -> str:
        """
        Returns the explanation of the code how it reached to the solution
        """
        try:
            prompt = ExplainPrompt(
                conversation=self.lake.memory.get_conversation(),
                code=self.lake.last_code_executed,
            )
            response = self.call_llm_with_prompt(prompt)
            self.logger.log(
                f"""Explanation:  {response}
                """
            )
            return response
        except Exception as exception:
            return (
                "Unfortunately, I was not able to explain, "
                "because of the following error:\n"
                f"\n{exception}\n"
            )

    def rephrase_query(self, query: str):
        try:
            prompt = RephraseQueryPrompt(
                query=query,
                dataframes=self.lake.dfs,
                conversation=self.lake.memory.get_conversation(),
            )
            response = self.call_llm_with_prompt(prompt)
            self.logger.log(
                f"""Rephrased Response:  {response}
                """
            )
            return response
        except Exception as exception:
            return (
                "Unfortunately, I was not able to rephrase query, "
                "because of the following error:\n"
                f"\n{exception}\n"
            )

    @property
    def last_code_generated(self):
        return self.lake.last_code_generated

    @property
    def last_code_executed(self):
        return self.lake.last_code_executed

    @property
    def last_prompt(self):
        return self.lake.last_prompt

    @property
    def last_query_log_id(self):
        return self.lake.last_query_log_id
