"""This module contains custom exceptions for the pytest plugin."""


class PytestNetworkError(Exception):
    """Class representing exceptions raised from the pytest plugin code."""

    def __init__(self, message: str) -> None:
        """Instantiate an object of this class.

        :param message: The exception message.
        """
        super().__init__(message)
