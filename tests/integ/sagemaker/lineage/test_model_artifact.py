# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License"). You
# may not use this file except in compliance with the License. A copy of
# the License is located at
#
#     http://aws.amazon.com/apache2.0/
#
# or in the "license" file accompanying this file. This file is
# distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF
# ANY KIND, either express or implied. See the License for the specific
# language governing permissions and limitations under the License.
"""This module contains code to test SageMaker ``DatasetArtifact``"""
from __future__ import absolute_import
from tests.integ.sagemaker.lineage.helpers import traverse_graph_forward, traverse_graph_back


def test_endpoints(
    sagemaker_session,
    model_artifact_associated_endpoints,
    endpoint_deployment_action_obj,
    endpoint_context_obj,
):

    model_list = model_artifact_associated_endpoints.endpoints()
    for model in model_list:
        assert model.source_arn == endpoint_deployment_action_obj.action_arn
        assert model.destination_arn == endpoint_context_obj.context_arn
        assert model.source_type == endpoint_deployment_action_obj.action_type
        assert model.destination_type == endpoint_context_obj.context_type


def test_endpoint_contexts(
    static_model_artifact,
    sagemaker_session,
):
    contexts_from_query = static_model_artifact.endpoint_contexts()

    associations_from_api = traverse_graph_forward(
        static_model_artifact.artifact_arn, sagemaker_session=sagemaker_session
    )

    assert len(contexts_from_query) > 0
    for context in contexts_from_query:
        # assert that the contexts from the query
        # appear in the association list from the lineage API
        assert any(
            x
            for x in associations_from_api
            if x["DestinationArn"] == context.context_arn and x["DestinationType"] == "Endpoint"
        )


def test_dataset_artifacts(
    static_model_artifact,
    sagemaker_session,
):
    artifacts_from_query = static_model_artifact.dataset_artifacts()

    associations_from_api = traverse_graph_back(
        static_model_artifact.artifact_arn, sagemaker_session=sagemaker_session
    )

    assert len(artifacts_from_query) > 0
    for artifact in artifacts_from_query:
        # assert that the artifacts from the query
        # appear in the association list from the lineage API
        assert any(
            x
            for x in associations_from_api
            if x["SourceArn"] == artifact.artifact_arn and x["SourceType"] == "DataSet"
        )


def test_training_job_arns(
    static_model_artifact,
):
    training_job_arns = static_model_artifact.training_job_arns()

    assert len(training_job_arns) > 0
    for arn in training_job_arns:
        assert "training-job" in arn


def test_pipeline_execution_arn(static_model_artifact, static_pipeline_execution_arn):
    pipeline_execution_arn = static_model_artifact.pipeline_execution_arn()

    assert pipeline_execution_arn == static_pipeline_execution_arn
