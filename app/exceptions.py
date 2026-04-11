class AppError(Exception):
    """Base application exception."""


class ConfigError(AppError):
    """Raised when configuration is invalid."""


class ValidationError(AppError):
    """Raised when imported data is invalid."""


class TelegramOperationError(AppError):
    """Raised when a Telegram action fails."""
