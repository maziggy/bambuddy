"""Schemas for timelapse video processing."""

from pydantic import BaseModel, Field


class TimelapseInfoResponse(BaseModel):
    """Video metadata response."""

    duration: float = Field(description="Video duration in seconds")
    width: int = Field(description="Video width in pixels")
    height: int = Field(description="Video height in pixels")
    fps: float = Field(description="Frames per second")
    codec: str = Field(description="Video codec name")
    file_size: int = Field(description="File size in bytes")
    has_audio: bool = Field(description="Whether video has audio track")


class ThumbnailResponse(BaseModel):
    """Timeline thumbnail response."""

    thumbnails: list[str] = Field(description="Base64 encoded JPEG thumbnails")
    timestamps: list[float] = Field(description="Timestamp for each thumbnail in seconds")


class TimelapseJobResponse(BaseModel):
    """A timelapse re-encode job."""

    status: str = Field(description="running, completed, failed or cancelled")
    progress: float | None = Field(default=None, description="Share done, 0.0 to 1.0; null before the first report")
    output_path: str | None = Field(default=None, description="Relative path to the output file, once completed")
    message: str | None = Field(default=None, description="Result message, once completed")
    error: str | None = Field(default=None, description="Why it failed")

    @classmethod
    def from_job(cls, job) -> "TimelapseJobResponse":
        return cls(
            status=job.status,
            progress=job.progress,
            output_path=job.output_path,
            message=job.message,
            error=job.error,
        )
