from uuid import UUID

from pydantic import BaseModel


class JobCreated(BaseModel):
    job_id: UUID
