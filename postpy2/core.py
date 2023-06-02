"""postpy2 core (coverfox_modified).
8-Mar-2023
This is modified version of postpy2 library
we are preventing usage of
-request_override,
-any file operations,
-also use of form_data as body,
-external libraries that are not already included in our requirements
Added features like
-Basic Auth (inside _handle_auth)
-Request specific Auth (inside _handle_auth)
-load_basic (inside CaseSensitiveDict)
)
"""
import base64
import difflib
import json
import logging
import re
from copy import copy

import requests

from postpy2.extractors import (
    extract_dict_from_headers,
    extract_dict_from_raw_mode_data,
    format_object,
)

logger = logging.getLogger(__name__)
TOP_REQUESTS = "Root"


class CaseSensitiveDict(dict):
    """Case sensitive dict."""

    def update(self, d=None, **kwargs):  # pylint: disable=W0613
        """Update."""
        d = d or {}
        for key, value in d.items():
            self[key] = value

    def load(self, postman_enviroment_json):
        """Load from env file."""
        postman_enviroment = json.load(postman_enviroment_json)
        for item in postman_enviroment["values"]:
            if item["enabled"]:
                self[item["key"]] = item["value"]

    def load_basic(self, d=None, **kwargs):  # CF Edited
        """This same as Load. but we can add basic dict as {"key":"value"}
        instead of {
                        "key": "VARIABLE_KEY",
                        "value": "the_value"
                }
        """
        d = d or {}
        self.update(d)


class PostPython:
    """Postman Collections 2.1 Python class."""

    def __init__(self, postman_collection_json):
        self.__postman_collection = json.loads(postman_collection_json)
        self.__folders = {}
        self.environments = CaseSensitiveDict()
        self.__load()

    def __enter__(self):
        """Enter using `with`."""
        return self

    def __exit__(self, type, value, traceback):  # pylint: disable=W0622
        """exit using `with`."""

    def _walk_folder(self, folder_name, folder):
        logger.debug("start %s", folder_name)
        if folder_name not in self.__folders:
            self.__folders[folder_name] = {}

        for thing in folder["item"]:
            if "item" not in thing:
                # request
                self.__folders[folder_name].update(self._add_requests([thing]))
            else:
                sub_folder = normalize_class_name(thing["name"])
                self._walk_folder(sub_folder, thing)

    def __load(self):
        """Walk through the folders and requests."""
        self._walk_folder(folder_name=TOP_REQUESTS, folder=self.__postman_collection)
        foldered_requests = self.__folders
        for folder_name, requests_list in foldered_requests.items():
            self.__folders[folder_name] = PostCollection(folder_name, requests_list)
        # they are flat here, does it matter?

    def auth(self):
        """Get Collection auth settings if exists."""
        if "auth" not in self.__postman_collection:
            return None
        return self.__postman_collection["auth"]

    def _add_requests(self, postman_list):
        """create a dict of requests keyed by name."""
        r_dict = {}
        for request in postman_list:
            if "request" in request:
                request["request"]["name"] = request["name"]
                r_dict[normalize_func_name(request["name"])] = PostRequest(
                    self,
                    request_data=request["request"],
                    response_data=request["response"],
                )
        return r_dict

    def walk(self):
        """walk the folders."""
        for folder in self.__folders:
            yield getattr(self, folder)

    def __getattr__(self, item):
        if item in self.__folders:
            return self.__folders[item]

        folders = list(self.__folders.keys())
        similar_folders = difflib.get_close_matches(item, folders)
        if len(similar_folders) > 0:
            similar = similar_folders[0]
            raise AttributeError(
                f"{item} folder does not exist in Postman collection.\n"
                f"Did you mean {similar}?",
            )

        raise AttributeError(
            f"{item} folder does not exist in Postman collection.\n"
            f"Your choices are: {', '.join(folders)}",
        )

    def help(self):
        """Display help info."""
        print("Possible methods:")
        for fol in self.__folders.values():
            fol.help()


def verify_url(url):
    """try to fix invalid urls (in requests view)."""
    # has to start with http
    if not url.startswith("http"):
        url = f"http://{url}"
    if "{{" in url:
        raise AssertionError(f"URL was not replaced from environment variables! {url}")
    return url


class PostCollection:
    """Postman requests collection."""

    def __init__(self, name, requests_list):
        self.name = name
        self.__requests = requests_list

    def __getattr__(self, item):
        if item in self.__requests:
            return self.__requests[item]

        post_requests = list(self.__requests.keys())
        similar_requests = difflib.get_close_matches(item, post_requests, cutoff=0.0)
        if len(similar_requests) > 0:
            similar = similar_requests[0]
            raise AttributeError(
                f"{item} request does not exist in {self.name} folder.\n"
                f"Did you mean {similar}",
            )

        raise AttributeError(
            f"{item} request does not exist in {self.name} folder.\n"
            f"Your choices are: {', '.join(post_requests)}",
        )

    def walk(self):
        """walk the requests."""
        for request in self.__requests:
            yield getattr(self, request)

    def help(self):
        """Display help info."""
        for req in self.__requests:
            print(f"post_python.{self.name}.{req}()")


class PostRequest:
    """Postman request."""

    def __init__(self, post_python, request_data, response_data=None):
        self.name = normalize_func_name(request_data["name"])
        self.post_python = post_python
        self.request_data = request_data
        self.request_kwargs = {}
        self.request_kwargs["url"] = request_data["url"]["raw"]
        self.is_graphql = False
        self.responses = response_data
        self.description = request_data.get("description")

        logger.debug("%s data keys: %s", self.__class__.__name__, request_data.keys())

        if (
            "body" in request_data
            and request_data["body"]["mode"] == "raw"
            and "raw" in request_data["body"]
        ):
            self.request_kwargs["json"] = extract_dict_from_raw_mode_data(
                request_data["body"]["raw"],
            )


        if "body" in request_data and request_data["body"]["mode"] == "graphql":
            # Graphql support
            self.request_kwargs["json"] = request_data["body"]["graphql"]

            # clean up the query some gql don't like whitespace
            self.request_kwargs["json"]["query"] = re.sub(
                r"\s+",
                " ",
                self.request_kwargs["json"]["query"],
            )

            # remove variables if blank
            if "variables" in self.request_kwargs["json"]:
                if self.request_kwargs["json"]["variables"] == "":
                    logger.info("default variables to {}")
                    self.request_kwargs["json"]["variables"] = "{}"
                # Fix for GO: encountered error parsing body: json: cannot unmarshal
                # object into Go value of type []*gateway.HTTPOperation
                # convert to dict for proper json request
                if not isinstance(self.request_kwargs["json"]["variables"], dict):
                    self.request_kwargs["json"]["variables"] = json.loads(
                        self.request_kwargs["json"]["variables"],
                    )

            self.is_graphql = True

        self.request_kwargs["headers"] = extract_dict_from_headers(
            request_data["header"],
        )
        self.request_kwargs["method"] = request_data["method"]

        logger.debug("init request_kwargs: %s", self.request_kwargs)

    def __call__(self, *args, **kwargs):
        logger.debug(args)
        current_request_kwargs = copy(self.request_kwargs)
        logger.debug("current_request_kwargs: %s", current_request_kwargs)

        new_env = copy(self.post_python.environments)
        new_env.update(kwargs)

        if "files" in current_request_kwargs:
            for _, file in current_request_kwargs["files"].items():
                file[1].seek(0)  # flip byte stream for subsequent reads

        formatted_kwargs = format_object(
            current_request_kwargs,
            new_env,
            self.is_graphql,
        )

        formatted_kwargs = self._handle_auth(formatted_kwargs, new_env)
        formatted_kwargs["url"] = verify_url(formatted_kwargs["url"])
        logger.info("formatted_kwargs: %s", formatted_kwargs)
        return requests.request(**formatted_kwargs)

    def get_auth_from_request(self):
        if "auth" not in self.request_data:
            return None
        return self.request_data["auth"]

    def _handle_auth(self, formatted_kwargs, new_env):
        auth = self.post_python.auth()
        if not auth:
            auth = self.get_auth_from_request()

        if not isinstance(auth, dict):
            # mostly to handle mock
            logger.error("Auth type (%s) not supported: %s", type(auth), auth)
            return formatted_kwargs
        if auth is not None:
            auth_type = auth.get("type", None)
            logger.info("auth type '%s'", auth_type)
            logger.debug(auth)
            if auth_type is None:
                pass
            elif auth_type.lower() == "bearer":
                # add to the headers
                if len(auth[auth_type]) > 1:
                    raise Exception("More than one auth per type is not supported")
                formatted_kwargs["headers"][
                    "Authorization"
                ] = f"Bearer {format_object(auth[auth_type][0]['value'], new_env)}"
            elif auth_type.lower() == "basic":
                # add to the headers
                if len(auth[auth_type]) > 2:
                    raise Exception("More than one auth per type is not supported")
                auth_dict = auth[auth_type]
                username = format_object(
                    next(item for item in auth_dict if item["key"] == "username")[
                        "value"
                    ],
                    new_env,
                )
                password = format_object(
                    next(item for item in auth_dict if item["key"] == "password")[
                        "value"
                    ],
                    new_env,
                )
                combined_string = username + ":" + password
                formatted_kwargs["headers"][
                    "Authorization"
                ] = f"Basic {base64.b64encode(combined_string.encode()).decode()}"
            else:
                logger.debug(auth)
                raise Exception(f"Auth type not supported: {auth}")
        return formatted_kwargs


    def set_data(self, data):
        """Add data to request args dict."""
        for row in data:
            self.request_kwargs["data"][row["key"]] = row["value"]

    def set_json(self, data):
        """Set the json value for request args."""
        self.request_kwargs["json"] = data


def normalize_class_name(string):
    """normalize class name."""
    string = re.sub(r"[?!@#$%^&*()_\-+=,./\'\\\"|:;{}\[\]]", " ", string)
    return string.title().replace(" ", "")


def normalize_func_name(string):
    """normalize func name."""
    string = re.sub(r"[?!@#$%^&*()_\-+=,./\'\\\"|:;{}\[\]]", " ", string)
    return "_".join(string.lower().split())
