from app.multitenancy.task_worker import WORKER_MODULES


def test_task_worker_supervises_all_durable_worker_roles() -> None:
    assert WORKER_MODULES == (
        "app.multitenancy.research_worker",
        "app.multitenancy.runtime",
        "app.multitenancy.notification_worker",
        "app.multitenancy.scheduled_worker",
    )
