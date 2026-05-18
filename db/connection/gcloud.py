
import os 
from google.cloud import storage
from google.auth.exceptions import RefreshError
from google.api_core.exceptions import Forbidden, NotFound, GoogleAPICallError
import json
from typing import Tuple, List, Optional

import logging, sys

logger = logging.getLogger(__name__)

if not logger.handlers: 
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    ))
    logger.addHandler(handler)

logger.setLevel(logging.INFO)
logger.propagate = False  

# Silence gpt logs
for name in ("LiteLLM", "LiteLLM Proxy", "LiteLLM Router"):
    lg = logging.getLogger(name)
    lg.setLevel(logging.ERROR)
    for h in lg.handlers:
        h.setLevel(logging.ERROR)


class GcloudConnection:
    def __init__(self, bucket_name: str):
        """
        Initialize a connection to a Google Cloud Storage (GCS) bucket.

        This constructor creates a GCS client, retrieves the bucket reference,
        and validates its existence. It logs connection status and raises
        exceptions if authentication or connectivity fails.

        Args:
            bucket_name (str): The name of the GCS bucket to connect to.

        Attributes:
            bucket_name (str): Stores the bucket name provided by the caller.
            client (google.cloud.storage.Client): GCS client instance.
            bucket (google.cloud.storage.bucket.Bucket): Reference to the target bucket.

        """
        self.bucket_name = bucket_name
        self.client = None
        self.bucket = None

    def connect(self):
        """
        Establish a connection to the configured Google Cloud Storage bucket.

        This method initializes the storage client and validates that the target
        bucket exists and is accessible. Raises an exception if authentication or
        permission errors occur.

        Raises:
            RefreshError: If authentication fails.
            Forbidden: If access to the bucket is denied (403).
            NotFound: If the bucket does not exist (404).
            GoogleAPICallError: For other API-level errors.
            Exception: For any unexpected error during connection.
        """
        

        try:
            logger.info(f"Connecting to GCS bucket '{self.bucket_name}'")
            self.client = storage.Client()  
            user_info = self.client._credentials.service_account_email
            self.bucket = self.client.bucket(self.bucket_name)

            if not self.bucket.exists(timeout=180):
                raise NotFound(f"Bucket {self.bucket_name} not found")

            logger.info(f"SUCCESS to Connected in GCS bucket '{self.bucket_name}'")
            logger.info(f"The SA - {user_info} is authenticated")


        except RefreshError:
            logger.error("Auth failed (RefreshError). Verifique a Service Account e papéis no GCS.")
            raise
        except Forbidden as e:   # 403
            logger.error(f"Permission denied (403): {e}")
            raise
        except NotFound as e:    # 404 
            logger.error(f"Bucket not found (404): {e}")
            raise
        except GoogleAPICallError as e: 
            logger.error(f"GCS API error: {e}")
            raise
        except Exception:
            logger.exception("Fail to Connect")
            raise
            

    def _read_text(self, path: str, encoding: str = "utf-8") -> str:
        """
        Read the contents of a text file stored in Google Cloud Storage.

        Args:
            path (str): The object path inside the GCS bucket (e.g., "folder/file.txt").
            encoding (str, optional): The character encoding to use for decoding
                the text. Defaults to "utf-8".

        Returns:
            str: The file contents as a decoded text string.

        """
        try:
            blob = self.bucket.blob(path)
            return blob.download_as_text(encoding=encoding)
        except Exception as e: 
            logger.exception(f"Fail in read text from gs://{self.bucket_name}/{path}")
            raise

    
    def read_json(self, path: str, encoding: str = "utf-8"):
        """
        Read a JSON file from Google Cloud Storage and parse it into a Python object.

        Args:
            path (str): The object path inside the GCS bucket (e.g., "folder/data.json").
            encoding (str, optional): The character encoding to use for decoding
                the text before parsing. Defaults to "utf-8".

        Returns:
            Any: A Python object (dict, list, etc.) resulting from `json.loads`.

        """
        try: 
            logger.info(f"Getting Json from '{path}'")
            text = self._read_text(path, encoding=encoding)
            return json.loads(text) 
        
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalide Json format from gs://{self.bucket_name}/{path}, "
                             f"Error: {e}")
        except Exception as e: 
            logger.exception(f"Fail in read_json gs://{self.bucket_name}/{path},"
                            f"Error {e}")
            raise
    
    def download_transcript(self, gcs_transcript_path: str, category_name: str, topic_name: str, dest_dir: str | None = None) -> str:
        """
        Download a JSON file from Google Cloud Storage to a local path.

        The file will be saved under:
        transcript_data/<category_name>/<topic_name>/transcript.json

        Args:
            gcs_transcript_path (str): Path of the JSON transcript file in the GCS bucket.
            category_name (str): The category name (used for local folder structure).
            topic_name (str): The topic name (used for local folder structure).

        Returns:
            str: Local file path where the JSON transcript was saved.
        """
        try:
            # choose local destination
            if dest_dir:
                local_dir = dest_dir
            else:
                local_dir = os.path.join("transcript_data", category_name, topic_name)

            os.makedirs(local_dir, exist_ok=True)
            local_file_path = os.path.join(local_dir, "transcript.json")

            logger.info(f"Downloading JSON from 'gs://{self.bucket_name}/{gcs_transcript_path}' to '{local_file_path}'")

            blob = self.bucket.blob(gcs_transcript_path)
            text = blob.download_as_text(encoding="utf-8")

            with open(local_file_path, "w", encoding="utf-8") as f:
                f.write(text)

            logger.info(f"Transcript successfully downloaded to {local_file_path}")
            return os.path.abspath(local_file_path)

        except NotFound:
            logger.error(f"File not found: gs://{self.bucket_name}/{gcs_transcript_path}")
            raise
        except Forbidden as e:
            logger.error(f"Permission denied to access: gs://{self.bucket_name}/{gcs_transcript_path} ({e})")
            raise
        except GoogleAPICallError as e:
            logger.error(f"GCS API error while downloading gs://{self.bucket_name}/{gcs_transcript_path}: {e}")
            raise
        except Exception as e:
            logger.exception(f"Fail to download JSON from gs://{self.bucket_name}/{gcs_transcript_path}: {e}")
            raise

    def upload_json(self, local_file_path: str, storage_path: str, overwrite: bool = True, validate: bool = False, encoding: str = "utf-8") -> None:
        """
        Upload a JSON file to Google Cloud Storage.

        This method can optionally validate the local JSON file before uploading.
        The object will be stored with MIME type `application/json`.

        Args:
            local_file_path (str): Path to the local JSON file to upload.
            storage_path (str): Destination object path inside the GCS bucket
                (e.g., "folder/data.json").
            overwrite (bool, optional): Whether to overwrite the file if it already exists.
                - True: Always overwrite (default).
                - False: Fail if the object already exists.
            validate (bool, optional): If True, attempts to parse the local file as JSON
                before upload to ensure it is valid. Defaults to False.
            encoding (str, optional): File encoding used for validation. Defaults to "utf-8".
        """
        if not os.path.exists(local_file_path):
            raise FileNotFoundError(local_file_path)

        storage_path = storage_path.replace("\\", "/")

        if validate:
            try:
                with open(local_file_path, "r", encoding=encoding) as f:
                    json.load(f)  
            except json.JSONDecodeError as e:
                raise ValueError(f"Invalide JSON {local_file_path}: {e}") from e

        blob = self.bucket.blob(storage_path)
        try:
            if overwrite:
                blob.upload_from_filename(local_file_path, content_type="application/json")
            else:
                blob.upload_from_filename(
                    local_file_path,
                    content_type="application/json",
                    if_generation_match=0,
                )
        except Exception as e:
            raise RuntimeError(
                f"Fail to upload  gs://{self.bucket_name}/{storage_path}: {e}"
            ) from e

        logger.info(f"Success to upload gs://{self.bucket_name}/{storage_path}")


    def upload_podcast(self, local_file_path: str, storage_path: str, overwrite: bool = True) -> None:
        """
        Upload a local podcast MP3 file to Google Cloud Storage.

        The file is uploaded with MIME type `audio/mpeg`.

        Args:
            local_file_path (str): Path to the local `.mp3` file to upload.
            storage_path (str): Destination object path inside the GCS bucket
                (e.g., "content/brain_health/topic/digital_detox/podcast/1234/edited_podcast/podcast.mp3").
            overwrite (bool, optional): Whether to overwrite the file if it already exists.
                - True: Always overwrite (default).
                - False: Fail if the object already exists.
        """

        storage_path = storage_path.replace("\\", "/")

        blob = self.bucket.blob(storage_path)

        try:
            if overwrite:
                blob.upload_from_filename(local_file_path, content_type="audio/mpeg")
            else:
                blob.upload_from_filename(local_file_path, content_type="audio/mpeg", if_generation_match=0)
        except Exception as e: 
            raise RuntimeError(
                f"Fail to upload  gs://{self.bucket_name}/{storage_path}: {e}"
            ) from e

        logger.info(f"Success to upload to gs://{self.bucket_name}/{storage_path}")


    def _find_podcast_prefix_by_id(self, env: str, category_name: str, topic_name: str, podcast_id: str) -> str | None:
        """
        Find the full GCS prefix (path) for a given podcast_id.

        This method searches under the given environment, category and topic,
        traverses the date directories, and finds the prefix that matches
        the given podcast_id.

        Args:
            env (str): Environment prefix (e.g., "dev", "prod").
            category_name (str): The category name of the podcast.
            topic_name (str): The topic name of the podcast.
            podcast_id (str): The identifier of the podcast.

        Returns:
            str | None: The full prefix for the given podcast_id if found,
            otherwise None."""
        # podcast base prefix
        base_prefix = f"{env}/category/{category_name}/topic/{topic_name}/podcast/"
        
        #first level of lv1
        # subdirectories of .../podcast/
        lvl1 = self.bucket.list_blobs(prefix=base_prefix, delimiter="/")
        lvl1_subdirs = set()
        for page in lvl1.pages:
            lvl1_subdirs.update(page.prefixes)

        # lvl1 subdirs is a set of date directories # eg.: .../podcast/2025-09-22/
        for sub in sorted(lvl1_subdirs):  
            it = self.bucket.list_blobs(prefix=sub, delimiter="/")
            for page in it.pages:
                for sub2 in page.prefixes: 
                    if sub2.rstrip("/").split("/")[-1] == podcast_id:
                        return sub2
        return None

    def list_podcast_files(self,env: str, category_name: str, topic_name: str, podcast_id: str) -> Tuple[List[str], Optional[str]]:
        """
        List the all paths that contains inside the podcast_id;
        
        Args:
            env (str): Environment prefix (e.g., "dev", "prod").
            category_name (str): The category name of the given podcast ID.
            topic_name (str): The topic name of the given podcast ID.
            podcast_id (str): The podcast identifier related to a specific podcast.
        
        """
        try: 
            prefix = self._find_podcast_prefix_by_id( env, category_name,  topic_name, podcast_id)
            if prefix is None: 
                raise FileNotFoundError(
                    f"No path found for podcast_id='{podcast_id}' in env='{env}'. "
                    f"Verify the podcast_id and environment."
                )
            
            paths = []
            for b in self.bucket.list_blobs(prefix=prefix):
                name = b.name
                if name.endswith("edited_podcast/podcast.mp3") or name.endswith("/transcript.json"):
                    paths.append(name)

            date = next((p.split("/")[-3] for p in paths if p.endswith("transcript.json")), None)
            if date is None:
                raise FileNotFoundError(
                    f"Could not determine date folder for podcast_id='{podcast_id}' in env='{env}'. "
                    "No transcript.json found or invalid GCS path schema."
                )
            return paths, date
                
        except Exception as e:
            logger.exception(f"Error while listing podcast files: {e}")
            raise
