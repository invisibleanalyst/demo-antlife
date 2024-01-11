from typing import List, Optional, Union, Any
from pandasai.helpers.cache import Cache

from pandasai.helpers.memory import Memory
from pandasai.helpers.query_exec_tracker import QueryExecTracker
from pandasai.helpers.skills_manager import SkillsManager
from pandasai.schemas.df_config import Config
import pandas as pd


class PipelineContext:
    """
    Pass Context to the pipeline which is accessible to each step via kwargs
    """

    def __init__(
        self,
        dfs: List[Union[pd.DataFrame, Any]],
        config: Optional[Union[Config, dict]] = None,
        memory: Optional[Memory] = None,
        skills_manager: Optional[SkillsManager] = None,
        cache: Optional[Cache] = None,
        query_exec_tracker: Optional[QueryExecTracker] = None,
        initial_values: dict = None,
    ) -> None:
        from pandasai.smart_dataframe import load_smartdataframes

        if isinstance(config, dict):
            config = Config(**config)

        self.dfs = load_smartdataframes(dfs, config)
        self.memory = memory or Memory()
        self.skills_manager = skills_manager or SkillsManager()

        if config.enable_cache:
            self.cache = cache if cache is not None else Cache()
        else:
            self.cache = None

        self.config = config
        self.query_exec_tracker = query_exec_tracker or QueryExecTracker()
        self.intermediate_values = initial_values or {}

    def add(self, key: str, value: Any):
        self.intermediate_values[key] = value

    def add_many(self, values: dict):
        self.intermediate_values.update(values)

    def get(self, key: str):
        return self.intermediate_values.get(key, "")
