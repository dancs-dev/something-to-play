from django.contrib.auth.models import User

from app.models import RecommendationRun
from app.recommendations import create_pending_run, run_recommendation


def create_run(user: User, context: str = "") -> RecommendationRun:
    """Run the provider synchronously and return the finished run.

    Tests use this to exercise the provider path directly instead of enqueuing
    a worker task.
    """
    run = create_pending_run(user, context)
    run_recommendation(run.pk)
    run.refresh_from_db()
    return run
