import importlib.util
import pathlib
import unittest
from unittest.mock import Mock, patch


SCRIPT_PATH = (
    pathlib.Path(__file__).resolve().parent.parent
    / "scripts"
    / "cleanup-qa-instances-script.py"
)
SPEC = importlib.util.spec_from_file_location("cleanup_qa_instances_script", SCRIPT_PATH)
cleanup_qa_instances_script = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cleanup_qa_instances_script)


class CleanupQaInstancesScriptTests(unittest.TestCase):
    def test_should_delete_deployment_cell_for_target_account_without_deployments(self):
        host_cluster = {
            "cloudProvider": "aws",
            "accountID": "637423310747",
            "currentNumberOfDeployments": 0,
        }

        self.assertTrue(
            cleanup_qa_instances_script.should_delete_deployment_cell(host_cluster)
        )

    def test_should_not_delete_deployment_cell_for_non_target_or_non_empty_cluster(self):
        self.assertFalse(
            cleanup_qa_instances_script.should_delete_deployment_cell(
                {
                    "cloudProvider": "aws",
                    "accountID": "000000000000",
                    "currentNumberOfDeployments": 0,
                }
            )
        )
        self.assertFalse(
            cleanup_qa_instances_script.should_delete_deployment_cell(
                {
                    "cloudProvider": "gcp",
                    "accountID": "app-plane-dev-f7a2434f",
                    "currentNumberOfDeployments": 1,
                }
            )
        )
        self.assertFalse(
            cleanup_qa_instances_script.should_delete_deployment_cell(
                {
                    "cloudProvider": "aws",
                    "accountID": "637423310747",
                }
            )
        )

    @patch.object(cleanup_qa_instances_script.requests, "delete")
    @patch.object(cleanup_qa_instances_script.requests, "get")
    def test_cleanup_deployment_cells_deletes_only_matching_empty_clusters(
        self, mock_get, mock_delete
    ):
        get_response = Mock()
        get_response.raise_for_status.return_value = None
        get_response.json.return_value = {
            "hostClusters": [
                {
                    "id": "hc-delete-me",
                    "cloudProvider": "gcp",
                    "accountID": "app-plane-dev-f7a2434f",
                    "currentNumberOfDeployments": 0,
                },
                {
                    "id": "hc-keep-me",
                    "cloudProvider": "aws",
                    "accountID": "637423310747",
                    "currentNumberOfDeployments": 2,
                },
            ]
        }
        mock_get.return_value = get_response

        delete_response = Mock()
        delete_response.status_code = 200
        mock_delete.return_value = delete_response

        cleanup_qa_instances_script.cleanup_deployment_cells({"Authorization": "Bearer"})

        mock_delete.assert_called_once_with(
            "https://api.omnistrate.cloud/2022-09-01-00/fleet/host-clusters/hc-delete-me",
            headers={"Authorization": "Bearer"},
            timeout=60,
        )


if __name__ == "__main__":
    unittest.main()
