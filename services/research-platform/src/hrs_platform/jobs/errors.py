"""Translate domain task failures exactly once at the Temporal activity boundary."""

from functools import wraps
from inspect import iscoroutinefunction

from temporalio import activity
from temporalio.exceptions import ApplicationError

from hrs_platform.domain.errors import TaskError
from hrs_platform.services.storage import storage_heartbeat


def activity_errors(operation):
    def temporal(error):
        return ApplicationError(
            error.message, *error.details, type=error.type, non_retryable=error.non_retryable
        )

    if iscoroutinefunction(operation):

        @wraps(operation)
        async def asynchronous(*args, **kwargs):
            token = storage_heartbeat.set(activity.heartbeat if activity.in_activity() else None)
            try:
                return await operation(*args, **kwargs)
            except TaskError as error:
                raise temporal(error) from error
            finally:
                storage_heartbeat.reset(token)

        return asynchronous

    @wraps(operation)
    def synchronous(*args, **kwargs):
        token = storage_heartbeat.set(activity.heartbeat if activity.in_activity() else None)
        try:
            return operation(*args, **kwargs)
        except TaskError as error:
            raise temporal(error) from error
        finally:
            storage_heartbeat.reset(token)

    return synchronous
