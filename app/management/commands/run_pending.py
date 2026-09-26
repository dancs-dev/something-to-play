import time
from django.core.management.base import BaseCommand
from app.worker import process_pending, worker_lock


class Command(BaseCommand):
    help = 'Process queued Steam syncs and AI runs serially. Default: one pass.'

    def add_arguments(self, parser):
        parser.add_argument('--watch', action='store_true', help='Keep polling every five seconds')

    def handle(self, *args, **options):
        with worker_lock():
            try:
                while True:
                    count = process_pending()
                    self.stdout.write(f'Processed {count} tasks.')
                    if not options['watch']:
                        return
                    time.sleep(5)
            except KeyboardInterrupt:
                self.stdout.write('Worker stopped.')
