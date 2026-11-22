class ModelError(RuntimeError):
    def __init__(self, message, *, issues=None, usage=None, validation_events=None):
        super().__init__(message)
        self.issues = issues or []
        self.usage = usage
        self.validation_events = validation_events

