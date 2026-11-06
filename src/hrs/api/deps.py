"""FastAPI dependencies resolved from this application's owned resources."""

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.engine import Engine

from hrs.config import Settings
from hrs.library.cards import Cards
from hrs.library.catalog import Library
from hrs.library.exports import Exports
from hrs.library.review import Review
from hrs.library.runs.outputs import Outputs
from hrs.search.search import Search


def get_engine(request: Request) -> Engine:
    return request.app.state.engine


EngineDep = Annotated[Engine, Depends(get_engine)]


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


SettingsDep = Annotated[Settings, Depends(get_settings)]


def get_review(request: Request) -> Review:
    return request.app.state.review


ReviewDep = Annotated[Review, Depends(get_review)]


def get_library(request: Request) -> Library:
    return request.app.state.library


LibraryDep = Annotated[Library, Depends(get_library)]


def get_outputs(request: Request) -> Outputs:
    return request.app.state.outputs


OutputsDep = Annotated[Outputs, Depends(get_outputs)]


def get_cards(request: Request) -> Cards:
    return request.app.state.cards


CardsDep = Annotated[Cards, Depends(get_cards)]


def get_exports(request: Request) -> Exports:
    return request.app.state.exports


ExportsDep = Annotated[Exports, Depends(get_exports)]


def get_search(settings: SettingsDep, engine: EngineDep) -> Search:
    return Search(settings, engine)


SearchDep = Annotated[Search, Depends(get_search)]
