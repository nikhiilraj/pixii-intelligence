from fastapi import APIRouter
from pydantic import BaseModel

from app.config import settings

router = APIRouter(prefix="/publishing", tags=["publishing"])


class PublishingTarget(BaseModel):
    """Where a publication command would go, and whether it may go at all.

    Its own route rather than a few more keys on `/health`, because `/health` states the
    rule it would break: presence flags and scalar ceilings, never a value. An account
    identifier is a value, and the Inbox footer renders that response row-per-key as a
    health light — a string in there arrives as a junk boolean.
    """

    # Whether `PUBLISHING_ENABLED` is on. Without this the review screen has to fire a
    # command and read the 403 to discover the capability is off, which means the only way
    # to find out is to attempt the thing.
    enabled: bool

    platform: str

    # The Zernio account id `publishing.push_draft` actually sends, or None when unset.
    #
    # None rather than "", so the client renders `—` and not a blank that reads as a name
    # nobody typed. An unconfigured destination is a real state: pushes fail, and a review
    # screen claiming a destination it does not have is worse than one admitting it.
    #
    # **Not `settings.voice_account`.** That names whose writing templates are allowed to
    # describe — an extraction setting — and it happens to be a human-readable name, which
    # is exactly what makes the substitution tempting and wrong. The two can differ and
    # nothing would report it.
    #
    # An opaque id and not a display name, because a display name is not something this
    # application knows. Zernio holds it, behind an accounts/profiles call this client does
    # not speak. ponytail: add `ZernioClient.accounts()` and resolve the label the day a
    # reviewer says the id is not enough to recognise the destination by.
    account_id: str | None

    # Not a credential, which is why it may be sent at all: `config.Settings` gives every
    # secret `repr=False` and this field deliberately has none. The bearer key is the
    # credential; this is the address.
    @staticmethod
    def current() -> "PublishingTarget":
        return PublishingTarget(
            enabled=settings.publishing_enabled,
            platform="linkedin",
            account_id=settings.getlate_linkedin_id or None,
        )


@router.get("")
def publishing_target() -> PublishingTarget:
    """The destination a schedule or publish command would reach, for the confirmation step.

    ADR 0002 makes the review screen responsible for showing what is about to happen before
    it happens. "Publish now" over an unnamed destination is the one-click publish that ADR
    exists to prevent.
    """
    return PublishingTarget.current()
