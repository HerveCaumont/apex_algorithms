from typing import Dict, Any
from shapely.geometry import Polygon
from shapely.ops import transform
from pyproj import Transformer
from pyproj import Transformer
from shapely.geometry import box
from shapely.ops import transform
from typing import Dict, Any
import datetime
from dateutil.relativedelta import relativedelta
from typing import Dict, Any

# spatial
def create_spatial_extent(base_extent: Dict[str, Any], size: float = 100) -> Dict[str, Any]:
        """Create a spatial extent dictionary based on a base extent and size."""
        return {
            "west": base_extent["west"],
            "south": base_extent["south"],
            "east": base_extent["west"] + size,
            "north": base_extent["south"] + size,
            "crs": base_extent["crs"],
            "srs": base_extent["srs"]
        }

def create_bbox(extent: Dict[str, Any]) -> Dict[str, Any]:
    """
    Create a Bounding Box (BBox) compatible with openEO from the spatial extent and transform the CRS if necessary.

    Parameters:
    - extent (Dict[str, Any]): A dictionary containing the spatial extent with keys "west", "south", "east", "north", and "crs".

    Returns:
    - Dict[str, Any]: A dictionary representing the bounding box in EPSG:4326 (WGS 84).
    """
    
    # Extract coordinates from extent dictionary
    west = extent["west"]
    south = extent["south"]
    east = extent["east"]
    north = extent["north"]

    # Create a bounding box (as a shapely geometry) from the corner coordinates
    bbox_geom = box(west, south, east, north)

    # Convert to EPSG:4326 (WGS 84) if necessary
    if extent["crs"] != "EPSG:4326":
        transformer = Transformer.from_crs(extent["crs"], "EPSG:4326", always_xy=True).transform
        bbox_geom = transform(transformer, bbox_geom)

    # Extract the transformed coordinates from the bounding box geometry
    minx, miny, maxx, maxy = bbox_geom.bounds

    # Return the bounding box in the format compatible with openEO
    return {
        "west": minx,
        "south": miny,
        "east": maxx,
        "north": maxy
    }

#temporal

def create_temporal_extent(start_date_str: str, nb_months:int):
    """
    Create a temporal extent by adding months to the start date and adjusting for invalid dates.
    
    Args:
    start_date_str (str): The start date as a string in "YYYY-MM-DD" format.
    nb_months (int): The number of months to add.

    Returns:
    list: A list with the start date and end date as strings in "YYYY-MM-DD" format.
    """
    # Convert the start date string to a datetime object
    startdate = datetime.datetime.strptime(start_date_str, "%Y-%m-%d")
    
    # Add the number of months using relativedelta
    enddate = startdate + relativedelta(months=nb_months)
    
    # Convert the datetime objects back to strings
    return [startdate.strftime("%Y-%m-%d"), enddate.strftime("%Y-%m-%d")]


import pandas as pd
from utils import create_spatial_extent, create_bbox, create_temporal_extent

def prepare_jobs_df(spatial_extents, temporal_extents, start_spatial, start_temporal, repetition) -> pd.DataFrame:
    """Prepare a DataFrame containing job configurations for benchmarking."""
    jobs = []

    # Create combinations for the spatial grid
    for spatial in spatial_extents:
            for temporal in temporal_extents:
                 
                spatial_extent = create_bbox(create_spatial_extent(start_spatial, spatial))
                temporal_extent = create_temporal_extent(start_temporal, temporal)

                for ii in range(repetition):

                        jobs.append({
                            "spatial_extent": spatial_extent,
                            "temporal_extent": temporal_extent,
                        })

    return pd.DataFrame(jobs)