"""Exception diagnostics for boundaries that must not log provider or user payloads."""
from __future__ import annotations

from traceback import walk_tb


def exception_type_info(error: BaseException) -> tuple[type[Exception], Exception, None]:
    """Keep frame locations and the error type, excluding messages, source lines and locals.

    Standard traceback formatting includes the raising source line and chained exception
    messages. Either can carry credentials; a fresh exception with no traceback avoids
    both while retaining the original call sites as diagnostic metadata.
    """
    frames = [f'  File "{frame.f_code.co_filename}", line {line}, in {frame.f_code.co_name}'
              for frame, line in walk_tb(error.__traceback__)]
    diagnostic = '\n'.join([type(error).__name__, *frames])
    return Exception, Exception(diagnostic), None
