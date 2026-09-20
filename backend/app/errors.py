class DomainError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class ConflictError(DomainError):
    def __init__(self, message: str = "版本冲突或终态不可变更"):
        super().__init__(message, status_code=409)
