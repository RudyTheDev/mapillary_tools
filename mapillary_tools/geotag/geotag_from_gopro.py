import logging
import os
import typing as T
from pathlib import Path

from tqdm import tqdm

from .. import constants, exceptions, geo, types, utils
from . import gpmf_parser, utils as geotag_utils
from .geotag_from_generic import GeotagFromGeneric

from .geotag_from_gpx import GeotagFromGPXWithProgress


LOG = logging.getLogger(__name__)


class GeotagFromGoPro(GeotagFromGeneric):
    def __init__(
        self,
        image_dir: str,
        source_path: str,
        offset_time: float = 0.0,
    ):
        self.image_dir = image_dir
        if os.path.isdir(source_path):
            self.videos = utils.get_video_file_list(source_path, abs_path=True)
        else:
            # it is okay to not suffix with .mp4
            self.videos = [source_path]
        self.offset_time = offset_time
        super().__init__()

    def _filter_noisy_points(
        self, points: T.Sequence[gpmf_parser.PointWithFix], video: Path
    ) -> T.Sequence[gpmf_parser.PointWithFix]:
        num_points = len(points)
        points = [
            p
            for p in points
            if p.gps_fix is not None and p.gps_fix.value in constants.GOPRO_GPS_FIXES
        ]
        if len(points) < num_points:
            LOG.warning(
                "Removed %d points with the GPS fix not in %s from %s",
                num_points - len(points),
                constants.GOPRO_GPS_FIXES,
                video,
            )

        num_points = len(points)
        points = [
            p
            for p in points
            if p.gps_precision is not None
            and p.gps_precision <= constants.GOPRO_MAX_GPS_PRECISION
        ]
        if len(points) < num_points:
            LOG.warning(
                "Removed %d points with DoP value higher than %d from %s",
                num_points - len(points),
                constants.GOPRO_MAX_GPS_PRECISION,
                video,
            )

        return points

    def to_description(self) -> T.List[types.ImageDescriptionFileOrError]:
        descs: T.List[types.ImageDescriptionFileOrError] = []

        images = utils.get_image_file_list(self.image_dir)
        for video in self.videos:
            LOG.debug("Processing GoPro video: %s", video)

            sample_images = utils.filter_video_samples(images, video)
            LOG.debug(
                "Found %d sample images from video %s",
                len(sample_images),
                video,
            )

            if not sample_images:
                continue

            points = self._filter_noisy_points(
                gpmf_parser.parse_gpx(Path(video)), Path(video)
            )

            # bypass empty points to raise MapillaryGPXEmptyError
            if points and geotag_utils.is_video_stationary(
                geo.get_max_distance_from_start([(p.lat, p.lon) for p in points])
            ):
                LOG.warning(
                    "Fail %d sample images due to stationary video %s",
                    len(sample_images),
                    video,
                )
                for image in sample_images:
                    err = types.describe_error(
                        exceptions.MapillaryStationaryVideoError(
                            "Stationary GoPro video"
                        )
                    )
                    descs.append({"error": err, "filename": image})
                continue

            with tqdm(
                total=len(sample_images),
                desc=f"Interpolating {os.path.basename(video)}",
                unit="images",
                disable=LOG.getEffectiveLevel() <= logging.DEBUG,
            ) as pbar:
                geotag = GeotagFromGPXWithProgress(
                    self.image_dir,
                    sample_images,
                    points,
                    use_gpx_start_time=False,
                    use_image_start_time=True,
                    offset_time=self.offset_time,
                    progress_bar=pbar,
                )
                descs.extend(geotag.to_description())

        return descs
