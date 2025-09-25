from unittest.mock import patch, MagicMock
import pandas as pd

from src.prep import prep_tables


@patch('src.prep.publish_job_status')
@patch('src.prep.vector_index')
@patch('src.prep.postprocess_bq')
@patch('src.prep.export_to_bq')
@patch('src.prep.efm_plot_agg')
@patch('src.prep.gdf_to_fc')
@patch('src.prep.plot_to_gdf')
def test_prep_tables_small_dataset_no_vector_index(
    mock_plot_to_gdf,
    mock_gdf_to_fc,
    mock_efm_plot_agg,
    mock_export_to_bq,
    mock_postprocess_bq,
    mock_vector_index,
    mock_publish_job_status
):
    """
    Tests prep_tables with a small dataset (< 5000 rows)
    and verifies that vector_index is NOT called.
    """
    # Arrange: Set up test data and mock return values
    gcp_file = "gs://fake-bucket/fake-file.geojson"
    project = "test-project"
    dataset = "test-dataset"
    topic_id = "test_topic"
    years = [2020]
    project_id = project # for clarity in assertion
    table_base_name = "fake-file"
    exported_table_name = f"{table_base_name}_{years[0]}_random123"

    # Mock the return values of the dependent functions
    mock_plot_to_gdf.return_value = pd.DataFrame({'plotid': range(100)})  # < 5000 rows
    mock_gdf_to_fc.return_value = MagicMock(name="FeatureCollection")
    mock_efm_plot_agg.return_value = [MagicMock(name="FC_embedding_2020")]
    # The postprocess mock needs to return a value for the success message
    mock_export_to_bq.return_value = exported_table_name

    # Act: Call the function under test
    prep_tables(gcp_file, project, dataset, years, topic_id)

    # Assert: Verify that the mocked functions were called correctly
    mock_plot_to_gdf.assert_called_once_with(gcp_file)
    mock_gdf_to_fc.assert_called_once_with(mock_plot_to_gdf.return_value)
    mock_efm_plot_agg.assert_called_once_with(mock_gdf_to_fc.return_value, years)

    mock_export_to_bq.assert_called_once_with(
        mock_efm_plot_agg.return_value[0],
        project, dataset, table_base_name, str(years[0]),
        wait=True, dry_run=False
    )
    mock_postprocess_bq.assert_called_once_with(
        project, dataset, exported_table_name, wait=True
    )
    # Key assertion for this test: vector_index should not be called for small tables
    mock_vector_index.assert_not_called()
    # Assert that a success message was published
    success_message = {
        "status": "SUCCESS",
        "source_file": gcp_file,
        "year": years[0],
        "processed_table": f"{project_id}.{dataset}.{mock_postprocess_bq.return_value}"
    }
    mock_publish_job_status.assert_called_once_with(
        project_id, topic_id, success_message
    )



@patch('src.prep.publish_job_status')
@patch('src.prep.vector_index')
@patch('src.prep.postprocess_bq')
@patch('src.prep.export_to_bq')
@patch('src.prep.efm_plot_agg')
@patch('src.prep.gdf_to_fc')
@patch('src.prep.plot_to_gdf')
def test_prep_tables_large_dataset_creates_vector_index(
    mock_plot_to_gdf,
    mock_gdf_to_fc,
    mock_efm_plot_agg,
    mock_export_to_bq,
    mock_postprocess_bq,
    mock_vector_index,
    mock_publish_job_status
):
    """
    Tests prep_tables with a large dataset (> 5000 rows)
    and verifies that vector_index IS called.
    """
    # Arrange: Set up test data and mock return values
    gcp_file = "gs://fake-bucket/fake-file.geojson"
    project = "test-project"
    dataset = "test-dataset"
    topic_id = "test_topic"
    project_id = project # for clarity in assertion
    years = [2020]
    table_base_name = "fake-file"
    exported_table_name = f"{table_base_name}_{years[0]}_random123"
    processed_table_name = f"{exported_table_name}_pp"

    # Mock the return values of the dependent functions
    mock_plot_to_gdf.return_value = pd.DataFrame({'plotid': range(6000)})  # > 5000 rows
    mock_gdf_to_fc.return_value = MagicMock(name="FeatureCollection")
    mock_efm_plot_agg.return_value = [MagicMock(name="FC_embedding_2020")]
    mock_export_to_bq.return_value = exported_table_name
    mock_postprocess_bq.return_value = processed_table_name

    # Act: Call the function under test
    prep_tables(gcp_file, project, dataset, years, topic_id)

    # Assert: Verify that the mocked functions were called correctly
    mock_plot_to_gdf.assert_called_once_with(gcp_file)
    mock_gdf_to_fc.assert_called_once_with(mock_plot_to_gdf.return_value)
    mock_efm_plot_agg.assert_called_once_with(mock_gdf_to_fc.return_value, years)
    mock_export_to_bq.assert_called_once()
    mock_postprocess_bq.assert_called_once_with(
        project, dataset, exported_table_name, wait=True
    )
    # Key assertion for this test: vector_index should be called for large tables
    mock_vector_index.assert_called_once_with(
        project, dataset, processed_table_name,
        embedding_col='embedding', wait=True
    )
    # Assert that a success message was published
    success_message = {
        "status": "SUCCESS",
        "source_file": gcp_file,
        "year": years[0],
        "processed_table": f"{project_id}.{dataset}.{processed_table_name}"
    }
    mock_publish_job_status.assert_called_once_with(
        project_id, topic_id, success_message
    )