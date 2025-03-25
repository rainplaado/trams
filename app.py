import streamlit as st
import geopandas as gpd
from shapely.geometry import LineString, MultiLineString
from shapely.affinity import rotate
import numpy as np
import tempfile
import zipfile
import os
import leafmap.foliumap as leafmap
import streamlit.components.v1 as components

st.set_page_config(page_title="Field Path Optimizer", layout="wide")
st.title("🚜 Field Path Optimizer with Google Maps")
st.markdown("Upload a zipped **shapefile** (even JD-style folder-in-zip format). The app will show your field and tramlines on a Google Maps base.")

# === USER INPUT ===
machine_width = st.number_input("Machine width (m)", value=48)
current_heading = st.slider("Current driving heading (°)", min_value=0, max_value=359, value=0)
angle_step = 0.5  # Optimization resolution

# === UPLOAD SHAPEFILE ===
uploaded_file = st.file_uploader("Upload zipped shapefile (.zip)", type="zip")

if uploaded_file:
    with tempfile.TemporaryDirectory() as tmpdir:
        with zipfile.ZipFile(uploaded_file, 'r') as zip_ref:
            zip_ref.extractall(tmpdir)

        # Recursively find all .shp files
        shp_files = []
        for root, dirs, files in os.walk(tmpdir):
            for file in files:
                if file.endswith(".shp"):
                    full_path = os.path.join(root, file)
                    shp_files.append(full_path)

        if not shp_files:
            st.error("No .shp files found in the zip.")
        else:
            selected_shp = None
            if len(shp_files) == 1:
                selected_shp = shp_files[0]
                st.success(f"Found shapefile: {os.path.basename(selected_shp)}")
            else:
                file_names = [os.path.relpath(f, tmpdir) for f in shp_files]
                choice = st.selectbox("Multiple shapefiles found. Select one:", file_names)
                selected_shp = os.path.join(tmpdir, choice)

            # === LOAD FIELD ===
            gdf = gpd.read_file(selected_shp)
            if gdf.crs is None or not gdf.crs.is_projected:
                gdf = gdf.to_crs(epsg=32750)  # Adjust CRS for your region

            field_geom = gdf.geometry.iloc[0].buffer(0)
            origin = field_geom.centroid

            # === OPTIMIZATION ===
            angles = np.arange(0, 180, angle_step)
            best_angle = None
            best_pass_count = float("inf")
            best_lines = []

            for angle in angles:
                rotated_field = rotate(field_geom, angle, origin=origin, use_radians=False)
                rminy, rmaxy = rotated_field.bounds[1], rotated_field.bounds[3]
                cx = origin.x

                lines = []
                y = rminy - 2 * machine_width
                while y <= rmaxy + 2 * machine_width:
                    line = LineString([(cx - 1e5, y), (cx + 1e5, y)])
                    lines.append(line)
                    y += machine_width

                clipped = [line.intersection(rotated_field) for line in lines if not line.intersection(rotated_field).is_empty]

                pass_count = 0
                for geom in clipped:
                    if geom.is_empty:
                        continue
                    elif isinstance(geom, LineString):
                        pass_count += 1
                    elif isinstance(geom, MultiLineString):
                        pass_count += len(geom.geoms)

                if pass_count < best_pass_count:
                    best_pass_count = pass_count
                    best_angle = angle
                    best_lines = clipped

            optimized_heading_forward = (best_angle - 90) % 360
            optimized_heading_reverse = (optimized_heading_forward + 180) % 360
            final_lines = [rotate(line, -best_angle, origin=origin, use_radians=False) for line in best_lines]

            # === CURRENT HEADING ===
            adjusted_current_heading = current_heading + 90
            rotated_current_field = rotate(field_geom, adjusted_current_heading, origin=origin, use_radians=False)
            cminy, cmaxy = rotated_current_field.bounds[1], rotated_current_field.bounds[3]
            current_lines = []
            cx = origin.x
            y = cminy - 2 * machine_width
            while y <= cmaxy + 2 * machine_width:
                line = LineString([(cx - 1e5, y), (cx + 1e5, y)])
                current_lines.append(line)
                y += machine_width

            clipped_current = [line.intersection(rotated_current_field)
                               for line in current_lines if not line.intersection(rotated_current_field).is_empty]

            current_passes = 0
            for geom in clipped_current:
                if geom.is_empty:
                    continue
                elif isinstance(geom, LineString):
                    current_passes += 1
                elif isinstance(geom, MultiLineString):
                    current_passes += len(geom.geoms)

            final_current_lines = [rotate(line, -adjusted_current_heading, origin=origin, use_radians=False)
                                   for line in clipped_current]

            def heading_label(deg):
                dirs = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW', 'N']
                ix = int((deg % 360) / 45 + 0.5)
                return dirs[ix]

            # === STATS ===
            st.subheader("📊 Coverage Summary")
            col1, col2 = st.columns(2)
            with col1:
                st.metric("Optimized Heading (fwd)", f"{optimized_heading_forward:.1f}° ({heading_label(optimized_heading_forward)})")
                st.metric("Optimized Heading (rev)", f"{optimized_heading_reverse:.1f}° ({heading_label(optimized_heading_reverse)})")
                st.metric("Passes Needed", best_pass_count)
            with col2:
                st.metric("Current Heading", f"{current_heading}° ({heading_label(current_heading)})")
                st.metric("Passes Needed", current_passes)

            # === MAP ===
            st.subheader("🗺️ Field Coverage Map (Google Base)")

            # Convert lines to GeoDataFrames
            optimized_gdf = gpd.GeoDataFrame(geometry=final_lines, crs=gdf.crs)
            current_gdf = gpd.GeoDataFrame(geometry=final_current_lines, crs=gdf.crs)

            # Reproject to lat/lon
            gdf_latlon = gdf.to_crs(epsg=4326)
            optimized_latlon = optimized_gdf.to_crs(epsg=4326)
            current_latlon = current_gdf.to_crs(epsg=4326)
            origin_latlon = gdf_latlon.geometry.iloc[0].centroid

            m = leafmap.Map(center=(origin_latlon.y, origin_latlon.x), zoom=17)
            m.add_basemap("HYBRID")
            m.add_gdf(gdf_latlon, layer_name="Field Boundary", style={"color": "black", "fillOpacity": 2})
            m.add_gdf(optimized_latlon, layer_name="Optimized Lines", style={"color": "blue", "weight": 2})
            m.add_gdf(current_latlon, layer_name="Current Lines", style={"color": "red", "weight": "1,5"})

            components.html(m.to_html(), height=600)
