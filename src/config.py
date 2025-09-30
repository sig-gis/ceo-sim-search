from pydantic import Field, BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache

class GcpSettings(BaseModel):
    """A simple Pydantic model to hold GCP settings, not for loading."""
    project: str
    bq_dataset: str
    bucket: str
    pubsub_topic_table_jobs: str

class AppSettings(BaseSettings):
    """The main settings object that loads all variables from the environment."""
    # Define fields at the top level, using aliases to map to env vars.
    # This is the most robust way to load them.
    gcp_project: str = Field(..., alias='GCP_PROJECT')
    gcp_bq_dataset: str = Field(..., alias='GCP_BQ_DATASET')
    gcp_bucket: str = Field(..., alias='GCP_BUCKET')
    gcp_pubsub_topic_table_jobs: str = Field(..., alias='GCP_PUBSUB_TOPIC_TABLE_JOBS')

    model_config = SettingsConfigDict(
        env_file='.env', # Load environment variables from .env file for local development
        env_file_encoding='utf-8',
        extra='ignore' # Ignore extra fields in .env or environment
    )

    @property
    def gcp(self) -> GcpSettings:
        """Provides a nested `gcp` object for convenient access in the app."""
        return GcpSettings(project=self.gcp_project, 
                           bq_dataset=self.gcp_bq_dataset, 
                           bucket=self.gcp_bucket,
                           pubsub_topic_table_jobs=self.gcp_pubsub_topic_table_jobs)

@lru_cache()
def get_settings() -> AppSettings:
    """Loads and validates application settings from environment variables."""
    return AppSettings()