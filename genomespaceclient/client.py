import logging
import re
import glob
import os

from genomespaceclient import storage_handlers
from genomespaceclient import gs_glob
from genomespaceclient.exceptions import GSClientException

import requests
from requests.exceptions import HTTPError
try:
    from urllib.parse import urlparse
except ImportError:
    from urlparse import urlparse


log = logging.getLogger(__name__)


class GSDataFormat(object):
    """
    See: http://www.genomespace.org/support/api/restful-access-to-dm#appendix_c
    """

    def __init__(self, name, url, fileExtension, description):
        self.name = name
        self.url = url
        self.fileExtension = fileExtension
        self.description = description

    @staticmethod
    def from_json(json_data):
        if json_data:
            return GSDataFormat(
                json_data.get('name'),
                json_data.get('url'),
                json_data.get('fileExtension'),
                json_data.get('description')
            )
        else:
            return None


class GSFileMetadata(object):
    """
    See: http://www.genomespace.org/support/api/restful-access-to-dm#appendix_a
    """

    def __init__(self, name, path, url, parentUrl, size, owner, isDirectory,
                 isLink, targetPath, lastModified, dataFormat,
                 availableDataFormats):
        self.name = name
        self.path = path
        self.url = url
        self.parentUrl = parentUrl
        self.size = size
        self.owner = owner
        self.isDirectory = isDirectory
        self.isLink = isLink
        self.targetPath = targetPath
        self.lastModified = lastModified
        self.dataFormat = dataFormat
        self.availableDataFormats = availableDataFormats

    @staticmethod
    def from_json(json_data):
        return GSFileMetadata(
            json_data.get('name'),
            json_data.get('path'),
            json_data.get('url'),
            json_data.get('parentUrl'),
            json_data.get('size'),
            json_data.get('owner'),
            json_data.get('isDirectory'),
            json_data.get('isLink'),
            json_data.get('targetPath'),
            json_data.get('lastModified'),
            GSDataFormat.from_json(json_data.get('dataFormat')),
            [GSDataFormat.from_json(data_fmt)
             for data_fmt in json_data.get('availableDataFormats', [])]
        )


class GSDirectoryListing(object):
    """
    See: http://www.genomespace.org/support/api/restful-access-to-dm#appendix_b
    """

    def __init__(self, contents, directory):
        self.contents = contents
        self.directory = directory

    @staticmethod
    def from_json(json_data):
        return GSDirectoryListing(
            [GSFileMetadata.from_json(content)
             for content in json_data.get('contents', [])],
            GSFileMetadata.from_json(json_data.get('directory'))
        )


class GenomeSpaceClient():
    """
    A simple GenomeSpace client
    """

    def __init__(self, username=None, password=None, token=None):
        """
        Constructs a new GenomeSpace client. A username/password
        combination or a token must be supplied.

        :type username: :class:`str`
        :param username: GenomeSpace username

        :type password: :class:`str`
        :param password: GenomeSpace password

        :type token: :class:`str`
        :param token: A GenomeSpace auth token. If supplied, the token will be
                      used instead of the username/password.
        """
        self.username = username
        self.password = password
        self.token = token

    def _get_gs_auth_cookie(self, server_url):
        """
        Returns a cookie containing a GenomeSpace auth token.
        If an auth token was not provided at client initalisation, a request
        is made to the identity server to obtain a new session token.
        """
        if not self.token:
            parsed_uri = urlparse(server_url)
            url = "{uri.scheme}://{uri.netloc}/identityServer/basic".format(
                uri=parsed_uri)
            response = requests.get(url,
                                    auth=requests.auth.HTTPBasicAuth(
                                        self.username,
                                        self.password))
            response.raise_for_status()
            self.token = response.cookies.get("gs-token")
        return {"gs-token": self.token}

    def _api_generic_request(self, request_func, genomespace_url, headers=None,
                             body=None, allow_redirects=True):
        """
        Makes a request to a GenomeSpace API endpoint, after adding some
        standard headers, including authentication headers.
        Also performs some standard validations on the result.

        :type request_func: :func to call. Must be from the requests package,
                            and maybe a get, put, post etc.
        :param request_func: Calls the requested method in the requests package
                            after adding some standard headers.

        :type genomespace_url: :class:`str`
        :param genomespace_url: GenomeSpace API URL to perform the request
                                against.

        :type headers: :class:`dict`
        :param headers: A dict containing additional headers to include with
                        the request.

        :type body: :class:`bytes`
        :param body: Optional data to send as the request body.

        :return: a JSON response after performing some sanity checks. Raises
                 an exception in case of an unexpected response.
        """
        req_headers = {'Accept': 'application/json',
                       'Content-Type': 'application/json'}
        req_headers.update(headers or {})

        response = request_func(genomespace_url,
                                cookies=self._get_gs_auth_cookie(
                                    genomespace_url),
                                headers=req_headers,
                                data=body,
                                allow_redirects=allow_redirects)
        response.raise_for_status()
        return response

    def _api_json_request(self, request_func, genomespace_url, headers=None,
                          body=None):
        """
        Makes a request to a GenomeSpace API endpoint, after adding some
        standard headers, including authentication headers.
        Also performs some standard validations on the result.

        :return: a JSON response after performing some sanity checks. Raises
                 an exception in case of an unexpected response.
        """
        response = self._api_generic_request(request_func,
                                             genomespace_url,
                                             headers=headers,
                                             body=body)
        if "application/json" not in response.headers["content-type"]:
            raise GSClientException("Expected json content but received: %s" %
                                    (response.headers["content-type"],))

        return response.json()

    def _api_get_request(self, genomespace_url, headers=None):
        return self._api_json_request(
            requests.get, genomespace_url, headers=headers)

    def _api_put_request(self, genomespace_url, headers=None, body=None):
        return self._api_json_request(
            requests.put, genomespace_url, headers=headers, body=body)

    def _api_delete_request(self, genomespace_url, headers=None, body=None):
        return self._api_generic_request(
            requests.delete, genomespace_url, headers=headers)

    def _infer_dest_filename(self, source, destination):
        if self._is_dir_path(destination) and not self._is_dir_path(source):
            # Extract the filename from source and append it to destination
            return destination + source.rsplit("/", 1)[-1]
        else:
            return destination

    def _internal_copy(self, source, destination):
        if not gs_glob.is_same_genomespace_server(source, destination):
            raise GSClientException(
                "Copying between two different GenomeSpace servers is"
                " currently unsupported.")
        for f in gs_glob.gs_iglob(self, source):
            self._internal_copy_single_file(f, destination)

    def _internal_copy_single_file(self, source, destination):
        destination = self._infer_dest_filename(source, destination)
        copy_source = source.replace(
            gs_glob.GENOMESPACE_URL_REGEX.match(source).group(1),
            "/")
        return self._api_put_request(
            destination, headers={'x-gs-copy-source': copy_source})

    def _get_upload_info(self, genomespace_url):
        url = genomespace_url.replace("/datamanager/v1.0/file/",
                                      "/datamanager/v1.0/uploadinfo/")
        return self._api_get_request(url)

    def _get_download_info(self, genomespace_url):
        response = self._api_generic_request(requests.get, genomespace_url,
                                             allow_redirects=False)
        # This is for an edge case where GenomeSpace urls such as
        # https://dm.genomespace.org/datamanager/file/Home redirect to
        # https://gsui.genomespace.org/datamanager/v1.0/file/Home/ before
        # redirecting to the actual storage URL.
        # Therefore, keep checking the response headers till it
        # no longer matches an API URL.
        redirect_count = 0
        while gs_glob.is_genomespace_url(response.headers['Location']):
            response = self._api_generic_request(requests.get,
                                                 response.headers['Location'],
                                                 allow_redirects=False)
            if redirect_count > 4:
                raise GSClientException("Too many redirects while trying to"
                                        " fetch: {}".format(genomespace_url))
            redirect_count += 1

        return response.headers

    def _upload(self, source, destination):
        for f in glob.iglob(source):
            self._upload_single_file(f, destination)

    def _upload_single_file(self, source, destination):
        destination = self._infer_dest_filename(source, destination)
        upload_info = self._get_upload_info(destination)
        handler = storage_handlers.create_handler(
            upload_info.get("uploadType"))
        handler.upload(source, upload_info)

    def _download(self, source, destination):
        for f in gs_glob.gs_iglob(self, source):
            self._download_single_file(f, destination)

    def _download_single_file(self, source, destination):
        destination = self._infer_dest_filename(source, destination)
        download_info = self._get_download_info(source)
        storage_type = gs_glob.GENOMESPACE_URL_REGEX.match(
            source).group(4)
        handler = storage_handlers.create_handler(storage_type)
        handler.download(download_info, destination)

    def _is_dir_path(self, path):
        if path and path.endswith("/"):
            return True
        return False

    def copy(self, source, destination):
        """
        Copies a file to/from/within GenomeSpace.

        E.g.

        client.copy("/tmp/local_file.txt",
        "https://dm.genomespace.org/datamanager/v1.0/file/Home/MyBucket/hello.txt")

        :type source: :class:`str`
        :param source: Local filename or GenomeSpace URL of source file.

        :type destination: :class:`str`
        :param destination: Local filename or GenomeSpace URL of destination
                            file.

        """
        log.debug("copy: %s -> %s", source, destination)
        if self._is_dir_path(source) and not self.is_dir_path(destination):
            raise GSClientException(
                "Source is a folder, and therefore, the destination must also"
                " be a folder.")

        if gs_glob.is_genomespace_url(
                source) and gs_glob.is_genomespace_url(destination):
            self._internal_copy(source, destination)
        elif gs_glob.is_genomespace_url(
                source) and not gs_glob.is_genomespace_url(destination):
            self._download(source, destination)
        elif not gs_glob.is_genomespace_url(
                source) and gs_glob.is_genomespace_url(destination):
            self._upload(source, destination)
        else:
            raise GSClientException(
                "Either source or destination must be a valid GenomeSpace"
                " location")

    def move(self, source, destination):
        """
        Moves a file within GenomeSpace.

        E.g.

        client.move("https://dm.genomespace.org/datamanager/v1.0/file/Home/MyBucket/hello.txt",
        "https://dm.genomespace.org/datamanager/v1.0/file/Home/MyBucket/world.txt")

        :type source: :str:
        :param source: GenomeSpace URL of source file. Cannot be a local file.

        :type destination: :str:
        :param destination: Local filename or GenomeSpace URL of destination
                            file. If destination is a local file, the file
                            will be copied to the destination and the source
                            file deleted.
        """
        log.debug("move: %s -> %s", source, destination)
        if gs_glob.is_genomespace_url(source):
            self.copy(source, destination)
            self.delete(source)
        else:
            raise GSClientException(
                "Source must be a valid GenomeSpace location")

    def list(self, genomespace_url):
        """
        Returns a list of files within a GenomeSpace folder.

        E.g.

        client.list("https://dm.genomespace.org/datamanager/v1.0/file/Home/MyBucket/")

        :type genomespace_url: :class:`str`
        :param genomespace_url: GenomeSpace URL of folder to list.

        :rtype:  :class:`dict`
        :return: a JSON dict in the format documented here:
                 http://www.genomespace.org/support/api/restful-access-to-dm#appendix_b
        """
        log.debug("list: %s", genomespace_url)
        json_data = self._api_get_request(genomespace_url)
        return GSDirectoryListing.from_json(json_data)

    def delete(self, genomespace_url):
        """
        Deletes a file within a GenomeSpace folder.

        E.g.

        client.delete("https://dm.genomespace.org/datamanager/v1.0/file/Home/MyBucket/world.txt")

        :type genomespace_url: :class:`str`
        :param genomespace_url: GenomeSpace URL of file to delete.
        """
        log.debug("delete: %s", genomespace_url)
        for f in gs_glob.gs_iglob(self, genomespace_url):
            self._delete_single_file(f)

    def _delete_single_file(self, genomespace_url):
        return self._api_delete_request(genomespace_url)

    def isdir(self, genomespace_url):
        try:
            entries = self.list(genomespace_url)
            if entries['directory'] and entries['directory']['isDirectory']:
                return True
        except HTTPError as e:
            if e.status_code == 404:
                return False
        except GSClientException:
            return False
        return False

    def mkdir(self, genomespace_url, create_path=True):
        """
        Creates a folder at a given location.

        E.g.
        client.mkdir("https://dm.genomespace.org/datamanager/v1.0/file/Home/MyBucket/Folder1")

        :type genomespace_url: :class:`str`
        :param genomespace_url: GenomeSpace URL of file to delete.

        :type create_path: :class:`boolean`
        :param create_path: Create intermediate directories as required.
        """
        log.debug("mkdir: %s", genomespace_url)
        if create_path:
            dirname, _ = os.path.split(genomespace_url)
            if not gs_glob.is_genomespace_url(dirname):
                return
            else:
                self.mkdir(dirname, create_path)

        return self._api_put_request(genomespace_url,
                                     body='{"isDirectory": true}')

    def get_metadata(self, genomespace_url):
        """
        Gets metadata information of a genomespace file/folder. See:
        http://www.genomespace.org/support/api/restful-access-to-dm#file_metadata

        E.g.

        client.get_metadata("https://dm.genomespace.org/datamanager/v1.0/file/Home/MyBucket/world.txt")

        :type genomespace_url: :class:`str`
        :param genomespace_url: GenomeSpace URL of file to delete.

        :rtype:  :class:`dict`
        :return: a JSON dict in the format documented here:
                 http://www.genomespace.org/support/api/restful-access-to-dm#appendix_b
        """
        log.debug("get_metadata: %s", genomespace_url)
        url = re.sub(r"((http[s]?://.*/datamanager/)(v[0-9]+.[0-9]+/)?file)",
                     r'\g<2>v1.0/filemetadata', genomespace_url)
        json_data = self._api_get_request(url)
        return GSFileMetadata.from_json(json_data)
