import re
import ast
import uuid
from collections import defaultdict

import astor
import pandas as pd
from pandasai.helpers.path import find_project_root

from pandasai.helpers.skills_manager import SkillsManager
from pandasai.helpers.sql import extract_table_names

from .node_visitors import AssignmentVisitor, CallVisitor
from .save_chart import add_save_chart
from .optional import import_dependency
from ..exceptions import (
    BadImportError,
    MaliciousQueryError,
    NoResultFoundError,
    InvalidConfigError,
)
from ..constants import WHITELISTED_BUILTINS, WHITELISTED_LIBRARIES
from ..connectors.sql import SQLConnector
from typing import Union, List, Generator, Any
from ..helpers.logger import Logger
from ..schemas.df_config import Config
import logging
import traceback
from ..connectors import BaseConnector


class CodeExecutionContext:
    def __init__(
        self,
        prompt_id: uuid.UUID,
        skills_manager: SkillsManager,
    ):
        """
        Code Execution Context
        Args:
            prompt_id (uuid.UUID): Prompt ID
            skills_manager (SkillsManager): Skills Manager
        """
        self.skills_manager = skills_manager
        self.prompt_id = prompt_id


class CodeManager:
    _dfs: List
    _config: Union[Config, dict]
    _logger: Logger = None
    _additional_dependencies: List[dict] = []
    _ast_comparator_map: dict = {
        ast.Eq: "=",
        ast.NotEq: "!=",
        ast.Lt: "<",
        ast.LtE: "<=",
        ast.Gt: ">",
        ast.GtE: ">=",
        ast.Is: "is",
        ast.IsNot: "is not",
        ast.In: "in",
        ast.NotIn: "not in",
    }
    _current_code_executed: str = None
    _last_code_executed: str = None

    def __init__(
        self,
        dfs: List,
        config: Union[Config, dict],
        logger: Logger,
    ):
        """
        Code Manager to execute the code generated by LLMs.
        Args:
            dfs (List): List of Dataframes
            config (Union[Config, dict]): Config
            logger (Logger): Logger
        """
        self._dfs = dfs
        self._config = config
        self._logger = logger

    def _required_dfs(self, code: str) -> List[str]:
        """
        List the index of the DataFrames that are needed to execute the code. The goal
        is to avoid to run the connectors if the code does not need them.

        Args:
            code (str): Python code to execute

        Returns:
            List[int]: A list of the index of the DataFrames that are needed to execute
            the code.
        """

        # Sometimes GPT-3.5/4 use a for loop to iterate over the dfs (even if there is only one)
        # or they concatenate the dfs. In this case we need all the dfs
        if "for df in dfs" in code or "pd.concat(dfs)" in code:
            return self._dfs

        required_dfs = []
        for i, df in enumerate(self._dfs):
            if f"dfs[{i}]" in code:
                required_dfs.append(df)
            else:
                required_dfs.append(None)
        return required_dfs

    def _replace_plot_png(self, code):
        """
        Replace plot.png with temp_chart.png
        Args:
            code (str): Python code to execute
        Returns:
            str: Python code with plot.png replaced with temp_chart.png
        """
        return re.sub(r"""(['"])([^'"]*\.png)\1""", r"\1temp_chart.png\1", code)

    def execute_code(self, code: str, context: CodeExecutionContext) -> Any:
        """
        Execute the python code generated by LLMs to answer the question
        about the input dataframe. Run the code in the current context and return the
        result.

        Args:
            code (str): Python code to execute.
            context (CodeExecutionContext): Code Execution Context
                    with prompt id and skills.

        Returns:
            Any: The result of the code execution. The type of the result depends
                on the generated code.

        """
        code = self._replace_plot_png(code)
        self._current_code_executed = code

        # Add save chart code
        if self._config.save_charts:
            code = add_save_chart(
                code,
                logger=self._logger,
                file_name=str(context.prompt_id),
                save_charts_path_str=self._config.save_charts_path,
            )
        else:
            # Temporarily save generated chart to display
            code = add_save_chart(
                code,
                logger=self._logger,
                file_name="temp_chart",
                save_charts_path_str=find_project_root() + "/exports/charts",
            )

        # Reset used skills
        context.skills_manager.used_skills = []

        # Get the code to run removing unsafe imports and df overwrites
        code_to_run = self._clean_code(code, context)
        self.last_code_executed = code_to_run
        self._logger.log(
            f"""
Code running:
```
{code_to_run}
        ```"""
        )

        # List the required dfs, so we can avoid to run the connectors
        # if the code does not need them
        dfs = self._required_dfs(code_to_run)
        environment: dict = self._get_environment()
        environment["dfs"] = self._get_originals(dfs)

        if self._config.direct_sql:
            environment["execute_sql_query"] = self._dfs[0].execute_direct_sql_query

        # Add skills to the env
        if context.skills_manager.used_skills:
            for skill_func_name in context.skills_manager.used_skills:
                skill = context.skills_manager.get_skill_by_func_name(skill_func_name)
                environment[skill_func_name] = skill

        # Execute the code
        exec(code_to_run, environment)

        # Get the result
        if "result" not in environment:
            raise NoResultFoundError("No result returned")

        return environment["result"]

    def _get_originals(self, dfs):
        """
        Get original dfs

        Args:
            dfs (list): List of dfs

        Returns:
            list: List of dfs
        """
        original_dfs = []
        for index, df in enumerate(dfs):
            if df is None:
                original_dfs.append(None)
                continue

            extracted_filters = self._extract_filters(self._current_code_executed)
            filters = extracted_filters.get(f"dfs[{index}]", [])
            df.set_additional_filters(filters)
            df.execute()
            # df.load_connector(partial=len(filters) > 0)

            original_dfs.append(df.pandas_df)

        return original_dfs

    def _get_environment(self) -> dict:
        """
        Returns the environment for the code to be executed.

        Returns (dict): A dictionary of environment variables
        """
        return {
            "pd": pd,
            **{
                lib["alias"]: getattr(import_dependency(lib["module"]), lib["name"])
                if hasattr(import_dependency(lib["module"]), lib["name"])
                else import_dependency(lib["module"])
                for lib in self._additional_dependencies
            },
            "__builtins__": {
                **{builtin: __builtins__[builtin] for builtin in WHITELISTED_BUILTINS},
                "__build_class__": __build_class__,
                "__name__": "__main__",
            },
        }

    def _is_jailbreak(self, node: ast.stmt) -> bool:
        """
        Remove jailbreaks from the code to prevent malicious code execution.
        Args:
            node (ast.stmt): A code node to be checked.
        Returns (bool):
        """

        DANGEROUS_BUILTINS = ["__subclasses__", "__builtins__", "__import__"]

        node_str = ast.dump(node)

        return any(builtin in node_str for builtin in DANGEROUS_BUILTINS)

    def _is_unsafe(self, node: ast.stmt) -> bool:
        """
        Remove unsafe code from the code to prevent malicious code execution.

        Args:
            node (ast.stmt): A code node to be checked.

        Returns (bool):
        """

        code = astor.to_source(node)
        return any(
            (
                method in code
                for method in [
                    ".to_csv",
                    ".to_excel",
                    ".to_json",
                    ".to_sql",
                    ".to_feather",
                    ".to_hdf",
                    ".to_parquet",
                    ".to_pickle",
                    ".to_gbq",
                    ".to_stata",
                    ".to_records",
                    ".to_latex",
                    ".to_html",
                    ".to_markdown",
                    ".to_clipboard",
                ]
            )
        )

    def find_function_calls(self, node: ast.AST, context: CodeExecutionContext):
        if (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Call)
            and hasattr(node.value.func, "id")
            and context.skills_manager.skill_exists(node.value.func.id)
        ):
            function_name = node.value.func.id
            context.skills_manager.add_used_skill(function_name)
        for child_node in ast.iter_child_nodes(node):
            self.find_function_calls(child_node, context)

    def check_direct_sql_func_def_exists(self, node: ast.AST):
        return (
            self._validate_direct_sql(self._dfs)
            and isinstance(node, ast.FunctionDef)
            and node.name == "execute_sql_query"
        )

    def _validate_direct_sql(self, dfs: List[BaseConnector]) -> bool:
        """
        Raises error if they don't belong sqlconnector or have different credentials
        Args:
            dfs (List[BaseConnector]): list of BaseConnectors

        Raises:
            InvalidConfigError: Raise Error in case of config is set but criteria is not met
        """

        if self._config.direct_sql:
            if all((isinstance(df, SQLConnector) and df.equals(dfs[0])) for df in dfs):
                return True
            else:
                raise InvalidConfigError(
                    "Direct requires all SQLConnector and they belong to same datasource "
                    "and have same credentials"
                )
        return False

    def _get_sql_irrelevant_tables(self, node: ast.Assign):
        for target in node.targets:
            if (
                isinstance(target, ast.Name)
                and target.id in "sql_query"
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            ):
                sql_query = node.value.value
                table_names = extract_table_names(sql_query)
                allowed_table_names = [df.name for df in self._dfs]
                return [
                    table_name
                    for table_name in table_names
                    if table_name not in allowed_table_names
                ]

    def _clean_code(self, code: str, context: CodeExecutionContext) -> str:
        """
        A method to clean the code to prevent malicious code execution.

        Args:
            code(str): A python code.

        Returns:
            str: A clean code string.

        """

        # Clear recent optional dependencies
        self._additional_dependencies = []

        tree = ast.parse(code)

        # Check for imports and the node where analyze_data is defined
        new_body = []
        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                self._check_imports(node)
                continue

            if (
                self._is_df_overwrite(node)
                or self._is_jailbreak(node)
                or self._is_unsafe(node)
            ):
                continue

            # if generated code contain execute_sql_query def remove it
            # function already defined
            if self.check_direct_sql_func_def_exists(node):
                continue

            # Sanity for sql query the code should only use allowed tables
            if (
                isinstance(node, ast.Assign)
                and self._config.direct_sql
                and (unauthorized_tables := self._get_sql_irrelevant_tables(node))
            ):
                raise MaliciousQueryError(
                    f"Query uses unauthorized tables: {unauthorized_tables}. Please add them as new datatables or update the query."
                )

            self.find_function_calls(node, context)

            new_body.append(node)

        new_tree = ast.Module(body=new_body)
        return astor.to_source(new_tree, pretty_source=lambda x: "".join(x)).strip()

    def _is_df_overwrite(self, node: ast.stmt) -> bool:
        """
        Remove df declarations from the code to prevent malicious code execution.

        Args:
            node (ast.stmt): A code node to be checked.

        Returns (bool):

        """

        return (
            isinstance(node, ast.Assign)
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "dfs"
        )

    def _check_imports(self, node: Union[ast.Import, ast.ImportFrom]):
        """
        Add whitelisted imports to _additional_dependencies.

        Args:
            node (object): ast.Import or ast.ImportFrom

        Raises:
            BadImportError: If the import is not whitelisted

        """
        module = node.names[0].name if isinstance(node, ast.Import) else node.module
        library = module.split(".")[0]

        if library == "pandas":
            return

        if (
            library
            in WHITELISTED_LIBRARIES + self._config.custom_whitelisted_dependencies
        ):
            for alias in node.names:
                self._additional_dependencies.append(
                    {
                        "module": module,
                        "name": alias.name,
                        "alias": alias.asname or alias.name,
                    }
                )
            return

        if library not in WHITELISTED_BUILTINS:
            raise BadImportError(library)

    @staticmethod
    def _get_nearest_func_call(current_lineno, calls, func_name):
        """
        Utility function to get the nearest previous call node.

        Sort call nodes list (copy of the list) by line number.
        Iterate over the call nodes list. If the call node's function name
        equals to `func_name`, set `nearest_call` to the node object.

        Args:
            current_lineno (int): Number of the current processed line.
            calls (list[ast.Assign]): List of call nodes.
            func_name (str): Name of the target function.

        Returns:
            ast.Call: The node of the nearest previous call `<func_name>()`.
        """
        for call in reversed(calls):
            if call.lineno < current_lineno:
                try:
                    if call.func.attr == func_name:
                        return call
                except AttributeError:
                    continue

        return None

    @staticmethod
    def _tokenize_operand(operand_node: ast.expr) -> Generator[str, None, None]:
        """
        Utility generator function to get subscript slice constants.

        Args:
            operand_node (ast.expr):
                The node to be tokenized.
        Yields:
            str: Token string.

        Examples:
            >>> code = '''
            ... foo = [1, [2, 3], [[4, 5], [6, 7]]]
            ... print(foo[2][1][0])
            ... '''
            >>> tree = ast.parse(code)
            >>> res = CodeManager._tokenize_operand(tree.body[1].value.args[0])
            >>> print(list(res))
            ['foo', 2, 1, 0]
        """
        if isinstance(operand_node, ast.Subscript):
            slice_ = operand_node.slice.value
            yield from CodeManager._tokenize_operand(operand_node.value)
            yield slice_

        if isinstance(operand_node, ast.Name):
            yield operand_node.id

        if isinstance(operand_node, ast.Constant):
            yield operand_node.value

    @staticmethod
    def _get_df_id_by_nearest_assignment(
        current_lineno: int, assignments: list[ast.Assign], target_name: str
    ):
        """
        Utility function to get df label by finding the nearest assignment.

        Sort assignment nodes list (copy of the list) by line number.
        Iterate over the assignment nodes list. If the assignment node's value
        looks like `dfs[<index>]` and target label equals to `target_name`,
        set `nearest_assignment` to "dfs[<index>]".

        Args:
            current_lineno (int): Number of the current processed line.
            assignments (list[ast.Assign]): List of assignment nodes.
            target_name (str): Name of the target variable. The assignment
                node is supposed to assign to this name.

        Returns:
            str: The string representing df label, looks like "dfs[<index>]".
        """
        nearest_assignment = None
        assignments = sorted(assignments, key=lambda node: node.lineno)
        for assignment in assignments:
            if assignment.lineno > current_lineno:
                return nearest_assignment
            try:
                is_subscript = isinstance(assignment.value, ast.Subscript)
                dfs_on_the_right = assignment.value.value.id == "dfs"
                assign_to_target = assignment.targets[0].id == target_name
                if is_subscript and dfs_on_the_right and assign_to_target:
                    nearest_assignment = f"dfs[{assignment.value.slice.value}]"
            except AttributeError:
                continue

    def _extract_comparisons(self, tree: ast.Module) -> dict[str, list]:
        """
        Process nodes from passed tree to extract filters.

        Collects all assignments in the tree.
        Collects all function calls in the tree.
        Walk over the tree and handle each comparison node.
        For each comparison node, defined what `df` is this node related to.
        Parse constants values from the comparison node.
        Add to the result dict.

        Args:
            tree (str): A snippet of code to be parsed.

        Returns:
            dict: The `defaultdict(list)` instance containing all filters
                parsed from the passed instructions tree. The dictionary has
                the following structure:
                {
                    "<df_number>": [
                        ("<left_operand>", "<operator>", "<right_operand>")
                    ]
                }
        """
        comparisons = defaultdict(list)
        current_df = "dfs[0]"

        visitor = AssignmentVisitor()
        visitor.visit(tree)
        assignments = visitor.assignment_nodes

        call_visitor = CallVisitor()
        call_visitor.visit(tree)

        for node in ast.walk(tree):
            if isinstance(node, ast.Compare) and isinstance(node.left, ast.Subscript):
                name, *slices = self._tokenize_operand(node.left)
                current_df = (
                    self._get_df_id_by_nearest_assignment(
                        node.lineno, assignments, name
                    )
                    or current_df
                )
                left_str = slices[-1] if slices else name

                for op, right in zip(node.ops, node.comparators):
                    op_str = self._ast_comparator_map.get(type(op), "Unknown")
                    name, *slices = self._tokenize_operand(right)
                    right_str = slices[-1] if slices else name

                    comparisons[current_df].append((left_str, op_str, right_str))
        return comparisons

    def _extract_filters(self, code) -> dict[str, list]:
        """
        Extract filters to be applied to the dataframe from passed code.

        Args:
            code (str): A snippet of code to be parsed.

        Returns:
            dict: The dictionary containing all filters parsed from
                the passed code. The dictionary has the following structure:
                {
                    "<df_number>": [
                        ("<left_operand>", "<operator>", "<right_operand>")
                    ]
                }

        Raises:
            SyntaxError: If the code is unable to be parsed by `ast.parse()`.
            Exception: If any exception is raised during working with nodes
                of the code tree.
        """
        try:
            parsed_tree = ast.parse(code)
        except SyntaxError:
            self._logger.log(
                "Invalid code passed for extracting filters", level=logging.ERROR
            )
            self._logger.log(f"{traceback.format_exc()}", level=logging.DEBUG)
            raise

        try:
            filters = self._extract_comparisons(parsed_tree)
        except Exception:
            self._logger.log(
                "Unable to extract filters for passed code", level=logging.ERROR
            )
            self._logger.log(f"{traceback.format_exc()}", level=logging.DEBUG)
            raise

        return filters

    @property
    def last_code_executed(self):
        return self._last_code_executed

    @last_code_executed.setter
    def last_code_executed(self, code: str):
        self._last_code_executed = code
