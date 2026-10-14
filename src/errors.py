class MediaError(Exception):
    def __init__(self, code: str, message: str, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def not_found() -> MediaError:
    return MediaError("IMAGE_NOT_FOUND", "Image not found", 404)
