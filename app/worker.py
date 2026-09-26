"""One process owns external imports/enhancement; network never holds a DB lock."""
import fcntl
from contextlib import contextmanager
from datetime import timedelta
from django.conf import settings
from django.core.management.base import CommandError
from django.db.models import Q
from django.utils import timezone
from .models import RecommendationRun, SteamAccount


@contextmanager
def worker_lock():
    settings.PRIVATE_CACHE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (settings.PRIVATE_CACHE_DIR / 'worker.lock').open('a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise CommandError('Another import/worker command is running.') from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def process_pending():
    from .steam import sync_account
    from .llm import enhance_run
    count = 0
    stale = timezone.now() - timedelta(minutes=5)
    for account in SteamAccount.objects.filter(Q(sync_requested=True) | Q(status='syncing', last_attempt_at__lt=stale)).select_related('user'):
        sync_account(account)
        count += 1
    # ponytail: one serial worker; move to a proper queue only after SQLite migration and measured demand.
    for run in RecommendationRun.objects.filter(Q(status='pending') | Q(status='processing', lease_until__lt=timezone.now())).select_related('user').order_by('created_at'):
        run.status = 'processing'
        run.lease_until = timezone.now() + timedelta(minutes=5)
        run.save(update_fields=['status', 'lease_until'])
        enhance_run(run)
        count += 1
    return count
