from enum import Enum


class OutputFormat(Enum):
    IMAGE_TIFF_DEPTH_32 = "tiff32"
    APPLICATION_OCTET_STREAM = "octet"
    OTHER = None


def calculate_processing_units(
    batch_processing: bool,
    width: int,
    height: int,
    n_input_bands_without_datamask: int,
    output_format: OutputFormat,
    n_data_samples: int = 1,
    s1_orthorectification: bool = False,
) -> float:
    # https://docs.sentinel-hub.com/api/latest/api/overview/processing-unit/
    pu = 1.0

    # Processing with batch processing API will result in a multiplication factor of 1/3.
    # Thus, three times more data can be processed comparing to process API for the same amount of PUs.
    if batch_processing:
        pu = pu / 3.0

    # The multiplication factor is calculated by dividing requested output (image) size (width x height) by 512 x 512.
    # The minimum value of this multiplication factor is 0.01. This corresponds to an area of 0.25 km2 for
    # Sentinel-2 data at 10 m spatial resolution.
    pu *= max((width * height) / (512.0 * 512.0), 0.01)

    # The multiplication factor is calculated by dividing the requested number of input bands by 3.
    # An exception is requesting dataMask which is not counted.
    pu *= n_input_bands_without_datamask / 3.0

    # Requesting 32 bit float TIFF will result in a multiplication factor of 2 due to larger memory consumption and data traffic.
    # Requesting application/octet-stream will result in a multiplication factor of 1.4 due to additional integration costs
    # (This is used for integration with external tools such as xcube.).
    if output_format == OutputFormat.IMAGE_TIFF_DEPTH_32:
        pu *= 2.0
    elif output_format == OutputFormat.APPLICATION_OCTET_STREAM:
        pu *= 1.4

    # The multiplication factor equals the number of data samples per pixel.
    pu *= n_data_samples

    # Requesting orthorectification (for S1 GRD data) will result in a multiplication factor of 2 due to additional
    # processing requirements (This rule is not applied at the moment.).
    # if s1_orthorectification:
    #     pu *= 2.

    # The minimal weight for a request is 0.001 PU.
    pu = max(pu, 0.001)
    return pu
