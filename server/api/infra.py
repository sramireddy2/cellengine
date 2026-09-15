"""Redis + job queue access behind one seam.

Production: django-rq queue and its Redis connection.
Tests:      one fake in-memory Redis, jobs run synchronously in-process.

Everything else in the app calls get_redis() / enqueue() and never imports rq
directly, so the test mode is a swap here, not a sprinkle of mocks.
"""
from django.conf import settings

_fake = None


def get_redis():
    global _fake
    if settings.TESTING:
        if _fake is None:
            import fakeredis
            _fake = fakeredis.FakeStrictRedis()
        return _fake
    import django_rq
    return django_rq.get_connection("default")


def enqueue(func, *args, **kwargs):
    if settings.TESTING:
        # Mirror RQ: a failing job is recorded by the job itself, never raised
        # into the HTTP request that enqueued it.
        try:
            return func(*args, **kwargs)
        except Exception as e:                       # noqa: BLE001
            import logging
            logging.getLogger(__name__).warning("sync job %s failed: %s", func.__name__, e)
            return None
    import django_rq
    return django_rq.get_queue("default").enqueue(func, *args, **kwargs)
