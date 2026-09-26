from django.core.management.base import BaseCommand, CommandError
from app.models import SteamAccount
from app.steam import sync_account
from app.worker import worker_lock


class Command(BaseCommand):
    help = 'Import linked Steam libraries serially; no API calls run in web requests.'

    def add_arguments(self, parser):
        parser.add_argument('--user', type=int, help='Local user ID; omit for all linked accounts')
        parser.add_argument('--force', action='store_true', help='Bypass response cache')

    def handle(self, *args, **options):
        accounts = SteamAccount.objects.select_related('user').all()
        if options['user']:
            accounts = accounts.filter(user_id=options['user'])
        failed = False
        with worker_lock():
            for account in accounts:
                ok = sync_account(account, force=options['force'])
                failed |= not ok
                account.refresh_from_db()
                self.stdout.write(f'User {account.user_id}: {account.status} {account.error}')
        if failed:
            raise CommandError('Some imports failed; see sync status. Previous/manual data was preserved.')
