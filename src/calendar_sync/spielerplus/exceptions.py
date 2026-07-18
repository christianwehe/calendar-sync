class SpielerPlusError(Exception):
    """Base class for all SpielerPlus client errors."""


class AuthenticationError(SpielerPlusError):
    """Raised when login fails or a session is not authenticated."""


class ParseError(SpielerPlusError):
    """Raised when the SpielerPlus HTML does not have the expected shape.

    SpielerPlus exposes no public API; this client scrapes server-rendered
    HTML. A ParseError almost always means SpielerPlus changed markup and
    the CSS selectors in ``parser.py`` need updating.
    """
