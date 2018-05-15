import datetime
import uuid
import os
import json
import time
import sys
import shutil
import hashlib
import base64
from collections import OrderedDict
from exif_read import ExifRead
from exif_write import ExifEdit
from exif_aux import verify_exif
from geo import normalize_bearing, interpolate_lat_lon, gps_distance
import config
import uploader
from dateutil.tz import tzlocal
from gps_parser import get_lat_lon_time_from_gpx


STATUS_PAIRS = {"success": "failed",
                "failed": "success"
                }
'''
auxillary processing functions
'''


def exif_time(filename):
    '''
    Get image capture time from exif
    '''
    metadata = ExifRead(filename)
    return metadata.extract_capture_time()


def timestamp_from_filename(filename,
                            start_time,
                            video_sample_interval=1,
                            video_duration=None,
                            video_duration_ratio=1.0):
    seconds = (int(filename.lstrip("0").rstrip(".jpg"))) * \
        video_sample_interval * video_duration_ratio
    if video_duration:
        if seconds > video_duration:
            seconds = video_duration
    return start_time + datetime.timedelta(seconds=seconds)


def timestamps_from_filename(full_image_list,
                             video_start_time,
                             video_sample_interval=1,
                             video_duration=None,
                             video_duration_ratio=1.0):
    capture_times = []
    for image in full_image_list:
        capture_times.append(timestamp_from_filename(os.path.basename(image),
                                                     video_start_time,
                                                     video_sample_interval,
                                                     video_duration,
                                                     video_duration_ratio))
    return capture_times


def estimate_sub_second_time(files, interval=0.0):
    '''
    Estimate the capture time of a sequence with sub-second precision
    EXIF times are only given up to a second of precission. This function
    uses the given interval between shots to Estimate the time inside that
    second that each picture was taken.
    '''
    if interval <= 0.0:
        return [exif_time(f) for f in files]

    onesecond = datetime.timedelta(seconds=1.0)
    T = datetime.timedelta(seconds=interval)
    for i, f in enumerate(files):
        m = exif_time(f)
        if i == 0:
            smin = m
            smax = m + onesecond
        else:
            m0 = m - T * i
            smin = max(smin, m0)
            smax = min(smax, m0 + onesecond)

    if smin > smax:
        print('Interval not compatible with EXIF times')
        return None
    else:
        s = smin + (smax - smin) / 2
        return [s + T * i for i in range(len(files))]


def geotag_from_exif(process_file_list,
                     import_path,
                     offset_angle=0.0,
                     verbose=False):

    for image in process_file_list:
        geotag_properties = get_geotag_properties_from_exif(
            image, offset_angle)

        create_and_log_process(image,
                               import_path,
                               "geotag_process",
                               "success",
                               geotag_properties,
                               verbose)


def get_geotag_properties_from_exif(image, offset_angle=0.0):
    try:
        exif = ExifRead(image)
    except:
        print("Error, EXIF could not be read for image " +
              image + ", geotagging process failed for this image since gps/time properties not read.")
        return None
    # required tags
    try:
        lon, lat = exif.extract_lon_lat()
    except:
        print("Error, " + image +
              " image latitude or longitude tag not in EXIF. Geotagging process failed for this image, since this is required information.")
        return None
    if lat != None and lon != None:
        geotag_properties = {"MAPLatitude": lat}
        geotag_properties["MAPLongitude"] = lon
    else:
        print("Error, " + image + " image latitude or longitude tag not in EXIF. Geotagging process failed for this image, since this is required information.")
        return None
    try:
        timestamp = exif.extract_capture_time()
    except:
        print("Error, " + image +
              " image capture time tag not in EXIF. Geotagging process failed for this image, since this is required information.")
        return None
    geotag_properties["MAPCaptureTime"] = datetime.datetime.strftime(
        timestamp, "%Y_%m_%d_%H_%M_%S_%f")[:-3]

    # optional fields
    try:
        geotag_properties["MAPAltitude"] = exif.extract_altitude()
    except:
        if verbose:
            print("Warning, image altitude tag not in EXIF.")
    try:
        heading = exif.extract_direction()
        if heading is None:
            heading = 0.0
        heading = normalize_bearing(heading + offset_angle)
        # bearing of the image
        geotag_properties["MAPCompassHeading"] = {"TrueHeading": heading,
                                                  "MagneticHeading": heading}
    except:
        if verbose:
            print("Warning, image direction tag not in EXIF.")

    return geotag_properties


def geotag_from_gpx(process_file_list,
                    import_path,
                    geotag_source_path,
                    offset_time=0.0,
                    offset_angle=0.0,
                    local_time=False,
                    sub_second_interval=1.0,
                    timestamp_from_filename=False,
                    use_gps_start_time=False,
                    verbose=False,
                    video_start_time=None,
                    video_duration=None,
                    video_duration_ratio=1.0,
                    video_sample_interval=1.0):

    # print time now to warn in case local_time
    if local_time:
        now = datetime.datetime.now(tzlocal())
        if verbose:
            print("Your local timezone is {0}. If not, the geotags will be wrong."
                  .format(now.strftime('%Y-%m-%d %H:%M:%S %Z')))
    else:
        # if not local time to be used, warn UTC will be used
        if verbose:
            print(
                "It is assumed that the image timestamps are in UTC. If not, try using the option --local_time.")

    # read gpx file to get track locations
    gpx = get_lat_lon_time_from_gpx(geotag_source_path,
                                    local_time)

    # Estimate capture time with sub-second precision, reading from image EXIF
    # or estimating from filename
    if timestamp_from_filename:
        if use_gps_start_time or not video_start_time:
            video_start_time = gpx[0][0]

        sub_second_times = timestamps_from_filename(process_file_list,
                                                    video_start_time,
                                                    video_sample_interval,
                                                    video_duration,
                                                    video_duration_ratio)
    else:
        sub_second_times = estimate_sub_second_time(process_file_list,
                                                    sub_second_interval)
    if not sub_second_times:
        print("Error, capture times could not be estimated to sub second precision, images can not be geotagged.")
        create_and_log_process_in_list(process_file_list,
                                       import_path,
                                       "geotag_process"
                                       "failed",
                                       verbose)
        return

    if not gpx:
        print("Error, gpx file was not read, images can not be geotagged.")
        create_and_log_process_in_list(process_file_list,
                                       import_path,
                                       "geotag_process"
                                       "failed",
                                       verbose)
        return

    for image, capture_time in zip(process_file_list,
                                   sub_second_times):

        geotag_properties = get_geotag_properties_from_gpx(
            image, capture_time, gpx, offset_angle, offset_time, verbose)

        create_and_log_process(image,
                               import_path,
                               "geotag_process",
                               "success",
                               geotag_properties,
                               verbose)


def get_geotag_properties_from_gpx(image, capture_time, gpx, offset_angle=0.0, offset_time=0.0, verbose=False):

    capture_time = capture_time - \
        datetime.timedelta(seconds=offset_time)
    try:
        lat, lon, bearing, elevation = interpolate_lat_lon(gpx,
                                                           capture_time)
    except Exception as e:
        if verbose:
            print(
                "Warning, {}, interpolation of latitude and longitude failed for image {}".format(e, image))
        return None

    corrected_bearing = (bearing + offset_angle) % 360

    if lat != None and lon != None:
        geotag_properties = {"MAPLatitude": lat}
        geotag_properties["MAPLongitude"] = lon
    else:
        if verbose:
            print(
                "Warning, invalid latitude and longitude for image {}".format(image))
        return None

    geotag_properties["MAPCaptureTime"] = datetime.datetime.strftime(capture_time,
                                                                     "%Y_%m_%d_%H_%M_%S_%f")[:-3]
    if elevation:
        geotag_properties["MAPAltitude"] = elevation
    else:
        if verbose:
            print("Warning, image altitude tag not set.")
    if corrected_bearing:
        geotag_properties["MAPCompassHeading"] = {
            "TrueHeading": corrected_bearing, "MagneticHeading": corrected_bearing}
    else:
        if verbose:
            print("Warning, image direction tag not set.")
    return geotag_properties


def geotag_from_csv(process_file_list,
                    import_path,
                    geotag_source_path,
                    offset_time,
                    offset_angle,
                    verbose=False):
    pass


def geotag_from_json(process_file_list,
                     import_path,
                     geotag_source_path,
                     offset_time,
                     offset_angle,
                     verbose=False):
    pass


def get_upload_param_properties(log_root, image, user_name, user_upload_token, user_permission_hash, user_signature_hash, user_email, verbose=False):

    if not os.path.isdir(log_root):
        if verbose:
            print("Warning, sequence process has not been done for image " + image +
                  ", therefore it will not be included in the upload params processing.")
        return None

    # check if geotag process was a success
    log_sequence_process_success = os.path.join(
        log_root, "sequence_process_success")
    if not os.path.isfile(log_sequence_process_success):
        if verbose:
            print("Warning, sequence process failed for image " + image +
                  ", therefore it will not be included in the upload params processing.")
        return None

    duplicate_flag_path = os.path.join(log_root,
                                       "duplicate")
    upload_params_process_success_path = os.path.join(log_root,
                                                      "upload_params_process_success")
    if os.path.isfile(duplicate_flag_path):
        if verbose:
            print("Warning, duplicate flag for " + image +
                  ", therefore it will not be included in the upload params processing.")
        return {"duplicate": True}  # hacky

    # load the sequence json
    sequence_process_json_path = os.path.join(log_root,
                                              "sequence_process.json")
    sequence_data = ""
    try:
        sequence_data = load_json(
            sequence_process_json_path)
    except:
        if verbose:
            print("Warning, sequence data not read for image " + image +
                  ", therefore it will not be included in the upload params processing.")
        return None

    if "MAPSequenceUUID" not in sequence_data:
        if verbose:
            print("Warning, sequence uuid not in sequence data for image " + image +
                  ", therefore it will not be included in the upload params processing.")
        return None

    sequence_uuid = sequence_data["MAPSequenceUUID"]
    upload_params = {
        "url": "https://s3-eu-west-1.amazonaws.com/mapillary.uploads.manual.images",
        "permission": user_permission_hash,
        "signature": user_signature_hash,
        "key": user_name + "/" + sequence_uuid + "/"
    }

    try:
        settings_upload_hash = hashlib.sha256("%s%s%s" % (user_upload_token,
                                                          user_email,
                                                          base64.b64encode(image))).hexdigest()
        save_json({"MAPSettingsUploadHash": settings_upload_hash},
                  os.path.join(log_root, "settings_upload_hash.json"))
    except:
        if verbose:
            print("Warning, settings upload hash not set for image " + image +
                  ", therefore it will not be uploaded.")
        return None
    return upload_params


def get_final_mapillary_image_description(log_root, image, master_upload=False, verbose=False):
    sub_commands = ["user_process", "geotag_process", "sequence_process",
                    "upload_params_process", "settings_upload_hash", "import_meta_data_process"]

    final_mapillary_image_description = {}
    for sub_command in sub_commands:
        sub_command_status = os.path.join(
            log_root, sub_command + "_failed")

        if os.path.isfile(sub_command_status) and sub_command != "import_meta_data_process":
            if verbose:
                print("Warning, required {} failed for image ".format(sub_command) +
                      image)
            return None

        sub_command_data_path = os.path.join(
            log_root, sub_command + ".json")
        if not os.path.isfile(sub_command_data_path) and sub_command != "import_meta_data_process":
            if (sub_command == "settings_upload_hash" or sub_command == "upload_params_process") and master_upload:
                continue
            else:
                if verbose:
                    print("Warning, required {} did not result in a valid json file for image ".format(
                        sub_command) + image)
                return None
        if sub_command == "settings_upload_hash" or sub_command == "upload_params_process":
            continue
        try:
            sub_command_data = load_json(sub_command_data_path)
            if not sub_command_data:
                if verbose:
                    print(
                        "Warning, no data read from json file " + json_file)
                return None

            if "MAPSettingsEmail" in sub_command_data:
                del sub_command_data["MAPSettingsEmail"]

            final_mapillary_image_description.update(sub_command_data)
        except:
            if sub_command == "import_meta_data_process":
                if verbose:
                    print("Warning, could not load json file " +
                          sub_command_data_path)
                continue
            else:
                if verbose:
                    print("Warning, could not load json file " +
                          sub_command_data_path)
                return None

    # a unique photo ID to check for duplicates in the backend in case the
    # image gets uploaded more than once
    final_mapillary_image_description['MAPPhotoUUID'] = str(
        uuid.uuid4())

    # insert in the EXIF image description
    try:
        image_exif = ExifEdit(image)
    except:
        print("Error, image EXIF could not be loaded for image " + image)
        return None
    try:
        image_exif.add_image_description(
            final_mapillary_image_description)
    except:
        print(
            "Error, image EXIF tag Image Description could not be edited for image " + image)
        return None
    try:
        image_exif.write()
    except:
        print("Error, image EXIF could not be written back for image " + image)
        return None

    return final_mapillary_image_description


def get_geotag_data(log_root, image, import_path, verbose=False):
    if not os.path.isdir(log_root):
        if verbose:
            print("Warning, no logs for image " + image)
        return None
    # check if geotag process was a success
    log_geotag_process_success = os.path.join(log_root,
                                              "geotag_process_success")
    if not os.path.isfile(log_geotag_process_success):
        if verbose:
            print("Warning, geotag process failed for image " + image +
                  ", therefore it will not be included in the sequence processing.")
        return None
    # load the geotag json
    geotag_process_json_path = os.path.join(log_root,
                                            "geotag_process.json")
    try:
        geotag_data = load_json(geotag_process_json_path)
        return geotag_data
    except:
        if verbose:
            print("Warning, geotag data not read for image " + image +
                  ", therefore it will not be included in the sequence processing.")
        return None


def format_orientation(orientation):
    '''
    Convert orientation from clockwise degrees to exif tag

    # see http://sylvana.net/jpegcrop/exif_orientation.html
    '''
    mapping = {
        0: 1,
        90: 8,
        180: 3,
        270: 6,
    }
    if orientation not in mapping:
        raise ValueError("Orientation value has to be 0, 90, 180, or 270")

    return mapping[orientation]


def load_json(file_path):
    try:
        with open(file_path, "rb") as f:
            dict = json.load(f)
        return dict
    except:
        return {}


def save_json(data, file_path):
    with open(file_path, "wb") as f:
        f.write(json.dumps(data, indent=4))


def update_json(data, file_path, process):
    original_data = load_json(file_path)
    original_data[process] = data
    save_json(original_data, file_path)


def get_process_file_list(import_path, process, rerun=False, verbose=False, skip_subfolders=False):
    process_file_list = []
    if skip_subfolders:
        process_file_list.extend(os.path.join(import_path, file) for file in os.listdir(import_path) if file.lower().endswith(
            ('jpg', 'jpeg', 'png', 'tif', 'tiff', 'pgm', 'pnm', 'gif')) and preform_process(import_path, import_path, file, process, rerun))
    else:
        for root, dir, files in os.walk(import_path):
            process_file_list.extend(os.path.join(root, file) for file in files if preform_process(
                import_path, root, file, process, rerun) and file.lower().endswith(('jpg', 'jpeg', 'png', 'tif', 'tiff', 'pgm', 'pnm', 'gif')))

    if verbose:
        if process != "sequence_process":
            inform_processing_start(import_path,
                                    len(process_file_list),
                                    process)
        else:
            print("Running sequence_process for {} images".format(
                len(process_file_list)))
    return process_file_list


def preform_process(import_path, root, file, process, rerun=False):
    file_path = os.path.join(root, file)
    log_root = uploader.log_rootpath(import_path, file_path)
    process_succes = os.path.join(log_root, process + "_success")
    upload_succes = os.path.join(log_root, "upload_success")
    preform = not os.path.isfile(upload_succes) and (
        not os.path.isfile(process_succes) or rerun)
    return preform


def video_upload(video_file, import_path, verbose=False):
    root_path = os.path.dirname(os.path.abspath(video_file))
    log_root = uploader.log_rootpath(root_path, video_file)
    import_paths = video_import_paths(video_file)
    if os.path.isdir(import_path):
        if verbose:
            print("Warning, {} has already been sampled into {}, previously sampled frames will be deleted".format(
                video_file, import_path))
        shutil.rmtree(import_path)
    if not os.path.isdir(import_path):
        os.makedirs(import_path)
    if import_path not in import_paths:
        import_paths.append(import_path)
    for video_import_path in import_paths:
        if os.path.isdir(video_import_path):
            if len(uploader.get_success_upload_file_list(video_import_path)):
                if verbose:
                    print("no")
                return 1
    return 0


def create_and_log_video_process(video_file, import_path):
    root_path = os.path.dirname(os.path.abspath(video_file))
    log_root = uploader.log_rootpath(root_path, video_file)
    if not os.path.isdir(log_root):
        os.makedirs(log_root)
    # set the log flags for process
    log_process = os.path.join(
        log_root, "video_process.json")
    import_paths = video_import_paths(video_file)
    if import_path in import_paths:
        return
    import_paths.append(import_path)
    video_process = load_json(log_process)
    video_process.update({"sample_paths": import_paths})
    save_json(video_process, log_process)


def video_import_paths(video_file):
    root_path = os.path.dirname(os.path.abspath(video_file))
    log_root = uploader.log_rootpath(root_path, video_file)
    if not os.path.isdir(log_root):
        return []
    log_process = os.path.join(
        log_root, "video_process.json")
    if not os.path.isfile(log_process):
        return []
    video_process = load_json(log_process)
    if "sample_paths" in video_process:
        return video_process["sample_paths"]
    return []


def create_and_log_process_in_list(process_file_list,
                                   import_path,
                                   process,
                                   status,
                                   verbose=False,
                                   mapillary_description={}):
    for image in process_file_list:
        create_and_log_process(image,
                               import_path,
                               process,
                               status,
                               mapillary_description,
                               verbose)


def create_and_log_process(image, import_path, process, status, mapillary_description={}, verbose=False):
    # set log path
    log_root = uploader.log_rootpath(import_path, image)
    # make all the dirs if not there
    if not os.path.isdir(log_root):
        os.makedirs(log_root)
    # set the log flags for process
    log_process = os.path.join(
        log_root, process)
    log_process_succes = log_process + "_success"
    log_process_failed = log_process + "_failed"
    log_MAPJson = os.path.join(log_root, process + ".json")

    if status == "success" and not mapillary_description:
        status = "failed"
    elif status == "success":
        try:
            save_json(mapillary_description, log_MAPJson)
            open(log_process_succes, "w").close()
            open(log_process_succes + "_" +
                 str(time.strftime("%Y:%m:%d_%H:%M:%S", time.gmtime())), "w").close()
            # if there is a failed log from before, remove it
            if os.path.isfile(log_process_failed):
                os.remove(log_process_failed)
        except:
            # if the image description could not have been written to the
            # filesystem, log failed
            print("Error, " + process + " logging failed for image " + image)
            status = "failed"

    if status == "failed":
        open(log_process_failed, "w").close()
        open(log_process_failed + "_" +
             str(time.strftime("%Y:%m:%d_%H:%M:%S", time.gmtime())), "w").close()
        # if there is a success log from before, remove it
        if os.path.isfile(log_process_succes):
            os.remove(log_process_succes)
        # if there is meta data from before, remove it
        if os.path.isfile(log_MAPJson):
            if verbose:
                print("Warning, {} in this run has failed, previously generated properties will be removed.".format(
                    process))
            os.remove(log_MAPJson)


def user_properties(user_name,
                    import_path,
                    process_file_list,
                    organization_name=None,
                    organization_key=None,
                    private=False,
                    verbose=False):
    # basic
    try:
        user_properties = uploader.authenticate_user(user_name)
    except:
        print("Error, user authentication failed for user " + user_name)
        return None
    # organization validation
    if organization_name or organization_key:
        organization_key = process_organization(user_properties,
                                                organization_name,
                                                organization_key,
                                                private)
        user_properties.update(
            {'MAPOrganizationKey': organization_key, 'MAPPrivate': private})

    # remove uneeded credentials
    if "user_upload_token" in user_properties:
        del user_properties["user_upload_token"]
    if "user_permission_hash" in user_properties:
        del user_properties["user_permission_hash"]
    if "user_signature_hash" in user_properties:
        del user_properties["user_signature_hash"]

    return user_properties


def user_properties_master(user_name,
                           import_path,
                           process_file_list,
                           organization_key=None,
                           private=False,
                           verbose=False):

    try:
        master_key = uploader.get_master_key()
    except:
        print("Error, no master key found.")
        print("If you are a user, run the process script without the --master_upload, if you are a Mapillary employee, make sure you have the master key in your config file.")
        return None

    user_properties = {"MAPVideoSecure": master_key}
    user_properties["MAPSettingsUsername"] = user_name
    try:
        user_key = uploader.get_user_key(user_name)
    except:
        print("Error, no user key obtained for the user name " + user_name +
              ", check if the user name is spelled correctly and if the master key is correct")
        return None
    user_properties['MAPSettingsUserKey'] = user_key

    if organization_key and private:
        user_properties.update(
            {'MAPOrganizationKey': organization_key, 'MAPPrivate': private})

    return user_properties


def process_organization(user_properties, organization_name=None, organization_key=None, private=False):
    if not "user_upload_token" in user_properties or not "MAPSettingsUserKey" in user_properties:
        print(
            "Error, can not authenticate to validate organization import, upload token or user key missing in the config.")
        sys.exit()
    user_key = user_properties["MAPSettingsUserKey"]
    user_upload_token = user_properties["user_upload_token"]
    if not organization_key and organization_name:
        try:
            organization_key = uploader.get_organization_key(user_key,
                                                             organization_name,
                                                             user_upload_token)
        except:
            print("Error, could not obtain organization key, exiting...")
            sys.exit()

    # validate key
    try:
        uploader.validate_organization_key(user_key,
                                           organization_key,
                                           user_upload_token)
    except:
        print("Error, organization key validation failed, exiting...")
        sys.exit()

    # validate privacy
    try:
        uploader.validate_organization_privacy(user_key,
                                               organization_key,
                                               private,
                                               user_upload_token)
    except:
        print("Error, organization privacy validation failed, exiting...")
        sys.exit()

    return organization_key


def inform_processing_start(import_path, len_process_file_list, process, skip_subfolders=False):

    total_file_list = uploader.get_total_file_list(
        import_path, skip_subfolders)
    print("Running {} for {} images, skipping {} images.".format(process,
                                                                 len_process_file_list,
                                                                 len(total_file_list) - len_process_file_list))


def load_geotag_points(process_file_list, import_path, verbose=False):

    file_list = []
    capture_times = []
    lats = []
    lons = []
    directions = []

    for image in process_file_list:
                # check the status of the geotagging
        log_root = uploader.log_rootpath(import_path,
                                         image)
        geotag_data = get_geotag_data(log_root,
                                      image,
                                      import_path,
                                      verbose)
        if not geotag_data:
            create_and_log_process(image,
                                   import_path,
                                   "sequence_process",
                                   "failed",
                                   verbose=verbose)
            continue
        # assume all data needed available from this point on
        file_list.append(image)
        capture_times.append(datetime.datetime.strptime(geotag_data["MAPCaptureTime"],
                                                        '%Y_%m_%d_%H_%M_%S_%f'))
        lats.append(geotag_data["MAPLatitude"])
        lons.append(geotag_data["MAPLongitude"])
        directions.append(
            geotag_data["MAPCompassHeading"]["TrueHeading"])

        # remove previously created duplicate flags
        duplicate_flag_path = os.path.join(log_root,
                                           "duplicate")
        if os.path.isfile(duplicate_flag_path):
            os.remove(duplicate_flag_path)

    return file_list, capture_times, lats, lons, directions


def split_sequences(capture_times, lats, lons, file_list, directions, cutoff_time, cutoff_distance):

    split_sequences = []
    # sort based on time
    sort_by_time = zip(capture_times,
                       file_list,
                       lats,
                       lons,
                       directions)
    sort_by_time.sort()
    capture_times, file_list, lats, lons, directions = [
        list(x) for x in zip(*sort_by_time)]
    latlons = zip(lats,
                  lons)

    # interpolate time, in case identical timestamps
    capture_times, file_list = interpolate_timestamp(capture_times,
                                                     file_list)

    # initialize first sequence
    sequence_index = 0
    split_sequences.append({"file_list": [
        file_list[0]], "directions": [directions[0]], "latlons": [latlons[0]]})

    if len(file_list) >= 1:
        # diff in capture time
        capture_deltas = [
            t2 - t1 for t1, t2 in zip(capture_times, capture_times[1:])]

        # distance between consecutive images
        distances = [gps_distance(ll1, ll2)
                     for ll1, ll2 in zip(latlons, latlons[1:])]

        # if cutoff time is given use that, else assume cutoff is
        # 1.5x median time delta
        if cutoff_time is None:
            if verbose:
                print(
                    "Warning, sequence cut-off time is None and will therefore be derived based on the median time delta between the consecutive images.")
            median = sorted(capture_deltas)[
                len(capture_deltas) // 2]
            if type(median) is not int:
                median = median.total_seconds()
            cutoff_time = 1.5 * median
        else:
            cutoff_time = float(cutoff_time)
        cut = 0
        for i, filepath in enumerate(file_list[1:]):
            cut_time = capture_deltas[i].total_seconds(
            ) > cutoff_time
            cut_distance = distances[i] > cutoff_distance
            if cut_time or cut_distance:
                cut += 1
                # delta too big, start new sequence
                sequence_index += 1
                split_sequences.append({"file_list": [
                    filepath], "directions": [directions[1:][i]], "latlons": [latlons[1:][i]]})
                if verbose:
                    if cut_distance:
                        print('Cut {}: Delta in distance {} meters is bigger than cutoff_distance {} meters at {}'.format(
                            cut, distances[i], cutoff_distance, file_list[i + 1]))
                    elif cut_time:
                        print('Cut {}: Delta in time {} seconds is bigger then cutoff_time {} seconds at {}'.format(
                            cut, capture_deltas[i].total_seconds(), cutoff_time, file_list[i + 1]))
            else:
                # delta not too big, continue with current
                # group
                split_sequences[sequence_index]["file_list"].append(
                    filepath)
                split_sequences[sequence_index]["directions"].append(
                    directions[1:][i])
                split_sequences[sequence_index]["latlons"].append(
                    latlons[1:][i])
    return split_sequences


def interpolate_timestamp(capture_times,
                          file_list):
    '''
    Interpolate time stamps in case of identical timestamps
    '''
    timestamps = []
    num_file = len(file_list)

    time_dict = OrderedDict()

    if num_file < 2:
        return capture_times, file_list

    # trace identical timestamps (always assume capture_times is sorted)
    time_dict = OrderedDict()
    for i, t in enumerate(capture_times):
        if t not in time_dict:
            time_dict[t] = {
                "count": 0,
                "pointer": 0
            }

            interval = 0
            if i != 0:
                interval = (t - capture_times[i - 1]).total_seconds()
                time_dict[capture_times[i - 1]]["interval"] = interval

        time_dict[t]["count"] += 1

    if len(time_dict) >= 2:
        # set time interval as the last available time interval
        time_dict[time_dict.keys()[-1]
                  ]["interval"] = time_dict[time_dict.keys()[-2]]["interval"]
    else:
        # set time interval assuming capture interval is 1 second
        time_dict[time_dict.keys()[0]]["interval"] = time_dict[time_dict.keys()[
            0]]["count"] * 1.

    # interpolate timestampes
    for f, t in zip(file_list,
                    capture_times):
        d = time_dict[t]
        s = datetime.timedelta(
            seconds=d["pointer"] * d["interval"] / float(d["count"]))
        updated_time = t + s
        time_dict[t]["pointer"] += 1
        timestamps.append(updated_time)

    return timestamps, file_list