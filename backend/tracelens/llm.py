MODEL_HTTP_HINTS = {
    400: "请检查模型支持的请求参数和上下文长度。",
    401: "模型服务认证失败，请检查 API Key 或自定义认证头。",
    403: "模型服务拒绝访问，请检查模型权限和网关访问规则。",
    404: "模型服务未找到请求路径或模型，请核对接口地址和模型标识。",
    413: "请求超过服务端大小限制，请检查轨迹长度和网关限制。",
    422: "模型服务无法处理请求，请检查模型兼容性和参数。",
    429: "模型服务已限流或额度不足，请检查账户状态。",
}


class ModelError(RuntimeError):
    def __init__(self, message, *, issues=None, usage=None, validation_events=None):
        super().__init__(message)
        self.issues = issues or []
        self.usage = usage
        self.validation_events = validation_events

