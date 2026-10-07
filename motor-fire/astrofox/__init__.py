from .addons import DefaultAddons
from .async_api import AsyncCamoufox, AsyncNewBrowser, AsyncNewContext
from .sync_api import Camoufox, NewBrowser, NewContext
from .utils import launch_options

# Aliases oficiais Star Driver / AstroFox
AstroFox = Camoufox
AsyncAstroFox = AsyncCamoufox
StarDriver = Camoufox
AsyncStarDriver = AsyncCamoufox

__all__ = [
    "AstroFox",
    "AsyncAstroFox",
    "StarDriver",
    "AsyncStarDriver",
    "Camoufox",
    "NewBrowser",
    "NewContext",
    "AsyncCamoufox",
    "AsyncNewBrowser",
    "AsyncNewContext",
    "DefaultAddons",
    "launch_options",
]
