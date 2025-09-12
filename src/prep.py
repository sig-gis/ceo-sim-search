import os
import ee
import google.auth

from src.utils import efm_plot_agg, export_to_bq, postprocess_bq, vector_index, plot_to_gdf, gdf_to_fc


def generate_processed_table_names(gcp_file: str, years: list[int]) -> dict[int, str]:
    """
    Generates the final, post-processed BigQuery table names based on input file and years.

    This logic must be kept in sync with the table creation logic in prep_tables.
    The naming convention is `{base_filename}_{year}_pp`.
    """
    table_base_name = os.path.basename(gcp_file).split('.')[0]
    return {year: f"{table_base_name}_{year}_pp" for year in years}


def prep_tables(gcp_file:str,
                project:str,
                dataset:str,
                years:list[int],
                ) -> dict[int, str]:
    
    plot_gdf = plot_to_gdf(gcp_file)
    plot_fc = gdf_to_fc(plot_gdf)
    
    fc_embeddings = efm_plot_agg(plot_fc,years) # export EFM image data (n=64 bands) to each feature in collection
    
    new_table_base = f"{os.path.basename(gcp_file).split('.')[0]}"
    processed_tables = {}
    for i,yr_embed in enumerate(fc_embeddings):
        year_tag = str(years[i])
        table = export_to_bq(yr_embed, # export the featurecollection to BQ table
                            project,
                            dataset,
                            new_table_base,
                            year_tag,
                            wait=True,
                            dry_run=False)
        
        # If post-processing fails, we should not attempt to create an index.
        try:
            pp_table = postprocess_bq(project,dataset,table,wait=True) # fix the schema of the exported table to contain one 'embedding' column containing a 1x64 array

            # BigQuery does not allow creating a VECTOR index on a table with < 5k rows.
            # Perform the vector_index fn as the last part of postprocessing if the condition is met.
            row_count = len(plot_gdf)
            if row_count > 5000:
                print(f"Row count ({row_count}) > 5000. Creating Vector Index...")
                vector_index(project,dataset,pp_table,embedding_col='embedding',wait=True)

            print(f"Successfully created and processed table: {pp_table}")
            processed_tables[years[i]] = pp_table
        except Exception as e:
            print(f"Failed to post-process or index table for year {year_tag}. Reason: {e}")
            continue # Move to the next year in the loop
    return processed_tables
    
if __name__ == "__main__":
    # For local execution, ensure GCP_PROJECT, GCP_BQ_DATASET, GCP_BUCKET are set as environment variables
    # or in a .env file in the project root.
    project = os.environ.get('GCP_PROJECT')
    dataset = os.environ.get('GCP_BQ_DATASET')

    # Use google.auth.default() to get credentials. This works for local dev
    # when GOOGLE_APPLICATION_CREDENTIALS is set in the environment.
    credentials, _ = google.auth.default(
        scopes=[
            "https://www.googleapis.com/auth/cloud-platform",
            "https://www.googleapis.com/auth/earthengine",
        ]
    )
    ee.Initialize(credentials, project=project, opt_url="https://earthengine-highvolume.googleapis.com")

    ee.data.setWorkloadTag("efm-table-prep")

    prep_tables(gcp_file="gs://sim-search/ceo-100-plots.geojson",
                project=project,
                dataset=dataset,
                years=[2018,2019]
                )