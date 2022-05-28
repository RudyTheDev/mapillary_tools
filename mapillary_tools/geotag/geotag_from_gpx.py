import os
import typing as T
import datetime
import logging

from .geotag_from_generic import GeotagFromGeneric
from .. import types
from ..exceptions import (
    MapillaryGeoTaggingError,
    MapillaryOutsideGPXTrackError,
    MapillaryGPXEmptyError,
)
from ..exif_read import ExifRead
from ..geo import interpolate_lat_lon, Point, interpolate_idx


LOG = logging.getLogger(__name__)


class GeotagFromGPX(GeotagFromGeneric):
    def __init__(
        self,
        image_dir: str,
        images: T.List[str],
        points: T.List[types.GPXPoint],
        use_gpx_start_time: bool = False,
        offset_time: float = 0.0,
    ):
        super().__init__()
        self.image_dir = image_dir
        self.images = images
        self.points = points
        self.use_gpx_start_time = use_gpx_start_time
        self.offset_time = offset_time

    def read_image_capture_time(self, image: str) -> T.Optional[datetime.datetime]:
        image_path = os.path.join(self.image_dir, image)
        return ExifRead(image_path).extract_capture_time()

    def to_description(self) -> T.List[types.ImageDescriptionFileOrError]:
        descs: T.List[types.ImageDescriptionFileOrError] = []

        if not self.points:
            exc = MapillaryGPXEmptyError("Empty GPS extracted from the geotag source")
            for image in self.images:
                descs.append(
                    {
                        "error": types.describe_error(exc),
                        "filename": image,
                    }
                )
            return descs

        # pairing the timestamp and the image for sorting
        image_pairs = []
        for image in self.images:
            try:
                capture_time = self.read_image_capture_time(image)
            except Exception as exc:
                descs.append({"error": types.describe_error(exc), "filename": image})
                continue

            if capture_time is None:
                error = types.describe_error(
                    MapillaryGeoTaggingError(
                        "No capture time found from the image for interpolation"
                    )
                )
                descs.append({"error": error, "filename": image})
            else:
                image_pairs.append((capture_time, image))

        track = sorted(self.points, key=lambda p: p.time)
        sorted_images = sorted(image_pairs)

        image_time_offset = self.offset_time
        LOG.debug("Initial time offset for interpolation: %s", image_time_offset)

        if self.use_gpx_start_time:
            if sorted_images and track:
                # assume: the ordered image timestamps are [2, 3, 4, 5]
                # the ordered gpx timestamps are [5, 6, 7, 8]
                # then the offset will be 5 - 2 = 3
                time_delta = track[0].time - sorted_images[0][0]

                # my hackery - extra logs
                LOG.debug("First GPX point time is: %s", track[0].time)
                LOG.debug("First image time is: %s", sorted_images[0][0])
                
                # original:
                #LOG.debug("GPX start time delta: %s", time_delta)
                
                # my hackery - readable time when "negative"
                if time_delta.total_seconds() > 0:
                    LOG.debug("GPX start time delta: %s", time_delta)
                else:
                    LOG.debug("GPX start time delta: -%s", -time_delta) # because apparently python's negative date prints weirdness like "-1 day, 21:59:27.169000" for what is actually negative "2:00:32.831000", so just making something readable  
                
                image_time_offset += time_delta.total_seconds()

        LOG.debug("Final time offset for interpolation: %s sec", image_time_offset)

        # same thing but different type
        sorted_points = [
            Point(lat=p.lat, lon=p.lon, alt=p.alt, time=p.time, angle=None)
            for p in track
        ]
        
        # my hackery - gpx timestamps
        LOG.debug("GPX start timestamp: %s", types.datetime_to_map_capture_time(sorted_points[0].time))
        LOG.debug("GPX end timestamp:   %s", types.datetime_to_map_capture_time(sorted_points[-1].time))

        for exif_time, image in sorted_images:
            exif_time = exif_time + datetime.timedelta(seconds=image_time_offset)

            # my hackery - extra spammy per-image timestamp
            #LOG.debug("Image timestamp (offset): %s", exif_time)

            if exif_time < sorted_points[0].time:
                delta = sorted_points[0].time - exif_time
                exc2 = MapillaryOutsideGPXTrackError(
                    f"The image timestamp is {round(delta.total_seconds(), 2)} seconds behind the GPX start point",
                    image_time=types.datetime_to_map_capture_time(exif_time),
                    gpx_start_time=types.datetime_to_map_capture_time(
                        sorted_points[0].time
                    ),
                    gpx_end_time=types.datetime_to_map_capture_time(
                        sorted_points[-1].time
                    ),
                )
                descs.append({"error": types.describe_error(exc2), "filename": image})
                continue

            if sorted_points[-1].time < exif_time:
                delta = exif_time - sorted_points[-1].time
                exc2 = MapillaryOutsideGPXTrackError(
                    f"The image timestamp is {round(delta.total_seconds(), 2)} seconds beyond the GPX end point",
                    image_time=types.datetime_to_map_capture_time(exif_time),
                    gpx_start_time=types.datetime_to_map_capture_time(
                        sorted_points[0].time
                    ),
                    gpx_end_time=types.datetime_to_map_capture_time(
                        sorted_points[-1].time
                    ),
                )
                descs.append({"error": types.describe_error(exc2), "filename": image})
                continue

            interpolated = interpolate_lat_lon(sorted_points, exif_time)
            
            # my hackery - resulting interpolated point
            idx = interpolate_idx(sorted_points, exif_time)
            LOG.debug(f"GPX interpolated point lat, lon: {round(interpolated.lat, 6)}, {round(interpolated.lon, 6)} ({idx} / {len(sorted_points)}) @ {exif_time}")
            
            point = types.GPXPointAngle(
                point=types.GPXPoint(
                    time=exif_time,
                    lon=interpolated.lon,
                    lat=interpolated.lat,
                    alt=interpolated.alt,
                ),
                angle=interpolated.angle,
            )
            descs.append(
                T.cast(
                    types.ImageDescriptionFile, {**point.as_desc(), "filename": image}
                )
            )

        assert len(descs) == len(self.images)

        return descs


class GeotagFromGPXWithProgress(GeotagFromGPX):
    def __init__(
        self,
        image_dir: str,
        images: T.List[str],
        points: T.List[types.GPXPoint],
        use_gpx_start_time: bool = False,
        offset_time: float = 0.0,
        progress_bar=None,
    ) -> None:
        super().__init__(
            image_dir,
            images,
            points,
            use_gpx_start_time=use_gpx_start_time,
            offset_time=offset_time,
        )
        self._progress_bar = progress_bar

    def read_image_capture_time(self, image: str) -> T.Optional[datetime.datetime]:
        try:
            capture_time = super().read_image_capture_time(image)
        finally:
            if self._progress_bar:
                self._progress_bar.update(1)
        return capture_time
