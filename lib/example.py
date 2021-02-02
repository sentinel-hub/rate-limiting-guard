import os


import requests


from rlguard import calculate_processing_units, OutputFormat


def request_auth_token(client_id, client_secret):
    r = requests.post(
        "https://services.sentinel-hub.com/oauth/token",
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        },
    )
    r.raise_for_status()
    j = r.json()
    return j["access_token"]


def get_map(auth_token, output_filename=None):
    # example taken from: https://docs.sentinel-hub.com/api/latest/data/sentinel-2-l1c/examples/#true-color
    r = requests.post(
        "https://services.sentinel-hub.com/api/v1/process",
        headers={
            "Authorization": f"Bearer {auth_token}",
        },
        json={
            "input": {
                "bounds": {
                    "properties": {"crs": "http://www.opengis.net/def/crs/OGC/1.3/CRS84"},
                    "bbox": [13.822174072265625, 45.85080395917834, 14.55963134765625, 46.29191774991382],
                },
                "data": [
                    {
                        "type": "S2L1C",
                        "dataFilter": {"timeRange": {"from": "2018-10-01T00:00:00Z", "to": "2018-12-31T00:00:00Z"}},
                    }
                ],
            },
            "output": {
                "width": 512,
                "height": 512,
            },
            "evalscript": """
                //VERSION=3
                function setup() {
                  return {
                    input: ["B02", "B03", "B04"],
                    output: {
                      bands: 3,
                      sampleType: "AUTO" // default value - scales the output values from [0,1] to [0,255].
                     }
                  }
                }
                function evaluatePixel(sample) {
                  return [2.5 * sample.B04, 2.5 * sample.B03, 2.5 * sample.B02]
                }
            """,
        },
    )
    r.raise_for_status()
    if output_filename:
        with open(output_filename, "wb") as fh:
            fh.write(r.content)


def main():
    CLIENT_ID = os.environ.get("CLIENT_ID")
    CLIENT_SECRET = os.environ.get("CLIENT_SECRET")
    if not CLIENT_ID or not CLIENT_SECRET:
        raise Exception("Please supply CLIENT_ID and CLIENT_SECRET env vars!")

    auth_token = request_auth_token(CLIENT_ID, CLIENT_SECRET)

    pu = calculate_processing_units(False, 1024, 1024, 4, OutputFormat.IMAGE_TIFF_DEPTH_32, 2, True)
    print(pu)
    get_map(auth_token, "output.jpg")


if __name__ == "__main__":
    main()
