"""Errors shared by every provider connector."""


class TokenRevokedError(Exception):
    """The user or admin revoked access; the connection must be reconnected."""
