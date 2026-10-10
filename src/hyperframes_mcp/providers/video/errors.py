"""Errors of the image-to-video (I2V) adapter and its per-video clip cap."""


class I2VError(Exception):
    """Base class of every I2V failure."""


class MissingKeyError(I2VError):
    """The provider's key is not set; nothing was sent."""


class UnknownModelError(I2VError):
    """The model is not in the priced table, so it is refused rather than guessed."""


class ClipLengthError(I2VError):
    """The requested clip length is not one the model can make (too long, too short, odd)."""


class StillError(I2VError):
    """The still image to animate is missing or too big to send."""


class ClipCapError(I2VError):
    """The per-video clip cap refused a generation; nothing was sent to the provider."""


class ClipCountExceeded(ClipCapError):
    """The video already has its maximum number of distinct clips."""


class ClipAttemptsExceeded(ClipCapError):
    """One clip has used all its attempts (failed attempts count)."""


class ClipSecondsExceeded(ClipCapError):
    """The clips of the video would add up to more than the allowed seconds."""


class ClipSpendExceeded(ClipCapError):
    """The attempts so far plus this one would pass the per-video spend ceiling."""


class ContentPolicyError(I2VError):
    """The provider rejected the still or the prompt on content-policy grounds."""


class ClipTimeoutError(I2VError):
    """The job did not finish in time."""


class ClipGenerationError(I2VError):
    """The provider failed the job or returned no video."""


class ClipDownloadError(I2VError):
    """The finished video could not be downloaded within the limits."""
