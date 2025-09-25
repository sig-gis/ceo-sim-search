import logging
from fastapi import FastAPI, BackgroundTasks, HTTPException, Depends, Query
from pydantic import BaseModel, Field, ValidationError
import ee
import google.auth

from src.prep import prep_tables, generate_processed_table_names
from src.search import search_result
from src.config import get_settings, AppSettings

# --- Configuration and Initialization ---

# Use Python's logging module for better log management
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="GCP Similarity Search API",
    description="API for preparing data and running vector similarity searches.",
    version="1.0.0"
)

# --- Pydantic Models for Request Bodies ---

class PrepRequest(BaseModel):
    gcp_file: str = Field(..., example="gs://your-bucket/your-file.geojson", description="GCS path to the GeoJSON plot file.")
    years: list[int] = Field(..., example=[2020, 2021], description="List of years to process.")

class PrepResponse(BaseModel):
    message: str
    tables: dict[int, str] = Field(..., example={2020: "your-file_2020_pp", 2021: "your-file_2021_pp"}, description="A dictionary mapping each year to the name of the BigQuery table that will be created.")

class SearchResponse(BaseModel):
    target_plotid: int
    base_plotid: int
    distance: float

# --- API Endpoints ---

@app.on_event("startup")
async def startup_event():
    """Handles Earth Engine authentication on application startup."""
    try:
        # Load settings at startup to fail fast if config is missing/invalid
        settings = get_settings()
        project = settings.gcp.project
 
        # Use google.auth.default() to get credentials. This works in GHA, Cloud Run,
        # and local dev with GOOGLE_APPLICATION_CREDENTIALS set.
        credentials, _ = google.auth.default(
            scopes=[
                "https://www.googleapis.com/auth/cloud-platform",
                "https://www.googleapis.com/auth/earthengine",
            ]
        )
 
        ee.Initialize(
            credentials=credentials,
            project=project,
            opt_url="https://earthengine-highvolume.googleapis.com"
        )
        ee.data.setWorkloadTag("ceo-sim-search-api")
        logger.info("Earth Engine initialized successfully for project %s.", project)
    except ValidationError as e:
        logger.critical(
            "FATAL: Configuration validation error. Missing or invalid environment variables (e.g., GCP_PROJECT, GCP_BQ_DATASET, GCP_BUCKET). "
            "The application cannot start correctly. Pydantic error: %s", e, exc_info=True
        )
        # In a real-world scenario, you might want to exit the application
        # but for Cloud Run, letting it start and fail on requests is also an option.
    except Exception as e:
        # Log the error but allow the app to start, as the /search endpoint may still work.
        logger.error("FATAL: Could not initialize Earth Engine. The /prep endpoint will fail. Error: %s", e, exc_info=True)

@app.post("/prep", status_code=202, response_model=PrepResponse)
async def create_prep_job(
    request: PrepRequest,
    background_tasks: BackgroundTasks,
    settings: AppSettings = Depends(get_settings)
):
    """
    Accepts a data preparation job and runs it in the background.
    Returns the names of the tables that will be created.
    """
    print(f"Received prep job for {request.gcp_file} for years {request.years}")

    table_names = generate_processed_table_names(request.gcp_file, request.years)

    background_tasks.add_task(
        prep_tables,
        request.gcp_file,
        settings.gcp.project,
        settings.gcp.bq_dataset,
        request.years,
        settings.gcp.pubsub_topic_table_jobs
    )
    return {"message": "Data preparation job accepted and running in the background.", "tables": table_names}

@app.get("/search", response_model=list[SearchResponse])
async def run_search(
    uniqueid: int = Query(..., description="Unique ID of the plot to search for.", example=5),
    table: str = Query(..., description="The BigQuery table to search within.", example="my_processed_table_pp"),
    matches: int = Query(5, ge=1, le=50, description="Number of matches to return."),
    settings: AppSettings = Depends(get_settings)
):
    """
    Performs a vector similarity search on a prepared BigQuery table.
    """
    try:
        results_df = search_result(uniqueid, matches, settings.gcp.project, settings.gcp.bq_dataset, table)
        return results_df.to_dict(orient="records")
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {e}")