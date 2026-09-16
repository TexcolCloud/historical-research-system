"""Wait for only the explicitly isolated platform CI object store."""
import time

from hrs_platform.services.storage import objects_for
from hrs_platform.core.config import Settings


def main():
    settings = Settings.load()
    if settings.s3_endpoint != 'http://127.0.0.1:58339' or settings.s3_bucket != 'platform-ci':
        raise RuntimeError('This probe requires the dedicated platform CI S3 endpoint and bucket.')
    storage = objects_for(settings)
    for attempt in range(45):
        try:
            storage.client.head_bucket(Bucket=settings.s3_bucket)
            print('Isolated CI object store is ready.')
            return
        except Exception:
            if attempt == 44:
                raise RuntimeError('CI object store did not become ready.') from None
            time.sleep(2)


if __name__ == '__main__':
    main()
