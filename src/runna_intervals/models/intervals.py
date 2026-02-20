"""Intervals.icu API event models.

Reference: https://intervals.icu/api/v1/docs/swagger-ui/index.html
Forum guide: https://forum.intervals.icu/t/api-access-to-intervals-icu/609
"""

from pydantic import BaseModel, Field


class WorkoutStepPace(BaseModel):
    """Pace target for a workout step, expressed as % of threshold pace."""

    start: int = Field(description="Minimum pace as % of threshold")
    end: int = Field(description="Maximum pace as % of threshold")
    units: str = Field(default="%pace")


class WorkoutStep(BaseModel):
    """A single step in the Intervals.icu workout_doc structure."""

    text: str | None = None
    pace: WorkoutStepPace | None = None
    distance: int | None = Field(default=None, description="Distance in metres")
    duration: int | None = Field(default=None, description="Duration in seconds")
    reps: int | None = Field(
        default=None, description="Number of repetitions (for interval blocks)"
    )
    steps: list["WorkoutStep"] | None = Field(
        default=None, description="Sub-steps for interval blocks"
    )


class WorkoutDoc(BaseModel):
    """The structured workout definition used by Intervals.icu."""

    steps: list[WorkoutStep]


class IntervalsEvent(BaseModel):
    """A planned workout event for the Intervals.icu calendar.

    Upload via: POST /api/v1/athlete/{id}/events/bulk?upsert=true
    """

    category: str = Field(default="WORKOUT")
    start_date_local: str = Field(description="ISO datetime: YYYY-MM-DDT00:00:00")
    type: str = Field(default="Run")
    name: str
    description: str = Field(
        description="Workout steps in Intervals.icu markdown format. "
        "Server parses this to generate visual step blocks."
    )
    moving_time: int = Field(description="Estimated duration in seconds")
    indoor: bool = False
    notes: str | None = None
    external_id: str | None = Field(
        default=None,
        description="Optional stable ID for upsert deduplication "
        "(e.g. 'runna-2024-04-01-intervals')",
    )
    target: str | None = Field(
        default=None,
        description="Primary target metric: AUTO, POWER, HR, PACE. Defaults to athlete setting.",
    )
    workout_doc: WorkoutDoc | None = Field(
        default=None,
        description="Pre-computed step structure. Provided alongside description for caching.",
    )
