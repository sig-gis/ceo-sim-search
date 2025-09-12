import os
import ee
import geemap
import geopandas as gpd
import pandas as pd
import io
from google.cloud.bigquery import Client
import time
from google.cloud import storage

def poll_submitted_task(task,sleeper:int|float):
    """
    polls for the status of one started task, completes when task status is 'COMPLETED'
    args:
        task : task status dictionary returned by ee.batch.Task.status() method
        sleeper (int): minutes to sleep between status checks
    returns:
        None
    """
    # handles instances of ee.batch.Task, 
    # NOTE: task needs to be started in order to retrieve needed status info, 
    # so use this function after doing `task.start()`
    if isinstance(task,ee.batch.Task):
        t_id = task.status()['id']
        status = task.status()['state']
        
        if status == 'UNSUBMITTED':
            raise RuntimeError(f"run .start() method on task before polling. {t_id}:{status}")
        
        print(f"polling for task: {t_id}")
        while status != 'COMPLETED':
            if status in ['READY','RUNNING']:
                print(f"{t_id}:{status} [sleeping {sleeper} mins] ")
                time.sleep(60*sleeper)
                t_id = task.status()['id']
                status = task.status()['state'] 
            elif status in ['FAILED','CANCELLED','CANCEL_REQUESTED']:
                raise RuntimeError(f"problematic task status code - {t_id}:{status}")
        print(f"{t_id}:{status}")
    else:
        raise TypeError(f"{task} is not instance of <ee.batch.Task>. {type(task)}")
    return

def ee_task_list_complete(task_description: str, items: int) -> list[bool]:
    """
    Checks if the most recent Earth Engine tasks with a specific description have completed.

    Args:
        task_description (str): The description of the tasks to check.
        items (int): The number of recent tasks with that description to check.

    Returns:
        A list of booleans indicating the completion status (e.g., [True, True, False]).
    """
    all_tasks = ee.batch.Task.list()
    # Filter tasks by the exact description and get the most recent 'items'
    relevant_tasks = [t for t in all_tasks if t.config['description'] == task_description][:items]
    # Check if each task's state is COMPLETED
    return [t.state == ee.batch.Task.State.COMPLETED for t in relevant_tasks]

def ee_task_list_poller(task_description: str, items: int, sleep_minutes: int):
    """
    Polls for the completion of Earth Engine tasks with a specific description.

    Args:
        task_description (str): The description of the tasks to poll.
        items (int): The number of recent tasks to poll for.
        sleep_minutes (int): The number of minutes to wait between checks.
    """
    test_complete = ee_task_list_complete(task_description, items)
    while not all(test_complete):
        print(
            f"Waiting for one or more '{task_description}' tasks to complete: "
            f"{test_complete}. Sleeping {sleep_minutes} mins..."
        )
        time.sleep(60 * sleep_minutes)
        test_complete = ee_task_list_complete(task_description, items)

    print(f"All '{task_description}' tasks are complete.")
    return None

def plot_to_gdf(file:str):
        if "gs://" in file:
            print('reading from gcs in-memory..')
            client = storage.Client()
            bucket_name = file.split('/')[2]
            blob_name = '/'.join(file.split('/')[3:])
            bucket = client.get_bucket(bucket_name)
            blob = bucket.blob(blob_name)
            
            # Read the file directly into an in-memory bytes buffer
            # This avoids writing temporary files to disk.
            in_memory_file = io.BytesIO(blob.download_as_bytes())
            plots = gpd.read_file(in_memory_file)
        else:
            plots = gpd.read_file(file)
        
        # --- Schema and dtype enforcement ---
        required_cols = {'plotid', 'geometry'}
        if not required_cols.issubset(plots.columns):
            missing = required_cols - set(plots.columns)
            raise KeyError(f"Input file is missing required columns: {sorted(list(missing))}")
        
        # Reduce to a consistent schema and order, and enforce types
        schema_order = ['plotid', 'geometry']
        plots = plots[schema_order]
        plots['plotid'] = plots['plotid'].astype(float).astype(int)
        
        return plots 
        
def gdf_to_fc(gdf:gpd.GeoDataFrame):
    fc = geemap.gdf_to_ee(gdf)
    return fc

def efm_plot_agg(fc:ee.FeatureCollection,
                 years:list[int],
                 ) -> list[ee.FeatureCollection]:
        """
        Computes band-wise means of Google EFM imageCollection to featureCollection regions for each year provided in `years`.

        Args:
            fc (ee.FeatureCollection): Earth Engine FeatureCollection to aggregate EFM to
            years (list[int]): list of years of EFM to aggregate to

        Returns:
            list[ee.FeatureCollection]: one FeatureCollection result per year in `years`
        """
        efm = ee.ImageCollection("GOOGLE/SATELLITE_EMBEDDING/V1/ANNUAL")
        fcs=[]
        if isinstance(years,list):
                for yr in years:
                        efm_yr = (efm
                        .filter(ee.Filter.calendarRange(yr,yr,'year'))
                        .mosaic())
                        fc_reduced = efm_yr.reduceRegions(collection=fc,
                                                        reducer=ee.Reducer.mean(),
                                                        scale=10,
                                                        crs='EPSG:4326',
                                                        crsTransform=None,
                                                        maxPixelsPerRegion=1e12,
                                                        tileScale=16)
                        fcs.append(fc_reduced)
        else:
                raise ValueError(f"years expects a list. provided {type(years)}")
        return fcs

def export_to_bq(fc:ee.FeatureCollection,
                 project:str="collect-earth-online",
                 dataset:str="sim_search_test",
                 table:str='my_table',
                 yr_tag:str='year',
                 dry_run:bool=False,
                 wait:bool=False) -> str: 
        """
        Export an ee.FeatureCollection to a BigQuery table.

        Args:
            fc (ee.FeatureCollection): The input FeatureCollection to export.
            project (str): cloud project that resources are contained in
            dataset (str): BQ dataset to export tables into
            table (str): table name
            dry_run (bool): print table name to export and exit
            wait (bool): whether to poll for the submitted EE task to complete before returning
        
        Returns:
            str:fully qualified BQ table that was exported (e.g. my-project.my_dataset.my_table)
        """

        if not all((isinstance(yr_tag,str), len(yr_tag)==4)):
             raise ValueError(f" yr_tag expects a %04 formatted string (e.g. '2023'). provided {yr_tag}")
        # Removed random id for deterministic naming, allowing front-end to know table names in advance.
        tb = f'{project}.{dataset}.{table}_{yr_tag}'
        if len(tb) > 100: # simpler if desc and out table are same but ee.export desc has 100 char limit; 
                base_char_len = len(tb)-len(table)
                leftovers = 100-base_char_len
                tb = f'{project}.{dataset}.{table[:leftovers]}_{yr_tag}'
                
        if dry_run:
                print(f"Would Export {tb}")

        else:
                print(f"Exporting {tb}")
                taskBQ = ee.batch.Export.table.toBigQuery(
                        collection=fc,
                        description=tb,
                        table=tb,
                        overwrite=True # would probably want to overwrite as exporting to exact same table would only happen on error
                )
                     
                taskBQ.start()
                if wait:
                    poll_submitted_task(taskBQ,0.25)
        return tb.split(".")[-1]

def postprocess_bq(project:str="collect-earth-online",
                   dataset:str="sim_search_test",
                   table:str='my-table',
                   wait:bool=True) -> str:
    """Postprocesses an intermediary GEE BQ export table.

    This function takes a table exported from Earth Engine, aggregates all EFM
    band columns into a single 'embedding' array, drops the original table,
    and returns the name of the new, processed table.
    
    Args:
        project (str): The cloud project containing your BigQuery resources.
        dataset (str): The BigQuery dataset where your table resides.
        table (str): The name of the source table to process.
        wait (bool): Whether to wait for the BQ processing job to complete.

    Returns:
        str: The name of the new, processed table (e.g., 'my-table_processed').
    """
    client = Client(project=project)
    source_table_ref = f"{project}.{dataset}.{table}"
    processed_table_name = f"{table}_pp"
    processed_table_ref = f"{project}.{dataset}.{processed_table_name}"
    print(source_table_ref)
    print(processed_table_ref)
    # Note: Earth Engine exports tables with clustering on the 'geo' column.
    # We preserve this clustering in the new table for geospatial performance.
    # We must create a new table because BigQuery doesn't allow a query to
    # read from and replace the same table in one operation.
    query = f"""
        CREATE OR REPLACE TABLE `{processed_table_ref}`
        CLUSTER BY geo
        AS
        SELECT
            CAST(plotid AS INT64) AS plotid,
            geo,
            ARRAY[A00, A01, A02, A03, A04, A05, A06, A07, A08, A09, A10, A11, A12, A13, A14, A15, A16, A17, A18, A19, A20, A21, A22, A23, A24, A25, A26, A27, A28, A29, A30, A31, A32, A33, A34, A35, A36, A37, A38, A39, A40, A41, A42, A43, A44, A45, A46, A47, A48, A49, A50, A51, A52, A53, A54, A55, A56, A57, A58, A59, A60, A61, A62, A63] AS embedding
        FROM
            `{source_table_ref}`
        WHERE
            A00 IS NOT NULL
        """
    print(f"Creating processed table: {processed_table_name}")
    job = client.query(query)
    if wait:
        job.result()  # Wait for the job to complete

    # Drop the original source table now that the processed one is created
    print(f"Dropping original source table: {table}")
    client.delete_table(source_table_ref, not_found_ok=True) # have to exclude `` since not inside a BQ sql query

    return processed_table_name

def vector_index(project:str='collect-earth-online',
                 dataset:str='sim_search_test',
                 table:str='my-table',
                 embedding_col:str='embedding',
                 wait:bool=False) -> None:
    """create vector index on pre-existing table containing an embeddings column.
    
    Args:
        project (str): cloud project that your BQ resources are contained in
        dataset (str): BQ dataset containing table
        table (str): fully qualified table
        embedding_col (str): name of column containing embedding array
        wait (bool): whether to wait for BQ processing job to complete before returning

    Returns:
        None
    """
    in_table = f"`{dataset}.{table}`"
    
    print(f'indexing {in_table} for vector search')
    
    query = f"""
CREATE VECTOR INDEX my_index ON {in_table}({embedding_col})
OPTIONS(distance_type='COSINE', index_type='IVF', ivf_options='{{"num_lists": 1000}}');
"""
    print(query)
    # Run the query to create the index
    client = Client(project=project)
    job = client.query(query)
    if wait:
        job.result()  # Wait for the job to complete
    return None

def table_exists(project:str,
                 dataset:str,
                 table:str) -> bool:
    """Check if the result_table exists.
    
    Args:
        project (str): cloud project that your BQ resources are contained in
        dataset (str): BQ dataset your table is contained in
        table (str): BQ table name

    Returns:
        bool
    """
    try:
        client = Client(project=project)
        client.get_table(f"{project}.{dataset}.{table}")
        print(f"Table {table} exists.")
        return True
    except Exception as e:
        print(f"Table {table} does not exist. Error: {e}")
        return False
    
def vector_search(plotid:int,
                  n_matches:int,
                  project:str,
                  dataset:str,
                  table:str,
                  ) -> pd.DataFrame:
    """Performs a vector search and returns the result as a pandas.DataFrame.
    
    Args:
        plotid (int): unique plotid value of the search's target record
        n_matches (int): number of matches to return
        project (str): cloud project your resources are contained in
        dataset (str): BQ dataset your table is in_
        

    Returns:
        (Pandas DataFrame): https://cloud.google.com/python/docs/reference/bigquery/latest/google.cloud.bigquery.job
    
    """
    
    query = f"""
SELECT
  {plotid} AS target_plotid,
  base.plotid AS base_plotid,
  distance
FROM
    VECTOR_SEARCH(
        TABLE `{dataset}.{table}`,
        'embedding',
        (SELECT * FROM `{dataset}.{table}` WHERE plotid = {plotid} LIMIT 1),
        top_k => {n_matches + 1},
        distance_type => 'COSINE',
        options => '{{"fraction_lists_to_search": 0.005}}'
    )
ORDER BY distance
LIMIT {n_matches}
OFFSET 1 -- Offset 1 to exclude the query plot itself from the results
"""

    # Run the query and return the job result as pd.DF
    client = Client(project=project)
    job = client.query(query)
    return job.to_dataframe(max_results=n_matches)