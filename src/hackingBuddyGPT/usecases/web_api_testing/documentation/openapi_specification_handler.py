import os
import re
from collections import defaultdict
from datetime import datetime
import yaml
from hackingBuddyGPT.capabilities.yamlFile import YAMLFile
from hackingBuddyGPT.usecases.web_api_testing.documentation.pattern_matcher import PatternMatcher
from hackingBuddyGPT.utils.prompt_generation.information import PromptStrategy
from hackingBuddyGPT.usecases.web_api_testing.response_processing import ResponseHandler
from hackingBuddyGPT.usecases.web_api_testing.utils import LLMHandler


class OpenAPISpecificationHandler(object):
    """
    Handles the generation and updating of an OpenAPI specification document based on dynamic API responses.

    Attributes:
        response_handler (object): An instance of the response handler for processing API responses.
        schemas (dict): A dictionary to store API schemas.
        filename (str): The filename for the OpenAPI specification file.
        openapi_spec (dict): The OpenAPI specification document structure.
        llm_handler (object): An instance of the LLM handler for interacting with the LLM.
        api_key (str): The API key for accessing the LLM.
        file_path (str): The path to the directory where the OpenAPI specification file will be stored.
        file (str): The complete path to the OpenAPI specification file.
        _capabilities (dict): A dictionary to store capabilities related to YAML file handling.
    """

    def __init__(self, llm_handler: LLMHandler, response_handler: ResponseHandler, strategy: PromptStrategy, url: str,
                 description: str, name: str) -> None:
        """
        Initializes the handler with a template OpenAPI specification.

        Args:
            llm_handler (object): An instance of the LLM handler for interacting with the LLM.
            response_handler (object): An instance of the response handler for processing API responses.
            strategy (PromptStrategy): An instance of the PromptStrategy class.
        """
        self.unsuccessful_methods = {}
        self.response_handler = response_handler
        self.schemas = {}
        self.query_params = {}
        self.endpoint_methods = {}
        self.endpoint_examples = {}
        self.filename = f"{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.yaml"
        self.openapi_spec = {
            "openapi": "3.0.0",
            "info": {
                "title": f"Generated API Documentation {name}",
                "version": "1.0",
                "description": f"{description} + \nUrl:{url}",
            },
            "servers": [{"url": f"{url}"}],  # https://jsonplaceholder.typicode.com
            "endpoints": {},
            "components": {"schemas": {}},
        }
        self.llm_handler = llm_handler
        current_path = os.path.dirname(os.path.abspath(__file__))

        self.file_path = os.path.join(current_path, "openapi_spec", str(strategy).split(".")[1].lower(), name.lower())
        os.makedirs(self.file_path, exist_ok=True)
        self.file = os.path.join(self.file_path, self.filename)

        self._capabilities = {"yaml": YAMLFile()}
        self.unsuccessful_paths = []

        self.pattern_matcher = PatternMatcher()

    def is_partial_match(self, element, string_list):
        """
            Checks if the given path `element` partially matches any path in `string_list`,
            treating path parameters (e.g., `{id}`) as wildcards.

            A partial match is defined as:
            - Having the same number of path segments.
            - Matching all static segments (segments not wrapped in `{}`).

            This is useful when comparing generalized OpenAPI paths with actual request paths.

            Args:
                element (str): The path to check for partial matches (e.g., "/users/123").
                string_list (List[str]): A list of known paths (e.g., ["/users/{id}", "/posts/{postId}"]).

            Returns:
                bool: True if a partial match is found, False otherwise.
            """
        element_parts = element.split("/")

        for string in string_list:
            string_parts = string.split("/")
            if len(element_parts) != len(string_parts):
                continue  # Skip if structure differs

            for e_part, s_part in zip(element_parts, string_parts):
                if s_part.startswith("{") and s_part.endswith("}"):
                    continue  # Skip placeholders
                if e_part != s_part:
                    break  # No match
            else:
                return True  # All static parts matched

        return False

    def update_openapi_spec(self, resp, result, prompt_engineer):
        """
        Updates the OpenAPI specification based on the API response provided.

        Args:
            resp (object): The response object containing details like the path and method which should be documented.
            result (str): The result of the API call.
        """
        request = resp.action
        status_code, status_message = self.extract_status_code_and_message(result)

        if request.__class__.__name__ == "RecordNote":  # TODO: check why isinstance does not work
            # self.check_openapi_spec(resp)
            return list(self.openapi_spec["endpoints"].keys())

        if request.__class__.__name__ == "HTTPRequest":
            path = request.path
            method = request.method
            path = self.replace_id_with_placeholder(path, prompt_engineer)
            if not path or not method or path == "/" or not path.startswith("/"):
                return list(self.openapi_spec["endpoints"].keys())

            # replace specific values with generic values for doc
            path = self.pattern_matcher.replace_according_to_pattern(path)

            if path in self.unsuccessful_paths:
                return list(self.openapi_spec["endpoints"].keys())

            endpoint_methods = self.endpoint_methods
            endpoints = self.openapi_spec["endpoints"]

            # Extract the main part of the path for checking partial matches
            path_parts = path.split("/")
            main_path = path if len(path_parts) > 1 else ""

            # Initialize the path if it's not present and is valid
            if status_code.startswith("20"):
                if path not in endpoints and "?" not in path:
                    endpoints[path] = {}
                    endpoint_methods[path] = []
                    self.endpoint_examples[path] = {}

            unsuccessful_status_codes = ["400", "404", "500"]

            if path in endpoints and (status_code in unsuccessful_status_codes):

                self.unsuccessful_paths.append(path)
                if path not in self.unsuccessful_methods:
                    self.unsuccessful_methods[path] = []
                self.unsuccessful_methods[path].append(method)
                return list(self.openapi_spec["endpoints"].keys())

            # Parse the response into OpenAPI example and reference
            example, reference, self.openapi_spec = self.response_handler.parse_http_response_to_openapi_example(
                self.openapi_spec, result, path, method
            )

            self.schemas = self.openapi_spec["components"]["schemas"]

            # Check if the path exists in the dictionary and the method is not already defined for this path
            if path in endpoints and method.lower() not in endpoints[path]:
                # Create a new dictionary for this method if it doesn't exist
                endpoints[path][method.lower()] = {
                    "summary": f"{method} operation on {path}",
                    "responses": {
                        f"{status_code}": {
                            "description": status_message,
                            "content": {}
                        }
                    }
                }

                if path in endpoint_methods:
                    endpoint_methods[path] = []

                # Update endpoint methods for the path
                if path not in endpoint_methods:
                    endpoint_methods[path] = []
                endpoint_methods[path].append(method)

                # Ensure uniqueness of methods for each path
                endpoint_methods[path] = list(set(endpoint_methods[path]))

            # Check if there's a need to add or update the 'content' based on the conditions provided
            if example or reference or status_message == "No Content" and not path.__contains__("?"):
                if isinstance(example, list):
                    example = example[0]
                # Ensure the path and method exists and has the 'responses' structure
                if (path in endpoints and method.lower() in endpoints[path]):
                    if "responses" in endpoints[path][method.lower()].keys() and f"{status_code}" in \
                            endpoints[path][method.lower()]["responses"]:
                        # Get the response content dictionary
                        response_content = endpoints[path][method.lower()]["responses"][f"{status_code}"]["content"]

                        # Assign a new structure to 'content' under the specific status code
                        response_content["application/json"] = {
                            "schema": {"$ref": reference},
                            "examples": example
                        }

                        self.endpoint_examples[path] = example

            # Add query parameters to the OpenAPI path item object
            if path.__contains__('?'):
                query_params_dict = self.pattern_matcher.extract_query_params(path)
                new_path = path.split("?")[0]
                if query_params_dict != {}:
                    if path not in endpoints.keys():
                        endpoints[new_path] = {}
                    if method.lower() not in endpoints[new_path]:
                        endpoints[new_path][method.lower()] = {}
                    endpoints[new_path][method.lower()].setdefault('parameters', [])
                    for param, value in query_params_dict.items():
                        param_entry = {
                            "name": param,
                            "in": "query",
                            "required": True,  # Change this as needed
                            "schema": {
                                "type": self.get_type(value)  # Adjust the type based on actual data type
                            }
                        }
                        endpoints[new_path][method.lower()]['parameters'].append(param_entry)
                        if path not in self.query_params.keys():
                            self.query_params[new_path] = []
                        self.query_params[new_path].append(param)

        return list(self.openapi_spec["endpoints"].keys())

    def write_openapi_to_yaml(self):
        """
        Writes the updated OpenAPI specification to a YAML file with a timestamped filename.
        """
        try:
            # Prepare data to be written to YAML
            openapi_data = {
                "openapi": self.openapi_spec["openapi"],
                "info": self.openapi_spec["info"],
                "servers": self.openapi_spec["servers"],
                "components": self.openapi_spec["components"],
                "paths": self.openapi_spec["endpoints"],
            }

            # Create directory if it doesn't exist and generate the timestamped filename
            os.makedirs(self.file_path, exist_ok=True)

            # Write to YAML file
            with open(self.file, "w") as yaml_file:
                yaml.dump(openapi_data, yaml_file, allow_unicode=True, default_flow_style=False)
            print(f"OpenAPI specification written to {self.filename}.")
        except Exception as e:
            raise Exception(f"Error writing YAML file: {e}") from e

    def _update_documentation(self, response, result, result_str, prompt_engineer):
        """
    Updates the OpenAPI documentation based on a new API response and result string.

    This method performs the following:
      - Updates the OpenAPI specification using the latest API response.
      - Writes the updated OpenAPI spec to a YAML file if new endpoints are discovered.
      - Updates the schemas used by the `prompt_engineer`.
      - Constructs a mapping of HTTP methods to endpoints and stores it in the prompt helper.

    Args:
        response (Any): The original API response object, possibly including metadata or status.
        result (Any): The raw result of executing the API call, potentially including headers and body.
        result_str (str): A string representation of the HTTP response, including status line and body.
        prompt_engineer (PromptEngineer): An instance of the prompt engineer responsible for generating prompts and managing discovered schema information.

    Returns:
        PromptEngineer: The updated prompt engineer with any new endpoint or schema information applied.

    """
        if result_str is None:
            return prompt_engineer
        endpoints = self.update_openapi_spec(response, result, prompt_engineer)
        if prompt_engineer.prompt_helper.found_endpoints != endpoints and endpoints != [] and len(endpoints) != 1:
            self.write_openapi_to_yaml()
            prompt_engineer.prompt_helper.schemas = self.schemas

        http_methods_dict = defaultdict(list)
        for endpoint, methods in self.endpoint_methods.items():
            for method in methods:
                http_methods_dict[method].append(endpoint)

        prompt_engineer.prompt_helper.endpoint_found_methods = http_methods_dict
        prompt_engineer.prompt_helper.endpoint_methods = self.endpoint_methods
        return prompt_engineer

    def document_response(self, result, response, result_str, prompt_history, prompt_engineer):
        """
           Processes an API response and updates the OpenAPI documentation if the response is valid.

           This method filters out invalid or placeholder responses using a set of known flags.
           If the response appears valid, it triggers the `_update_documentation()` method
           to update the OpenAPI spec and associated prompt engineering logic.

           Args:
               result (Any): The raw execution result, typically the HTTP response body or object.
               response (Any): The full API response object, potentially containing metadata or headers.
               result_str (str): A string representation of the HTTP response for validation and parsing.
               prompt_history (Any): The accumulated history of prompt interactions.
               prompt_engineer (PromptEngineer): Instance responsible for generating and managing prompts.

           Returns:
               Tuple[Any, PromptEngineer]: A tuple containing the unchanged `prompt_history` and
               the (potentially updated) `prompt_engineer`.
           """

        invalid_flags = {"recorded"}
        if result_str not in invalid_flags or any(flag in result_str for flag in invalid_flags):
            prompt_engineer = self._update_documentation(response, result, result_str, prompt_engineer)

        return prompt_history, prompt_engineer

    def found_all_endpoints(self):
        """
            Determines whether a sufficient number of API endpoints have been discovered.

            Currently, this uses a simple heuristic: if the number of endpoint-method pairs
            is at least 10, it is assumed that all relevant endpoints have been found.

            Returns:
                bool: True if at least 10 endpoint-method entries exist, False otherwise.
            """
        if len(self.endpoint_methods.items()) < 10:
            return False
        else:
            return True

    def get_type(self, value):
        """
            Determines the data type of a given string value.

            Checks whether the input string represents an integer, a floating-point number (double),
            or should be treated as a generic string.

            Args:
                value (str): The value to inspect.

            Returns:
                str: One of "integer", "double", or "string" depending on the detected type.
            """

        def is_double(s):
            # Matches numbers like -123.456, +7.890, and excludes integers
            return re.fullmatch(r"[+-]?(\d+\.\d*|\.\d+)([eE][+-]?\d+)?", s) is not None

        if value.isdigit():
            return "integer"
        elif is_double(value):
            return "double"
        else:
            return "string"

    def extract_status_code_and_message(self, result):
        """
            Extracts the HTTP status code and status message from a response string.

            Args:
                result (str): A string containing the full HTTP response or just the status line.

            Returns:
                Tuple[Optional[str], Optional[str]]: A tuple containing the HTTP status code and message.
                Returns (None, None) if the pattern is not matched.
            """
        if not isinstance(result, str):
            result = str(result)
        match = re.search(r"^HTTP/\d\.\d\s+(\d+)\s+(.*)", result, re.MULTILINE)
        if match:
            status_code = match.group(1)
            status_message = match.group(2).strip()
            return status_code, status_message
        else:
            return None, None

    def replace_crypto_with_id(self, path):
        """
    Replaces any known cryptocurrency name in a URL path with a placeholder `{id}`.

    Useful for generalizing dynamic paths when generating or matching OpenAPI specs.

    Args:
        path (str): The URL path to process.

    Returns:
        str: The path with any matching cryptocurrency name replaced by `{id}`.
    """

        # Default list of cryptos to detect
        cryptos = ["bitcoin", "ethereum", "litecoin", "dogecoin",
                   "cardano", "solana"]

        # Convert to lowercase for the match, but preserve the original path for reconstruction if you prefer
        lower_path = path.lower()

        for crypto in cryptos:
            if crypto in lower_path:
                # Example approach: split by '/' and replace the segment that matches crypto
                parts = path.split('/')
                replaced_any = False
                for i, segment in enumerate(parts):
                    if segment.lower() == crypto:
                        parts[i] = "{id}"
                        if segment.lower() == crypto:
                            parts[i] = "{id}"
                            replaced_any = True
                            if replaced_any:
                                return "/".join(parts)

        return path

    def replace_id_with_placeholder(self, path, prompt_engineer):
        """
        Replaces numeric IDs in the URL path with a placeholder `{id}` for generalization.

        This function is used to abstract hardcoded numeric values (e.g., `/users/1`) in the path
        to a parameterized form (e.g., `/users/{id}`), which is helpful when building or inferring
        OpenAPI specifications.

        Behavior varies slightly depending on the current step tracked by the `prompt_engineer`.

        Args:
            path (str): The URL path to process.
            prompt_engineer (PromptEngineer): An object containing context about the current prompt
                and parsing state, specifically the current step of API exploration.

        Returns:
            str: The updated path with numeric IDs replaced by `{id}`.
        """
        if "1" in path:
            path = path.replace("1", "{id}")
        if prompt_engineer.prompt_helper.current_step == 2:
            parts = [part.strip() for part in path.split("/") if part.strip()]
            if len(parts) > 1:
                path = parts[0] + "/{id}"
        return path
