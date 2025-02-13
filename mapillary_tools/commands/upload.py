import inspect

from .. import constants
from ..upload import upload_multiple


class Command:
    name = "upload"
    help = "upload images to Mapillary"

    @staticmethod
    def add_common_upload_options(group):
        group.add_argument(
            "--user_name",
            help="The Mapillary user account to upload to. If you only have one account authorized, it will upload to that account by default.",
            required=False,
        )
        group.add_argument(
            "--organization_key",
            help="The Mapillary organization ID to upload to.",
            default=None,
            required=False,
        )
        group.add_argument(
            "--dry_run",
            help='Instead of uploading to the Mapillary server, simulate uploading to the local directory "mapillary_public_uploads" for debugging purposes.',
            action="store_true",
            default=False,
            required=False,
        )

    def add_basic_arguments(self, parser):
        group = parser.add_argument_group(
            f"{constants.ANSI_BOLD}UPLOAD OPTIONS{constants.ANSI_RESET_ALL}"
        )
        group.add_argument(
            "--desc_path",
            help=f"Path to the image description file. Only works for uploading image directories. [default: {{IMPORT_PATH}}/{constants.IMAGE_DESCRIPTION_FILENAME}]",
            default=None,
            required=False,
        )
        Command.add_common_upload_options(group)

    def run(self, vars_args: dict):
        args = {
            k: v
            for k, v in vars_args.items()
            if k in inspect.getfullargspec(upload_multiple).args
        }
        args["file_type"] = "images"
        upload_multiple(**args)
