class SampleAuditMiddleware:
    """Placeholder middleware hook for scaffold users to copy and customize."""

    def before_agent(self, state):
        return state

    def after_agent(self, state):
        return state


MIDDLEWARE = [SampleAuditMiddleware()]
