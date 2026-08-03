from app.domain.facts import FactRegistry
from app.evaluation.facts_agent import AGENT_FACTS
from app.evaluation.facts_queue import QUEUE_FACTS


def build_registry() -> FactRegistry:
    """Eager, explicit registration -- no decorator-based auto-registration
    (R1). The explicit imports above guarantee both fact modules have run
    their module-level FactSpec construction before this function can even
    be called. A decorator-based registry ("@register_fact") would instead
    depend on import order: if domain/rules.py's validation ran before
    something happened to import evaluation/facts_queue.py, the registry
    would silently look empty and a genuinely valid rule would be rejected
    as referencing an "unknown fact" -- a bug whose reproduction depends on
    which test ran first. Building the registry explicitly, and injecting it
    via ValidationInfo.context (domain/rules.py, next module) rather than a
    module-level singleton domain/ imports, makes that failure mode
    impossible by construction rather than by convention."""
    return FactRegistry(QUEUE_FACTS + AGENT_FACTS)
