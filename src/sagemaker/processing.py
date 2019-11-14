# Copyright 2019 Amazon.com, Inc. or its affiliates. All Rights Reserved.
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
"""This module contains code related to the Processor class, which is used
for Processing jobs. These jobs let customers perform data pre-processing,
post-processing, feature engineering, data validation, and model evaluation
and interpretation on SageMaker.
"""
from __future__ import print_function, absolute_import

import os

from six.moves.urllib.parse import urlparse

from sagemaker.job import _Job
from sagemaker.utils import base_name_from_image, name_from_base
from sagemaker.session import Session
from sagemaker.s3 import S3Uploader
from sagemaker.network import NetworkConfig  # noqa: F401 # pylint: disable=unused-import


class Processor(object):
    """Handles Amazon SageMaker processing tasks."""

    def __init__(
        self,
        role,
        image_uri,
        instance_count,
        instance_type,
        entrypoint=None,
        volume_size_in_gb=30,
        volume_kms_key=None,
        max_runtime_in_seconds=24 * 60 * 60,
        base_job_name=None,
        sagemaker_session=None,
        env=None,
        tags=None,
        network_config=None,
    ):
        """Initialize a ``Processor`` instance. The Processor handles Amazon
        SageMaker processing tasks.

        Args:
            role (str): An AWS IAM role. The Amazon SageMaker training jobs
                and APIs that create Amazon SageMaker endpoints use this role
                to access training data and model artifacts. After the endpoint
                is created, the inference code might use the IAM role, if it
                needs to access an AWS resource.
            image_uri (str): The uri of the image to use for the processing
                jobs started by the Processor.
            instance_count (int): The number of instances to run
                the Processing job with.
            instance_type (str): Type of EC2 instance to use for
                processing, for example, 'ml.c4.xlarge'.
            entrypoint (str): The entrypoint for the processing job.
            volume_size_in_gb (int): Size in GB of the EBS volume
                to use for storing data during processing (default: 30).
            volume_kms_key (str): A KMS key for the processing
                volume.
            max_runtime_in_seconds (int): Timeout in seconds
                (default: 24 * 60 * 60). After this amount of time Amazon
                SageMaker terminates the job regardless of its current status.
            base_job_name (str): Prefix for processing name. If not specified,
                the processor generates a default job name, based on the
                training image name and current timestamp.
            sagemaker_session (sagemaker.session.Session): Session object which
                manages interactions with Amazon SageMaker APIs and any other
                AWS services needed. If not specified, the processor creates one
                using the default AWS configuration chain.
            env (dict): Environment variables to be passed to the processing job.
            tags ([dict]): List of tags to be passed to the processing job.
            network_config (sagemaker.network.NetworkConfig): A NetworkConfig
                object that configures network isolation, encryption of
                inter-container traffic, security group IDs, and subnets.
        """
        self.role = role
        self.image_uri = image_uri
        self.instance_count = instance_count
        self.instance_type = instance_type
        self.entrypoint = entrypoint
        self.volume_size_in_gb = volume_size_in_gb
        self.volume_kms_key = volume_kms_key
        self.max_runtime_in_seconds = max_runtime_in_seconds
        self.base_job_name = base_job_name
        self.sagemaker_session = sagemaker_session or Session()
        self.env = env
        self.tags = tags
        self.network_config = network_config

        self.jobs = []
        self.latest_job = None
        self._current_job_name = None
        self.arguments = None

    def run(self, inputs=None, outputs=None, arguments=None, wait=True, logs=True, job_name=None):
        """Run a processing job.

        Args:
            inputs ([sagemaker.processor.ProcessingInput]): Input files for the processing
                job. These must be provided as ProcessingInput objects.
            outputs ([sagemaker.processor.ProcessingOutput]): Outputs for the processing
                job. These can be specified as either a path string or a ProcessingOutput
                object.
            arguments ([str]): A list of string arguments to be passed to a
                processing job.
            wait (bool): Whether the call should wait until the job completes (default: True).
            logs (bool): Whether to show the logs produced by the job.
                Only meaningful when wait is True (default: True).
            job_name (str): Processing job name. If not specified, the processor generates
                a default job name, based on the image name and current timestamp.
        """
        if logs and not wait:
            raise ValueError(
                """Logs can only be shown if wait is set to True.
                Please either set wait to True or set logs to False."""
            )

        self._current_job_name = self._generate_current_job_name(job_name=job_name)

        normalized_inputs = self._normalize_inputs(inputs)
        normalized_outputs = self._normalize_outputs(outputs)
        self.arguments = arguments

        self.latest_job = ProcessingJob.start_new(self, normalized_inputs, normalized_outputs)
        self.jobs.append(self.latest_job)
        if wait:
            self.latest_job.wait(logs=logs)

    def _generate_current_job_name(self, job_name=None):
        """Generate the job name before running a processing job.

        Args:
            job_name (str): Name of the processing job to be created. If not
                specified, one is generated, using the base name given to the
                constructor if applicable.

        Returns:
            str: The supplied or generated job name.
        """
        if job_name is not None:
            return job_name
        # Honor supplied base_job_name or generate it.
        if self.base_job_name:
            base_name = self.base_job_name
        else:
            base_name = base_name_from_image(self.image_uri)

        return name_from_base(base_name)

    def _normalize_inputs(self, inputs=None):
        """Ensure that all the ProcessingInput objects have names and S3 uris.

        Args:
            inputs ([sagemaker.processor.ProcessingInput]): A list of ProcessingInput
                objects to be normalized.

        Returns:
            [sagemaker.processor.ProcessingInput]: The list of normalized
            ProcessingInput objects.
        """
        # Initialize a list of normalized ProcessingInput objects.
        normalized_inputs = []
        if inputs is not None:
            # Iterate through the provided list of inputs.
            for count, file_input in enumerate(inputs, 1):
                if not isinstance(file_input, ProcessingInput):
                    raise TypeError("Your inputs must be provided as ProcessingInput objects.")
                # Generate a name for the ProcessingInput if it doesn't have one.
                if file_input.input_name is None:
                    file_input.input_name = "input-{}".format(count)
                # If the source is a local path, upload it to S3
                # and save the S3 uri in the ProcessingInput source.
                parse_result = urlparse(file_input.source)
                if parse_result.scheme != "s3":
                    desired_s3_uri = os.path.join(
                        "s3://",
                        self.sagemaker_session.default_bucket(),
                        self._current_job_name,
                        "input",
                        file_input.input_name,
                    )
                    s3_uri = S3Uploader.upload(
                        local_path=file_input.source,
                        desired_s3_uri=desired_s3_uri,
                        session=self.sagemaker_session,
                    )
                    file_input.source = s3_uri
                normalized_inputs.append(file_input)
        return normalized_inputs

    def _normalize_outputs(self, outputs=None):
        """Ensure that all the outputs are ProcessingOutput objects with
        names and S3 uris.

        Args:
            outputs ([sagemaker.processor.ProcessingOutput]): A list
                of outputs to be normalized. Can be either strings or
                ProcessingOutput objects.

        Returns:
            [sagemaker.processor.ProcessingOutput]: The list of normalized
                ProcessingOutput objects.
        """
        # Initialize a list of normalized ProcessingOutput objects.
        normalized_outputs = []
        if outputs is not None:
            # Iterate through the provided list of outputs.
            for count, output in enumerate(outputs, 1):
                if not isinstance(output, ProcessingOutput):
                    raise TypeError("Your outputs must be provided as ProcessingOutput objects.")
                # Generate a name for the ProcessingOutput if it doesn't have one.
                if output.output_name is None:
                    output.output_name = "output-{}".format(count)
                # If the output's destination is not an s3_uri, create one.
                parse_result = urlparse(output.destination)
                if parse_result.scheme != "s3":
                    s3_uri = os.path.join(
                        "s3://",
                        self.sagemaker_session.default_bucket(),
                        self._current_job_name,
                        "output",
                    )
                    output.destination = s3_uri
                normalized_outputs.append(output)
        return normalized_outputs


class ScriptProcessor(Processor):
    """Handles Amazon SageMaker processing tasks for jobs using a machine learning framework."""

    def __init__(
        self,
        role,
        image_uri,
        instance_count,
        instance_type,
        volume_size_in_gb=30,
        volume_kms_key=None,
        max_runtime_in_seconds=24 * 60 * 60,
        base_job_name=None,
        sagemaker_session=None,
        env=None,
        tags=None,
        network_config=None,
    ):
        """Initialize a ``ScriptProcessor`` instance. The ScriptProcessor
        handles Amazon SageMaker processing tasks for jobs using script mode.

        Args:
            role (str): An AWS IAM role. The Amazon SageMaker training jobs
                and APIs that create Amazon SageMaker endpoints use this role
                to access training data and model artifacts. After the endpoint
                is created, the inference code might use the IAM role, if it
                needs to access an AWS resource.
            image_uri (str): The uri of the image to use for the processing
                jobs started by the Processor.
            instance_count (int): The number of instances to run
                the Processing job with.
            instance_type (str): Type of EC2 instance to use for
                processing, for example, 'ml.c4.xlarge'.
            py_version (str): The python version to use, for example, 'py3'.
            volume_size_in_gb (int): Size in GB of the EBS volume
                to use for storing data during processing (default: 30).
            volume_kms_key (str): A KMS key for the processing
                volume.
            max_runtime_in_seconds (int): Timeout in seconds
                (default: 24 * 60 * 60). After this amount of time Amazon
                SageMaker terminates the job regardless of its current status.
            base_job_name (str): Prefix for processing name. If not specified,
                the processor generates a default job name, based on the
                training image name and current timestamp.
            sagemaker_session (sagemaker.session.Session): Session object which
                manages interactions with Amazon SageMaker APIs and any other
                AWS services needed. If not specified, the processor creates one
                using the default AWS configuration chain.
            env (dict): Environment variables to be passed to the processing job.
            tags ([dict]): List of tags to be passed to the processing job.
            network_config (sagemaker.network.NetworkConfig): A NetworkConfig
                object that configures network isolation, encryption of
                inter-container traffic, security group IDs, and subnets.
        """
        self._CODE_CONTAINER_BASE_PATH = "/input/"
        self._CODE_CONTAINER_INPUT_NAME = "code"

        super(ScriptProcessor, self).__init__(
            role=role,
            image_uri=image_uri,
            instance_count=instance_count,
            instance_type=instance_type,
            volume_size_in_gb=volume_size_in_gb,
            volume_kms_key=volume_kms_key,
            max_runtime_in_seconds=max_runtime_in_seconds,
            base_job_name=base_job_name,
            sagemaker_session=sagemaker_session,
            env=env,
            tags=tags,
            network_config=network_config,
        )

    def run(
        self,
        command,
        code,
        script_name=None,
        inputs=None,
        outputs=None,
        arguments=None,
        wait=True,
        logs=True,
        job_name=None,
    ):
        """Run a processing job with Script Mode.

        Args:
            command([str]): This is a list of strings that includes the executable, along
                with any command-line flags. For example: ["python3", "-v"]
            code (str): This can be an S3 uri or a local path to either
                a directory or a file with the user's script to run.
            script_name (str): If the user provides a directory for source,
                they must specify script_name as the file within that
                directory to use.
            inputs ([sagemaker.processor.ProcessingInput]): Input files for the processing
                job. These must be provided as ProcessingInput objects.
            outputs ([str or sagemaker.processor.ProcessingOutput]): Outputs for the processing
                job. These can be specified as either a path string or a ProcessingOutput
                object.
            arguments ([str]): A list of string arguments to be passed to a
                processing job.
            wait (bool): Whether the call should wait until the job completes (default: True).
            logs (bool): Whether to show the logs produced by the job.
                Only meaningful when wait is True (default: True).
            job_name (str): Processing job name. If not specified, the processor generates
                a default job name, based on the image name and current timestamp.
        """
        self._current_job_name = self._generate_current_job_name(job_name=job_name)

        customer_script_name = self._get_customer_script_name(code, script_name)
        customer_code_s3_uri = self._upload_code(code)
        inputs_with_code = self._convert_code_and_add_to_inputs(inputs, customer_code_s3_uri)

        self._set_entrypoint(command, customer_script_name)

        super(ScriptProcessor, self).run(
            inputs=inputs_with_code,
            outputs=outputs,
            arguments=arguments,
            wait=wait,
            logs=logs,
            job_name=job_name,
        )

    def _get_customer_script_name(self, code, script_name):
        """Finds the customer script name using the provided code file,
        directory, or script name.

        Args:
            code (str): This can be an S3 uri or a local path to either
                a directory or a file.
            script_name (str): If the user provides a directory as source,
                they must specify script_name as the file within that
                directory to use.

        Returns:
            str: The script name from the S3 uri or from the file found
                on the user's local machine.
        """
        parse_result = urlparse(code)

        if os.path.isdir(code) and script_name is None:
            raise ValueError(
                """You provided a directory without providing a script name.
                Please provide a script name inside the directory that you specified.
                """
            )
        if (parse_result.scheme == "s3" or os.path.isdir(code)) and script_name is not None:
            return script_name
        if parse_result.scheme == "s3" or os.path.isfile(code):
            return os.path.basename(code)
        raise ValueError("The file or directory you specified does not exist.")

    def _upload_code(self, code):
        """Uploads a code file or directory specified as a string
        and returns the S3 uri.

        Args:
            code (str): A file or directory to be uploaded to S3.

        Returns:
            str: The S3 uri of the uploaded file or directory.

        """
        desired_s3_uri = os.path.join(
            "s3://",
            self.sagemaker_session.default_bucket(),
            self._current_job_name,
            "input",
            self._CODE_CONTAINER_INPUT_NAME,
        )
        return S3Uploader.upload(
            local_path=code, desired_s3_uri=desired_s3_uri, session=self.sagemaker_session
        )

    def _convert_code_and_add_to_inputs(self, inputs, s3_uri):
        """Creates a ProcessingInput object from an S3 uri and adds it to the list of inputs.

        Args:
            inputs ([sagemaker.processor.ProcessingInput]): List of ProcessingInput objects.
            s3_uri (str): S3 uri of the input to be added to inputs.

        Returns:
            [sagemaker.processor.ProcessingInput]: A new list of ProcessingInput objects, with
                the ProcessingInput object created from s3_uri appended to the list.

        """
        code_file_input = ProcessingInput(
            source=s3_uri,
            destination=os.path.join(
                self._CODE_CONTAINER_BASE_PATH, self._CODE_CONTAINER_INPUT_NAME
            ),
            input_name=self._CODE_CONTAINER_INPUT_NAME,
        )
        return inputs + [code_file_input]

    def _set_entrypoint(self, command, customer_script_name):
        """Sets the entrypoint based on the customer's script and corresponding executable.

        Args:
            customer_script_name (str): A filename with an extension.
        """
        customer_script_location = os.path.join(
            self._CODE_CONTAINER_BASE_PATH, self._CODE_CONTAINER_INPUT_NAME, customer_script_name
        )
        self.entrypoint = command + [customer_script_location]


class ProcessingJob(_Job):
    """Provides functionality to start, describe, and stop processing jobs."""

    def __init__(self, sagemaker_session, job_name, inputs, outputs):
        self.inputs = inputs
        self.outputs = outputs
        super(ProcessingJob, self).__init__(sagemaker_session=sagemaker_session, job_name=job_name)

    @classmethod
    def start_new(cls, processor, inputs, outputs):
        """Start a new processing job using the provided inputs and outputs.

        Args:
            processor (sagemaker.processing.Processor): The Processor instance
                that started the job.
            inputs ([sagemaker.processor.ProcessingInput]): A list of ProcessingInput objects.
            outputs ([sagemaker.processor.ProcessingOutput]): A list of ProcessingOutput objects.

        Returns:
            sagemaker.processing.ProcessingJob: The instance of ProcessingJob created
                using the current job name.

        """
        # Initialize an empty dictionary for arguments to be passed to sagemaker_session.process.
        process_request_args = {}

        # Add arguments to the dictionary.
        process_request_args["inputs"] = [input.to_request_dict() for input in inputs]
        process_request_args["outputs"] = [output.to_request_dict() for output in outputs]
        process_request_args["job_name"] = processor._current_job_name
        process_request_args["resources"] = {
            "ClusterConfig": {
                "InstanceType": processor.instance_type,
                "InstanceCount": processor.instance_count,
                "VolumeSizeInGB": processor.volume_size_in_gb,
            }
        }
        process_request_args["stopping_condition"] = {
            "MaxRuntimeInSeconds": processor.max_runtime_in_seconds
        }
        process_request_args["app_specification"] = {"ImageUri": processor.image_uri}
        if processor.arguments is not None:
            process_request_args["app_specification"]["ContainerArguments"] = processor.arguments
        if processor.entrypoint is not None:
            process_request_args["app_specification"]["ContainerEntrypoint"] = processor.entrypoint
        process_request_args["environment"] = processor.env
        if processor.network_config is not None:
            process_request_args["network_config"] = processor.network_config.to_request_dict()
        else:
            process_request_args["network_config"] = None
        process_request_args["role_arn"] = processor.role
        process_request_args["tags"] = processor.tags

        # Print the job name and the user's inputs and outputs as lists of dictionaries.
        print("Job Name: ", process_request_args["job_name"])
        print("Inputs: ", process_request_args["inputs"])
        print("Outputs: ", process_request_args["outputs"])

        # Call sagemaker_session.process using the arguments dictionary.
        processor.sagemaker_session.process(**process_request_args)

        return cls(processor.sagemaker_session, processor._current_job_name, inputs, outputs)

    def _is_local_channel(self, input_url):
        """Used for Local Mode. Not yet implemented.
        Args:
            input_url (str):
        """
        raise NotImplementedError

    def wait(self, logs=True):
        if logs:
            self.sagemaker_session.logs_for_processing_job(self.job_name, wait=True)
        else:
            self.sagemaker_session.wait_for_processing_job(self.job_name)

    def describe(self, print_response=True):
        """Prints out a response from the DescribeProcessingJob API call."""
        describe_response = self.sagemaker_session.describe_analytics_job(self.job_name)
        if print_response:
            print(describe_response)
        return describe_response

    def stop(self):
        """Stops the processing job."""
        self.sagemaker_session.stop_processing_job(self.name)


class ProcessingInput(object):
    """Accepts parameters that specify an S3 input for a processing job and provides
    a method to turn those parameters into a dictionary."""

    def __init__(
        self,
        source,
        destination,
        input_name=None,
        s3_data_type="ManifestFile",
        s3_input_mode="File",
        s3_download_mode="Continuous",
        s3_data_distribution_type="FullyReplicated",
        s3_compression_type="None",
    ):
        """Initialize a ``ProcessingInput`` instance. ProcessingInput accepts parameters
        that specify an S3 input for a processing job and provides a method
        to turn those parameters into a dictionary.

        Args:
            source (str): The source for the input.
            destination (str): The destination of the input.
            input_name (str): The user-provided name for the input. If a name
                is not provided, one will be generated.
            s3_data_type (str): Valid options are "ManifestFile" or "S3Prefix".
            s3_input_mode (str): Valid options are "Pipe" or "File".
            s3_download_mode (str): Valid options are "StartOfJob" or "Continuous".
            s3_data_distribution_type (str): Valid options are "FullyReplicated"
                or "ShardedByS3Key".
            s3_compression_type (str): Valid options are "None" or "Gzip".
        """
        self.source = source
        self.destination = destination
        self.input_name = input_name
        self.s3_data_type = s3_data_type
        self.s3_input_mode = s3_input_mode
        self.s3_download_mode = s3_download_mode
        self.s3_data_distribution_type = s3_data_distribution_type
        self.s3_compression_type = s3_compression_type

    def to_request_dict(self):
        """Generates a request dictionary using the parameters provided to the class."""
        # Create the request dictionary.
        s3_input_request = {
            "InputName": self.input_name,
            "S3Input": {
                "S3Uri": self.source,
                "LocalPath": self.destination,
                "S3DataType": self.s3_data_type,
                "S3InputMode": self.s3_input_mode,
                "S3DownloadMode": self.s3_download_mode,
                "S3DataDistributionType": self.s3_data_distribution_type,
            },
        }

        # Check the compression type, then add it to the dictionary.
        if self.s3_compression_type == "Gzip" and self.s3_input_mode != "Pipe":
            raise ValueError("Data can only be gzipped when the input mode is Pipe.")
        if self.s3_compression_type is not None:
            s3_input_request["S3Input"]["S3CompressionType"] = self.s3_compression_type

        # Return the request dictionary.
        return s3_input_request


class ProcessingOutput(object):
    """Accepts parameters that specify an S3 output for a processing job and provides
    a method to turn those parameters into a dictionary."""

    def __init__(
        self, source, destination, output_name=None, kms_key_id=None, s3_upload_mode="Continuous"
    ):
        """Initialize a ``ProcessingOutput`` instance. ProcessingOutput accepts parameters that
        specify an S3 output for a processing job and provides a method to turn
        those parameters into a dictionary.

        Args:
            source (str): The source for the output.
            destination (str): The destination of the output.
            output_name (str): The name of the output.
            kms_key_id (str): The KMS key id for the output.
            s3_upload_mode (str): Valid options are "EndOfJob" or "Continuous".
        """
        self.source = source
        self.destination = destination
        self.output_name = output_name
        self.kms_key_id = kms_key_id
        self.s3_upload_mode = s3_upload_mode

    def to_request_dict(self):
        """Generates a request dictionary using the parameters provided to the class."""
        # Create the request dictionary.
        s3_output_request = {
            "OutputName": self.output_name,
            "S3Output": {
                "S3Uri": self.destination,
                "LocalPath": self.source,
                "S3UploadMode": self.s3_upload_mode,
            },
        }

        # Check the KMS key ID, then add it to the dictionary.
        if self.kms_key_id is not None:
            s3_output_request["S3Output"]["KmsKeyId"] = self.kms_key_id

        # Return the request dictionary.
        return s3_output_request
