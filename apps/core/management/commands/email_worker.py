"""Deliver queued emails. Runs forever in the `worker` container; use --once for cron/tests."""
import signal
import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections

from apps.core import mail


class Command(BaseCommand):
    help = "Send queued outgoing emails with retries."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Process the queue once and exit")
        parser.add_argument("--interval", type=int, default=5, help="Seconds between queue checks")

    def handle(self, *args, **opts):
        stop = {"flag": False}
        signal.signal(signal.SIGTERM, lambda *a: stop.update(flag=True))
        last_cleanup = 0
        self.stdout.write("Email worker started.")
        while True:
            if not opts["once"]:
                close_old_connections()  # long-running loop: drop stale DB connections
            mail.beat()
            sent, failed = mail.deliver_pending()
            if sent or failed:
                self.stdout.write(f"sent={sent} failed={failed}")
            if time.time() - last_cleanup > 86400:
                removed = mail.cleanup()
                if removed:
                    self.stdout.write(f"cleaned up {removed} old sent emails")
                last_cleanup = time.time()
            if opts["once"] or stop["flag"]:
                break
            time.sleep(opts["interval"])
