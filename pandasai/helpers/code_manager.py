import re
import ast
import astor
import pandas as pd
from .save_chart import add_save_chart
from .optional import import_dependency
from ..exceptions import BadImportError
from ..middlewares.base import Middleware
from ..constants import (
    WHITELISTED_BUILTINS,
    WHITELISTED_LIBRARIES,
)
from ..middlewares.charts import ChartsMiddleware
from typing import Union, List, Optional
from ..helpers.logger import Logger
from ..helpers.df_config import Config
from ..helpers.df_info import DataFrameType
import logging
import traceback


class CodeManager:
    _dfs: List[DataFrameType]
    _middlewares: List[Middleware] = [ChartsMiddleware()]
    _config: Config
    _logger: Logger = None
    _additional_dependencies: List[dict] = []

    def __init__(
        self,
        dfs: List[DataFrameType],
        config: Config,
        logger: Logger,
    ):
        """
        Args:
            config (Config, optional): Config to be used. Defaults to None.
            logger (Logger, optional): Logger to be used. Defaults to None.
        """

        self._dfs = dfs
        self._config = config
        self._logger = logger

        if self._config.middlewares is not None:
            self.add_middlewares(*self._config.middlewares)

    def add_middlewares(self, *middlewares: List[Middleware]):
        """
        Add middlewares to PandasAI instance.

        Args:
            *middlewares: A list of middlewares

        """
        self._middlewares.extend(middlewares)

    def _execute_catching_errors(
        self, code: str, environment: dict
    ) -> Optional[Exception]:
        """
        Perform execution of the code directly.
        Call `exec()` for the given `code`, catch any non-base exceptions.
        Args:
            code (str): Python code
            environment (dict): Context for the `exec()`
        Returns (Optional[Exception]): Any exception raised during execution.
                                       `None` if executed without exceptions.
        """
        try:
            # Check in the code that analyze_data function is called.
            # If not, add it.
            if " = analyze_data(" not in code:
                code += "\n\nresult = analyze_data(dfs)"

            exec(code, environment)
        except Exception as exc:
            self._logger.log("Error of executing code", level=logging.WARNING)
            self._logger.log(f"{traceback.format_exc()}", level=logging.DEBUG)

            return exc

    def _handle_error(
        self,
        exc: Exception,
        code: str,
        environment: dict,
        use_error_correction_framework: bool = True,
    ):
        """
        Handle error occurred during first executing of code.
        If `exc` is instance of `NameError`, try to import the name, extend
        the context and then call `_execute_catching_errors()` again.
        If OK, returns the code string; if failed, continuing handling.
        Args:
            exc (Exception): The caught exception.
            code (str): Python code.
            environment (dict): Context for the `exec()`
        Raises:
            Exception: Any exception which has been caught during
                       the very first execution of the code.
        Returns (str): Python code. Either an original or new, given by
                       error correction framework.
        """
        if isinstance(exc, NameError):
            name_to_be_imported = None
            if hasattr(exc, "name"):
                name_to_be_imported = exc.name
            elif exc.args and isinstance(exc.args[0], str):
                name_ptrn = r"'([0-9a-zA-Z_]+)'"
                if search_name_res := re.search(name_ptrn, exc.args[0]):
                    name_to_be_imported = search_name_res.group(1)

            if name_to_be_imported and name_to_be_imported in WHITELISTED_LIBRARIES:
                try:
                    package = import_dependency(name_to_be_imported)
                    environment[name_to_be_imported] = package

                    caught_error = self._execute_catching_errors(code, environment)
                    if caught_error is None:
                        return code

                except ModuleNotFoundError:
                    self._logger.log(
                        f"Unable to fix `NameError`: package '{name_to_be_imported}'"
                        f" could not be imported.",
                        level=logging.DEBUG,
                    )
                except Exception as new_exc:
                    exc = new_exc
                    self._logger.log(
                        f"Unable to fix `NameError`: an exception was raised: "
                        f"{traceback.format_exc()}",
                        level=logging.DEBUG,
                    )

            if not use_error_correction_framework:
                raise exc

    def execute_code(
        self,
        code: str,
        prompt_id: str,
    ) -> str:
        """
        Execute the python code generated by LLMs to answer the question
        about the input dataframe. Run the code in the current context and return the
        result.

        Args:
            code (str): Python code to execute
            data_frame (pd.DataFrame): Full Pandas DataFrame
            use_error_correction_framework (bool): Turn on Error Correction mechanism.
            Default to True

        Returns:
            result: The result of the code execution. The type of the result depends
            on the generated code.

        """

        for middleware in self._middlewares:
            code = middleware(code)

        # Add save chart code
        if self._config.save_charts:
            code = add_save_chart(
                code,
                logger=self._logger,
                folder_name=prompt_id,
                save_charts_path=self._config.save_charts_path,
            )

        # Get the code to run removing unsafe imports and df overwrites
        code_to_run = self._clean_code(code)
        self.last_code_executed = code_to_run
        self._logger.log(
            f"""
Code running:
```
{code_to_run}
        ```"""
        )

        environment: dict = self._get_environment()

        caught_error = self._execute_catching_errors(code_to_run, environment)
        if caught_error is not None:
            self._handle_error(
                caught_error,
                code_to_run,
                environment,
                use_error_correction_framework=self._config.use_error_correction_framework,
            )

        analyze_data = environment.get("analyze_data", None)

        return analyze_data(self._dfs)

    def _get_environment(self) -> dict:
        """
        Returns the environment for the code to be executed.

        Returns (dict): A dictionary of environment variables
        """

        dfs = []
        for df in self._dfs:
            dfs.append(df.original)

        return {
            "pd": pd,
            "dfs": dfs,
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
            node (object): ast.stmt
        Returns (bool):
        """

        DANGEROUS_BUILTINS = ["__subclasses__", "__builtins__", "__import__"]

        node_str = ast.dump(node)

        for builtin in DANGEROUS_BUILTINS:
            if builtin in node_str:
                return True

        return False

    def _is_unsafe(self, node: ast.stmt) -> bool:
        """
        Remove unsafe code from the code to prevent malicious code execution.

        Args:
            node (object): ast.stmt

        Returns (bool):
        """

        code = astor.to_source(node)
        if any(
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
                ".to_string",
                ".to_latex",
                ".to_html",
                ".to_markdown",
                ".to_clipboard",
            ]
        ):
            return True

        return False

    def _sanitize_analyze_data(self, analyze_data_node: ast.stmt) -> ast.stmt:
        # Sanitize the code within analyze_data
        sanitized_analyze_data = []
        for node in analyze_data_node.body:
            if (
                self._is_df_overwrite(node)
                or self._is_jailbreak(node)
                or self._is_unsafe(node)
            ):
                continue
            sanitized_analyze_data.append(node)

        analyze_data_node.body = sanitized_analyze_data
        return analyze_data_node

    def _clean_code(self, code: str) -> str:
        """
        A method to clean the code to prevent malicious code execution

        Args:
            code(str): A python code

        Returns (str): Returns a Clean Code String

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
            if isinstance(node, ast.FunctionDef) and node.name == "analyze_data":
                analyze_data_node = node
                sanitized_analyze_data = self._sanitize_analyze_data(analyze_data_node)
                new_body.append(sanitized_analyze_data)
                continue
            new_body.append(node)

        new_tree = ast.Module(body=new_body)
        return astor.to_source(new_tree, pretty_source=lambda x: "".join(x)).strip()

    def _is_df_overwrite(self, node: ast.stmt) -> bool:
        """
        Remove df declarations from the code to prevent malicious code execution.

        Args:
            node (object): ast.stmt

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
        if isinstance(node, ast.Import):
            module = node.names[0].name
        else:
            module = node.module

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

    @property
    def middlewares(self):
        return self._middlewares
