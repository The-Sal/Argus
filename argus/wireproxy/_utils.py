import os
import re
import requests
from tqdm import tqdm
from urllib.parse import urlsplit


def get_filename_from_cd(cd):
    """
    Get filename from Content-Disposition header
    """
    if not cd:
        return None
    fname = re.findall('filename=(.+)', cd)
    if len(fname) == 0:
        return None
    return fname[0].strip('"\'')


def download(url):
    # Make HEAD request first to get headers
    response = requests.get(url, stream=True, allow_redirects=True)

    # Try to get filename from Content-Disposition header
    filename = get_filename_from_cd(response.headers.get('content-disposition'))

    # If not found, extract from URL
    if not filename:
        urlpath = urlsplit(url).path
        filename = os.path.basename(urlpath)

    # Fallback if still no filename
    if not filename:
        filename = 'downloaded_file'

    # Get total file size
    total_size = int(response.headers.get('content-length', 0))

    # Download with progress bar
    with open(filename, 'wb') as file, tqdm(
            desc=filename,
            total=total_size,
            unit='iB',
            unit_scale=True,
            unit_divisor=1024,
    ) as bar:
        for data in response.iter_content(chunk_size=1024):
            size = file.write(data)
            bar.update(size)

    print(f"Downloaded: {filename}")
    return filename, response