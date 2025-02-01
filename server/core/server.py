import os
from typing import List

import pandas as pd
from fastapi import Depends, FastAPI, Request
from fastapi.middleware import Middleware
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api import router
from ee.api import ee_router
from app.controllers.workspace import WorkspaceController
from app.controllers.user import UserController
from app.models import Dataset, Workspace, User
from app.repositories.dataset import DatasetRepository
from app.repositories.workspace import WorkspaceRepository
from app.repositories.user import UserRepository
from core.config import config
from core.database.session import session
from core.exceptions import CustomException
from core.fastapi.dependencies import Logging
from core.fastapi.middlewares import (
    AuthBackend,
    AuthenticationMiddleware,
    SQLAlchemyMiddleware,
)
from core.utils.dataframe import convert_dataframe_to_dict


def on_auth_error(request: Request, exc: Exception):
    status_code, error_code, message = 401, None, str(exc)
    if isinstance(exc, CustomException):
        status_code = int(exc.code)
        error_code = exc.error_code
        message = exc.message

    return JSONResponse(
        status_code=status_code,
        content={"error_code": error_code, "message": message},
    )


def init_routers(app_: FastAPI) -> None:
    app_.include_router(router)
    app_.include_router(ee_router)


def init_listeners(app_: FastAPI) -> None:
    @app_.exception_handler(CustomException)
    async def custom_exception_handler(request: Request, exc: CustomException):
        return JSONResponse(
            status_code=exc.code,
            content={"error_code": exc.error_code, "message": exc.message},
        )


def make_middleware() -> List[Middleware]:
    middleware = [
        Middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        ),
        Middleware(
            AuthenticationMiddleware,
            backend=AuthBackend(),
            on_error=on_auth_error,
        ),
        Middleware(SQLAlchemyMiddleware),
    ]
    return middleware


async def init_user():
    user_repository = UserRepository(User, db_session=session)
    space_repository = WorkspaceRepository(Workspace, db_session=session)
    controller = UserController(user_repository, space_repository)
    await controller.create_default_user()
    users = await controller.get_all(limit=1, join_={"memberships"})
    return users[0]


def generate_csv_export_url(spreadsheet_id: str, gid: str) -> str:
    """
    Generates a CSV export URL for a specific sheet in a Google Spreadsheet.

    :param spreadsheet_id: The ID of the Google Spreadsheet.
    :param gid: The gid of the sheet.
    :return: A CSV export URL.
    """
    return f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/export?format=csv&gid={gid}"


def read_google_sheet(sheet_url: str, sheet_name: str):
    """
    Reads data from a specific sheet in a Google Spreadsheet.

    :param sheet_url: The CSV export URL of the sheet.
    :param sheet_name: The name of the sheet (for identification).
    :return: A dictionary with the DataFrame head, file name, and file path.
    """
    try:
        df = pd.read_csv(sheet_url)
        # Debugging: Print the first few rows of the data
        print(f"Data from sheet '{sheet_name}':\n{df.head()}")
        return {
            "head": convert_dataframe_to_dict(df.head()),
            "file_name": f"{sheet_name}_Data",
            "file_path": sheet_url,
        }
    except Exception as e:
        raise ValueError(f"Failed to read the sheet '{sheet_name}': {e}")


async def init_database():
    user = await init_user()
    spreadsheet_id = "1YoVfTgZNDk6d8OvKRWFnkIyqQFp6vMShBNjApCrzdK4"  # Replace with your actual spreadsheet ID

    # Map sheet names to their respective gid values
    sheets = {
        "campaign_data": "989190315",
        "maintenance_data": "1469795776",
        "room_management_data": "931441619",
    }

    sheet_data = []
    for sheet_name, gid in sheets.items():
        sheet_url = generate_csv_export_url(spreadsheet_id, gid)
        data = read_google_sheet(sheet_url, sheet_name)
        sheet_data.append(data)

    space_repository = WorkspaceRepository(Workspace, db_session=session)
    space = await space_repository.create_default_space_in_org(
        organization_id=user.memberships[0].organization_id, user_id=user.id
    )
    dataset_repository = DatasetRepository(Dataset, db_session=session)
    space_controller = WorkspaceController(
        space_repository=space_repository, dataset_repository=dataset_repository
    )

    await space_controller.reset_space_datasets(space.id)
    await space_controller.add_csv_datasets(sheet_data, user, space.id)


def create_app() -> FastAPI:
    app_ = FastAPI(
        title="PandasAI Server",
        description="PandasAI Backend server",
        version="1.0.0",
        docs_url=None if config.ENVIRONMENT == "production" else "/docs",
        redoc_url=None if config.ENVIRONMENT == "production" else "/redoc",
        dependencies=[Depends(Logging)],
        middleware=make_middleware(),
    )
    init_routers(app_=app_)
    init_listeners(app_=app_)

    @app_.on_event("startup")
    async def on_startup():
        await init_database()

    return app_


app = create_app()